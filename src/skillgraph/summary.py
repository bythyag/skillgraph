"""`skillgraph summary DIR`: the results of a run set as text, JSON, or JUnit XML, with
`--fail-on` to turn verdicts into a non-zero exit code for CI."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

# Everything --fail-on accepts. Claim verdicts, reference diagnoses, and two run-level checks.
FAIL_KEYS = {
    "ignored", "interfering", "untested",                       # skill claim verdicts
    "gap", "conflict", "partial", "unreached", "ref-ignored",   # reference diagnoses ("ref-ignored" = ignored reference step)
    "lint-error",                                               # static error in the skill or its claims
    "task-failure",                                             # a run with the skill failed a success check
}


def summarize(data: dict) -> dict:
    """Condense report data (report.build_data) into a stable, machine-readable shape."""
    skills = []
    for s in data["skills"]:
        claims = [{
            "id": c["id"], "kind": c["kind"], "text": c["text"], "lines": c["span"], "verdict": c["quadrant"],
            "weak": c.get("weak", False), "skill_rate": c["skill_rate"], "baseline_rate": c["baseline_rate"],
            "n_skill": (c["rates"].get("skill") or {}).get("n", 0), "n_baseline": (c["rates"].get("baseline") or {}).get("n", 0),
            "failure_mode": c.get("failure_mode"),
        } for c in s["claims"]]
        counts: dict[str, int] = {}
        for c in claims:
            counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
        failed_runs = [f"{r['task']}/{r['variant']}/r{r['rep']}" for r in s["runs"]
                       if r["variant"] == "skill" and r["all_success"] is False]
        entry = {
            "skill": s["skill"], "classification": s["classification"], "runs": s["totals"]["runs"],
            "cost_usd": round(s["totals"]["cost_usd"], 4), "success": s["totals"]["success"],
            "verdicts": counts, "claims": claims, "failed_runs": failed_runs,
            "lint_errors": [f["message"] for f in s["lint"] if f["severity"] == "error"],
            "ablations": [{"lines": a["remove"][0], "verdict": a["verdict"], "affected": a["affected"]} for a in s["ablations"]],
            "reference": None,
        }
        R = s.get("reference")
        if R:
            entry["reference"] = {
                "name": R["name"], "coverage": (R["score"] or {}).get("coverage"), "correctness": R["correctness"],
                "paths_tested": sum(p["tested"] for p in R["paths"]), "paths_total": len(R["paths"]),
                "diagnoses": R["diagnosis_counts"],
                "steps": [{"id": n["id"], "kind": n["kind"], "text": n["text"], "relation": n["relation"],
                           "claims": n["claims"], "rate_with": n["rate_with"], "rate_without": n["rate_without"],
                           "diagnosis": n["diagnosis"]} for n in R["nodes"]],
                "findings": [f["message"] for f in R["findings"]],
            }
        skills.append(entry)
    return {"runset": data.get("runset"), "model": data.get("model"), "skills": skills}


def failures(summary: dict, fail_on: set[str]) -> list[dict]:
    out = []
    for s in summary["skills"]:
        for c in s["claims"]:
            if c["verdict"] in fail_on:
                out.append({"skill": s["skill"], "id": c["id"], "kind": c["verdict"],
                            "message": f"{c['id']} (lines {c['lines'][0]}-{c['lines'][1]}) is {c['verdict']}: {c['text']}"})
        if "lint-error" in fail_on:
            out += [{"skill": s["skill"], "id": "lint", "kind": "lint-error", "message": m} for m in s["lint_errors"]]
        if "task-failure" in fail_on:
            out += [{"skill": s["skill"], "id": r, "kind": "task-failure", "message": f"{r} failed its success checks"}
                    for r in s["failed_runs"]]
        for step in (s["reference"] or {}).get("steps", []):
            kind = "ref-ignored" if step["diagnosis"] == "ignored" else step["diagnosis"]
            if kind in fail_on:
                out.append({"skill": s["skill"], "id": f"ref:{step['id']}", "kind": kind,
                            "message": f"reference step {step['id']} is {step['diagnosis']}: {step['text']}"})
    return out


def _pct(x) -> str:
    return "–" if x is None else f"{x:.0%}"


def to_text(summary: dict, fails: list[dict]) -> str:
    lines = [f"run set {summary['runset']}  (agent {summary['model']})"]
    for s in summary["skills"]:
        v = s["verdicts"]
        succ = s["success"]
        lines.append(f"\n{s['skill']}: {s['runs']} runs, ≈ ${s['cost_usd']:.2f}; task success {_pct(succ.get('skill'))} with the skill, "
                     f"{_pct(succ.get('baseline'))} without")
        lines.append("  verdicts: " + ", ".join(f"{k} {n}" for k, n in sorted(v.items(), key=lambda kv: -kv[1])))
        weak = sum(c["weak"] for c in s["claims"])
        if weak:
            lines.append(f"  {weak} verdicts rest on fewer than 3 runs per variant; add --runs before acting on them")
        for c in s["claims"]:
            if c["verdict"] in ("ignored", "interfering"):
                lines.append(f"  {c['verdict']:<11} {c['id']:<5} L{c['lines'][0]:<3} {c['text']}")
        R = s["reference"]
        if R:
            lines.append(f"  reference {R['name']}: {_pct(R['coverage'])} covered by the skill; steps done "
                         f"{_pct(R['correctness'].get('skill'))} with / {_pct(R['correctness'].get('baseline'))} without; "
                         f"{R['paths_tested']}/{R['paths_total']} paths tested")
            for st in R["steps"]:
                if st["diagnosis"] in ("gap", "conflict", "ignored", "partial", "unreached"):
                    lines.append(f"  {st['diagnosis']:<11} {st['id']:<14} {st['text']}")
    if fails:
        lines.append(f"\n{len(fails)} item(s) matched --fail-on:")
        lines += [f"  [{f['kind']}] {f['skill']}: {f['message']}" for f in fails]
    return "\n".join(lines) + "\n"


def to_junit(summary: dict, fail_on: set[str]) -> str:
    """One <testsuite> per skill, one <testcase> per claim and reference step."""
    suites = []
    for s in summary["skills"]:
        cases = []
        items = [(c["id"], c["verdict"], c["text"]) for c in s["claims"]]
        items += [(f"ref:{st['id']}", "ref-ignored" if st["diagnosis"] == "ignored" else st["diagnosis"], st["text"])
                  for st in (s["reference"] or {}).get("steps", [])]
        failed = 0
        for cid, kind, text in items:
            body = ""
            if kind in fail_on:
                failed += 1
                body = f"<failure message={quoteattr(kind)}>{escape(text)}</failure>"
            elif kind in ("untested",):
                body = "<skipped/>"
            cases.append(f'    <testcase classname={quoteattr(s["skill"])} name={quoteattr(f"{cid}: {text[:80]}")}>{body}</testcase>')
        suites.append(f'  <testsuite name={quoteattr(s["skill"])} tests="{len(items)}" failures="{failed}">\n'
                      + "\n".join(cases) + "\n  </testsuite>")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<testsuites name="skillgraph">\n' + "\n".join(suites) + "\n</testsuites>\n"
