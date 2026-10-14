import asyncio

from conftest import EXAMPLES

from skillgraph.aggregate import aggregate_skill
from skillgraph.schema import load_claims
from skillgraph.trees import coverage as C
from skillgraph.trees.reference import load_reference

REF = load_reference(EXAMPLES / "references" / "release-sop.ref.yaml")
SKILL_DOC = load_claims(EXAMPLES / "claims" / "release-notes.claims.json")


def alignment():
    nodes = [
        {"ref": "tag", "claims": ["c1"], "relation": "covers", "note": ""},
        {"ref": "commits", "claims": ["c2"], "relation": "covers", "note": ""},
        {"ref": "any", "claims": [], "relation": "missing", "note": ""},
        {"ref": "nothing", "claims": [], "relation": "missing", "note": ""},
        {"ref": "breaking", "claims": ["k2"], "relation": "covers", "note": ""},
        {"ref": "major", "claims": ["c9"], "relation": "covers", "note": ""},
        {"ref": "migration", "claims": ["c10"], "relation": "partial", "note": "no migration notes"},
        {"ref": "feat", "claims": ["k1"], "relation": "covers", "note": ""},
        {"ref": "minor", "claims": ["c7"], "relation": "covers", "note": ""},
        {"ref": "patch", "claims": ["c8"], "relation": "covers", "note": ""},
        {"ref": "group", "claims": ["c3", "c4", "c5", "c6"], "relation": "covers", "note": ""},
        {"ref": "credit", "claims": [], "relation": "missing", "note": ""},
        {"ref": "write", "claims": ["c11"], "relation": "covers", "note": ""},
        {"ref": "done", "claims": [], "relation": "missing", "note": ""},
        {"ref": "rule-never-create-git-tags", "claims": ["c12", "c13"], "relation": "covers", "note": ""},
        {"ref": "rule-link-every-entry-to", "claims": ["c99"], "relation": "covers", "note": "bogus id"},
    ]
    return {"nodes": nodes, "extra_claims": []}


def test_analysis_finds_gaps_branches_and_partials():
    cov = C.analyze(REF, SKILL_DOC, alignment())
    assert cov["nodes"]["rule-link-every-entry-to"]["relation"] == "missing"  # unknown claim ids are dropped
    rules = {(f["rule"], f["ref"]) for f in cov["findings"]}
    assert ("missing-step", "credit") in rules and ("missing-step", "any") in rules
    assert ("unhandled-branch", "any") in rules and ("partial", "migration") in rules
    assert ("missing-step", "done") not in rules  # end states aren't steps to ask for
    branch = {(b["decision"], b["label"]): b["covered"] for b in cov["branches"]}
    assert branch[("any", "no")] is False and branch[("feat", "yes")] is True
    s = cov["score"]
    assert s["total"] == 14 and s["missing"] == 3 and s["partial"] == 1
    assert cov["paths"] == 4 and cov["extra"] == []


def test_order_conflicts_are_detected():
    doc = {"skill": "s", "conditions": [], "claims": [
        {"id": "w", "kind": "step", "text": "write", "span": [1, 1], "after": [], "when": None, "check": {"type": "judge", "question": "?"}},
        {"id": "t", "kind": "step", "text": "tag", "span": [2, 2], "after": ["w"], "when": None, "check": {"type": "judge", "question": "?"}}]}
    al = {"nodes": [{"ref": "tag", "claims": ["t"], "relation": "covers", "note": ""},
                    {"ref": "write", "claims": ["w"], "relation": "covers", "note": ""}], "extra_claims": []}
    cov = C.analyze(REF, doc, al)
    assert cov["order_conflicts"] == [{"ref_before": "tag", "ref_after": "write", "claim_before": "t", "claim_after": "w"}]


def test_align_call_and_staleness(monkeypatch):
    from skillgraph import providers
    from skillgraph.providers import StructuredResult

    async def fake(prompt, schema, **kw):
        assert "- any [decision] Are there any commits since the tag?" in prompt and "- c1 [step]" in prompt
        return StructuredResult(alignment(), 0.05, "m")

    monkeypatch.setattr(providers, "structured", fake)
    al, cost = asyncio.run(C.align(REF, SKILL_DOC))
    doc = C.coverage_doc(REF, SKILL_DOC, al, "claude:x", cost)
    assert not C.is_stale(doc, REF, SKILL_DOC)
    edited = {**REF, "nodes": {**REF["nodes"], "tag": {**REF["nodes"]["tag"], "text": "Find the tag"}}}
    assert C.is_stale(doc, edited, SKILL_DOC)


def test_diagnosis_table():
    assert C.diagnose("missing", 0.0) == "gap"
    assert C.diagnose("missing", 1.0) == "implicit"
    assert C.diagnose("covers", 0.0) == "ignored"
    assert C.diagnose("covers", 1.0) == "ok"
    assert C.diagnose("partial", 0.0) == "partial"
    assert C.diagnose("conflicts", 1.0) == "conflict"
    assert C.diagnose("covers", None) == "untested"


def test_tested_paths():
    held = [{"any=yes": True, "breaking=no": True, "feat=yes": True}]
    assert C.tested_paths(REF, held) == [False, False, True, False]


def test_report_aggregation_includes_the_triangle():
    cov = C.coverage_doc(REF, SKILL_DOC, alignment(), "claude:x", 0.0)
    ids = [c["id"] for c in SKILL_DOC["claims"]]
    ref_ids = list(REF["nodes"]) + [c["id"] for c in REF["constraints"]]

    def row(variant, rep, ref_status):
        return {"dir": "x/y/z/r1", "trace": {"skill": "release-notes", "task": "t", "rep": rep, "variant": {"name": variant, "remove": []},
                                             "success": {"ok": True}, "events": [], "result": {}},
                "check": {"claims": {i: {"status": "satisfied", "reason": ""} for i in ids}, "conditions": {}, "judge_cost_usd": 0,
                          "reference": {"claims": {i: {"status": ref_status.get(i, "satisfied")} for i in ref_ids},
                                        "conditions": {"any=yes": {"value": True}, "breaking=no": {"value": True},
                                                       "feat=yes": {"value": True}}}}}

    rows = [row("skill", 1, {"credit": "missed", "nothing": "n/a", "patch": "n/a"}),
            row("baseline", 1, {"credit": "missed", "tag": "missed"})]
    agg = aggregate_skill("release-notes", SKILL_DOC, EXAMPLES / "skills" / "release-notes", rows, ref=REF, coverage=cov)
    nodes = {n["id"]: n for n in agg["reference"]["nodes"]}
    assert nodes["credit"]["diagnosis"] == "gap"
    assert nodes["tag"]["diagnosis"] == "ok" and nodes["tag"]["rate_without"] == 0.0
    assert nodes["patch"]["diagnosis"] == "untested"
    assert nodes["rule-link-every-entry-to"]["diagnosis"] == "implicit"
    assert nodes["feat"]["held"] == {"yes": 1, "no": 0}
    assert [p["tested"] for p in agg["reference"]["paths"]] == [False, False, True, False]
