---
name: skillgraph
description: Evaluate and harden an agent skill (a SKILL.md) with skillgraph. It runs an agent with and without the skill and reports which lines are followed, ignored, or unnecessary; grades real agent traces (Claude Code transcripts, OpenTelemetry, LangSmith, OpenAI Agents SDK, or any JSON via a mapping) against the skill; and compares the skill and its runs with a reference process (an SOP, runbook, decision tree, or flowchart). Use when the user wants to test, evaluate, benchmark, debug, or improve a skill, check production sessions against a skill, check a skill against a documented process, compare a skill before and after an edit, or install or set up skillgraph.
argument-hint: "<skill-dir or suite.yaml>"
---

# skillgraph

skillgraph measures a skill line by line. It compiles SKILL.md into checkable claims, runs an agent on real tasks with the skill, without it, and with spans removed, and gives every line a verdict: working, inert, ignored, interfering, or untested.

Pick the path that matches the request:

- Evaluate or improve a skill: **Evaluate a skill**.
- Grade real sessions or another framework's traces against a skill: **Grade real traces**.
- Check a skill (and its runs) against an SOP, runbook, decision tree, or flowchart: **Compare with a reference process**.
- Install the tool: **Set up**.
- Recreate the tool itself: **Rebuild from scratch**, only when the user explicitly asks for that.

Every path starts the same way: run `skillgraph doctor` (or set up first if `skillgraph --version` fails), and agree on cost before anything calls a model.

## Spending money

Extraction, coverage, reference conversion of prose, task generation, agent runs, and judging all call paid models. Before the first of these in a session:

1. Run `skillgraph estimate <suite> [--runs N] [--skills ...]`. It prints agent runs, judge calls, and extraction calls with a dollar estimate, and costs nothing.
2. Tell the user the number of runs, the models, and the estimate, and get a yes.
3. Only then pass `--yes` to `run`, `check`, `ablate`, or `pipeline`. Without it they stop and ask; in a non-interactive shell they refuse.
4. Add `--max-total-usd <amount>` so a run set can't overspend.

Ask again before anything that raises the cost: `--force`, more `--runs`, more tasks, ablation, or a more expensive model.

## Set up

