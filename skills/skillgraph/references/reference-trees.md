# Reference trees

A reference tree is the process a skill is supposed to encode: a team SOP, a runbook,
a support decision tree, a flowchart. Comparing it with the skill and with agent runs
answers three different questions:

```
        reference tree
        /            \
  coverage        correctness
      /                \
skill graph -adherence- run trees
```

- **coverage**: does the skill say everything the process requires, and nothing that contradicts it?
- **adherence**: does the agent do what the skill says? (the normal skillgraph verdicts)
- **correctness**: does the agent follow the process, whatever the skill says?

## 1. Convert the process document

```bash
skillgraph reference docs/release-sop.md           # writes docs/release-sop.ref.yaml
```

- A Mermaid flowchart (`.mmd`, or a ```mermaid block in Markdown) is parsed without a
  model: `[...]` actions, `{...}` decisions, `([...])` / `((...))` start and end states,
  edge labels as branch labels. Bullets under a `## Rules` (or Constraints, Guardrails)
  heading become rules.
- Anything else (prose SOPs, numbered lists, docs) goes to the extractor model. Pass
  `--llm` to use the model even when a chart is present.

**Review the YAML before using it.** Check that each decision is phrased as a question
about the situation, each branch goes where the document says, no steps were invented,
and global rules ended up in `constraints`. Everything downstream trusts this file.

Then point the skill at it in `suite.yaml`:

```yaml
skills:
  - path: skills/release-notes
    claims: claims/release-notes.claims.json
    reference: references/release-sop.ref.yaml
```

## 2. Compile it and compare with the skill

```bash
skillgraph extract evals/x/suite.yaml     # also compiles the reference -> claims/<skill>.reference.claims.json
skillgraph coverage evals/x/suite.yaml    # aligns reference <-> skill -> claims/<skill>.coverage.json
```

The reference compiles into the same claims format as a skill. Its structure comes from
the tree: an action must follow its predecessors, and a step on a branch only applies
when every branch above it held. A model writes only the checks. Review the
reference claims like any lockfile (regexes that accept only one way of doing a step
are the usual problem).

Coverage findings:

| rule | meaning |
|---|---|
| missing-step | the process has a step the skill never asks for |
| unhandled-branch | the skill never says what to do on one side of a decision |
| partial | the skill mentions the step more weakly or narrowly |
| conflict | the skill says something incompatible with the step |
| order-conflict | the skill orders two steps the opposite way from the process |
| skill-extra | the skill asks for something the process doesn't have |

`coverage` exits 1 when there are conflicts. The alignment in `coverage.json` can be
edited and is reused until the skill's claims or the reference change.

## 3. Make sure every path gets a run

```bash
skillgraph tasks evals/x/suite.yaml --skill x --untested-in runs/x
```

This drafts one task per root-to-leaf path that no run has taken yet: a realistic
prompt, a fixture built so the path's decisions come out the intended way, and
success checks. It writes `suite.generated.yaml` and `fixtures/generated/<task>/`.

**Read every generated `setup.sh` before running.** These scripts run on this machine.
The command prints `REVIEW setup.sh` when a script uses absolute paths, leaves the
workspace, uses the network, or runs dangerous commands. Fix those by hand. Also check
that the success checks test the task, not your preferred wording.

Run the generated suite into the same run directory; run sets merge skills from
several suites:

```bash
skillgraph run evals/x/suite.generated.yaml --out runs/x --runs 2
skillgraph check runs/x && skillgraph report runs/x
```

## 4. Read the triangle

`check` grades every run against the skill and the reference in one judge call. The
report's **Reference process** panel shows coverage, the share of reference steps done
with and without the skill, which paths have been tested, the tree colored by
diagnosis, and a row per step:

| diagnosis | what it means | what to do |
|---|---|---|
| gap | not in the skill, and agents don't do it | add it to the skill |
| ignored | in the skill, but agents don't do it | harden the matching skill lines |
| partial | only partly in the skill, and not done | make the skill explicit |
| conflict | the skill contradicts the process | fix the skill text |
| implicit | not in the skill, but agents do it anyway | usually fine; add it only if it must never be skipped |
| ok | in the skill and done | keep |
| unreached | an end state runs on its path didn't reach | check the outcome the skill asks for |
| untested | no run reached the step | generate a task for its path |

Lead with gaps and conflicts when reporting to the user. A gap is the clearest
improvement a skill can get: the process needs it, the skill doesn't ask for it, and
the agent doesn't do it on its own.
