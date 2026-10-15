# skillgraph dashboard: product requirements (v2, "platform" UI)

Status: proposal for the next build. Scope: the web dashboard only (`src/skillgraph/dashboard/`). No CLI, checker, or aggregation changes except the small read-only API additions in section 6.

Constraints that do not change: vanilla JS, no build step, no external JS/CSS, DOM built with `h()` (never `innerHTML`), strict CSP, every style value a `--ds-*` token from `tokens.css`, server read-only on 127.0.0.1, static export (`data.js`) must keep working, hash routes.

---

## 1. Diagnosis: why the current dashboard is hard to grasp

Observed on `examples/runs/demo` and `runs/digest-validation/long` at 800px and 375px (screens: Overview, Skill, Runs, Run, Diff).

### 1.1 The first screen answers "what is here", not "is my skill good"

The Overview opens with six KPI tiles (Runs 40, Claims 40, Task success 93%, Skill loaded 100%, Spend $41.86, Long traces 0). A first-time user cannot tell which of these matters. "Claims 40" and "Long traces 0" are inventory, not outcomes. The one number that answers the product question ("does the skill change what the agent does?") is buried inside each skill card as a six-segment bar with a legend ("Working 10 · Inert 5 · Untested 1"). Nothing says "csv-report works; bugfix-tdd is mostly redundant; release-notes has one gap", which is exactly what the README says in prose.

### 1.2 Jargon with no "so what" next to it

Words a new user meets in the first minute, with no inline explanation except a hover title: *claim, verdict, working, inert, ignored, interfering, untested, weak, quadrant, baseline, ablation, load-bearing, no-effect, removal-helped, judge, windowed, skeleton, off-skill, routed, mechanically checkable, lint, reference, coverage, diagnosis, gap, implicit, partial, unreached, n/a, out of order, episode.* The "Needs attention" list mixes four vocabularies in four rows (`lint error`, `ignored`, `reference gap`, `task failed`) with no priority and no action. "38% checked mechanically" on the Skill page header is a methodology detail presented as a headline stat.

### 1.3 Numbers are dead ends

KPIs are not links. "Task success 93% with the skill; 64% without" cannot be clicked to see the failing runs. On a claim, "with skill 4/4 (100%) without 0/4 (0%)" does not link to those 8 runs. On the Runs table the claim bar shows "1 missed · 10 satisfied" but not *which* claim was missed; the user has to open each run. The only path from "c8 is ignored" to "here is the run where it was missed, and here is the moment" is: Overview → Skill page → expand "How it's checked" → read `miss_notes` text (`slugify/r1: ...`) → go to Runs → find the row manually → open → scroll the results panel.

### 1.4 Navigation is flat and changes shape

The top bar lists Overview, one tab per skill (csv-report, bugfix-tdd, release-notes), Runs, Diff. Skills are peers of "Runs" and "Diff", so the hierarchy (run set → skill → claim → run → event) is invisible. With 10 skills the bar overflows. On the Diff page the skill tabs disappear and the run-set picker still says "demo" while the page compares demo→long. There is no "where am I" on the Run page except a breadcrumb that reads `Overview › csv-report › runs › valid-data-needle / skill / r1`.

### 1.5 The Skill page is a wall

Below the fold: SKILL.md gutter + claims list (two sticky columns), then Branch conditions table, Ablations table, Reference table, Lint list, each as a separate full-width section. It is one 3,000px scroll with no tabs or anchors. The line gutter is the best idea in the app (colored left edge per line) but at 375px it is a 20-line monospace block that pushes the claims list below the fold entirely; the two-column split collapses into "SKILL.md first, claims later", so clicking a line scrolls you away from the result.

### 1.6 The Run page is a transcript, not a trace viewer

Everything is rendered at once: 201 turns, 511 events, each tool call a `<details>` row. The "Skill claims" panel on the right is sticky but is sorted by status, not by time; "evidence for c15" chips sit on turn headers but there is no overview of *where in the run* the evidence is. There is no search within the run, no collapsing of repeated turns (the digest already collapses polling, the UI does not use it), no phase/intent navigation beyond a text divider, no way to select an event and see its full input/output beside the timeline (input/output expand inline and push the page down). "Scores" (claim results) and "observations" (events) are not linked both ways in a visible manner: the chip on the event says `c15 satisfied` with no reason.

### 1.7 Density without hierarchy

Everything is 12-14px with the same weight: run-set name, model, "trigger invoke", built timestamp. Verdict chips, kind chips (`step`, `must_not`), line chips (`L12`), and `weak` chips sit in one row with equal visual weight. The eye has nowhere to land.

### 1.8 Compare is a table without a verdict

Diff shows "1 better, 1 worse, 9 same" and a table. It does not say whether the edit helped overall, does not show which SKILL.md lines changed, and the "worse" rows (c9 inert→inert, 100%→67%) need a tooltip to understand.

### 1.9 Slow first paint, no skeleton

`/api/set/long` takes ~1.4 s (the run index re-renders every trace to count tokens), `/api/diff` ~0.9 s; the page shows "Loading…" in the corner for that time, and the Run page for a 1.3 MB trace shows "Loading…" then pops the whole DOM in at once.

---

## 2. Personas, jobs, journeys

### 2.1 Personas (validated against the README and the data)

| Persona | Situation | What they need from the dashboard | Confirmed by |
|---|---|---|---|
| **Skill author** (primary) | Just ran `skillgraph pipeline`, has `runs/x` open, SKILL.md in the editor | "Is my skill good? What should I change first, and where? Did my edit help?" | README quick start; `summary` and `diff` CLI exist for this |
| **Eval / platform engineer** | Runs suites in CI (`--fail-on`, `--junit`), reviews regressions across run sets and models | "What failed, in which runs, is the verdict trustworthy (weak? judge or mechanical?), what did it cost?" | README "Use it in CI"; costs tracked per run |
| **Auditor of real sessions** | Imported Claude Code / OTel / LangSmith traces (`skillgraph import`), no baseline, long windowed traces, optional SOP | "Did this session follow the skill / the SOP? Show me the moment it broke a rule. What was it doing when it was off-skill?" | `trees/runs.py`, windowed judging, intents, episodes, reference trees |