1. `skillgraph --version`. If it prints a version, go to step 3.
2. Install from a checkout of the repository (ask the user where it is if you can't find it): `uv sync --all-extras` inside it, then `uv run skillgraph ...`; or `pip install -e "<checkout>[claude]"`.
3. `skillgraph doctor` and fix what it reports:
   - Claude models (`claude:<id>`) need the `claude` extra and either `ANTHROPIC_API_KEY` or a logged-in Claude Code.
   - OpenRouter models (`openrouter:<vendor>/<model>`) need `OPENROUTER_API_KEY`.
   - Ask the user to export keys in their own shell. Never ask them to paste a key into the conversation.
4. Choose models with the user if they care: `skillgraph models --search <text>`. Roles are set with flags, `SKILLGRAPH_<ROLE>_MODEL`, or a `models:` block in the suite.
   - agent: the model whose use of the skill is measured. Use the model the skill is written for.
   - extractor: compiles SKILL.md into claims. A strong model; it runs once per skill change.
   - judge: grades claims that need judgment. A cheaper model is fine for large suites.

## Evaluate a skill

1. `skillgraph init <skill-dir> --dir evals/<skill-name>`.
2. Write the tasks in `evals/<skill-name>/suite.yaml`. Read `references/writing-suites.md` first. Aim for 2-4 realistic tasks, with at least one per branch the skill describes.
3. Agree on cost (see **Spending money**), then compile and review the claims:
   - `skillgraph extract evals/<skill-name>/suite.yaml` (one extractor call per skill)
   - `skillgraph lint evals/<skill-name>/suite.yaml`. Fix `dangling-file` and `order-cycle` errors in the skill first; `pipeline` continues past lint errors unless given `--strict`.
   - Read `claims/<skill-name>.claims.json` and fix ordering edges the text doesn't state, regexes that accept only one way of doing something, and conditions phrased as agent observations. Edit the JSON directly; it is a reviewed lockfile.
4. Smoke test one task: `skillgraph run <suite> --out runs/<skill-name> --runs 1 --tasks <one-task> --yes`, then `skillgraph check runs/<skill-name> --yes`.
5. Run everything: `skillgraph pipeline <suite> --out runs/<skill-name> --runs 3 --yes --max-total-usd <agreed amount>`. Finished runs are skipped on reruns; `--retry-errors` reruns failed ones.
6. Read the results:
   - `skillgraph summary runs/<skill-name>` (or `--json`) for verdicts, weak verdicts, and reference diagnoses.
   - Before calling a line ignored, read one failing run with `skillgraph show runs/<skill-name>/<skill>/<task>/skill/r1` and confirm the miss is real. Mechanical checks can miss an equivalent way of doing the same thing.
   - Verdicts marked weak rest on fewer than 3 runs per variant; say so instead of acting on them.
   - Point the user at the report: `open runs/<skill-name>/report.html` (macOS) or `xdg-open` (Linux).
7. Propose edits to the skill as diffs. Don't edit the user's skill until they agree.
   - ignored: make the instruction concrete (name the command, file, or exact output), give it a done-criterion, and put it where the agent needs it.
   - inert: candidate to cut, which saves context on every use. Keep safety rules even when inert.
   - interfering: look for another line that pushes the agent the other way.
   - untested: add a task that reaches it.
8. After the user edits the skill: `skillgraph extract` notices the change. Run the pipeline into a new `--out` directory, then `skillgraph diff runs/<before> runs/<after>` to show what moved.

## Grade real traces

Read `references/importing-traces.md` first.

1. Make sure there is a suite and a reviewed claims file for the skill (**Evaluate a skill**, steps 1-3).
2. `skillgraph import <files or dirs> --suite <suite> --skill <name> --out runs/<name>-prod --dry-run`. Ask before importing transcripts that aren't the user's own.
3. If the format isn't detected, inspect a few records and write a mapping file as the reference describes. If unmapped tools run commands or touch files, add a `--tool-map`. Repeat the dry run until the summary looks right.
4. Import for real, then `skillgraph check runs/<name>-prod --yes` and `skillgraph summary runs/<name>-prod`.
5. Imported sessions have no "without the skill" twin, so report followed and ignored lines, not working or inert.

## Compare with a reference process

Read `references/reference-trees.md` first.

1. `skillgraph reference <doc>`. Mermaid charts parse without a model; prose costs one extractor call. Review the YAML with the user: decisions, branches, and rules must match the document, with nothing invented.
2. Add `reference: <path>` to the skill in the suite, then `skillgraph extract <suite>` (compiles the reference) and `skillgraph coverage <suite>`.
3. Review `claims/<name>.reference.claims.json` like any lockfile.
4. Draft tasks for paths no run has taken: `skillgraph tasks <suite> --skill <name> --untested-in runs/<name>`. Read every generated `setup.sh` before running anything, and fix any the command flags with `REVIEW`.
5. Run and check as usual. `check` grades runs against the skill and the reference together.
6. Report from `skillgraph summary`: gaps and conflicts first (fix the skill), then ignored steps (harden lines), then untested paths.

## Report back

End an evaluation with this shape, so results read the same across sessions:

```
<skill>: <runs> runs on <agent model>, ≈ $<cost>. Task success <x>% with the skill, <y>% without.
Verdicts: <n> working, <n> inert, <n> ignored, <n> interfering, <n> untested (<n> weak).
[Reference <name>: <coverage>% covered; gaps: ...; conflicts: ...]
Top changes:
1. <line/claim>: <why> -> <diff>
2. ...
3. ...
```

## Rules

- Agent runs execute shell commands on this machine inside temporary workspaces under the run directory. For skills from untrusted sources, use the container from the README instead.
- Don't use `--enforce` unless the user asks for it; it changes what you are measuring.
- Keep run directories (`runs/`) out of version control; check the project's `.gitignore`. Commit the suite, the claims lockfiles, the reference tree, and the coverage lockfile.
- Generated `setup.sh` scripts and fixtures run on this machine. Read them before the first run.

## Rebuild from scratch

Only when the user explicitly asks to recreate skillgraph itself (not when an install fails). `references/build-spec.md` specifies the whole tool: architecture, data formats, prompts, CLI, report, and tests.

1. Confirm the target directory with the user.
2. Build in the milestone order the spec gives. Run the tests at the end of each milestone before moving on.
3. Copy the prompts and JSON schemas from the spec word for word. The tool's behavior depends on them.
4. Finish with the spec's acceptance checklist, including one real run on the bundled examples once the user approves the cost.
