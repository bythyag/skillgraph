"""Roll checked runs up into per-claim rates, quadrants, ablation effects, and line heat."""

from __future__ import annotations

import json
from pathlib import Path

from skillgraph.graph import checkability, lint
from skillgraph.schema import doc_sha, sha256_text

FOLLOWED = 0.5  # follow rate at or above this counts as "followed"
MIN_RUNS = 3    # verdicts from fewer applicable runs per variant are marked weak

QUADRANTS = {
    "working": "Followed with the skill, not without it: the text is doing its job.",
    "inert": "Followed with and without the skill: the agent does this anyway, so the text costs context.",
    "ignored": "Not followed even with the skill: harden or rewrite this text.",
    "interfering": "Followed without the skill but not with it: something in the skill suppresses it.",
    "followed": "Followed with the skill (no baseline runs to compare against).",
    "untested": "Never applicable in these runs (e.g. a branch that was never taken).",
}


def collect(out: Path) -> list[dict]:
    rows = []
    for tp in sorted(out.glob("*/*/*/r*/trace.json")):
        rd = tp.parent
        cp = rd / "check.json"
        trace = json.loads(tp.read_text())
        rows.append({"dir": rd, "trace": trace, "check": json.loads(cp.read_text()) if cp.exists() else None})
    return rows


def _rate(statuses: list[str]) -> dict:
    counts = {s: statuses.count(s) for s in set(statuses)}
    applicable = [s for s in statuses if s in ("satisfied", "missed", "violated", "out_of_order")]
    n = len(applicable)
    return {
        "followed": (sum(s == "satisfied" for s in applicable) / n) if n else None,
        "n": n,
        "counts": counts,
    }


def quadrant(skill_rate: float | None, base_rate: float | None) -> str:
    if skill_rate is None:
        return "untested"
    s = skill_rate >= FOLLOWED
    if base_rate is None:
        return "followed" if s else "ignored"
    b = base_rate >= FOLLOWED
    return {(True, True): "inert", (True, False): "working", (False, False): "ignored", (False, True): "interfering"}[(s, b)]


def _overlaps(span: list[int], ranges: list[list[int]]) -> bool:
    return any(span[0] <= b and a <= span[1] for a, b in ranges)