A fourth, occasional role is the **skeptical reviewer** (teammate reading a shared static export, wants to audit a verdict's reason). The static export must serve them without a server.

### 2.2 Journeys

Each step: screen → the one question it answers → next action.

**J1. "I just ran a pipeline: is my skill good, and what should I change first?"** (skill author)

| Step | Screen | Question answered | Next action |
|---|---|---|---|
| 1 | Overview | Does the skill change behavior at all? (task success with/without; one-sentence health per skill) | Click a skill's health line or "Recommendations" |
| 2 | Recommendations | What are the top things to change, ranked, with the SKILL.md lines they touch? | Click a recommendation |
| 3 | Claim detail (drawer) | Why is this claim ignored? (rates with/without, failure mode, the judge's reasons per run, the lines) | Click a failing run |
| 4 | Run (trace) with claim selected | What did the agent do instead, at which event? | Edit SKILL.md, rerun into `runs/after` |
| 5 | Compare | Did the edit help? | Done, or back to 2 |

**J2. "Why did this run fail this claim?"** (eval engineer, auditor)

| Step | Screen | Question | Next |
|---|---|---|---|
| 1 | Runs table, filter "has problems" / status = violated | Which runs broke something? | Click a row |
| 2 | Run: Scores tab | Which claims failed, with what reason and evidence? | Click a failed claim |
| 3 | Run: event selected in tree, detail pane | What exactly happened (tool, input, output, the turn around it)? | Decide: skill text problem, judge mistake, or agent mistake |
| 4 | Claim detail | Is this a pattern across runs or one-off? | Recommendations / edit |

**J3. "Did my edit help?"** (skill author, eval engineer)

| Step | Screen | Question | Next |
|---|---|---|---|
| 1 | Compare (pick before/after) | Net result: better/worse counts, task success delta, cost delta, verdict in one line | Expand a skill |
| 2 | Compare: skill section | Which claims changed verdict or rate, including new/removed (renumbered) claims? | Click a claim → Claim detail in the *after* set |
| 3 | Claim detail | Is "worse" real or weak (few runs)? | Add runs or revert |

**J4. "Audit production sessions against the skill"** (auditor)

| Step | Screen | Question | Next |
|---|---|---|---|
| 1 | Overview (imported set: no baseline, "followed" instead of "working") | How many sessions followed the skill, which rules were broken? | Runs |
| 2 | Runs grouped by task/session, columns: episode, length, judge mode, violated count | Which sessions have violations? Which were judged windowed? | Open a session |
| 3 | Run: phases (intents) in the tree, off-skill turns dimmed, evidence markers | What was the agent doing, and where did the rule break? | Select the violated claim |
| 4 | Event detail with "decided by window / skeleton" and reason | Can I trust this verdict? Which view decided it? | Export link / share static site |

**J5. "Check against our SOP"** (skill author, auditor)

| Step | Screen | Question | Next |
|---|---|---|---|
| 1 | Skill: Reference tab | Which SOP steps does the skill not cover, and do agents do them anyway? (coverage %, diagnosis per step) | Click a `gap` row |
| 2 | Reference step detail | Which claims map to this step; rates with/without; runs that missed it | Add lines to SKILL.md; or `skillgraph tasks` for untested paths |
| 3 | Recommendations (reference items are included) | Where does this sit among all the other fixes? | Edit |

---

## 3. Information architecture

### 3.1 App shell

```
┌───────────┬──────────────────────────────────────────────────────────────┐
│ ▽ skill-  │ [run set: demo ▾]   Overview › csv-report › c8        ☾ ⌕ ?  │  top bar (48px)
│   graph   ├──────────────────────────────────────────────────────────────┤
│           │                                                              │
│ Overview  │                     page content                             │
│ Recommend.│                                                              │
│ Skills    │                                                              │
│  csv-rep. │                                                              │
│  bugfix.  │                                                              │
│  release. │                                                              │
│ Runs      │                                                              │
│ Compare   │                                                              │
│ ───────── │                                                              │
│ Glossary  │                                                              │
│ Report ↗  │                                                              │
└───────────┴──────────────────────────────────────────────────────────────┘
```

- **Left sidebar** (232px, `--ds-color-surface-nav`): sections *Overview, Recommendations, Skills (expandable list of skills in the current set), Runs, Compare*; footer: *Glossary, HTML report ↗*, theme toggle. Active item uses `--ds-color-bg-neutral-subtle-hover` + label weight; each item shows a count badge where useful (Recommendations: number of high-priority items; Runs: run count).
- **Top bar**: run-set switcher (select, always visible, even with one set: it then shows the name, disabled), breadcrumbs, search (⌕ opens a command-palette style search over skills, claims by id/text, tasks, run paths; MVP: simple filtered list), help (?) opens Glossary.
- **Breadcrumbs** are the hierarchy: `run set › skill › claim` or `run set › runs › task / variant / rN`.
- **Responsive**: ≥1100px sidebar open; 768–1099px sidebar collapses to 56px icon rail with tooltips, expands on hover/click; <768px sidebar becomes a slide-in drawer from a ☰ button in the top bar, breadcrumbs collapse to the last two segments.

### 3.2 Page list

| # | Page | Route | MVP |
|---|---|---|---|
| P0 | Redirect | `#/` → `#/s/<first set>` | yes |
| P1 | Overview | `#/s/:set` | yes |
| P2 | Recommendations | `#/s/:set/recommendations?skill=&kind=` | yes |
| P3 | Skills list | `#/s/:set/skills` | yes (small) |
| P4 | Skill | `#/s/:set/skill/:skill?tab=lines|claims|conditions|ablations|reference|lint&verdict=&line=&claim=` | yes |
| P5 | Claim detail | `#/s/:set/claim/:skill/:claimId` (also opens as a right-side drawer over P2/P4) | yes |
| P6 | Runs | `#/s/:set/runs?skill=&task=&variant=&outcome=&status=&claim=&judge=&q=&sort=&dir=&group=` | yes |
| P7 | Run (trace) | `#/s/:set/run/:skill/:task/:variant/:rN?event=&claim=&tab=&q=` | yes |
| P8 | Compare | `#/compare/:a/:b?skill=` | yes |
| P9 | Glossary | `#/glossary` | yes (static content) |
| P10 | Reference step detail | `#/s/:set/ref/:skill/:nodeId` (drawer over P4 reference tab) | later (MVP: expand row inline) |
| P11 | Saved views / dashboards | — | later |

Old routes (`#/s/:set/skill/:name?claim=`, `#/s/:set/runs?skill=`, `#/s/:set/run/...`, `#/diff/:a/:b`) keep working: `#/diff/...` redirects to `#/compare/...`; `?claim=` on the skill route opens the claim drawer.

### 3.3 Link rules ("every number is a link")

| Where | Number | Links to |
|---|---|---|
| Overview KPI "task success 93%" | | P6 `?variant=skill&outcome=failed` (the failing ones) |
| Overview KPI "without 64%" | | P6 `?variant=baseline&outcome=failed` |
| Skill health "1 line needs attention" | | P2 `?skill=x` |
| Verdict count chips (Working 10) | | P4 `?tab=claims&verdict=working` |
| Claim rate "with skill 1/4" | | P6 `?skill=x&variant=skill&claim=c8` (claim column shows that claim's status per run) |
| Claim rate "without 3/4" | | P6 `?skill=x&variant=baseline&claim=c8` |
| Reference step "with 100% / without 83%" | | P6 `?skill=x&claim=ref:credit` |
| Run row claim counts "1 missed" | | P7 `?tab=scores&status=missed` |
| Evidence "event 199" | | P7 `?event=199&claim=c14` |
| Ablation "L13 load-bearing" | | P6 `?skill=x&variant=ablate-L13-13` |
| Cost "$41.86" | | P6 sorted by cost desc |
| Compare "2 better" | | scrolls to / filters that skill's table by change |

---

## 4. Page specs

Common vocabulary used below: `S` = `/api/set/<id>` response; `S.data.skills[i]` = aggregate per skill (`aggregate.py`); `S.summary` = `summary.py`; `S.runs[]` = run index rows (`dashboard/__init__.py: RunSets.runs`); `R` = `/api/run/<id>?path=` response (`trace`, `check`, `digest`, `claims`, `conditions`, `reference`); `D` = `/api/diff/<a>/<b>`.

### 4.0 Shared components

| Component | Spec |
|---|---|
| **Verdict badge** | Label + leading glyph, colors from the verdict map (section 5.3). Always has `title` = plain-language one-liner (section 4.10). Clickable variant navigates to the filtered claims list. |
| **Status badge** (per-run claim status) | satisfied ✓ success, missed ✗ danger, violated ⊘ danger, out of order ⇄ warning, unclear ? info, n/a · neutral dashed. |
| **Score chip** | `4/4` tabular numerals + small bar (width = rate). Variants: with (solid success), without (outline), ablate (neutral). |
| **Rate pair** | "with 4/4 · without 0/4" rendered as two score chips with the word labels; clicking a chip opens runs filtered by variant and claim. |
| **Confidence tag** | `weak` (warning, outline) when `claim.weak`; tooltip "fewer than 3 runs per variant; add --runs before acting on it". Also "judge" / "tool" / "file" method tag on per-run results. |
| **Data table** | Sticky header, sortable columns (click header; `aria-sort`), column filter row (select/search per column where listed), row click opens detail, `overflow-x: auto` on the wrapper, min-width per table, right-aligned numerics with `font-variant-numeric: tabular-nums`, zebra off, hover row. Pagination: none (client-side, ≤ a few thousand rows); virtualize later. |
| **Filter bar** | Chips for active filters with ×, "Clear all", result count ("12 of 40"). Filters mirror into the URL query so views are shareable in the export. |
| **Drawer** | Right side, 480px (100% below 768px), `--ds-color-surface-overlay`, `--ds-elevation-overlay`, Esc closes, focus trapped, URL updates (so it is bookmarkable and works in export). |
| **Empty / loading / error** | Loading: skeleton rows (3 gray bars, `--ds-color-bg-neutral-subtle`) in place, not a corner "Loading…". Error: `notice.bad` with the message and a "Retry" button. Empty: icon-less centered text with the CLI command to run (see per page). |
| **Help popover** | A small `?` next to any jargon word opens a popover with the glossary entry (copy from section 4.10). |

### 4.1 P1 Overview

Purpose: in ten seconds, say whether the skills work and what to look at first.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ demo                                claude-opus-5-5 · 40 runs · 3 skills │
│ built 2026-10-15 14:15 UTC · trigger: invoke · spend $41.86 (agent $35.33, judge $6.53)
├──────────────────────────────────────────────────────────────────────────┤
│ ┌ Task success ─────┐ ┌ Lines that work ──┐ ┌ Needs attention ─┐ ┌ Confidence ─┐
│ │ 93% with skill    │ │ 17 of 40 claims   │ │ 4 items          │ │ 12 weak     │
│ │ 64% without  ↗    │ │ working ↗         │ │ 1 ignored, 1 gap,│ │ verdicts ↗  │
│ │                   │ │ 21 inert, 1 ign.  │ │ 1 lint, 1 failed │ │ 0 stale     │
│ └───────────────────┘ └───────────────────┘ └──────────────────┘ └─────────────┘
│                                                                          │
│ Skills                                                                   │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ csv-report      ████████████░░░░░  "Does real work: 10 of 16 lines   │ │
│ │ procedural      Working 10 Inert 5 Untested 1   change behavior.     │ │
│ │                 success 100% with · 0% without  Task success goes   │ │
│ │                 14 runs · $10.12                 from 0% to 100%."   │ │
│ │                 ⚠ 1 lint error                 [Recommendations →]  │ │
│ ├──────────────────────────────────────────────────────────────────────┤ │
│ │ bugfix-tdd      ██░░░░░░░░░░░░░░░  "Mostly redundant: 8 of 11 lines │ │
│ │                 Ignored 1 Working 2 Inert 8      the agent does any- │ │
│ │                 success 100% with · 100% without way. 1 line ignored."│ │
│ ├──────────────────────────────────────────────────────────────────────┤ │
│ │ release-notes   ...   Reference "release-sop": 75% covered, 1 gap     │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│ Top recommendations (3)                              [See all 9 →]       │
│  1. ● Rewrite L10 (bugfix-tdd): "Write FIX_NOTES.md…" is ignored (1/4)   │
│  2. ● Add to release-notes: the SOP step "Credit each commit's author"   │
│  3. ● Fix lint: L24 references references/regions.md which doesn't exist │
└──────────────────────────────────────────────────────────────────────────┘
```

Fields:

| Element | Source |
|---|---|
| Title, model, trigger, built | `S.data.runset`, `S.data.model`, `S.data.trigger`, `S.data.generated` |
| runs / skills | `S.runs.length`, `S.data.skills.length` |
| Task success with / without | rate of `S.runs[].all_success` over `variant=="skill"` / `"baseline"` (ignore `null`); omit "without" when no baseline runs (imported sets) |
| Lines that work | sum over skills of `totals.quadrants.working` (+ `followed` for no-baseline sets) / total claims |
| Needs attention count | = number of P2 items with priority high (section 4.2) |
| Confidence | weak = count of `claims[].weak`; stale = sum of `skills[].stale_checks` plus `skills[].stale` flags; show a warning notice "SKILL.md changed since extract" when any `skill.stale` |
| Skill health sentence | rule table below |
| Skill row bar + counts | `totals.quadrants` in order ignored, interfering, working, followed, inert, untested |
| Reference line | `reference.name`, `reference.score.coverage`, `reference.diagnosis_counts` |
| Spend | sum of `S.runs[].cost_usd` and `judge_cost_usd` |

Health sentence rules (first matching, per skill; `W`=working+followed, `I`=inert, `G`=ignored, `F`=interfering, `n`=claims):

| Condition | Sentence |
|---|---|
| `G+F == 0 and W/n >= 0.5` | "Does real work: {W} of {n} lines change behavior." + if baseline: " Task success {base}% → {skill}%." |
| `I/n >= 0.6` | "Mostly redundant: {I} of {n} lines describe what the agent does anyway." + (G ? " {G} line(s) ignored." : "") |
| `G+F > 0` | "{G+F} line(s) need attention: {G} ignored, {F} interfering. {W} of {n} lines work." |
| no baseline (imported) | "{followed} of {n} rules followed in {runs} sessions; {violated-run-count} session(s) broke a rule." |
| else | "{W} working, {I} inert, {untested} untested of {n} lines." |

Empty states: no checked runs → "No graded runs yet. Run `skillgraph check <dir>`." with the run index table still shown below. `S.error` → danger notice with the message (claims file moved, etc.).

### 4.2 P2 Recommendations ("what to change first")

Purpose: turn verdicts into an ordered to-do list with the lines they touch. Computed client-side from `S` (no model calls). Pure function `recommend(S) -> items[]`, unit-testable; also port to Python later for `summary --json`.

```
┌ Recommendations · demo ───────────────────────── skill: [all ▾] kind: [all ▾] ┐
│ 9 items · 3 high · 4 medium · 2 low                                          │
├──────────────────────────────────────────────────────────────────────────────┤
│ ● HIGH  Rewrite  bugfix-tdd · L10                                             │
│   "Write FIX_NOTES.md with three headings: Root cause, Fix, Test."            │
│   Ignored: followed 1 of 4 runs with the skill, 0 of 4 without.  missed       │
│   Why: slugify/r1: the agent wrote FIX_NOTES.md with different headings…      │
│   Do: make it concrete (exact file name, exact headings) and state a          │
│   done-criterion.                      [3 failing runs →] [claim c8 →] [L10 →]│
├──────────────────────────────────────────────────────────────────────────────┤
│ ● HIGH  Add  release-notes · reference step "credit"                          │
│   The SOP asks to "Credit each commit's author"; the skill never does, and    │
│   agents didn't (0 of 6 with, 0 of 6 without).                                │
│   Do: add a line asking for the author on each entry.    [reference →]        │
├──────────────────────────────────────────────────────────────────────────────┤
│ ● HIGH  Fix  csv-report · L24   lint: references/regions.md is not in the     │
│   skill directory.                                               [L24 →]      │
├──────────────────────────────────────────────────────────────────────────────┤
│ ○ MED   Add a task  release-notes · L17 "Otherwise bump the patch version"    │
│   Untested: no task reached this branch (k2 false and feat none).             │
│   Do: `skillgraph tasks examples/suite.yaml --skill release-notes --untested-in …` │
├──────────────────────────────────────────────────────────────────────────────┤
│ ○ LOW   Consider cutting  bugfix-tdd · L8–L14 (6 lines, 8 inert claims)       │
│   The agent does these without being told (4/4 without the skill). Ablation   │
│   of L8 and L10: no effect. Keep rules that protect against damage.           │
└──────────────────────────────────────────────────────────────────────────────┘
```

Item generation (per skill `sk` in `S.data.skills`), in this order; each item: `{priority, action, skill, lines, title, why, do, links}`:

| # | Source | Condition | priority | action | title / why / do |
|---|---|---|---|---|---|
| 1 | `sk.claims[]` | `quadrant == "interfering"` | high | Investigate | why: "Followed {b}/{nb} without the skill, {s}/{ns} with it: something in the skill pushes the other way." do: "Look for a line that contradicts or crowds out this one; compare a baseline run with a skill run." Links: runs with claim (both variants). |
| 2 | `sk.claims[]` | `quadrant == "ignored"` | high (medium if `weak`) | Rewrite | why from `rates`; failure_mode copy: `missed` → "the agent never did it"; `violated` → "the agent did what this rule forbids"; `out_of_order` → "done, but before {after ids}". do: by `check.type`: judge → "Give it a concrete done-criterion (a file, a command, an exact string) so the judge isn't needed"; tool/file → "State it as an explicit step with the exact command/file"; `kind == must_not` → "Put the rule next to the step it guards and say what to do instead." Evidence: first 3 `miss_notes`. |
| 3 | `sk.reference.nodes[]` | `diagnosis in (gap, conflict, partial)` | high (conflict), high (gap), medium (partial) | Add / Fix / Make explicit | why: `node.note` + "agents did it {rate_with}% with, {rate_without}% without". do: gap → "Add a step to the skill: '{node.text}'"; conflict → "The skill contradicts the process here; change the skill text or the SOP"; partial → "Say the whole step; only part of it is in the skill ({claims})". |
| 4 | `sk.reference.nodes[]` | `diagnosis == "ignored"` | high | Rewrite | same as #2 but for the mapped claims `node.claims`. |
| 5 | `sk.lint[]` | `severity == "error"` | high | Fix | `message`, lines from `span`. |
| 6 | `sk.runs[]` | `variant=="skill" and all_success==false` (grouped) | medium | Check task | "{k} of {n} runs with the skill failed their success checks: {task list}". Links: runs filtered outcome=failed. |
| 7 | `sk.ablations[]` | `verdict == "removal-helped"` | medium | Consider cutting | "Task success rose from {success_skill} to {success_ablated} without L{a}–{b}." |
| 8 | `sk.claims[]` | `quadrant == "untested"` | medium | Add a task | "No run took this branch" + `when` condition text from `sk.conditions`. do: the `skillgraph tasks … --untested-in` command (use `S.data.runset`). |
| 9 | `sk.reference.paths[]` | `tested == false` | medium | Add a task | "Reference path {id} ({steps}) was never taken." |
| 10 | `sk.lint[]` | `severity == "warn"` | low | Fix | message |
| 11 | `sk.claims[]` | `quadrant == "inert" and kind != "must_not"`, grouped into contiguous line ranges | low | Consider cutting | "{k} inert claims on L{a}–{b}: followed {b}/{nb} without the skill. Ablation: {verdict if an ablation covers the span}." do: "Cut to save context; keep anything that guards against damage." |
| 12 | `sk.claims[]` | `weak == true` (count only, one item per skill) | low | Add runs | "{k} verdicts rest on fewer than 3 runs per variant. Rerun with --runs 3." |

Ordering: priority (high, medium, low), then within priority by `(skill_rate gap)` descending for claims (`(baseline_rate or 0) - (skill_rate or 0)` for interfering, `1 - skill_rate` for ignored), then line number. Suppress #11 entirely when the skill has no baseline. Filters: skill, kind (action). Empty: "Nothing to change: every claim is working or untested-by-design." Count of high items feeds the sidebar badge and the Overview tile.

### 4.3 P3 Skills list

A compact table (one row per skill): name, classification, claims, verdict bar, task success with/without, reference coverage, runs, cost, warnings (stale, lint errors). Same data as the Overview skill rows; exists so the sidebar "Skills" header has a landing page. Row click → P4.

### 4.4 P4 Skill

Purpose: the skill text and the verdicts, side by side, with everything else behind tabs.

```
┌ csv-report  procedural · 16 claims · 14 runs · $10.12          [Report ↗] [Runs →] ┐
│ Validates a sales CSV with a bundled script, then …                                │
│ ⚠ SKILL.md changed since its claims were extracted. Run skillgraph extract.        │
│ Working 10 · Inert 5 · Untested 1         success 100% with · 0% without           │
├ Lines ┬ Claims ┬ Conditions (1) ┬ Ablations (3) ┬ Reference ┬ Lint (1) ────────────┤
│                                                                                    │
│ Lines tab (default):                                                               │
│ ┌ SKILL.md ───────────────────────────────┐ ┌ Claims on L12 (2) ─────────────────┐ │
│ │  10  ## Workflow                        │ │ ● Working  c1  step        4/4·0/4  │ │
│ │  11                                     │ │   Validate the input first by …     │ │
│ │▌ 12  1. Validate the input first by …   │ │ ● Working  c2  must_not    4/4·0/4  │ │
│ │▌ 13  2. If validation fails, copy the … │ │   Never compute any statistics …    │ │
│ │▌ 14  3. If validation passes, compute … │ │                                     │ │
│ │▌ 15  4. Read references/format.md …     │ │ (click a line on the left; the list │ │
│ │  …                                      │ │  shows all claims when none picked) │ │
│ └─────────────────────────────────────────┘ └─────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────────┘
```

Tabs (URL `?tab=`):

**Lines** (default): left = `sk.lines[]` in mono, 1-based numbers, a 3px left edge colored by the worst verdict of claims whose `span` covers the line (order: interfering, ignored, working, followed, inert, untested; `sk.lint` spans with `severity==error` add a danger underline). Lines with claims are clickable; selecting one filters the right list and sets `?line=`. Right = claim cards (compact: badge, id, kind, rate pair, text, `weak`, `failure_mode`); clicking a card opens P5 drawer (`?claim=`). Verdict filter chips above the list (`?verdict=`), counts from `totals.quadrants`. Mobile (<768px): the two panes become a segmented control "Text | Claims"; selecting a line switches to Claims automatically; a sticky "← back to text" pill.

**Claims**: data table. Columns: Verdict (badge, sortable by verdict order), ID, Kind, Lines (`L12` link → Lines tab with `?line=`), Claim text, With (score chip `rates.skill.followed * n / n`), Without (`rates.baseline`; "–" when absent), Ablations (one chip per `ablate-*` rate), Check (`tool` / `file` / `judge` from `check.type`), Confidence (`weak`), Failure (`failure_mode`). Filters: verdict, kind, check type, weak. Search over text and id. Row click → P5 drawer.

**Conditions**: `sk.conditions[]`: id, text, lines, per-variant "held {true}/{n}" (`held[v]`), and the claims gated by it (claims whose `when.cond == id`). Explanation line: "A condition decides which branch of the skill applies. Claims on the other branch are n/a in that run."

**Ablations**: `sk.ablations[]` as cards: `L{remove[0][0]}–{remove[0][1]}` + verdict badge (load-bearing success, removal-helped warning, no-effect neutral) + `note` + the removed text (`text`, mono, clipped 6 lines with "show all") + effects table (`effects[]`: claim id, from → to as score chips, Δ, "affected" tag) + "success with skill {success_skill} → without these lines {success_ablated}" + link "{runs} runs →" (P6 `?variant=`). Explanation line: "Lines were deleted and the tasks rerun. If behavior changed, the lines were doing work."

**Reference** (only when `sk.reference`): header: name, source, "{score.coverage}% of the process is in the skill · steps done {correctness.skill}% with / {correctness.baseline}% without · {paths tested}/{paths total} paths tested", stale warning (`coverage_stale`). Diagnosis count chips (filter). Table: Diagnosis badge, Step (`id`, `text`, `kind` tag: action/decision/end/rule), In the skill (`relation` + mapped `claims` as chips linking to P5; `note` under), With (`rate_with`), Without (`rate_without`). Row expands inline with the plain-language diagnosis (`reference.diagnoses[diagnosis]`) and, for decisions, `held` per branch. Second block "Paths": each `paths[]` as a chip row of step ids with a tested/untested tag. Third block: `findings[]` (severity badge + message). "Later": a tree drawing of `edges`.

**Lint**: `sk.lint[]` table: severity, rule, lines, message, claim ids (links). Explanation: "Static checks on the skill text and its claims. Errors block `pipeline --strict`."

Empty: skill not in set → "No graded runs for {name} in this run set."

### 4.5 P5 Claim detail (drawer or page)

```
┌ c8 · bugfix-tdd ────────────────────────────────────────────── [open page ↗] [×] ┐
│ ● Ignored   step   L10   weak                                                    │
│ "Write FIX_NOTES.md with three headings: Root cause, Fix, Test."                 │
│ Not followed even with the skill. Harden or rewrite this text.                   │
│                                                                                  │
│ With skill  ▮▮▯▯ 1/4       Without  ▯▯▯▯ 0/4       ablate-L10-10  0/2            │
│ Failure: missed (3 of 4 runs with the skill)                                     │
│                                                                                  │
│ SKILL.md L10                                                                     │
│ │ 10  5. Write FIX_NOTES.md with three headings: Root cause, Fix, Test.          │
│                                                                                  │
│ How it is checked:  judge · "Did the agent write FIX_NOTES.md with exactly …?"   │
│ After: c7   When: –                                                              │
│                                                                                  │
│ Runs (8)                                                   [all runs for c8 →]   │
│ ┌ task       variant   status      reason                              event ─┐  │
│ │ slugify    skill r1  ✗ missed    wrote FIX_NOTES.md with headings …   12   │  │
│ │ slugify    skill r2  ✓ satisfied                                       14   │  │
│ │ word-count baseline  ✗ missed    no FIX_NOTES.md was written           –    │  │
│ └──────────────────────────────────────────────────────────────────────────────┘ │
│ Suggested change: Make it concrete…  (same copy as the Recommendation)           │
└──────────────────────────────────────────────────────────────────────────────────┘
```

Sources: claim from `sk.claims[]` (`id, kind, span, text, quadrant, weak, failure_mode, rates, check, after, when, miss_notes`); verdict one-liner from `S.data.quadrants[quadrant]`; the SKILL.md lines `sk.lines[span[0]-1..span[1]-1]`; the runs table from `sk.runs[]` (`task, variant, rep, statuses[id], path`) joined with the run's per-claim reason, which is **not** in `S` today: MVP shows status per run and lazy-loads `R.check.claims[id].reason/evidence` per row on expand (one `/api/run` call per expanded row, memoized); section 6 adds `reasons` to the aggregate so no per-row fetch is needed. Row click → P7 `?claim=c8&event=<evidence>`.

### 4.6 P6 Runs (the "traces table")

```
┌ Runs · demo                                              40 of 40   [⚙ columns] ┐
│ skill [all ▾] task [all ▾] variant [all ▾] outcome [all ▾] status [any ▾]      │
│ judge [all ▾] claim [– ▾]  ⌕ search task / path / intent    [Only problems]     │
│ group by: [none | task | skill]                                                  │
├──────────┬──────────┬──────────┬────────────────┬────────┬──────┬───────┬────────┤
│ Task     │ Skill    │ Variant  │ Claims         │ Outcome│ Tools│ Judge │ Cost   │
├──────────┼──────────┼──────────┼────────────────┼────────┼──────┼───────┼────────┤
│ slugify  │ bugfix-  │ skill r1 │ ████████░ 11 ✓ │ ✓ pass │ 6    │ full  │ $0.26  │
│          │ tdd      │          │                │        │ 2.5k │       │ +$1.17 │
│ slugify  │ bugfix-  │ skill r2 │ ███████▮░ 10✓1⇄│ ✓ pass │ 6    │ full  │ $0.24  │
│ valid-   │ csv-     │ skill r1 │ ██████▮▮ 1⊘ 1✗ │ ✓ pass │ 310  │ windo-│ $1.30  │
│ data-nee…│ report   │          │                │        │ 92k  │ wed   │ +$0.85 │
└──────────┴──────────┴──────────┴────────────────┴────────┴──────┴───────┴────────┘
```

Columns (default visible marked ●), all from `S.runs[]` unless noted:

| Column | Field | Notes |
|---|---|---|
| ● Task | `task`; sub-line `task_intent` (clipped 90) and `episode` "episode {index} of {of}" | link → P7 |
| ● Skill | `skill` | hidden automatically when the set has one skill |
| ● Variant | `variant` + `r{rep}`; `skill_loaded===false` → warning tag "skill not loaded" | ablate variants show `L{a}–{b}` tooltip from `S.data.skills[].ablations` |
| ● Claims | stacked bar from `statuses` (order violated, missed, out_of_order, unclear, satisfied, n/a) + count labels; "not checked" when `!checked` | segment click → P7 `?tab=scores&status=` |
| ● Outcome | `all_success` → pass/fail badge; `null` → "–" (imported); `error` → danger "error" tag with tooltip | |
| ● Tools / tokens | `tool_calls`, sub-line `~{tokens}` | numeric, right |
| ● Judge | `judge_mode` badge (windowed = info) + sub-line "sent ~{tokens_sent}" | |
| ● Cost | `cost_usd` + sub-line "+{judge_cost_usd} judge" | numeric, right, sort default none |
| ○ Reference | bar from `reference` counts | shown by default when any run has reference counts |
| ○ Model | `model` | |
| ○ Events / messages | `events`, `messages` | |
| ○ Duration | `wall_s` (new field, section 6) | |
| ○ Claim {id} | when `?claim=` is set: that claim's status in each run from `S.data.skills[].runs[].statuses[id]` (joined by `path`), as a status badge; the column header shows the claim id with a link to P5 | this is how "1/4 with the skill" becomes a list |

Filters: skill, task, variant (values from data), outcome (passed / failed / unknown), status (any run containing ≥1 claim with that status: violated, missed, out of order, unclear), judge (full / windowed), claim (list of claim ids of the selected skill), search (task, path, intent), "Only problems" = outcome failed OR any violated/missed/out_of_order OR error. Sorting: any column; default: task, variant order (skill, baseline, ablate…), rep. Group by task (collapsible groups with a per-group summary: n runs, pass rate, worst status). State in URL. Row click opens P7; Cmd/Ctrl-click opens in a new tab (plain `<a>`).

Empty: no runs match → "No runs match these filters. [Clear filters]". No runs at all → "No runs in this set. Run `skillgraph run <suite> --out <dir>` or `skillgraph import …`."

### 4.7 P7 Run (trace view)

Purpose: Langfuse-style trace page: a navigable tree/timeline on the left, a detail pane on the right, scores (claim results) as a first-class tab, and two-way links between a claim and the event that decided it.

```
┌ runs › valid-data-needle / skill / r1                                                  ┐
│ valid-data-needle   skill r1 · claude-opus-5-5 · 201 turns · 310 tool calls · ~92k tok │
│ $1.30 agent · $0.85 judge · judged windowed (2 calls, ~57k of 92k tokens sent)         │
│ Scores: ✓ 12 satisfied  ⊘ 1 violated  ✗ 1 missed  · 2 n/a      ✓ task passed (2/2)     │
├──────────────────────────────────────┬─────────────────────────────────────────────────┤
│ ⌕ filter events…   [tools ▾] [⊘ only evidence] [collapse off-skill]   │  Event 199 · Bash  ⊘ c14 violated          │
│                                      │  ─────────────────────────────────────────────── │
│ ▸ Phase: Explore the codebase (0–37)  │  Input                                          │
│   ▸ Turns 1–37 · off-skill (37) ····· │  { "command": "sed -i '' 's/…/' data/sales.csv" }│
│ ▾ Phase: Build the report (38–120)    │                                                 │
│   ▾ Turn 38  agent · 3 actions  ● c15 │  Output (error)                                 │
│       38  Read   SKILL.md             │  sed: 1: "…": invalid command code              │
│       39  Bash   python3 scripts/va…  │                                                 │
│     ⊘ 40  Bash   sed -i '' … sales.csv│  ─────────────────────────────────────────────── │
│   ▸ Turn 39  agent · 1 action         │  Decided here                                   │
│   ▸ Turns 41–76 · off-skill (36) ···· │  ⊘ c14 violated  "Never modify the input CSV"   │
│   ▾ Turn 77  agent · 2 actions  ● c2  │  The agent ran an in-place sed -i edit on …     │
│       …                               │  method: judge · view: skeleton · L15           │
│ ▸ Phase: Finish (121–200)             │  [open claim c14 →]                             │
│                                      │                                                 │
│ Prompt · Final answer · Files (2)    │  tabs: [Detail] [Scores 16] [Prompt] [Answer] [Files 2] [Info] │
└──────────────────────────────────────┴─────────────────────────────────────────────────┘
```

**Header**: `trace.task`, `trace.variant.name`, `r{rep}`, `trace.model`, `digest.stats.turns/tool_calls/tokens_full`, `trace.result.cost_usd`, `check.judge_cost_usd`, `check.judge_mode`, `check.window.calls/tokens_sent/tokens_full`, `check.window.escalated.length` ("{n} confirmed on the whole-trace skeleton"), `trace.episode`, `trace.skill_loaded===false` warning, `trace.error`. **Scores strip**: counts of `check.claims[].status` as status badges (each links to the Scores tab filtered), success badge from `trace.success`. Prev/next run arrows (within the current P6 filter order, stored in session) — later.

**Left pane: tree**. Built from `digest.turns[]` and `trace.events[]`:

- Level 0: phases from `digest.intents.data.phases[]` (`intent`, `first..last`); when no intents, level 0 is turns directly.
- Level 1: turns. Row shows `Turn {n}`, who (`kind`: agent / user "you" / meta "injected"; `agent` → "subagent {agent}" nested one level deeper under the turn that spawned it, using `events[].parent`/`depth` when present), `actions.length`, `errors` (danger count), evidence dots (claims whose `check.claims[id].evidence` falls in `start..end`, colored by status, max 3 + "+n"), `said` clipped as a secondary line. Turns listed in `check.window.off_skill_turns` are rendered dimmed; consecutive off-skill turns (or turns with identical `said`+`summary`, i.e. polling) collapse into one row "Turns 1–37 · off-skill (37)" which expands on click.
- Level 2: events of the turn: `i`, kind glyph, `tool` (and `raw_tool` in tooltip when different), target (`input.command|file_path|pattern|skill|description|url|query`, mono, ellipsized), error tag, evidence badge(s). Text events show the first line of `text`.
- Selection: click selects (`?event=`), ↑/↓ or j/k move, Enter expands/collapses, `/` focuses the filter. Selected row uses `--ds-color-bg-accent-subtle`.
- Filter: text matches tool, target, text; "tools" select; "only evidence" shows only events referenced by any claim (plus their turns); "collapse off-skill" default on when `window` exists.
- A thin **minimap** strip above the tree (later): one tick per turn, colored by evidence status, off-skill gray; click jumps.
- Lazy rendering: render turn rows for all turns, but event rows only when a turn is expanded (keeps a 511-event trace light). Auto-expand the turn containing `?event=` and scroll it into view.

**Right pane: tabs**

| Tab | Content |
|---|---|
| Detail (default) | For a tool event: title `Event {i} · {tool}`, Input (`input` pretty JSON, `pre`, copy button), Output (`output`, error styling when `is_error`), "Decided here": every claim/condition/reference result whose `evidence == i`, each with status badge, id, text (`R.claims[id].text`), `reason`, `method`, `view` ("decided by window excerpt" / "decided by whole-trace skeleton"), lines `L{span}`, link to P5. For a text event: the full text. Nothing selected: the turn summary (`said`, `summary`, files) or a hint "Select an event". |
| Scores | Table of all results: group Skill claims (`check.claims`), Branch conditions (`check.conditions`: value held / didn't hold / unknown), Reference steps (`check.reference.claims`). Columns: status badge, id, text, method (`tool`/`file`/`judge`/`branch`), view (`window`/`skeleton`), evidence (link selects event in the tree and switches to Detail), reason. Filter chips by status (`?status=`). Sort by status order then id. Unrouted (`window.unrouted`) noted: "No turn was found for {ids}; judged on the whole trace." |
| Prompt | `trace.task_prompt` |
| Answer | `trace.result.text` |
| Files | `trace.files` as collapsible mono blocks; header note when `trace.files_reconstructed` ("rebuilt from Write/Edit calls"); `trace.git.status/log/tags`; `trace.success` checks as pass/fail rows |
| Info | `trace.init` (model, tools, version), `trace.trigger`, `trace.enforce`, `trace.blocked[]`, `trace.wall_s`, `check.claims_sha` vs stale, `check.window.router_model/router_cost_usd`, `digest.intents.model` + "named by a model that never sees the skill; the judge never sees this text", `digest.stats` |

Deep links: `?event=199` selects and scrolls; `?claim=c14` selects that claim's evidence event and opens Detail (if no evidence, opens Scores with the row highlighted); `?tab=scores&status=missed`.

Responsive: <900px the two panes become a top segmented control "Timeline | Detail | Scores"; selecting an event in Timeline switches to Detail; a back pill returns. Tree rows have 44px touch targets.

Empty/error: `check` missing → Scores tab shows "Not graded yet: run `skillgraph check <dir>`"; the tree still works. Trace > 2 MB: show a one-time notice "large trace; events render as you expand turns".

### 4.8 P8 Compare

```
┌ Compare   before [demo ▾]  →  after [long ▾]  [swap]                                  ┐
│ Verdict: mixed. bugfix-tdd: 1 better, 1 worse. release-notes: 2 better, 2 worse.      │
│ Task success with skill 93% → 100% · cost $41.86 → $12.40 · runs 40 → 11              │
│ Claims are matched by id and text; editing a skill renumbers claims, so ids can differ.│
├───────────────────────────────────────────────────────────────────────────────────────┤
│ bugfix-tdd   SKILL.md 16 → 16 lines · success 100% → 100%        [1 better][1 worse][9 same]
│ ┌ change  claim                               verdict            with skill   runs ─┐ │
│ │ ▼ worse  c9 Never edit or delete existing…  Inert → Inert      100% → 67%   4 → 2 │ │
│ │ ▲ better c8 Write FIX_NOTES.md with three…  Ignored → Working   25% → 67%   4 → 3 │ │
│ │ (9 same, collapsed) [show]                                                        │ │
│ └───────────────────────────────────────────────────────────────────────────────────┘ │
│ release-notes · Reference: coverage 75% → 75% · steps done 91% → 100%                 │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

Sources: `D.skills[]` (`claims[].{before, after, text, verdict[2], skill_rate[2], baseline_rate[2], change}`, `counts`, `success`, `cost_usd`, `runs`, `lines`, `reference`), `D.only_before/only_after`. Verdict sentence rule: better > worse → "Improved"; worse > better → "Regressed"; equal and >0 → "Mixed"; both 0 → "No change in verdicts". Per-skill: counts as filter chips; `same` rows collapsed by default; rows sorted worse, better, changed, new, removed, same. "weak" marker on a row when the after-set claim is weak (requires `S` of after set; MVP: fetch `/api/set/<b>` and join by claim id). Row click → P5 in the after set (`#/s/:b/claim/:skill/:after`), or before set when removed. Skill filter `?skill=`. The run-set switcher in the top bar is replaced by the before/after pickers on this page. Empty: fewer than 2 sets → "Compare two run sets: `skillgraph dashboard runs/before runs/after`". `a === b` → "Pick two different run sets."

### 4.9 P9 Glossary

Static page listing every term from section 4.10 with the one-liner and "what to do". Linked from the `?` in the top bar and from every help popover ("more →").

### 4.10 Copy: verdicts, statuses, diagnoses, tooltips

Verdicts (claim level; the first sentence is the badge tooltip, the second the "what to do"):

| Verdict | Plain language | What to do |
|---|---|---|
| working | The agent does this with the skill and not without it. This line is doing its job. | Keep it. |
| inert | The agent does this with or without the skill. The line costs context and changes nothing. | Cut it, unless it guards against damage. |
| ignored | The agent doesn't do this even with the skill. | Make it concrete; give it a done-criterion (a file, a command, an exact string). |
| interfering | The agent does this without the skill but stops when the skill is loaded. Something in the skill pushes the other way. | Find the line that conflicts and fix it. |
| followed | The agent does this with the skill. There are no runs without the skill to compare against. | Add baseline runs to learn whether the line matters. |
| untested | No run reached this branch of the skill. | Add a task that takes this branch (`skillgraph tasks`). |
| weak (tag) | This verdict rests on fewer than 3 runs per variant. | Treat as a hint; rerun with `--runs 3`. |

Per-run statuses:

| Status | Plain language |
|---|---|
| satisfied | The run did this. |
| missed | The run didn't do this. |
| violated | The run did what this rule forbids. |
| out of order | The run did this, but before a step it must follow. |
| unclear | The judge couldn't tell from the trace. |
| n/a | This branch didn't apply in this run. |

Reference diagnoses (from `coverage.DIAGNOSES`, reuse verbatim): ok, gap, ignored, conflict, partial, implicit, untested, unreached.

Other terms: *claim* ("one checkable instruction extracted from SKILL.md, anchored to its lines"), *condition* ("a branch in the skill; decides which claims apply"), *baseline* ("runs without the skill"), *ablation* ("runs with some lines deleted, to see whether they matter"), *load-bearing* / *no-effect* / *removal-helped* (use `ablations[].note`), *judge* ("a model answers a yes/no question about the trace; mechanical checks need no model"), *windowed* ("long trace: the judge read the turns that hold the evidence plus a skeleton of the whole run"), *window / skeleton* ("which view decided the verdict"), *off-skill turn* ("no judge question pointed at this turn; it's likely unrelated to the skill"), *phase* ("a name for a stretch of turns, given by a model that never sees the skill"), *episode* ("one skill load inside a longer session"), *mechanically checkable* ("graded by a regex on tool calls or files, no model"), *lint* ("static problems in the skill text or its claims"), *coverage* ("share of the reference process the skill asks for"), *stale* ("SKILL.md or the claims changed after these results were made; rerun extract/check").

Tone: sentence case, no exclamation marks, no emoji; buttons say what they do ("Open run", "Clear filters").

---

## 5. Design direction (OpenDesigner tokens, platform look)

### 5.1 Shell

| Part | Tokens |
|---|---|
| Sidebar | width 232px (collapsed 56px); background `--ds-color-surface-nav`; border-right `--ds-border-width-1` `--ds-color-border-subtle`; items `--ds-size-control-md` tall, `--ds-text-label-lg`, `--ds-color-text-secondary`; active: `--ds-color-bg-neutral-subtle-hover` + `--ds-color-text-primary`; section headers `--ds-text-label-sm` (caps) `--ds-color-text-tertiary`; icons 16px inline SVG, stroke `--ds-icon-stroke-sm` |
| Top bar | height `--ds-size-row-lg`, `--ds-color-surface-base`, bottom border subtle; switcher = `.control`; breadcrumbs `--ds-text-body-sm`, separators `--ds-color-text-tertiary` |
| Page | padding `--ds-space-24` `--ds-space-16` (`--ds-space-16` all round below 768px); max content width none (tables need it); page title `--ds-text-title-lg`; meta line `--ds-text-body-sm` secondary |
| Cards | `--ds-color-surface-raised`, `--ds-radius-8`, `--ds-elevation-raised`; use only to group, never nested |
| Tabs (in-page) | underline style: 32px tall, `--ds-text-label-lg`, active underline `--ds-border-width-2` `--ds-color-bg-accent-bold`, count badge neutral |

### 5.2 Tables

Row height `--ds-size-row-sm` (36px) compact; header `--ds-text-label-md` secondary, sticky; cell padding `--ds-space-6` `--ds-space-8`; separators `--ds-color-border-separator`; hover `--ds-color-bg-neutral-subtle`; selected `--ds-color-bg-accent-subtle`; numerics `--ds-text-numeric-md` right-aligned; truncate text columns with ellipsis and a `title`; the wrapper is `overflow-x: auto` with `min-width` set per table (Runs 880px, Claims 760px) so the page itself never scrolls horizontally.

### 5.3 Color map (the only non-neutral color on screen)

| Meaning | Badge bg / text | Bar segment / line edge |
|---|---|---|
| working, followed, satisfied, ok, passed, better, load-bearing | `bg-success-subtle` / `text-success` | `bg-success-bold` (followed: `border-success`) |
| ignored, violated, missed, gap, conflict, failed, worse, error | `bg-danger-subtle` / `text-danger` | `bg-danger-bold` |
| interfering, out of order, partial, weak, removal-helped, stale | `bg-warning-subtle` / `text-warning` | `bg-warning-bold` |
| unclear, implicit, windowed, changed, evidence-for | `bg-info-subtle` / `text-info` | `bg-info-bold` |
| inert, no-effect, same | `bg-neutral-subtle` / `text-secondary` | `border-strong` |
| untested, unreached, n/a, off-skill | transparent, dashed `border-strong` | `border-default` dashed |
| selected, phase headers, links | accent | `bg-accent-subtle` |

Every badge has a glyph or word, never color alone (DESIGN.md "Do"). Accent is reserved for selection, links, the one primary action per view (DC-L15-03).

### 5.4 Light / dark

Already handled by `tokens.css` (`prefers-color-scheme` + `[data-theme]`). Keep the three-state toggle; move it to the sidebar footer. Check the new verdict ramps in dark: `bg-*-subtle` on `surface-nav` for sidebar badges.

### 5.5 Responsive rules

- 375px: no horizontal page scroll (acceptance test); tables scroll inside `.table-wrap`; two-pane layouts (Skill lines/claims, Run tree/detail) become segmented views; drawers are full-width sheets; touch targets 44px (`--ds-size-target-touch`) while visuals stay compact.
- Sidebar: drawer <768, rail 768–1099, open ≥1100.
- Motion: 200ms `--ds-motion-transition-*`, fades under `prefers-reduced-motion`.

### 5.6 Typography hierarchy per page

Exactly three sizes per view: title `title-lg`, body `body-md`, meta `body-sm`; labels `label-md`. Mono (`font-family-mono`, 12px) only for SKILL.md lines, ids, paths, tool targets, JSON.

---

## 6. Data / API changes (server stays read-only; export keeps working)

All additions are additive; `data.js` export gets them automatically because it serializes the same functions.

### 6.1 `/api/sets` (RunSets.sets)

Add per set: `tasks` (distinct task count), `has_reference` (any skill with `reference`), `imported` (any trace with `result.subtype == "imported"`), `has_baseline` (any `baseline` variant), `updated` (max mtime of `*/check.json|trace.json`, ISO). Used by the switcher and to pick "followed" wording.

### 6.2 `/api/set/<id>` (RunSets.set / runs)

- Run index rows: add `wall_s` (`trace.wall_s`), `duration_ms` (`trace.result.duration_ms`), `blocked` (`len(trace.blocked)`), `imported` (bool), `prompt` (`task_prompt` clipped to 160 chars), `failed` (list of claim ids with status in missed/violated/out_of_order), `unclear` (count), `turns` (`digest.stats.turns` when digest exists else `None`).
- Aggregate claims (`aggregate_skill`): add `results` per claim: `[{"path", "task", "variant", "rep", "status", "reason", "evidence", "method", "view"}]` for every run (not just failures). This removes the per-row fetch on P5. Size: ~40 runs × 16 claims × ~120 bytes ≈ 80 KB for demo; acceptable. Keep `miss_notes` for backward compatibility.
- `summary` already carries what CI uses; no change.
- Keep the `error` field.

### 6.3 `/api/run/<id>?path=`

- Add `turn_of_event`: list where index = event `i`, value = turn `n` (cheap; avoids scanning `digest.turns` in JS). Optional; JS can compute from `turns[].start/end`.
- Keep `routes` popped (big); the window's `routes` (claim → turns) stays in `check.window.routes`.
- No change to trace size. Document that `/api/run` can be ~1.3 MB for 90k-token traces.

### 6.4 Caching (the expensive parts)

- `RunSets.set()` costs ~1.4 s on 11 long runs because `runs()` renders every trace for `approx_tokens`. Cache in-process: key `(sid, signature)` where signature = tuple of `(path, mtime, size)` for `*/*/*/r*/{trace,check,digest}.json` plus `runset.json` and the claims files; recompute only when the signature changes. Same for `diff(a,b)` (keyed on both signatures) and `report(sid)`.
- Token counts: prefer `digest.json.stats.tokens_full` when present and current (`digest.trace_sha` matches), else compute once and keep in the cache.
- `run()` is fast (0.2 s); memoize per `(sid, path, mtime)` anyway so revisits are instant.
- Static export: unchanged shape; optionally `--export --no-runs` later to omit traces when the site is only for the aggregate (not MVP).

### 6.5 Recommendations

Client-side in MVP (`recommend(S)` in `app.js`, pure, with a test in `tests/test_dashboard.py` via a small JSON fixture is not possible without a JS runtime; instead keep the function simple and add a Python port to `summary.py` later so `summary --json` and the UI agree).

### 6.6 Nothing else

No writes, no model calls, no new static assets besides `app.js`/`app.css` changes (icons are inline SVG built by `h()`), CSP unchanged, `allowed_hosts` unchanged.

---

## 7. Scope

### 7.1 MVP (this build)

1. App shell: sidebar (3 responsive modes), top bar with switcher, breadcrumbs, theme in sidebar footer, Glossary page, help popovers on verdict/status/diagnosis badges.
2. P1 Overview with health sentences, linked KPIs, top-3 recommendations.
3. P2 Recommendations (rules 1–12).
4. P4 Skill with tabs (Lines, Claims, Conditions, Ablations, Reference table, Lint) and verdict/line filters in the URL.
5. P5 Claim drawer (status per run; reasons via `results` from 6.2, falling back to lazy `/api/run` fetch if the field is absent so old exports still work).
6. P6 Runs table with column filters, sort, "Only problems", claim column, group by task.
7. P7 Run: tree (phases → turns → events, off-skill collapse, evidence dots, filter, keyboard), detail pane with Detail/Scores/Prompt/Answer/Files/Info tabs, deep links `?event=&claim=`.
8. P8 Compare with verdict sentence and collapsed "same" rows.
9. Server: fields in 6.1–6.2, cache in 6.4. Existing tests updated; add tests for new fields and for cache invalidation on mtime change.
10. Static export verified: every page reachable from `index.html` opened from disk.

### 7.2 Later

Minimap strip in the trace tree; prev/next run inside the trace page; saved views (localStorage) and column chooser persistence; reference tree drawing from `edges`; dashboards/charts (cost over time, verdicts by model); multi-select compare (3+ sets); virtualized tables; global search palette with keyboard; per-event "copy as JSON"; sessions page grouping episodes of one source session (`trace.episode`, when `import` records a session id).

### 7.3 Non-goals

No auth; no editing of SKILL.md or claims from the UI; no model calls or re-grading from the UI; no external JS/CSS or fonts; no build step; no server-side state or writes; no analytics; no horizontal page scroll at any width.

---

## 8. Acceptance criteria (check in a browser)

Start: `uv run skillgraph dashboard examples/runs/demo runs/digest-validation/long --no-open --port 8790`, then open `http://127.0.0.1:8790/`.

Shell and navigation
1. The page shows a left sidebar with Overview, Recommendations, Skills (csv-report, bugfix-tdd, release-notes), Runs, Compare, and a footer with Glossary, Report, theme toggle. The sidebar item of the current page is highlighted on every page, including Compare and Run.
2. The top bar shows the run-set switcher on every page; switching sets keeps the current page type (Overview → Overview, Runs → Runs).
3. Breadcrumbs on a run page read `demo › csv-report › runs › broken-data / skill / r1` and every segment except the last is a link.
4. At 375px wide: no horizontal page scrollbar on any page; the sidebar is a drawer behind a ☰ button; the Runs table scrolls inside its container; the Skill page shows a "Text | Claims" switch; the Run page shows "Timeline | Detail | Scores".
5. Old links still work: `#/s/demo/skill/csv-report?claim=c3` opens the Skill page with the c3 drawer; `#/diff/demo/long` redirects to `#/compare/demo/long`.
6. Theme toggle cycles system/light/dark and persists across reloads; dark mode has no unreadable badge (spot-check ignored/interfering/inert on the sidebar and tables).

Overview
7. Each skill row has a one-sentence health line; for csv-report it says the skill does real work and mentions 0% → 100% task success; for bugfix-tdd it says mostly redundant.
8. Clicking "93% with skill" opens Runs filtered to `variant=skill, outcome=failed` and the URL contains those parameters.
9. "Needs attention: 4" equals the number of high-priority items on the Recommendations page.

Recommendations
10. The list for `demo` contains, as high items: bugfix-tdd c8 (ignored, L10), release-notes reference step `credit` (gap), csv-report lint error (L24). Each item shows the SKILL.md line(s), a "why" with counts, a "do", and links; the c8 item's "3 failing runs" link opens Runs with the c8 column showing a status badge per row.
11. Filtering by skill = release-notes hides the other skills' items and the URL has `?skill=release-notes`.

Skill
12. The Lines tab colors L12–L16 of csv-report with the success edge and shows the lint error underline on L24; clicking L13 filters the claim list to c3 and c4 and sets `?line=13`; clicking the line again clears it.
13. The Claims tab sorts by any column; filtering verdict = inert shows 5 rows for csv-report; "with" and "without" score chips link to Runs with the claim column.
14. The Reference tab for release-notes shows coverage 75%, 1 gap (`credit`), 2 implicit, and the paths list with tested/untested tags.
15. Ablations tab shows three cards for csv-report with verdict badges and the removed text.

Claim drawer
16. Opening c14 (csv-report) in the `long` set shows "violated" in the run `valid-data-needle/skill/r1` with the judge's reason and an "event 199" link that opens the run with event 199 selected and its Detail pane showing the `sed -i` command.
17. Esc closes the drawer and the URL returns to the skill page.

Runs
18. Filters: skill, task, variant, outcome, status, judge, claim, search, "Only problems"; each updates the URL; "Clear filters" restores 40 of 40.
19. Sorting by cost desc puts the most expensive run first; header shows `aria-sort`.
20. Group by task shows collapsible groups with a run count and pass rate.

Run (trace)
21. For `long/csv-report/valid-data-needle/skill/r1` the tree shows collapsed rows for off-skill turns ("Turns 1–37 · off-skill"), evidence dots on turns 38 and 77, and expands turn 38 to its events; typing `sed` in the filter leaves only matching events (and their turns).
22. Selecting event 199 shows Input, Output (marked error), and "Decided here: c14 violated … decided by whole-trace skeleton"; the Scores tab lists 16 claim results with method and view columns; clicking "event 100" on c1 selects event 100 in the tree.
23. Keyboard: j/k move the selection, Enter toggles a turn, `/` focuses the filter; the URL `?event=` follows the selection.
24. A run without `check.json` shows the tree and "Not graded yet" in Scores (verify by temporarily copying a run dir without check.json into a scratch set, or by unit test).
25. The page is interactive within 1 s after the JSON arrives for the 511-event trace (events render on expand).

Compare
26. `#/compare/demo/long` shows a verdict sentence, per-skill counts as chips, "same" rows collapsed with a "show 9 same" control, and a row click opens the claim in the after set.

Server / export
27. `/api/sets` rows include `tasks`, `has_reference`, `has_baseline`, `imported`, `updated`; `/api/set/demo` run rows include `wall_s`, `failed`, `prompt`; each aggregate claim has `results[]`.
28. Second request to `/api/set/long` returns in under 100 ms (cache); touching a `check.json` (`touch`) makes the next request recompute.
29. `uv run skillgraph dashboard examples/runs/demo runs/digest-validation/long --export /tmp/site` produces a site where, opened from disk, Overview, Recommendations, Skill tabs, Claim drawer, Runs filters, Run tree with event selection, Compare, and Glossary all work with no network requests and no console errors; CSP is unchanged.
30. `uv run pytest tests/test_dashboard.py` passes, with new tests for the added fields and the cache signature.
