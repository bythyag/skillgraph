# Design notes

## The problem

A skill is a prompt that ships as a file. Teams write them for multi-step
workflows (release processes, data pipelines, review checklists) and then can't
tell which parts of the file the agent actually uses. Pass/fail evals say whether
a task succeeded, not which sentence made the difference, so skills grow by
accretion: every incident adds a line and nothing gets removed.

skillgraph answers a narrower question: **for each line of this skill, what
happens to the agent's behavior with it, without it, and without just that line?**

## Why claims, not a full workflow graph

The first design compiled each skill into a DAG of steps and checked traces for
path coverage, in the spirit of SOP-compliance benchmarks. Two problems:

- Most real skills aren't procedures. Reference skills, style guides, and tool
  docs are bags of rules with a short spine of steps. A DAG adds nothing for them.
- An LLM-extracted graph graded by an LLM judge is, underneath, a rubric. What is
  actually new is that the rubric is stable (a reviewed lockfile), graded per item,
  and tied to source lines.

So the core artifact is a list of **span-anchored, checkable claims**, with just
enough graph structure to matter in workflows: sparse `after` edges where the text
states an order, and branch conditions for "if X, do Y". The graph exists for the
checks it enables (out-of-order, not-applicable branches) and for the picture in
the report.

## Why a baseline and ablation

"The agent missed step 4" means little if the agent does step 4 anyway without the
skill, or never does it either way. The **baseline** (same task, no skill) turns
each follow rate into a comparison, which gives the verdicts:

- **working**: the line changes behavior. Keep it.
- **inert**: the model does this unprompted. The line costs context on every use.
- **ignored**: the line asks and the agent doesn't comply. Rewrite it.
- **interfering**: the agent does it without the skill but not with it.
- **untested**: no task reached it. That's a gap in the suite, not a property of the skill.

**Ablation** is the closest thing to ground truth: delete a span, rerun, compare.
Automatic picks include the clearest *working* span as a positive control; if
deleting it changes nothing, either the instruction is redundant with another line
or the method is broken. In the bundled demo, deleting bugfix-tdd's "read
conventions.md" line dropped that claim from 100% to 0% (control passed), while
deleting csv-report's "validate first" line changed nothing because two later
lines already imply validation.

## Checking

Mechanical checks (tool-call regexes, final-file globs) run first because they are
cheap, deterministic, and auditable. Everything else goes to one judge call per
trace. Judge verdicts are cached by trace and question, so reviewing the lockfile
(fixing an ordering edge, widening a regex) and re-checking costs nothing.

Mechanical checks fail in a known way: an agent reaches the same result by a
different route than the regex expects, and the claim is marked missed. The
extractor is told to write permissive alternatives, the lockfile review step exists
to catch the rest, and the skill tells Claude to read a failing trace before
calling a line ignored.

## Trees: real traces and reference processes

Test runs answer "does the agent follow the skill?" Two questions remain: does it follow
the skill on real work, and is the skill even right?

**Run trees.** Production traces from any framework become skillgraph traces. Agent traces
are trees (subagents, nested spans); import keeps parent, depth, and agent per event, and
maps each framework's tool names onto the vocabulary claims use, so one lockfile grades
them all. The cost: real sessions have no "without the skill" twin, so they support
followed/ignored but not working/inert.

**Reference trees.** A team's SOP is the ground truth a skill tries to encode. Compiling
it into the same claims format means one checker grades runs against both, in one judge
call. That gives three comparisons:

- coverage (reference vs skill, static): what the skill leaves out, contradicts, or orders
  differently, before any run;
- adherence (runs vs skill): the existing verdicts;
- correctness (runs vs reference): whether agents follow the process regardless of the skill.

Read together, they separate the fixes: a step the process needs that the skill doesn't
mention and agents skip is a **gap** (add it); a step the skill states but agents skip is
**ignored** (harden it); a step agents do without being told is **implicit** (optional).
The reference's paths also drive task generation, which closes the "untested branch" hole
that hand-written suites leave.

Two details matter for correctness. Branch conditions are facts about the situation, and a
fact can be true where the branch is never reached (a feature commit exists, but a breaking
change took the other branch first), so reference steps are gated on every branch above
them (`when_all`), not just the nearest. And end states are outcomes, not instructions, so
they are checked by their observable result and diagnosed as reached or not.

## Providers

The agent under test runs either in Claude Code (via the Claude Agent SDK, so the
real harness, system prompt, and skill loading) or in skillgraph's own harness for
OpenRouter models. The harness uses Claude Code's tool names and input fields, so a
lockfile extracted once checks traces from either. Results across providers are
comparable in what the agent did, but not a pure model comparison: the harness
around the model differs.

## Prior art

- **SOPBench** (2025) and **SAGE** (2026) compile standard operating procedures into
  graphs and check agent compliance and path coverage, on customer-service domains.
- **ContrAgent** compiles SOPs into temporal contracts and enforces them at runtime;
  skillgraph's `--enforce` does the mechanical subset of this with a PreToolUse hook.
- Skill-evaluation work from 2026 ("A Framework for Evaluating Agentic Skills at
  Scale", SkillAudit) scores adherence per skill with rubric judges.
- Claude Code's `plugin eval` grades outcomes with hand-written graders
  (`tool_used`, `tool_order`, regex, LLM); `/skill-doctor` reports usage and context
  cost.
- Format linters (skilllint, skills_lint, agnix) and prompt linters (Arbiter,
  promptproof, lintlang) check structure or find conflicting instructions statically.

What none of these do is attribute behavior to individual lines across many runs,
with a baseline and ablation behind each verdict. That is skillgraph's job.

## Known limits

- About half of the claims in typical workflow skills need the judge.
- Two runs per variant gives coarse rates; use 3-5 before rewriting a skill.
- Extraction is not deterministic. Two extractions of the same skill split claims
  differently, which is why the lockfile is pinned and reviewed.
- The extractor sees SKILL.md and the names of bundled files, not their contents.
- Agent runs execute shell commands on the host. Use a container or VM for skills
  from untrusted sources.

## Possible next steps

- Ambiguity scoring: extract several times and flag paragraphs where extractions
  disagree, once that signal is shown to predict miss rates.
- Trace mining: induce a reference tree from successful imported runs and diff it against
  the skill to find undocumented steps (the reference machinery already does the diff).
- Path-weighted reports: weight reference paths by how often they occur in production
  traces, so a gap on a common path ranks above one on a rare path.
- Cross-skill conflicts when several skills load in one session.
- A container runner for untrusted skills.