def aggregate_skill(name: str, doc: dict, skill_dir: Path, rows: list[dict], *,
                    ref: dict | None = None, ref_doc: dict | None = None, coverage: dict | None = None) -> dict:
    skill_md = (skill_dir / "SKILL.md").read_text()
    rows = [r for r in rows if r["trace"]["skill"] == name and r["check"]]
    variants = sorted({r["trace"]["variant"]["name"] for r in rows},
                      key=lambda v: (v != "skill", v != "baseline", v))
    tasks = sorted({r["trace"]["task"] for r in rows})

    by_variant: dict[str, list[dict]] = {v: [r for r in rows if r["trace"]["variant"]["name"] == v] for v in variants}

    def claim_rate(cid: str, variant: str) -> dict:
        return _rate([r["check"]["claims"].get(cid, {}).get("status", "unclear") for r in by_variant.get(variant, [])])

    def success_rate(variant: str) -> float | None:
        vals = [all(r["trace"]["success"].values()) for r in by_variant.get(variant, []) if r["trace"]["success"]]
        return sum(vals) / len(vals) if vals else None

    claims = []
    for c in doc["claims"]:
        rates = {v: claim_rate(c["id"], v) for v in variants}
        s = rates.get("skill", {}).get("followed")
        b = rates.get("baseline", {}).get("followed") if "baseline" in rates else None
        reasons = []
        for r in by_variant.get("skill", []):
            res = r["check"]["claims"].get(c["id"], {})
            if res.get("status") not in ("satisfied", "n/a") and res.get("reason"):
                reasons.append(f"{r['trace']['task']}/r{r['trace']['rep']}: {res['reason']}")
        failures = {k: v for k, v in rates.get("skill", {}).get("counts", {}).items()
                    if k in ("missed", "violated", "out_of_order")}
        n_skill = rates.get("skill", {}).get("n", 0)
        n_base = rates.get("baseline", {}).get("n", 0) if "baseline" in rates else None
        claims.append({
            **{k: c[k] for k in ("id", "kind", "text", "span", "after", "when", "check")},
            "rates": rates,
            "skill_rate": s,
            "baseline_rate": b,
            "quadrant": quadrant(s, b),
            "weak": s is not None and (n_skill < MIN_RUNS or (n_base is not None and n_base < MIN_RUNS)),
            "failure_mode": max(failures, key=failures.get) if failures else None,
            "miss_notes": reasons[:6],
        })

    conditions = []
    for k in doc["conditions"]:
        held = {}
        for v in variants:
            vals = [r["check"]["conditions"].get(k["id"], {}).get("value") for r in by_variant[v]]
            known = [x for x in vals if x is not None]
            held[v] = {"true": sum(known), "n": len(known)}
        conditions.append({**k, "held": held})

    runs = []
    for r in rows:
        t, chk = r["trace"], r["check"]
        runs.append({
            "task": t["task"],
            "variant": t["variant"]["name"],
            "rep": t["rep"],
            "success": t["success"],
            "all_success": all(t["success"].values()) if t["success"] else None,
            "cost_usd": (t.get("result") or {}).get("cost_usd"),
            "judge_cost_usd": chk.get("judge_cost_usd"),
            "turns": (t.get("result") or {}).get("num_turns"),
            "wall_s": t.get("wall_s"),
            "tool_calls": sum(e["kind"] == "tool" for e in t["events"]),
            "skill_loaded": t.get("skill_loaded"),
            "blocked": len(t.get("blocked", [])),
            "error": t.get("error"),
            "statuses": {cid: res["status"] for cid, res in chk["claims"].items()},
            "path": "/".join(Path(r["dir"]).parts[-4:]),  # skill/task/variant/rN, no local paths
        })
    runs.sort(key=lambda x: (x["task"], variants.index(x["variant"]), x["rep"]))

    ablations = []
    for v in variants:
        if not v.startswith("ablate-"):
            continue
        removed = by_variant[v][0]["trace"]["variant"]["remove"]
        affected = [c["id"] for c in claims if _overlaps(c["span"], removed)]
        effects = []
        for c in claims:
            before, after = c["rates"].get("skill", {}).get("followed"), c["rates"][v]["followed"]
            if before is None or after is None:
                continue
            delta = after - before
            if c["id"] in affected or abs(delta) >= 0.5:
                effects.append({"id": c["id"], "from": before, "to": after, "delta": delta, "affected": c["id"] in affected})
        aff = [e for e in effects if e["affected"]]
        drop = min((e["delta"] for e in aff), default=0.0)
        s_before, s_after = success_rate("skill"), success_rate(v)
        if aff and drop <= -0.25:
            verdict = "load-bearing"
            note = "Removing these lines made the agent stop doing what they ask."
        elif s_before is not None and s_after is not None and s_after > s_before:
            verdict = "removal-helped"
            note = "Task success went up without these lines."
        else:
            verdict = "no-effect"
            note = "Behavior didn't change without these lines: trim them, or rewrite them if the behavior matters."
        lines = skill_md.splitlines()[removed[0][0] - 1: removed[0][1]]
        ablations.append({
            "variant": v, "remove": removed, "text": "\n".join(lines),
            "affected": affected, "effects": effects,
            "success_skill": s_before, "success_ablated": s_after,
            "runs": len(by_variant[v]), "verdict": verdict, "note": note,
        })

    ablations.sort(key=lambda a: a["remove"][0][0])
    cost = sum((r["cost_usd"] or 0) + (r["judge_cost_usd"] or 0) for r in runs)
    loaded = [r["skill_loaded"] for r in runs if r["variant"] != "baseline"]
    quad_counts = {}
    for c in claims:
        quad_counts[c["quadrant"]] = quad_counts.get(c["quadrant"], 0) + 1

    reference = aggregate_reference(ref, ref_doc, coverage, doc, by_variant, variants) if ref else None
    from skillgraph.trees.reference import merge_docs
    expected_sha = doc_sha(merge_docs(doc, ref_doc))
    stale_checks = sum(r["check"].get("claims_sha") != expected_sha for r in rows)

    return {
        "reference": reference,
        "stale_checks": stale_checks,
        "skill": name,
        "classification": doc.get("classification"),
        "summary": doc.get("summary"),
        "checkability": checkability(doc),
        "stale": doc.get("source_sha256") != sha256_text(skill_md),
        "lines": skill_md.splitlines(),
        "claims": claims,
        "conditions": conditions,
        "lint": lint(doc, skill_dir, skill_md),
        "variants": variants,
        "tasks": tasks,
        "runs": runs,
        "ablations": ablations,
        "totals": {
            "runs": len(runs),
            "cost_usd": cost,
            "success": {v: success_rate(v) for v in variants},
            "skill_load_rate": (sum(bool(x) for x in loaded) / len(loaded)) if loaded else None,
            "quadrants": quad_counts,
            "mechanical_share": checkability(doc),
        },
    }


def pick_ablation_spans(agg: dict, top: int = 1) -> list[list[int]]:
    """Pick spans to remove: the most clearly working claims (a positive control),
    the most inert, and the most ignored."""
    def ranked(quad: str, key):
        return sorted([c for c in agg["claims"] if c["quadrant"] == quad], key=key)

    picks = []
    picks += ranked("working", lambda c: -(c["skill_rate"] - (c["baseline_rate"] or 0)))[:top]
    picks += ranked("inert", lambda c: -(c["baseline_rate"] or 0))[:top]
    picks += ranked("ignored", lambda c: c["skill_rate"] or 0)[:top]
    spans: list[list[int]] = []
    for c in picks:
        if not any(_overlaps(c["span"], [s]) for s in spans):
            spans.append(list(c["span"]))
    return spans


