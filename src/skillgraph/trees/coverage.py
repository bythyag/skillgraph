"""Coverage: how a skill's claim graph lines up with a reference tree.

One LLM call aligns each reference step with the skill claims that state it. Everything
after that is deterministic: missing steps, branches the skill never handles, steps the
skill adds, and ordering the skill gets backwards. The alignment is saved as a lockfile
(claims/<skill>.coverage.json) so it can be reviewed and edited like the claims.
"""

from __future__ import annotations

import json

from skillgraph import providers
from skillgraph.config import DEFAULTS
from skillgraph.schema import doc_sha
from skillgraph.trees.reference import _targets, all_paths, cond_id, dominating_branch, precedes, reference_sha

RELATIONS = ["covers", "partial", "conflicts", "missing"]
EXTRA_RELATIONS = ["detail", "extra", "conflicts"]

ALIGN_SYSTEM = (
    "You compare a reference process with the instructions in an agent skill. You match by meaning, "
    "not wording, and you are strict: a skill line covers a step only if following it would make the "
    "agent do that step."
)

ALIGN_PROMPT = """Align this reference process with the skill's claims.

<reference name="{ref_name}">
{ref_items}
</reference>

<skill name="{skill}">
{skill_items}
</skill>

nodes: one entry per reference id above (steps, decisions, end states, and rules).
- claims: ids of the skill claims (c*) and conditions (k*) that state this step or decision. [] if none.
- relation:
  - covers: the skill tells the agent to do this (or, for a decision, handles this fork).
  - partial: the skill mentions it but more weakly or narrowly (e.g. only one branch of a decision, or a softer wording).
  - conflicts: the skill tells the agent to do something incompatible with this step.
  - missing: nothing in the skill corresponds to it.
- note: one short sentence explaining the match, especially for partial and conflicts.

extra_claims: every skill claim that matches no reference id.
- relation: detail (an implementation detail consistent with the reference, such as an exact command), extra (a step the reference doesn't have), or conflicts (contradicts the reference).
- note: one short sentence."""

ALIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {"type": "array", "items": {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "claims": {"type": "array", "items": {"type": "string"}},
                           "relation": {"type": "string", "enum": RELATIONS}, "note": {"type": "string"}},
            "required": ["ref", "claims", "relation", "note"], "additionalProperties": False}},
        "extra_claims": {"type": "array", "items": {
            "type": "object",
            "properties": {"claim": {"type": "string"}, "relation": {"type": "string", "enum": EXTRA_RELATIONS},
                           "note": {"type": "string"}},
            "required": ["claim", "relation", "note"], "additionalProperties": False}},
    },
    "required": ["nodes", "extra_claims"],
    "additionalProperties": False,
}


def _ref_items(ref: dict) -> str:
    lines = []
    for nid, n in ref["nodes"].items():
        if n["kind"] == "decision":
            branches = "; ".join(f"{b['label']} -> {b.get('next') or 'stop'}" for b in n["branches"])
            lines.append(f"- {nid} [decision] {n['text']} (branches: {branches})")
        else:
            lines.append(f"- {nid} [{n['kind']}] {n['text']}" + (f" (then {n['next']})" if n.get("next") else ""))
    lines += [f"- {c['id']} [rule] {c['text']}" for c in ref["constraints"]]
    return "\n".join(lines)


def _skill_items(doc: dict) -> str:
    lines = [f"- {k['id']} [condition] {k['text']}" for k in doc["conditions"]]
    for c in doc["claims"]:
        extra = []
        if c.get("after"):
            extra.append("after " + ", ".join(c["after"]))
        if c.get("when"):
            extra.append(f"only when {c['when']['cond']} is {str(c['when']['value']).lower()}")
        lines.append(f"- {c['id']} [{c['kind']}] {c['text']}" + (f" ({'; '.join(extra)})" if extra else ""))
    return "\n".join(lines)


async def align(ref: dict, skill_doc: dict, *, model: str = DEFAULTS["extractor"]) -> tuple[dict, float]:
    prompt = ALIGN_PROMPT.format(ref_name=ref["name"], ref_items=_ref_items(ref), skill=skill_doc["skill"],
                                 skill_items=_skill_items(skill_doc))
    res = await providers.structured(prompt, ALIGN_SCHEMA, system=ALIGN_SYSTEM, model=model)
    return res.data, res.cost_usd


