import json

import pytest

from skillgraph.aggregate import aggregate_skill, pick_ablation_spans, quadrant
from skillgraph.providers import parse_model
from skillgraph.report import render
from skillgraph.runner import (
    RunConfig,
    Variant,
    ablate_text,
    load_runset,
    load_suite,
    make_deny,
    prepare_workspace,
    run_success_checks,
    save_runset,
)
from skillgraph.schema import load_claims


def test_variants_and_ablation():
    assert Variant.parse("ablate-L3-5").remove == [[3, 5]]
    with pytest.raises(ValueError):
        Variant.parse("nope")
    assert ablate_text("a\nb\nc\nd\n", [[2, 3]]) == "a\nd\n"


def test_suite_loads_examples(examples):
    suite = load_suite(examples / "suite.yaml")
    assert [s.name for s in suite.skills] == ["csv-report", "bugfix-tdd", "release-notes"]
    assert all(t["fixture"].startswith("/") for s in suite.skills for t in s.tasks)


def test_workspace_variants(examples, tmp_path):
    suite = load_suite(examples / "suite.yaml")
    rn = suite.skill("release-notes")
    task = rn.tasks[0]
    prepare_workspace(tmp_path / "base", task, rn, Variant.parse("baseline"))
    prepare_workspace(tmp_path / "skill", task, rn, Variant.parse("skill"))
    prepare_workspace(tmp_path / "abl", task, rn, Variant.ablate(15, 15))
    assert not (tmp_path / "base" / ".claude").exists() and not (tmp_path / "base" / "setup.sh").exists()
    md = (tmp_path / "skill" / ".claude" / "skills" / "release-notes" / "SKILL.md").read_text()
    ablated = (tmp_path / "abl" / ".claude" / "skills" / "release-notes" / "SKILL.md").read_text()
    assert "Never create git tags" in md and "Never create git tags" not in ablated
    results = run_success_checks(tmp_path / "base", task)
    assert results == {"file RELEASE_NOTES.md ~ /v1\\.5\\.0/": False,
                       "cmd test \"$(git tag -l | wc -l | tr -d ' ')\" = \"1\"": True}


def test_runset_paths_are_relative(examples, tmp_path):
    suite = load_suite(examples / "suite.yaml")
    out = tmp_path / "runs" / "x"
    out.mkdir(parents=True)
    save_runset(out, suite, parse_model("openrouter:a/b"), "auto")
    raw = json.loads((out / "runset.json").read_text())
    assert not raw["suite"].startswith("/") and raw["model"] == "openrouter:a/b"
    meta = load_runset(out)
    assert meta["suite"] == str(examples / "suite.yaml")


def test_enforcement_rules(examples):
    deny, n = make_deny(load_claims(examples / "claims" / "release-notes.claims.json"))
    assert n == 2
    assert deny("Bash", {"command": "git tag -a v1.5.0 -m x"})
    assert deny("Bash", {"command": "git push origin main"})
    assert deny("Bash", {"command": "git tag -l"}) is None


def test_quadrants():
    assert quadrant(1.0, 0.0) == "working"
    assert quadrant(1.0, 1.0) == "inert"
    assert quadrant(0.0, 0.0) == "ignored"
    assert quadrant(0.0, 1.0) == "interfering"
    assert quadrant(1.0, None) == "followed"
    assert quadrant(None, 0.5) == "untested"


def _row(skill, variant, rep, statuses, success=True):
    return {"dir": "x", "trace": {"skill": skill, "task": "t", "rep": rep, "variant": {"name": variant, "remove": [[5, 5]]},
                                  "success": {"ok": success}, "events": [], "result": {"cost_usd": 0.1}},
            "check": {"claims": {k: {"status": v, "reason": ""} for k, v in statuses.items()},
                      "conditions": {}, "judge_cost_usd": 0.0}}


def test_aggregate_and_ablation_picks(examples):
    d = load_claims(examples / "claims" / "bugfix-tdd.claims.json")
    ids = [c["id"] for c in d["claims"]]
    sat = {i: "satisfied" for i in ids}
    rows = [_row("bugfix-tdd", "skill", r, sat) for r in (1, 2)]
    rows += [_row("bugfix-tdd", "baseline", r, {**sat, "c1": "missed", "c8": "missed"}, success=False) for r in (1, 2)]
    agg = aggregate_skill("bugfix-tdd", d, examples / "skills" / "bugfix-tdd", rows)
    by = {c["id"]: c["quadrant"] for c in agg["claims"]}
    assert by["c1"] == "working" and by["c2"] == "inert"
    assert agg["totals"]["success"] == {"skill": 1.0, "baseline": 0.0}
    spans = pick_ablation_spans(agg, top=1)
    assert spans and all(len(s) == 2 for s in spans)


def test_report_renders_and_escapes():
    html = render({"skills": [{"skill": "x", "summary": "</script><script>alert(1)</script>"}]})
    assert "/*__SKILLGRAPH_DATA__*/" not in html
    assert "</script><script>alert(1)" not in html and html.startswith("<!doctype html>")
    assert not render({"skills": []}, fragment=True).startswith("<!doctype")


def test_run_config_default_agent():
    assert str(RunConfig().agent) == "claude:claude-opus-5-5"
