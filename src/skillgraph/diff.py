"""`skillgraph diff BEFORE AFTER`: what changed between two run sets, typically before and
after editing a skill. Claims are matched by id and text, then by text similarity,
because editing a skill shifts line spans and re-extraction renumbers claims."""

from __future__ import annotations

from difflib import SequenceMatcher

GOOD = {"working", "followed", "inert"}
BAD = {"ignored", "interfering"}


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def match_claims(before: list[dict], after: list[dict], threshold: float = 0.75) -> list[tuple[dict | None, dict | None]]:
    pairs, used_b = [], set()
    by_id_b = {c["id"]: c for c in after}
    rest_a = []
    for a in before:
        b = by_id_b.get(a["id"])
        if b is not None and b["id"] not in used_b and _similar(a["text"], b["text"]) >= threshold:
            pairs.append((a, b))
            used_b.add(b["id"])
        else:
            rest_a.append(a)
    for a in rest_a:
        best, score = None, threshold
        for b in after:
            if b["id"] in used_b:
                continue
            s = _similar(a["text"], b["text"])
            if s >= score:
                best, score = b, s
        if best is not None:
            used_b.add(best["id"])
        pairs.append((a, best))
    pairs += [(None, b) for b in after if b["id"] not in used_b]
    return pairs


def _change(a: dict | None, b: dict | None) -> str:
    if a is None:
        return "new"
    if b is None:
        return "removed"
    ra, rb = a.get("skill_rate"), b.get("skill_rate")
    if a["quadrant"] in BAD and b["quadrant"] in GOOD or (ra is not None and rb is not None and rb - ra >= 0.25):
        return "better"
    if b["quadrant"] in BAD and a["quadrant"] not in BAD or (ra is not None and rb is not None and ra - rb >= 0.25):
        return "worse"
    return "same" if a["quadrant"] == b["quadrant"] else "changed"


def compare(data_a: dict, data_b: dict, skills: list[str] | None = None) -> dict:
    a_by = {s["skill"]: s for s in data_a["skills"]}
    b_by = {s["skill"]: s for s in data_b["skills"]}
    names = [n for n in a_by if n in b_by and (not skills or n in skills)]
    out = {"before": data_a.get("runset"), "after": data_b.get("runset"), "skills": [],
           "only_before": sorted(set(a_by) - set(b_by)), "only_after": sorted(set(b_by) - set(a_by))}
    for name in names:
        A, B = a_by[name], b_by[name]
        rows = []
        for ca, cb in match_claims(A["claims"], B["claims"]):
            ref = cb or ca
            rows.append({
                "before": ca and ca["id"], "after": cb and cb["id"], "text": ref["text"],
                "verdict": [ca and ca["quadrant"], cb and cb["quadrant"]],
                "skill_rate": [ca and ca["skill_rate"], cb and cb["skill_rate"]],
                "baseline_rate": [ca and ca["baseline_rate"], cb and cb["baseline_rate"]],
                "change": _change(ca, cb),
            })
        entry = {
            "skill": name, "claims": rows,
            "counts": {k: sum(r["change"] == k for r in rows) for k in ("better", "worse", "changed", "same", "new", "removed")},
            "success": {v: [A["totals"]["success"].get(v), B["totals"]["success"].get(v)] for v in ("skill", "baseline")},
            "cost_usd": [round(A["totals"]["cost_usd"], 4), round(B["totals"]["cost_usd"], 4)],
            "runs": [A["totals"]["runs"], B["totals"]["runs"]],
            "lines": [len(A["lines"]), len(B["lines"])],
        }
        ra, rb = A.get("reference"), B.get("reference")
        if ra and rb:
            da = {n["id"]: n["diagnosis"] for n in ra["nodes"]}
            entry["reference"] = {
                "coverage": [(ra["score"] or {}).get("coverage"), (rb["score"] or {}).get("coverage")],
                "correctness": [ra["correctness"].get("skill"), rb["correctness"].get("skill")],
                "steps": [{"id": n["id"], "text": n["text"], "diagnosis": [da.get(n["id"]), n["diagnosis"]]}
                          for n in rb["nodes"] if da.get(n["id"]) != n["diagnosis"]],
            }
        out["skills"].append(entry)
    return out


def _pct(x) -> str:
    return "–" if x is None else f"{x:.0%}"


def _arrow(pair, fmt=_pct) -> str:
    a, b = pair
    return fmt(a) if a == b else f"{fmt(a)} → {fmt(b)}"


def to_text(d: dict) -> str:
    lines = [f"{d['before']} → {d['after']}"]
    for s in d["skills"]:
        c = s["counts"]
        lines.append(f"\n{s['skill']}: SKILL.md {s['lines'][0]} → {s['lines'][1]} lines; task success with the skill "
                     f"{_arrow(s['success']['skill'])}, without {_arrow(s['success']['baseline'])}; "
                     f"cost {_arrow(s['cost_usd'], lambda x: f'${x:.2f}')}")
        lines.append(f"  claims: {c['better']} better, {c['worse']} worse, {c['changed']} changed, {c['same']} same, "
                     f"{c['new']} new, {c['removed']} removed")
        for r in s["claims"]:
            if r["change"] == "same":
                continue
            ids = f"{r['before'] or '–'} → {r['after'] or '–'}"
            verdict = f"{r['verdict'][0] or '–'} → {r['verdict'][1] or '–'}"
            rate = _arrow(r["skill_rate"])
            lines.append(f"  {r['change']:<8} {ids:<12} {verdict:<24} {rate:<12} {r['text'][:70]}")
        R = s.get("reference")
        if R:
            lines.append(f"  reference: coverage {_arrow(R['coverage'])}, steps done {_arrow(R['correctness'])}")
            for st in R["steps"]:
                lines.append(f"    {st['id']:<14} {st['diagnosis'][0] or '–'} → {st['diagnosis'][1]}  {st['text'][:60]}")
    if d["only_before"] or d["only_after"]:
        lines.append(f"\nonly in {d['before']}: {', '.join(d['only_before']) or '–'}; only in {d['after']}: {', '.join(d['only_after']) or '–'}")
    return "\n".join(lines) + "\n"


def to_markdown(d: dict) -> str:
    """For pull-request comments."""
    out = [f"### skillgraph: `{d['before']}` → `{d['after']}`"]
    for s in d["skills"]:
        c = s["counts"]
        out += ["", f"**{s['skill']}**: task success with the skill {_arrow(s['success']['skill'])}, without "
                f"{_arrow(s['success']['baseline'])}. Claims: {c['better']} better, {c['worse']} worse, {c['new']} new, "
                f"{c['removed']} removed.", ""]
        changed = [r for r in s["claims"] if r["change"] != "same"]
        if changed:
            out += ["| change | claim | verdict | with skill |", "|---|---|---|---|"]
            for r in changed:
                text = r["text"].replace("|", "\\|")[:80]
                out.append(f"| {r['change']} | {text} | {r['verdict'][0] or '–'} → {r['verdict'][1] or '–'} | {_arrow(r['skill_rate'])} |")
        R = s.get("reference")
        if R:
            out += ["", f"Reference: coverage {_arrow(R['coverage'])}, steps done {_arrow(R['correctness'])}."]
    return "\n".join(out) + "\n"