def _after_closure(doc: dict) -> dict[str, set[str]]:
    """claim -> every claim it must come after (transitively)."""
    direct = {c["id"]: set(c.get("after") or []) for c in doc["claims"]}
    out = {}
    for cid in direct:
        seen, stack = set(), list(direct[cid])
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(direct.get(cur, ()))
        out[cid] = seen
    return out


def analyze(ref: dict, skill_doc: dict, alignment: dict) -> dict:
    """Deterministic comparison on top of the alignment."""
    known = {c["id"] for c in skill_doc["claims"]} | {k["id"] for k in skill_doc["conditions"]}
    ref_ids = list(ref["nodes"]) + [c["id"] for c in ref["constraints"]]
    by_ref = {m["ref"]: m for m in alignment.get("nodes", []) if m.get("ref") in ref_ids}
    nodes = {}
    for rid in ref_ids:
        m = by_ref.get(rid) or {"claims": [], "relation": "missing", "note": "not aligned"}
        claims = [c for c in m.get("claims", []) if c in known]
        relation = m.get("relation", "missing") if m.get("relation") in RELATIONS else "missing"
        if relation in ("covers", "partial") and not claims:
            relation = "missing"
        node = ref["nodes"].get(rid)
        nodes[rid] = {"relation": relation, "claims": claims, "note": m.get("note", ""),
                      "kind": node["kind"] if node else "rule",
                      "text": node["text"] if node else next(c["text"] for c in ref["constraints"] if c["id"] == rid)}

    matched = {c for n in nodes.values() for c in n["claims"]}
    extras_given = {e["claim"]: e for e in alignment.get("extra_claims", [])}
    extra = []
    for c in skill_doc["claims"]:
        if c["id"] in matched:
            continue
        e = extras_given.get(c["id"], {"relation": "extra", "note": ""})
        extra.append({"claim": c["id"], "text": c["text"], "span": c["span"],
                      "relation": e.get("relation", "extra"), "note": e.get("note", "")})

    # Branches: a branch is handled if anything gated on it (or its first step) is in the skill.
    gate = dominating_branch(ref)
    branches = []
    for did, d in ref["nodes"].items():
        if d["kind"] != "decision":
            continue
        for b in d["branches"]:
            cid = cond_id(did, b["label"])
            gated = [nid for nid, g in gate.items() if g == cid]
            if b.get("next") and b["next"] not in gated:
                gated.append(b["next"])
            hit = [nid for nid in gated if nodes.get(nid, {}).get("relation") in ("covers", "partial")]
            branches.append({"decision": did, "label": b["label"], "covered": bool(hit), "nodes": gated,
                             "covered_by": sorted({c for nid in hit for c in nodes[nid]["claims"]})})

    # Ordering: the reference says a before b; the skill requires a's claim after b's claim.
    closure = _after_closure(skill_doc)
    conflicts = []
    for a, b in sorted(precedes(ref)):
        for ca in nodes.get(a, {}).get("claims", []):
            for cb in nodes.get(b, {}).get("claims", []):
                if ca != cb and cb in closure.get(ca, set()):
                    conflicts.append({"ref_before": a, "ref_after": b, "claim_before": ca, "claim_after": cb})

    counted = [n for n in nodes.values() if n["kind"] != "end"]
    score = {r: sum(n["relation"] == r for n in counted) for r in RELATIONS}
    score["total"] = len(counted)
    score["coverage"] = (score["covers"] + 0.5 * score["partial"]) / score["total"] if score["total"] else None

    findings = []
    for rid, n in nodes.items():
        if n["relation"] == "missing" and n["kind"] != "end":
            findings.append({"severity": "warn", "rule": "missing-step", "ref": rid, "claims": [],
                             "message": f"The skill never asks for {rid}: {n['text']!r}."})
        elif n["relation"] == "conflicts":
            findings.append({"severity": "error", "rule": "conflict", "ref": rid, "claims": n["claims"],
                             "message": f"{', '.join(n['claims'])} contradict {rid} ({n['text']!r}). {n['note']}".strip()})
        elif n["relation"] == "partial":
            findings.append({"severity": "info", "rule": "partial", "ref": rid, "claims": n["claims"],
                             "message": f"{rid} is only partly covered by {', '.join(n['claims'])}. {n['note']}".strip()})
    for b in branches:
        if not b["covered"]:
            findings.append({"severity": "warn", "rule": "unhandled-branch", "ref": b["decision"], "claims": [],
                             "message": f"The skill never says what to do when {b['decision']} is {b['label']!r}."})
    for o in conflicts:
        findings.append({"severity": "error", "rule": "order-conflict", "ref": o["ref_after"],
                         "claims": [o["claim_before"], o["claim_after"]],
                         "message": f"The reference does {o['ref_before']} before {o['ref_after']}, but the skill "
                                    f"puts {o['claim_before']} after {o['claim_after']}."})
    for e in extra:
        if e["relation"] in ("extra", "conflicts"):
            findings.append({"severity": "error" if e["relation"] == "conflicts" else "info",
                             "rule": f"skill-{e['relation']}", "ref": None, "claims": [e["claim"]],
                             "message": f"{e['claim']} ({e['text']!r}) isn't in the reference. {e['note']}".strip()})
    sev = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: sev[f["severity"]])
    return {"nodes": nodes, "extra": extra, "branches": branches, "order_conflicts": conflicts,
            "score": score, "findings": findings, "paths": len(all_paths(ref))}


