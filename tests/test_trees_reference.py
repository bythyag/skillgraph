import asyncio

import pytest
from conftest import EXAMPLES

from skillgraph import providers
from skillgraph.providers import StructuredResult
from skillgraph.runner import load_suite
from skillgraph.trees import reference as R
from skillgraph.trees import taskgen

SOP = EXAMPLES / "references" / "release-sop.md"


@pytest.fixture
def ref():
    r, problems = asyncio.run(R.convert(SOP))
    assert problems == []
    return r


def test_mermaid_conversion(ref):
    n = ref["nodes"]
    assert ref["root"] == "tag" and "start" not in n
    assert n["any"]["kind"] == "decision" and [b["label"] for b in n["any"]["branches"]] == ["no", "yes"]
    assert n["nothing"]["kind"] == "end" and n["group"]["next"] == "credit"
    assert [c["id"] for c in ref["constraints"]] == ["rule-never-create-git-tags", "rule-link-every-entry-to"]
    assert R.load_reference(EXAMPLES / "references" / "release-sop.ref.yaml")["nodes"] == n


def test_mermaid_edge_syntaxes():
    text = "flowchart LR\n  A[Start work] -- ok --> B{Pass?}\n  B -.->|yes| C((Done))\n  B ==> D[Fix] & E[Log]\n"
    r, warnings = R.parse_mermaid(text)
    assert r["nodes"]["A"]["next"] == "B"
    assert [(b["label"], b["next"]) for b in r["nodes"]["B"]["branches"]] == [("yes", "C"), ("Fix", "D"), ("Log", "E")]
    assert r["nodes"]["C"]["kind"] == "end"


def test_paths_gates_and_order(ref):
    paths = R.all_paths(ref)
    assert len(paths) == 4
    assert R.path_id(paths[0]) == "path-any-no"
    gate = R.dominating_branch(ref)
    assert gate["minor"] == "feat=yes" and gate["migration"] == "breaking=yes"
    assert gate["group"] == "any=yes" and gate["tag"] is None
    assert R.action_predecessors(ref)["group"] == ["migration", "minor", "patch"]
    assert ("tag", "write") in R.precedes(ref) and ("write", "tag") not in R.precedes(ref)


def test_validation_catches_broken_trees():
    broken = R.normalize_reference({"root": "a", "nodes": {"a": {"kind": "decision", "text": "?", "branches": [{"label": "y", "next": "zz"}]},
                                                           "b": {"text": "orphan"}}})
    problems = R.validate_reference(broken)
    assert any("unknown node 'zz'" in p for p in problems)
    assert any("fewer than two branches" in p for p in problems)
    assert any("b can't be reached" in p for p in problems)


def test_compile_structure_comes_from_the_tree(ref):
    compiled = {"items": [{"id": "tag", "kind": "step", "check": {"type": "tool", "tools": ["Bash"], "input_any": ["git describe"],
                                                                "output_any": [], "path_glob": "", "content_any": [], "question": ""}}],
                "conditions": [{"id": "breaking=yes", "text": "a commit contains BREAKING CHANGE",
                                "check": {"type": "judge", "tools": [], "input_any": [], "output_any": [], "path_glob": "",
                                          "content_any": [], "question": "Does a commit contain BREAKING CHANGE?"}}]}
    doc, warnings = R.build_claims_doc(ref, compiled, "release-notes")
    claims = {c["id"]: c for c in doc["claims"]}
    conds = {k["id"]: k for k in doc["conditions"]}
    assert set(conds) == {"any=no", "any=yes", "breaking=yes", "breaking=no", "feat=yes", "feat=no"}
    assert claims["tag"]["check"]["type"] == "tool" and claims["credit"]["check"]["type"] == "judge"
    assert claims["minor"]["when"] == {"cond": "feat=yes", "value": True}
    assert [w["cond"] for w in claims["minor"]["when_all"]] == ["any=yes", "breaking=no", "feat=yes"]
    assert "when_all" not in claims["tag"] and "when_all" not in claims["commits"]
    assert claims["group"]["after"] == ["migration", "minor", "patch"]
    assert claims["rule-never-create-git-tags"]["kind"] == "must_not" and claims["rule-never-create-git-tags"]["after"] == []
    assert claims["done"]["kind"] == "artifact"
    assert any("no check written for credit" in w for w in warnings)