def aggregate_reference(ref: dict, ref_doc: dict | None, coverage: dict | None, skill_doc: dict,
                        by_variant: dict[str, list[dict]], variants: list[str]) -> dict:
    """Per reference step: is it in the skill (coverage), does the agent do it (correctness),
    and what to do about it (diagnosis)."""
    from skillgraph.trees.coverage import DIAGNOSES, diagnose, is_stale, tested_paths, tree_edges
    from skillgraph.trees.reference import all_paths, dominating_branches, path_id

    gates = dominating_branches(ref)

    def rate(node_id: str, variant: str) -> dict:
        statuses = [r["check"].get("reference", {}).get("claims", {}).get(node_id, {}).get("status", "unclear")
                    for r in by_variant.get(variant, []) if "reference" in r["check"]]
        return _rate(statuses)

    cov_nodes = (coverage or {}).get("nodes", {})
    held_skill = [{k: v.get("value") for k, v in r["check"].get("reference", {}).get("conditions", {}).items()}
                  for r in by_variant.get("skill", []) if "reference" in r["check"]]
    graded = any("reference" in r["check"] for rs in by_variant.values() for r in rs)

    nodes = []
    items = [(nid, n) for nid, n in ref["nodes"].items()] + [(c["id"], {"kind": "rule", "text": c["text"]}) for c in ref["constraints"]]
    for nid, n in items:
        cov = cov_nodes.get(nid, {"relation": "unknown", "claims": [], "note": ""})
        entry = {"id": nid, "kind": n["kind"], "text": n["text"], "next": n.get("next"),
                 "branches": n.get("branches"), "relation": cov["relation"], "claims": cov["claims"],
                 "note": cov.get("note", "")}
        if n["kind"] == "decision":
            reached_runs = [h for h in held_skill if all(h.get(c) is True for c in gates.get(nid, []))]
            entry["held"] = {b["label"]: sum(h.get(f"{nid}={_slug(b['label'])}") is True for h in reached_runs)
                             for b in n["branches"]}
            entry["rates"] = {}
            entry["rate_with"] = entry["rate_without"] = None
            entry["diagnosis"] = "conflict" if cov["relation"] == "conflicts" else "untested"  # refined below
        else:
            entry["rates"] = {v: rate(nid, v) for v in variants}
            entry["rate_with"] = entry["rates"].get("skill", {}).get("followed")
            entry["rate_without"] = entry["rates"].get("baseline", {}).get("followed") if "baseline" in entry["rates"] else None
            entry["diagnosis"] = diagnose(cov["relation"], entry["rate_with"], n["kind"]) if coverage or n["kind"] == "end" else (
                "untested" if entry["rate_with"] is None else "ok" if entry["rate_with"] >= FOLLOWED else "ignored")
        nodes.append(entry)

    # Decisions: judge them by what happened right after the fork, on the branches runs took.
    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        if n["kind"] != "decision" or n["relation"] == "conflicts":
            continue
        taken = [b for b in n.get("branches") or [] if n["held"].get(b["label"]) and b.get("next") in by_id]
        if not taken:
            n["diagnosis"] = "untested"
            continue
        followed = all(by_id[b["next"]]["kind"] == "decision" or (by_id[b["next"]]["rate_with"] or 0) >= FOLLOWED
                       for b in taken)
        if n["relation"] == "missing":
            n["diagnosis"] = "implicit" if followed else "gap"
        else:
            n["diagnosis"] = "ok" if followed else "ignored"
    counts = {}

    tested = tested_paths(ref, held_skill)
    paths = [{"id": path_id(p), "steps": [{"node": nid, "label": lbl} for nid, lbl in p], "tested": t}
             for p, t in zip(all_paths(ref), tested, strict=True)]
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n["diagnosis"]] = counts.get(n["diagnosis"], 0) + 1
    correctness = {}
    for v in variants:
        rs = [x["rates"].get(v, {}).get("followed") for x in nodes if x["kind"] != "decision"]
        rs = [x for x in rs if x is not None]
        correctness[v] = sum(rs) / len(rs) if rs else None
    return {
        "name": ref["name"], "description": ref.get("description", ""), "source": ref.get("source", ""),
        "root": ref["root"], "nodes": nodes, "edges": tree_edges(ref),
        "branches": (coverage or {}).get("branches", []), "extra": (coverage or {}).get("extra", []),
        "order_conflicts": (coverage or {}).get("order_conflicts", []),
        "findings": (coverage or {}).get("findings", []), "score": (coverage or {}).get("score"),
        "coverage_stale": bool(coverage and is_stale(coverage, ref, skill_doc)),
        "has_coverage": coverage is not None, "graded": graded,
        "correctness": correctness, "paths": paths, "diagnosis_counts": counts, "diagnoses": DIAGNOSES,
    }


def _slug(text: str) -> str:
    from skillgraph.trees.reference import _slug as slug
    return slug(text)
