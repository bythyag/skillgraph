"""summary, diff, show, estimate, and the spend gate. All offline, on the shipped demo."""

import json
import shutil

import pytest
from conftest import EXAMPLES

from skillgraph import costs
from skillgraph.cli import main
from skillgraph.diff import compare, match_claims, to_markdown
from skillgraph.providers import parse_model
from skillgraph.report import build_data
from skillgraph.show import render
from skillgraph.summary import failures, summarize, to_junit

DEMO = EXAMPLES / "runs" / "demo"


def run_cli(*argv):
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    return exc.value.code


def test_summary_json_and_fail_on(capsys):
    s = summarize(build_data(DEMO))
    names = [x["skill"] for x in s["skills"]]
    assert names == ["csv-report", "bugfix-tdd", "release-notes"]
    rn = next(x for x in s["skills"] if x["skill"] == "release-notes")
    assert rn["reference"]["paths_tested"] == 4 and rn["reference"]["diagnoses"]["gap"] >= 1
    assert any(f["kind"] == "gap" for f in failures(s, {"gap"}))
    assert failures(s, set()) == []
    assert "<testsuites" in to_junit(s, {"gap"}) and 'failures="' in to_junit(s, {"gap"})
    assert run_cli("summary", str(DEMO), "--fail-on", "gap") == 1
    assert run_cli("summary", str(DEMO), "--fail-on", "bogus") != 0
    capsys.readouterr()  # drop the text output of the calls above
    main(["summary", str(DEMO), "--json"])
    assert json.loads(capsys.readouterr().out)["skills"][0]["skill"] == "csv-report"


def test_diff_matches_claims_across_edits(tmp_path):
    data = build_data(DEMO)
    same = compare(data, data)
    assert all(r["change"] == "same" for s in same["skills"] for r in s["claims"])
    before = [{"id": "c1", "text": "Run the tests with pytest", "quadrant": "ignored", "skill_rate": 0.0},
              {"id": "c2", "text": "Write NOTES.md", "quadrant": "working", "skill_rate": 1.0}]
    after = [{"id": "c7", "text": "Run the tests with pytest -q", "quadrant": "working", "skill_rate": 1.0},
             {"id": "c8", "text": "Credit every author", "quadrant": "ignored", "skill_rate": 0.0}]
    pairs = [(a and a["id"], b and b["id"]) for a, b in match_claims(before, after)]
    assert pairs == [("c1", "c7"), ("c2", None), (None, "c8")]
    md = to_markdown(same)
    assert md.startswith("### skillgraph")


def test_show_renders_a_run():
    out = render(DEMO / "release-notes" / "minor-release" / "skill" / "r1", color=False)
    assert "release-notes / minor-release / skill / r1" in out and "skill claims" in out and "reference steps" in out
    assert "✓" in out


def test_estimates_and_the_gate(tmp_path, monkeypatch, capsys):
    est = costs.estimate(agent_runs=4, agent=parse_model("claude:claude-opus-5-5"), judge=parse_model("claude:claude-haiku-5-5"),
                         history_dirs=[DEMO])
    assert est.lines[0].basis.startswith("average of") and est.total > 0
    fresh = costs.estimate(agent_runs=2, agent=parse_model("claude:claude-sonnet-5-5"), judge=parse_model("claude:claude-sonnet-5-5"),
                           history_dirs=[tmp_path])
    assert fresh.lines[0].each == costs.CLAUDE_DEFAULTS["sonnet"][0]
    with pytest.raises(costs.Declined, match="--yes"):
        costs.confirm(est, yes=False, max_total_usd=None)  # pytest's stdin isn't a terminal
    with pytest.raises(costs.Declined, match="over --max-total-usd"):
        costs.confirm(est, yes=True, max_total_usd=0.01)
    costs.confirm(est, yes=True, max_total_usd=None)
    costs.confirm(costs.Estimate(), yes=False, max_total_usd=None)  # nothing to spend: no question


def test_run_asks_before_spending(tmp_path, capsys):
    code = run_cli("run", str(EXAMPLES / "suite.yaml"), "--out", str(tmp_path / "r"), "--skills", "release-notes",
                   "--tasks", "minor-release", "--runs", "1")
    assert "not started" in str(code) and "--yes" in str(code)
    assert "estimated total" in capsys.readouterr().out
    assert not (tmp_path / "r").exists()


def test_estimate_command(capsys):
    main(["estimate", str(EXAMPLES / "suite.yaml"), "--runs", "1", "--top", "0"])
    out = capsys.readouterr().out
    assert "agent runs" in out and "estimated total" in out


def test_check_on_the_demo_is_free(tmp_path):
    copy = tmp_path / "examples"
    shutil.copytree(EXAMPLES, copy)
    main(["check", str(copy / "runs" / "demo"), "--yes"])  # every trace is current: nothing to grade