def test_merge_and_split_roundtrip():
    skill = {"skill": "s", "claims": [{"id": "c1", "after": [], "when": None}], "conditions": []}
    refdoc = {"claims": [{"id": "a", "after": [], "when": None}, {"id": "b", "after": ["a"], "when": {"cond": "d=yes", "value": True}}],
              "conditions": [{"id": "d=yes"}]}
    merged = R.merge_docs(skill, refdoc)
    assert [c["id"] for c in merged["claims"]] == ["c1", "ref:a", "ref:b"]
    assert merged["claims"][2]["after"] == ["ref:a"] and merged["claims"][2]["when"]["cond"] == "ref:d=yes"
    res = R.split_result({"claims": {"c1": 1, "ref:a": 2, "ref:b": 3}, "conditions": {"ref:d=yes": 4}})
    assert res["claims"] == {"c1": 1} and res["reference"] == {"claims": {"a": 2, "b": 3}, "conditions": {"d=yes": 4}}


def test_prose_goes_to_the_model(tmp_path, monkeypatch):
    doc = tmp_path / "sop.md"
    doc.write_text("1. Check out main.\n2. If tests fail, stop.\n3. Otherwise tag the release.\n")
    seen = {}

    async def fake(prompt, schema, **kw):
        seen["prompt"] = prompt
        return StructuredResult({"name": "deploy", "description": "", "root": "checkout", "constraints": [],
                                 "nodes": [
                                     {"id": "checkout", "kind": "action", "text": "Check out main", "next": "tests", "branches": [], "line_start": 1, "line_end": 1},
                                     {"id": "tests", "kind": "decision", "text": "Do tests fail?", "next": "", "line_start": 2, "line_end": 3,
                                      "branches": [{"label": "yes", "next": "stop"}, {"label": "no", "next": "tag"}]},
                                     {"id": "stop", "kind": "end", "text": "Stop", "next": "", "branches": [], "line_start": 2, "line_end": 2},
                                     {"id": "tag", "kind": "action", "text": "Tag the release", "next": "", "branches": [], "line_start": 3, "line_end": 3}]}, 0.01, "m")

    monkeypatch.setattr(providers, "structured", fake)
    r, problems = asyncio.run(R.convert(doc))
    assert problems == [] and r["nodes"]["tests"]["kind"] == "decision" and "   2| 2. If tests fail" in seen["prompt"]
    assert r["nodes"]["checkout"]["lines"] == [1, 1]


def test_task_generation_writes_a_runnable_draft_suite(ref, tmp_path, monkeypatch):
    suite = load_suite(EXAMPLES / "suite.yaml")
    skill = suite.skill("release-notes")

    async def fake(prompt, schema, **kw):
        return StructuredResult({"id": "x", "prompt": "Get the notes ready for the release.",
                                 "files": [{"path": "README.md", "content": "demo"}, {"path": "../escape.txt", "content": "no"}],
                                 "setup_sh": "git init -q\ngit add README.md\ngit commit -qm 'fix: a'",
                                 "success": [{"type": "contains", "path": "RELEASE_NOTES.md", "pattern": "v1\\.4\\.1", "cmd": ""},
                                             {"type": "cmd", "path": "", "pattern": "", "cmd": "test -f RELEASE_NOTES.md"}],
                                 "notes": "patch path"}, 0.02, "m")

    monkeypatch.setattr(providers, "structured", fake)
    out = tmp_path / "suite.generated.yaml"
    path, summaries = asyncio.run(taskgen.generate(ref, skill=skill, suite_dir=tmp_path, out_file=out, only=[3]))
    gen = load_suite(path)
    task = gen.skills[0].tasks[0]
    assert gen.skills[0].name == "release-notes" and gen.skills[0].reference_path is not None
    assert task["id"] == "path-any-yes-breaking-no-feat-no"
    assert task["success"] == [{"file": "RELEASE_NOTES.md", "contains": "v1\\.4\\.1"}, {"cmd": "test -f RELEASE_NOTES.md"}]
    fx = tmp_path / "fixtures" / "generated" / task["id"]
    assert (fx / "README.md").exists() and not (tmp_path / "fixtures" / "generated" / "escape.txt").exists()
    assert (fx / "setup.sh").read_text().startswith("set -e\n")
    assert summaries[0]["files"] == ["README.md", "setup.sh"]
