# skillgraph

[![ci](https://github.com/bythyag/skillgraph/actions/workflows/ci.yml/badge.svg)](https://github.com/bythyag/skillgraph/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.10%E2%80%933.14-blue)

**Find out which lines of your agent skill actually do anything.**

skillgraph compiles a `SKILL.md` into checkable claims, runs an agent on real tasks
with the skill, without it, and with parts of it deleted, then tells you, line by
line, what the agent follows, what it ignores, and what it would have done anyway.

```
SKILL.md ─extract─▶ claims lockfile ─lint─▶ static findings
                         │
tasks + fixtures ─run─▶ traces: with skill / without skill / lines removed
                         │
                    ─check─▶ verdict per claim per run ─report─▶ HTML: line heatmap, claim graph, ablations
```

Every line gets one of five verdicts:

| Verdict | What happened | What to do |
|---|---|---|
| **working** | followed with the skill, not without it | keep it |
| **inert** | followed with and without the skill | cut it to save context (keep safety rules) |
| **ignored** | not followed even with the skill | make it concrete; give it a done-criterion |
| **interfering** | followed without the skill, not with it | find the line that pushes the other way |
| **untested** | no task reached it | add a task for that branch |

It works with Claude (through the Claude Agent SDK, so agents run in the real Claude
Code harness) and with any model on [OpenRouter](https://openrouter.ai). The agent,
the claim extractor, and the judge can each be a different model.

Two more inputs plug into the same checker:

- **Run trees**: real agent traces, imported from Claude Code transcripts, OpenTelemetry,
  LangSmith, the OpenAI Agents SDK, or any JSON through a small mapping file.
- **Reference trees**: the process a skill is meant to encode (an SOP, runbook, decision
  tree, or flowchart), compared with both the skill and what agents did.

```
        reference tree
        /            \
  coverage        correctness
      /                \
skill graph -adherence- run trees
```

## Quick start

Requires Python 3.10+ and git. [uv](https://docs.astral.sh/uv/) is recommended.

```bash
git clone https://github.com/bythyag/skillgraph && cd skillgraph
uv sync --all-extras          # or: pip install -e ".[claude]"
```

Look at real results first; this costs nothing. `examples/runs/demo` is a finished run set
(40 agent runs on three example skills, already graded):

```bash
uv run skillgraph summary examples/runs/demo                  # verdicts per skill, gaps against the SOP
uv run skillgraph show examples/runs/demo/release-notes/minor-release/skill/r1   # one run, claim by claim
uv run skillgraph report examples/runs/demo -o demo.html && open demo.html
```

Then check your credentials:

```bash
uv run skillgraph doctor      # provider status and which model each role uses
```

Credentials, depending on which models you use:

- **Claude** (`claude:<model>`): export `ANTHROPIC_API_KEY`, or be logged in to Claude Code
  on this machine. The Agent SDK uses whichever it finds. Logging in with your own Claude
  account is fine for local evaluation; if you build skillgraph into a product for other
  people, Anthropic's terms require API-key authentication.
- **OpenRouter** (`openrouter:<vendor>/<model>`): export `OPENROUTER_API_KEY`
  ([create one](https://openrouter.ai/keys)).

Run one task yourself. On Claude Opus this is about $3 (a run with the skill, one without,
judging, and compiling the claims); with a cheap model as agent and judge it's cents:

```bash
uv run skillgraph estimate examples/suite.yaml --skills release-notes --tasks minor-release --runs 1 --top 0
uv run skillgraph pipeline examples/suite.yaml --out runs/try \
  --skills release-notes --tasks minor-release --runs 1 --top 0
open runs/try/report.html
```

Everything that spends money shows an estimate first and asks before starting; pass `--yes`
in scripts, and `--max-total-usd` to cap a run set.

## Choosing models

Models are written `provider:model`:

```
claude:claude-opus-5-5                   Claude via the Agent SDK (Claude Code harness)
claude:claude-sonnet-5-5
openrouter:anthropic/claude-sonnet-5.5   any OpenRouter model (skillgraph's own agent harness)
openrouter:openai/gpt-6.1-sol
openrouter:google/gemini-3.8-flash
```

`skillgraph models --search <text>` lists what's available, with prices and whether a
model supports tool calling (needed for the agent role).

There are three roles:

| Role | What it does | Flag | Env var |
|---|---|---|---|
| agent | runs the tasks: the model whose skill use you're measuring | `--agent-model` | `SKILLGRAPH_AGENT_MODEL` |
| extractor | compiles SKILL.md into claims (once per skill change) | `--extractor-model` | `SKILLGRAPH_EXTRACTOR_MODEL` |
| judge | grades claims that need judgment | `--judge-model` | `SKILLGRAPH_JUDGE_MODEL` |

A flag beats the env var, which beats the suite's `models:` block, which beats the
default (`claude:claude-opus-5-5` for all three). For example:

```bash
# measure how GPT follows the skill, with Claude extracting and a cheap model judging
skillgraph pipeline evals/my-skill/suite.yaml --out runs/gpt \
  --agent-model openrouter:openai/gpt-6.1-sol \
  --judge-model openrouter:google/gemini-3.8-flash --judge-effort low
```

`--effort` (agent), `--extractor-effort` and `--judge-effort` set reasoning effort
(`low` … `max`) on models that support it.

Claude agents run in Claude Code itself. OpenRouter agents run in a small harness
that offers the same tools (`Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `Skill`)
with the same input fields, and loads skills the same way. One claims file therefore
checks runs from either provider. The surrounding harness differs, so
cross-provider numbers compare what agents did, not the models in isolation.

## Evaluate your own skill

```bash
skillgraph init path/to/my-skill --dir evals/my-skill    # scaffolds suite.yaml + fixtures/
```

1. **Write 2-4 tasks** in `evals/my-skill/suite.yaml`: realistic prompts, starting
   files under `fixtures/`, and success checks. Cover each branch the skill describes.
   [skills/skillgraph/references/writing-suites.md](skills/skillgraph/references/writing-suites.md)
   explains what makes a good suite.
2. **Extract and review the claims.** `skillgraph extract evals/my-skill/suite.yaml`
   writes `evals/my-skill/claims/my-skill.claims.json`. Read it, and fix ordering edges
   the text doesn't state, regexes that are too strict, and conditions phrased as agent
   observations. It's a lockfile: commit it. `skillgraph lint` shows static problems.
3. **Smoke test, then run.**
   ```bash
   skillgraph run evals/my-skill/suite.yaml --out runs/my-skill --runs 1 --tasks <one-task>
   skillgraph pipeline evals/my-skill/suite.yaml --out runs/my-skill --runs 3
   ```
4. **Read `runs/my-skill/report.html`**, edit the skill, and rerun into a new `--out`
   directory to compare. `extract` notices the skill changed.

## Use it from Claude Code

skillgraph ships an agent skill that lets Claude set up and drive the whole process:
scaffold a suite, write tasks, review claims, estimate cost, run, read the report, and
propose edits to your skill.

```bash
skillgraph install-skill --user              # ~/.claude/skills/skillgraph, all projects
skillgraph install-skill --project path/to/repo
```

Then ask Claude Code something like "evaluate my deploy skill" or type `/skillgraph`.

The skill covers evaluating a skill, grading imported traces, and comparing with a reference
process. It also carries [a complete build spec](skills/skillgraph/references/build-spec.md):
architecture, data formats, the exact prompts and schemas, CLI, and tests. Given only
that skill, Claude can rebuild this repository from scratch. A test keeps the spec in
sync with the code.

## Grade real traces

Import sessions from wherever your agent runs, then check and report as usual:

```bash
skillgraph import ~/.claude/projects/<project>/ --suite evals/my-skill/suite.yaml \
  --skill my-skill --out runs/my-skill-prod --dry-run      # summary only; drop --dry-run to write
skillgraph check runs/my-skill-prod && skillgraph report runs/my-skill-prod
```

| Format | Pass |
|---|---|
| Claude Code | a project directory under `~/.claude/projects/` or one session `.jsonl`; subagent transcripts are attached under the call that spawned them |
| OpenTelemetry | OTLP JSON or a list of spans, with GenAI (`gen_ai.*`) or OpenInference (`openinference.*`, `tool.name`, `input.value`) attributes |
| LangSmith | a list of runs, JSONL, or nested `child_runs` |
| OpenAI Agents SDK | exported spans (`span_data.type: function`) |
| Anything else | `--format mapping.yaml`: a few lines saying where tool calls, inputs, outputs, and parents live |

Each trace is cut at the point the skill loaded. Tool names are mapped onto the names claims
use (`execute_command` and `run_shell_command` become `Bash`, `read_file` becomes `Read`,
and so on). Final files are rebuilt from the Write and Edit calls, and the tree is kept
(parent, depth, subagent). Details, the mapping format, and limits are in
[importing-traces.md](skills/skillgraph/references/importing-traces.md).

## Compare with a reference process

```bash
skillgraph reference docs/release-sop.md        # SOP / decision tree / flowchart -> release-sop.ref.yaml (review it)
# add `reference: docs/release-sop.ref.yaml` to the skill in suite.yaml
skillgraph extract evals/my-skill/suite.yaml    # also compiles the reference into checkable steps
skillgraph coverage evals/my-skill/suite.yaml   # skill graph vs reference: gaps, unhandled branches, conflicts
skillgraph tasks evals/my-skill/suite.yaml --skill my-skill --untested-in runs/my-skill   # draft a task per untested path
```

Mermaid flowcharts are parsed directly; prose documents go through the extractor model.
After that, `check` grades every run against the skill and the reference in one judge call. The report's
**Reference process** panel puts the comparisons side by side. For each step of the process it shows
whether the skill covers it, whether agents do it with and without the skill, and a diagnosis:

| Diagnosis | Meaning |
|---|---|
| **gap** | the process needs it, the skill doesn't ask for it, and agents don't do it: add it to the skill |
| **ignored** | the skill asks for it and agents don't do it: harden those lines |
| **conflict** | the skill contradicts the process |
| **implicit** | not in the skill, but agents do it anyway |
| **untested** | no run took that path yet: `skillgraph tasks` drafts one |

Generated tasks come with fixtures and `setup.sh` scripts that run on your machine. Read them
first; the command flags scripts that use absolute paths, the network, or `sudo`. See
[reference-trees.md](skills/skillgraph/references/reference-trees.md).

## Commands

| Command | Purpose |
|---|---|
| `doctor [SUITE] [--ping]` | Provider status, resolved model per role, optional test call per model |
| `models [--search T]` | Claude models and OpenRouter models that support tool calling |
| `init SKILL_DIR` | Scaffold a suite for a skill |
| `install-skill [--user \| --project DIR]` | Install the skillgraph agent skill for Claude Code |
| `extract SUITE` | Compile each SKILL.md into a claims lockfile (skipped when current) |
| `lint SUITE` | Static findings; exits 1 on errors |
| `graph SUITE --skill X --format cypher\|graphml\|json` | Export the claim graph (Cypher loads into Neo4j) |
| `run SUITE --out DIR` | Run the agent: `--variants skill,baseline,ablate-L<a>-<b>`, `--runs`, `--trigger invoke\|auto`, `--enforce` |
| `check DIR` | Grade traces; judge verdicts are cached |
| `ablate DIR --top 1` | Remove the clearest working, inert, and ignored spans, rerun, grade |
| `report DIR` | Write `DIR/report.html` |
| `import SOURCES --suite S --skill X --out DIR` | Import traces from other tools as runs (`--dry-run`, `--format`, `--tool-map`, `--include-unloaded`) |
| `reference SOURCE` | Convert an SOP, decision tree, or flowchart into a reference tree |
| `coverage SUITE` | Compare each skill with its reference tree; exits 1 on conflicts |
| `tasks SUITE --skill X` | Draft one eval task per reference path (`--untested-in DIR` for only the missing ones) |
| `pipeline SUITE --out DIR` | extract, lint, coverage, run, check, ablate, report (one cost confirmation; `--strict` stops on lint errors) |
| `estimate SUITE` | What a pipeline would cost, from earlier runs or model prices; spends nothing |
| `summary DIR` | Results as text, `--json`, or `--junit FILE`; `--fail-on` sets the exit code |
| `diff BEFORE AFTER` | Claim-by-claim changes between two run sets (`--json`, `--markdown`) |
| `show RUN_DIR` | One run as a readable transcript with claim results inline |

Commands that spend money (`run`, `check`, `ablate`, `pipeline`) print an estimate and ask
first; `-y/--yes` skips the question and `--max-total-usd` caps the spend.

`--trigger auto` lets the agent decide whether to load the skill, so a run measures
triggering as well as adherence (the default, `invoke`, loads it up front).
`--enforce` turns mechanically checkable "never" rules into blocks on matching tool
calls during the run. Runs that already finished are skipped when you rerun a command, so
an interrupted run set resumes; `--retry-errors` reruns the ones that failed.

## Read the results

```bash
skillgraph summary runs/x                       # verdicts, weak verdicts, reference diagnoses
skillgraph summary runs/x --json                # the same, machine-readable
skillgraph show runs/x/<skill>/<task>/skill/r1  # one run as a transcript, each claim next to its evidence
skillgraph diff runs/before runs/after          # after editing a skill: which claims got better or worse
skillgraph diff runs/before runs/after --markdown   # for a pull-request comment
```

Verdicts that rest on fewer than 3 runs per variant are marked weak. Treat them as hints
until you add `--runs`.

## Use it in CI

`summary --fail-on` turns verdicts into an exit code, and `--junit` writes a report CI
systems display:

```yaml
# .github/workflows/skill-eval.yml (example)
on:
  pull_request:
    paths: ["skills/**", "evals/**"]
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv tool install "skillgraph[claude] @ git+https://github.com/bythyag/skillgraph"
      - run: skillgraph pipeline evals/my-skill/suite.yaml --out runs/ci --runs 2 --top 0 --yes --max-total-usd 15
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - run: skillgraph summary runs/ci --fail-on ignored,interfering,gap,conflict --junit skillgraph.xml
```

`--fail-on` accepts `ignored`, `interfering`, `untested`, `gap`, `conflict`, `partial`,
`unreached`, `ref-ignored`, `lint-error`, and `task-failure`. Every pull request that runs
this spends money, so keep `--runs` low and set `--max-total-usd`.

## How it works

- **Claims.** The extractor splits the skill into atomic instructions anchored to line
  spans. Each has a kind (`step`, `must_not`, `must_read`, `artifact`), optional `after`
  edges (only where the text states an order), an optional branch condition, and a check:
  a tool-call pattern, a final-file pattern, or a yes/no question for the judge.
- **Runs.** Each run copies the task's fixture into a fresh git workspace, installs the
  skill (or not, or with lines deleted), and runs the agent. The trace records every tool
  call with its input and output, the agent's text, the final files, and git state.
- **Checks.** Mechanical checks run first; the rest go to one judge call per trace. Claims
  on a branch that wasn't taken are `n/a`; a step done before something it must follow is
  `out_of_order`.
- **Verdicts** compare follow rates with and without the skill. **Ablation** deletes a
  span and reruns: deleting a working span should change behavior, and deleting an inert
  span shouldn't.

[docs/design.md](docs/design.md) covers the reasoning, prior art, and limits.

### Suite format

```yaml
models:                                   # optional
  judge: openrouter:google/gemini-3.8-flash
skills:
  - path: skills/csv-report               # directory containing SKILL.md
    claims: claims/csv-report.claims.json
    tasks:
      - id: broken-data
        fixture: fixtures/sales-broken    # copied into the workspace; setup.sh runs if present
        prompt: Create the sales report for data/sales.csv.
        success:
          - file: ERRORS.md               # exists
          - file: report.md               # exists and matches
            contains: "Top product"
          - unchanged: data/sales.csv     # byte-identical to the fixture
          - cmd: python3 -m unittest      # exits 0
```

### What a run produces

```
runs/<name>/
  runset.json                              suite, models, skill paths (relative)
  report.html
  <skill>/<task>/<variant>/r<n>/
    ws/                                    the agent's workspace after the run
    trace.json                             tool calls, text, final files, git state, success checks
    check.json                             status + reason per claim, cached judge verdicts
```

## Example results

`examples/` has three workflow skills with two tasks each. The demo run (each task twice
with the skill and twice without, plus ablations on two skills, on Claude Opus 5.5)
found:

- **csv-report does real work.** Tasks passed 4/4 with it and 0/4 without, and 10 of 16
  claims are working. Deleting "if validation fails, write ERRORS.md and stop" dropped
  task success to 50%. Deleting "validate first" changed nothing, because the next two
  lines already imply validation. The skill also points at a `references/regions.md`
  that doesn't exist, which lint reports as an error.
- **bugfix-tdd is mostly redundant for this model.** 8 of 11 claims are inert: the model
  writes a failing test first and doesn't commit without being told. What the skill
  adds is its conventions file, the exact test command, and FIX_NOTES.md.
- **release-notes** has a branch no task reaches (the patch bump), reported as untested.
- **release-notes vs the team SOP** (`examples/references/release-sop.md`): the skill covers
  75% of the process. Coverage flags that the skill never says what to do when there are no
  commits since the last tag, never asks to credit authors, and only partly asks for migration
  notes. `skillgraph tasks` drafted tasks for the two untested paths. In those runs the agent
  handled "nothing to release" correctly without instructions (implicit), but never credited
  authors (a gap). With the skill, agents did 91% of the SOP's steps; without it, 83%.

## Cost and safety

**Cost.** On Claude Opus, an agent run on these small tasks costs roughly $0.15-1.50 and
judging a run $0.05-0.50. A suite of 3 tasks × 2 variants × 2 runs plus ablations is 15-30
runs. `skillgraph estimate` shows the number before you start, paid commands ask first,
`--max-total-usd` caps a run set, and `--budget` caps each run. A cheap agent and judge model
cut this by 10-100x.

**What runs on your machine.** Agent runs execute shell commands on the host, inside
temporary workspaces under the run directory, with your user's permissions:

- Claude agents run in Claude Code with Bash allowed and no permission prompts, and with the
  credentials Claude Code needs. They are cut off from MCP servers, including claude.ai
  connectors, so a run can't reach your Gmail or Drive.
- OpenRouter agents run in skillgraph's harness: file tools can't leave the workspace and API
  keys are removed from the shell environment, but `Bash` can still reach the machine.
- Fixture `setup.sh` scripts and `cmd:` success checks run with API keys removed.

Treat a skill you didn't write like code you didn't write. See [SECURITY.md](SECURITY.md).

### Run untrusted skills in a container

```bash
docker build -t skillgraph .
docker run --rm -it -e ANTHROPIC_API_KEY -v "$PWD":/work skillgraph doctor
docker run --rm -it -e ANTHROPIC_API_KEY -v "$PWD":/work \
  skillgraph pipeline evals/x/suite.yaml --out runs/x --runs 2 --yes --max-total-usd 10
```

Inside the container only API-key auth works (`ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`),
and the agent can only reach the mounted folder.

## Development

```bash
uv sync --all-extras
uv run pytest            # no network; providers are mocked
uv run ruff check src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the rules (tests stay offline, the build spec
tracks every prompt) and [CHANGELOG.md](CHANGELOG.md) for what changed.

```
src/skillgraph/
  cli.py  config.py  schema.py  extract.py  graph.py  runner.py  checker.py  aggregate.py  report.py  scaffold.py
  providers/  base.py  claude.py  openrouter.py  harness.py  tools.py
  trees/      mapping.py  runs.py (import)  reference.py  coverage.py  taskgen.py
  templates/report.html
  costs.py (estimates, the spend gate)  summary.py  diff.py  show.py
skills/skillgraph/        agent skill: SKILL.md + references/ (build spec, writing suites, importing traces, reference trees)
examples/                 suite.yaml, suite.generated.yaml, skills/, fixtures/, claims/, references/, runs/demo, demo-report.html
scripts/export_demo.py    regenerates examples/runs/demo from a real run set, scrubbing paths and account details
tests/  docs/  .github/   Dockerfile
```

If you change a prompt or schema (extractor, judge, harness, reference conversion and compilation,
coverage alignment, task generation), update `skills/skillgraph/references/build-spec.md`;
`tests/test_spec.py` fails until the spec quotes the new text.

## Limitations

- About half the claims in a typical workflow skill need the judge model. Every verdict
  in `check.json` carries its reason, so you can audit it.
- Mechanical checks can miss an equivalent way of doing the same thing. Read a failing
  trace before rewriting a line marked ignored.
- Two runs per variant only gives rates of 0, 50, or 100%. Use 3-5 runs before acting on
  a single claim.
- Extraction isn't deterministic, which is why the claims file is a reviewed lockfile.