def coverage_doc(ref: dict, skill_doc: dict, alignment: dict, model: str, cost: float) -> dict:
    return {"skill": skill_doc["skill"], "reference": ref["name"], "reference_sha": reference_sha(ref),
            "claims_sha": doc_sha(skill_doc), "model": model, "cost_usd": round(cost, 4),
            "alignment": alignment, **analyze(ref, skill_doc, alignment)}


def is_stale(cov: dict, ref: dict, skill_doc: dict) -> bool:
    return cov.get("reference_sha") != reference_sha(ref) or cov.get("claims_sha") != doc_sha(skill_doc)


# ---------------------------------------------------------------- the triangle

DIAGNOSES = {
    "gap": "Not in the skill, and the agent doesn't do it. Add it to the skill.",
    "implicit": "Not in the skill, but the agent does it anyway.",
    "ignored": "The skill asks for it, but the agent doesn't do it. Harden the matching lines.",
    "partial": "Only partly in the skill, and the agent doesn't do it. Make the skill explicit.",
    "conflict": "The skill contradicts the reference here. Fix the skill text.",
    "ok": "In the skill, and the agent does it.",
    "untested": "No run reached this step. Generate a task for its path.",
    "unreached": "Runs on this path didn't end in this state. Check the outcome the skill asks for.",
}


def diagnose(relation: str, rate_with: float | None, kind: str = "action") -> str:
    if kind == "end":  # outcomes, not instructions: coverage doesn't apply
        return "untested" if rate_with is None else "ok" if rate_with >= 0.5 else "unreached"
    if relation == "conflicts":
        return "conflict"
    if rate_with is None:
        return "untested"
    done = rate_with >= 0.5
    if relation == "missing":
        return "implicit" if done else "gap"
    if relation == "partial":
        return "ok" if done else "partial"
    return "ok" if done else "ignored"


def tested_paths(ref: dict, held_by_run: list[dict[str, bool | None]]) -> list[bool]:
    """A path counts as tested when some run held every branch condition on it."""
    out = []
    for path in all_paths(ref):
        need = [cond_id(n, lbl) for n, lbl in path if lbl is not None]
        out.append(any(all(held.get(c) is True for c in need) for held in held_by_run) if held_by_run else False)
    return out


def tree_edges(ref: dict) -> list[dict]:
    edges = []
    for nid, n in ref["nodes"].items():
        if n["kind"] == "decision":
            edges += [{"source": nid, "target": b["next"], "label": b["label"]} for b in n["branches"] if b.get("next")]
        else:
            edges += [{"source": nid, "target": t, "label": ""} for t in _targets(n)]
    return edges


def dumps(cov: dict) -> str:
    return json.dumps(cov, indent=2, ensure_ascii=False) + "\n"
