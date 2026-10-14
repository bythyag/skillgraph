"""Regression tests for the pre-release bug hunt."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from conftest import EXAMPLES, claim, doc, make_trace, tool

from skillgraph import checker, providers
from skillgraph.aggregate import aggregate_skill, collect
from skillgraph.providers import AgentRequest, AgentResult, harness
from skillgraph.providers.tools import ToolBox, ToolError
from skillgraph.report import render
from skillgraph.runner import (
    RunConfig,
    Variant,
    clean_env,
    load_suite,
    plan_jobs,
    prepare_workspace,
    run_matrix,
    run_one,
    run_success_checks,
)
from skillgraph.schema import load_claims
from skillgraph.trees import reference as R
from skillgraph.trees import runs as imp
from skillgraph.trees.mapping import get_path
from skillgraph.trees.taskgen import _safe_rel

DEMO = EXAMPLES / "runs" / "demo"


# ---------------------------------------------------------------- mermaid and reference trees

def test_mermaid_arrows_without_spaces():
    r, _ = R.parse_mermaid("flowchart TD\n  A-->B\n  B-->C\n  my-step-->|yes|D\n  my-step-->|no|C")
    assert r["nodes"]["A"]["next"] == "B" and r["nodes"]["B"]["next"] == "C"
    assert [b["next"] for b in r["nodes"]["my-step"]["branches"]] == ["D", "C"]


def test_mermaid_keywords_are_whole_words():
    r, _ = R.parse_mermaid("graph TD\n start([Start]) --> classify[Classify]\n classify --> styleCheck{Ok?}\n"
                           " styleCheck -->|yes| done([Done])\n styleCheck -->|no| classify\n style classify fill:#fff")
    assert set(r["nodes"]) == {"classify", "styleCheck", "done"}
    assert len(r["nodes"]["styleCheck"]["branches"]) == 2


def test_loops_do_not_create_cyclic_ordering():
    ref = R.normalize_reference({"root": "fix", "nodes": {
        "fix": {"text": "fix", "next": "test"}, "test": {"text": "test", "next": "pass"},
        "pass": {"kind": "decision", "text": "pass?", "branches": [{"label": "yes", "next": "done"}, {"label": "no", "next": "fix"}]},
        "done": {"kind": "end", "text": "d"}}})
    d, _ = R.build_claims_doc(ref, {"items": [], "conditions": []}, "s")
    after = {c["id"]: c["after"] for c in d["claims"]}
    assert after["fix"] == [] and after["test"] == [] and after["done"] == ["test"]


def test_branch_labels_never_share_a_condition():
    ref = R.normalize_reference({"root": "d", "nodes": {"d": {"kind": "decision", "text": "?", "branches": [
        {"label": "Yes", "next": "a"}, {"label": "yes", "next": "b"}]}, "a": {"text": "a"}, "b": {"text": "b"}}})
    ids = [R.cond_id("d", b["label"]) for b in ref["nodes"]["d"]["branches"]]
    assert len(set(ids)) == 2


# ---------------------------------------------------------------- checker

def test_unseen_file_writes_do_not_cause_out_of_order():
    d = doc([claim("c1", kind="artifact", check={"type": "file", "path_glob": "report.md", "content_any": []}),
             claim("c2", after=["c1"], check={"type": "tool", "tools": ["Bash"], "input_any": ["validate"], "output_any": []})])
    trace = make_trace([tool(0, "Bash", {"command": "python gen.py"}), tool(1, "Bash", {"command": "python validate.py"})],
                       files={"report.md": "x"})
    res = asyncio.run(checker.check_trace(trace, d))
    assert {k: v["status"] for k, v in res["claims"].items()} == {"c1": "satisfied", "c2": "satisfied"}
    assert res["claims"]["c1"]["evidence"] is None


def test_globs_and_path_mentions():
    assert checker._file_matches("docs/*.md", "docs/a.md") and not checker._file_matches("docs/*.md", "docs/a/b.md")
    assert checker._file_matches("**/*.md", "report.md") and checker._file_matches("**/*.md", "a/b/c.md")
    assert not checker._file_matches("report.md", "sub/report.md")
    assert not checker._mentions("/w/data.md", "a.md") and checker._mentions("cat > a.md; ls", "a.md")


# ---------------------------------------------------------------- harness tools

def test_tool_errors_stay_inside_the_run(tmp_path):
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00")
    (tmp_path / "dir").mkdir()
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    box = ToolBox(tmp_path, {})
    for name, args, msg in [("Edit", {"file_path": "bin.dat", "old_string": "a", "new_string": "b"}, "isn't text"),
                            ("Write", {"file_path": "dir", "content": "x"}, "IsADirectoryError"),
                            ("Edit", {"file_path": "a.txt", "old_string": "", "new_string": "x"}, "old_string is empty"),
                            ("Rel", {"p": "."}, "Unknown tool")]:
        with pytest.raises(ToolError, match=msg):
            box.run(name, args)
    assert box.run("Read", {"file_path": "a.txt", "offset": "2", "limit": "1"}).endswith("two")


def test_unexpected_tool_failure_does_not_lose_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ToolBox, "run", lambda self, name, args: (_ for _ in ()).throw(RuntimeError("boom")))
    turns = iter([{"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": "Bash", "arguments": "{\"command\": \"ls\"}"}}]},
        {"role": "assistant", "content": "done"}])

    async def chat(messages, tools):
        return {"choices": [{"message": next(turns)}], "usage": {"cost": 0}}

    out = asyncio.run(harness.run(AgentRequest(workspace=tmp_path, prompt="x", model="m", skills=[]), chat))
    assert out.error is None and out.events[0]["is_error"] and "RuntimeError: boom" in out.events[0]["output"]


# ---------------------------------------------------------------- runner

def _suite_with_task(tmp_path, fixture_files, setup=None):
    fx = tmp_path / "fx"
    fx.mkdir()
    for name, body in fixture_files.items():
        (fx / name).parent.mkdir(parents=True, exist_ok=True)
        (fx / name).write_text(body)
    if setup is not None:
        (fx / "setup.sh").write_text(setup)
    suite = load_suite(EXAMPLES / "suite.yaml")
    sk = suite.skill("release-notes")
    task = {"id": "t", "fixture": str(fx), "prompt": "do it", "success": [{"cmd": "test -z \"$OPENROUTER_API_KEY\""}]}
    return sk, task


def test_fixture_scripts_and_checks_get_no_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    sk, task = _suite_with_task(tmp_path, {"a.txt": "x"}, setup="echo \"key=$OPENROUTER_API_KEY\" > seen.txt\n")
    prepare_workspace(tmp_path / "ws", task, sk, Variant.parse("baseline"))
    assert (tmp_path / "ws" / "seen.txt").read_text().strip() == "key="
    assert run_success_checks(tmp_path / "ws", task) == {"cmd test -z \"$OPENROUTER_API_KEY\"": True}
    assert "OPENROUTER_API_KEY" not in clean_env()


def test_failed_setup_is_recorded_not_raised(tmp_path, monkeypatch):
    sk, task = _suite_with_task(tmp_path, {"a.txt": "x"}, setup="echo nope >&2; exit 3\n")

    async def never(req, model):
        raise AssertionError("the agent must not run when setup failed")

    monkeypatch.setattr(providers, "run_agent", never)
    rd = tmp_path / "run"
    rd.mkdir()
    (rd / "check.json").write_text("{}")
    trace = asyncio.run(run_one(sk, task, Variant.parse("skill"), 1, rd, RunConfig()))
    assert "setup.sh failed (exit 3)" in trace["error"] and not (rd / "check.json").exists()


def test_fixture_with_claude_settings_is_flagged(tmp_path):
    sk, task = _suite_with_task(tmp_path, {".claude/settings.json": "{}"})
    warnings = prepare_workspace(tmp_path / "ws", task, sk, Variant.parse("baseline"))
    assert warnings and ".claude" in warnings[0]


def test_planning_validates_names_and_retries_errors(tmp_path):
    suite = load_suite(EXAMPLES / "suite.yaml")
    with pytest.raises(ValueError, match="no skill named nope"):
        plan_jobs(suite, tmp_path, variants=[Variant.parse("skill")], runs=1, skills=["nope"])
    with pytest.raises(ValueError, match="no task named zz"):
        plan_jobs(suite, tmp_path, variants=[Variant.parse("skill")], runs=1, skills=["release-notes"], tasks=["zz"])
    jobs = plan_jobs(suite, tmp_path, variants=[Variant.parse("skill")], runs=1, skills=["release-notes"], tasks=["minor-release"])
    rd = jobs[0][4]
    rd.mkdir(parents=True)
    (rd / "trace.json").write_text(json.dumps({"error": "boom"}))
    kw = dict(variants=[Variant.parse("skill")], runs=1, skills=["release-notes"], tasks=["minor-release"])
    assert plan_jobs(suite, tmp_path, **kw) == []
    assert len(plan_jobs(suite, tmp_path, retry_errors=True, **kw)) == 1
    with pytest.raises(ValueError, match="bad line range"):
        Variant.parse("ablate-L5-3")


def test_spend_cap_stops_new_runs(tmp_path, monkeypatch):
    calls = []

    async def fake(req, model):
        calls.append(req.prompt)
        return AgentResult(result={"subtype": "success", "cost_usd": 1.0, "num_turns": 1, "text": ""})

    monkeypatch.setattr(providers, "run_agent", fake)
    suite = load_suite(EXAMPLES / "suite.yaml")
    dirs = asyncio.run(run_matrix(suite, tmp_path / "out", variants=[Variant.parse("baseline")], runs=3,
                                  cfg=RunConfig(), skills=["bugfix-tdd"], tasks=["slugify"], concurrency=1, max_total_usd=1.5))
    assert len(calls) == 2 and len(dirs) == 2


# ---------------------------------------------------------------- importers

def test_reimporting_skillgraph_traces_keeps_their_state():
    t = next(DEMO.glob("csv-report/valid-data/skill/r1/trace.json"))
    raws, _ = imp.load_raw(t)
    out = imp.to_trace(raws[0], skill="csv-report", variant="skill", rep=1, aliases=imp.build_alias_map())
    assert out["skill_loaded"] and raws[0].variant["name"] == "skill"


@pytest.mark.parametrize("value, expected", [
    (1714564800, 1714564800.0), (1714564800123, 1714564800.123), (1714564800123456, 1714564800.123456),
    ("2026-10-14T10:00:00.1234567Z", None), ("2026-10-14T10:00:00+0000", None)])
def test_timestamps(value, expected):
    got = imp._ts(value)
    assert got is not None and (expected is None or abs(got - expected) < 1e-3)


def test_reminders_are_not_file_content():
    events = [{"i": 0, "kind": "tool", "tool": "Read", "input": {"file_path": "/w/a.csv"},
               "output": "     1\ta,b\n<system-reminder>\nnote\n</system-reminder>"}]
    assert imp.reconstruct_files(events, "/w") == {"a.csv": "a,b"}


def test_claude_code_labels_are_unique(tmp_path):
    for i in (1, 2):
        (tmp_path / f"session-abc-{i}.jsonl").write_text(json.dumps(
            {"type": "user", "uuid": "u", "sessionId": "s", "message": {"role": "user", "content": "hi"}}) + "\n")
    labels = {imp.load_raw(p)[0][0].label for p in sorted(tmp_path.glob("*.jsonl"))}
    assert len(labels) == 2


def test_negative_indexes_in_mapping_paths():
    assert get_path({"a": [1, 2, 3]}, "a[-1]") == 3


# ---------------------------------------------------------------- report, aggregate, taskgen

def test_report_payload_escapes_every_angle_bracket():
    html = render({"skills": [{"skill": "x", "summary": "<!--<script>"}]})
    payload = html.split('id="sg-data">', 1)[1].split("</script>", 1)[0]
    assert "<" not in payload


def test_stale_checks_and_weak_verdicts_are_reported():
    rows = collect(DEMO)
    doc_ = load_claims(EXAMPLES / "claims" / "bugfix-tdd.claims.json")
    agg = aggregate_skill("bugfix-tdd", doc_, EXAMPLES / "skills" / "bugfix-tdd", rows)
    assert agg["stale_checks"] == 0
    assert not any(c["weak"] for c in agg["claims"])  # 4 runs per variant in the demo
    few = [r for r in rows if not (r["trace"]["skill"] == "bugfix-tdd" and r["trace"]["rep"] == 2)]
    assert all(c["weak"] for c in aggregate_skill("bugfix-tdd", doc_, EXAMPLES / "skills" / "bugfix-tdd", few)["claims"]
               if c["skill_rate"] is not None)  # 2 runs per variant
    renamed = {**doc_, "claims": [{**c, "id": "x" + c["id"]} for c in doc_["claims"]]}
    assert aggregate_skill("bugfix-tdd", renamed, EXAMPLES / "skills" / "bugfix-tdd", rows)["stale_checks"] > 0


def test_generated_fixtures_cannot_write_claude_settings():
    assert _safe_rel(".claude/settings.json") is None and _safe_rel(".git/hooks/x") is None
    assert _safe_rel("../x") is None and _safe_rel("/etc/x") is None and _safe_rel("setup.sh") is None
    assert _safe_rel("src/app.py") == Path("src/app.py")


def test_no_local_paths_in_the_shipped_demo():
    login = os.environ.get("USER") or ""
    for f in DEMO.rglob("*.json"):
        text = f.read_text()
        assert "/Users/" not in text and "claude_ai_" not in text and "scratch-workspaces" not in text, f
        if len(login) > 3:
            assert login not in text, f
