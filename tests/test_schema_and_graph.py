from conftest import claim, doc

from skillgraph.graph import build_graph, checkability, lint, to_cypher
from skillgraph.schema import is_stale, normalize, sha256_text

SKILL_MD = """---
name: demo
description: demo skill
---

# Demo

Some background text with no instruction.

1. Run `scripts/check.sh` first.
2. If the check fails, write ERRORS.md.
3. Write the report.
"""


def raw_claim(cid, **kw):
    base = {"id": cid, "kind": "step", "text": "x", "line_start": 10, "line_end": 10, "after": [],
            "when_cond": "", "when_value": True,
            "check": {"type": "tool", "tools": ["Bash"], "input_any": [], "output_any": [], "path_glob": "",
                      "content_any": [], "question": ""}}
    base.update(kw)
    return base


def test_normalize_cleans_extractor_output():
    raw = {"classification": "procedural", "summary": "s", "conditions": [
        {"id": "k1", "text": "check fails", "line_start": 11, "line_end": 11,
         "check": {"type": "judge", "tools": [], "input_any": [], "output_any": [], "path_glob": "", "content_any": [],
                   "question": "Did the check fail?"}}],
        "claims": [
            raw_claim("c1", line_start=0, line_end=999, check={**raw_claim("c1")["check"], "input_any": ["check\\.sh", "("]}),
            raw_claim("c2", after=["c1", "c9", "c2"], when_cond="k1", when_value=True),
            raw_claim("c3", when_cond="k7", kind="bogus"),
        ]}
    out, warnings = normalize(raw, skill="demo", source_text=SKILL_MD)
    c1, c2, c3 = out["claims"]
    assert c1["span"] == [1, len(SKILL_MD.splitlines())]  # clamped
    assert c1["check"]["input_any"] == ["check\\.sh", "\\("]  # bad regex escaped
    assert c2["after"] == ["c1"] and c2["when"] == {"cond": "k1", "value": True}
    assert c3["when"] is None and c3["kind"] == "step"
    assert out["source_sha256"] == sha256_text(SKILL_MD)
    assert not is_stale(out, SKILL_MD) and is_stale(out, SKILL_MD + "\n")
    assert len(warnings) == 3


def test_graph_and_lint(tmp_path):
    (tmp_path / "SKILL.md").write_text(SKILL_MD)
    d = doc(
        [claim("c1", span=(10, 10)),
         claim("c2", after=["c3"], when={"cond": "k1", "value": True}, span=(11, 11)),
         claim("c3", after=["c2"], span=(12, 12), check={"type": "judge", "question": "q?"})],
        conditions=[{"id": "k1", "text": "check fails", "span": [11, 11], "check": {"type": "judge", "question": "?"}}],
    )
    g = build_graph(d)
    assert g.has_edge("k1", "c2") and g.has_edge("c2", "c3")
    assert checkability(d) == 2 / 3
    rules = {f["rule"] for f in lint(d, tmp_path, SKILL_MD)}
    assert {"order-cycle", "one-sided-branch", "subjective-condition", "dangling-file", "no-claims",
            "no-done-criterion"} <= rules
    cypher = to_cypher(d)
    assert "MERGE (a)-[:PRECEDES]->(b)" in cypher and "GATES" in cypher


def test_lint_clean_on_bundled_example(examples):
    from skillgraph.schema import load_claims

    d = load_claims(examples / "claims" / "release-notes.claims.json")
    findings = lint(d, examples / "skills" / "release-notes")
    assert not [f for f in findings if f["severity"] == "error"]
