# Writing a good suite

The suite decides what skillgraph can tell you. A claim can only be judged in a run
that reaches it, and a verdict is only as trustworthy as the tasks behind it.

## Tasks

- **Write prompts the way a user would.** "Create the sales report for data/sales.csv",
  not "Follow the csv-report skill to validate and then...". If the prompt restates
  the skill, every line looks inert.
- **Cover every branch.** For each "if X, do Y" in the skill, write one task where X
  holds and one where it doesn't. Otherwise half the claims come back `untested`.
- **Make the task need the skill.** If a capable model would do everything right with
  no instructions, the skill can only score inert. Use the house conventions, file
  names, formats, and steps the skill exists to teach.
- **Keep tasks small.** 1-3 minutes of agent work each. Long tasks cost more and make
  traces harder to read without telling you more about individual lines.
- **2-4 tasks per skill** is enough to start. Add tasks when a claim is `untested` or a
  verdict looks surprising.

## Fixtures

- A fixture is the directory the agent starts in. It is copied fresh for every run
  and becomes a git repository with one commit.
- Include only what the task needs. Every extra file is something the agent may read
  and get distracted by.
- For git history (tags, commit messages, branches), add a `setup.sh` that builds it
  with `git init`, commits, and tags. It runs once in the new workspace with a fixed
  author and is deleted before the agent starts. Add files to commits explicitly; don't
  `git add -A` (that would commit setup.sh).
- Two tasks can share a fixture when only the prompt differs.

## Success checks

Success checks answer "did the task get done?", separately from "did the agent follow
the skill?". Keep them few and strict:

- `file: path` and `file: path` + `contains: regex` for required outputs.
- `unchanged: path` for inputs the agent must not modify.
- `cmd: ...` for anything a command can verify (tests pass, no new git tags, a script
  exits 0). It runs in the workspace after the agent finishes.

A task that fails its success checks with the skill but passes without it points at an
interfering instruction.

## Reviewing the claims lockfile

After `skillgraph extract`, read `claims/<skill>.claims.json` before running anything.
Fix these by editing the JSON:

- **Ordering edges the text doesn't state.** The extractor sometimes orders steps by
  list position. If the skill says "read X before writing the report", the edge belongs
  on the report claim, not on the previous list item. A wrong edge makes correct runs
  look out of order.
- **Regexes that accept only one way of doing something.** `pytest` will miss
  `python -m pytest` and `make test`. Widen `input_any`, or switch the check to `judge`.
- **Conditions phrased as agent observations.** "validate.py reported errors" can't be
  true in a baseline run where the agent never ran validate.py. Rephrase as a fact
  about the input ("the CSV has invalid rows") with a judge question.
- **Claims that bundle two instructions.** Split them so each line can be attributed.

Add a `review_notes` list to the lockfile to record what you changed and why.

## Reading verdicts

| verdict | meaning | usual action |
|---|---|---|
| working | followed with the skill, not without | keep |
| inert | followed either way | cut to save context, unless it's a safety rule |
| ignored | not followed even with the skill | make it concrete, give a done-criterion, move it to where it's needed |
| interfering | followed without the skill, not with it | find the instruction that pushes the other way |
| untested | never applicable | add a task that reaches it |

Before acting on `ignored`, open a failing `trace.json` and confirm the miss is real.
`failure_mode: out_of_order` means the agent did it, just at a different point than the
skill orders it; check whether the order actually matters.

Ablation confirms what verdicts suggest: removing a working span should change
behavior (if it doesn't, the instruction is redundant with another line); removing an
inert span should change nothing.

Two runs per variant gives rates of 0, 50, or 100%. Use 3-5 runs before rewriting a
skill based on a single claim.
