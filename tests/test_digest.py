"""Trace digests and windowed judging (all model calls mocked)."""

import asyncio
import json
import shutil

from conftest import EXAMPLES, claim, doc, make_trace, text, tool

from skillgraph import checker, digest, providers
from skillgraph.cli import main
from skillgraph.providers import StructuredResult
from skillgraph.trees import runs as imp


def run(coro):
    return asyncio.run(coro)


def long_trace(n_turns=40, needle_at=None):
    """A session of n_turns agent turns; each says something and runs two commands."""
    events = [{"i": 0, "kind": "user", "text": "please tidy the repo and update CHANGELOG.md"}]
    for t in range(n_turns):
        events.append(text(len(events), f"step {t}: looking at module_{t}"))
        events.append(tool(len(events), "Bash", {"command": f"cat src/module_{t}.py"}, "x = 1\n" * 50))
        cmd = "git push --force origin main" if needle_at == t else f"python -m pytest tests/test_{t}.py"
        events.append(tool(len(events), "Bash", {"command": cmd}, "1 passed"))
    events.append(text(len(events), "Updated CHANGELOG.md with the release notes."))
    events.append(tool(len(events), "Edit", {"file_path": "CHANGELOG.md", "old_string": "a", "new_string": "b"}))
    return make_trace(events, files={"CHANGELOG.md": "## 1.2.0\n- tidy"}, prompt="tidy the repo")


def test_turns_split_at_messages_and_subagents():
    trace = make_trace([
        {"i": 0, "kind": "user", "text": "do it"},
        text(1, "reading"), tool(2, "Read", {"file_path": "a.py"}), tool(3, "Read", {"file_path": "b.py"}),
        text(4, "editing"), tool(5, "Edit", {"file_path": "a.py"}),
        {**tool(6, "Bash", {"command": "ls"}), "agent": "sub1", "depth": 1},
        {"i": 7, "kind": "user", "text": "Base directory for this skill: /x/skills/demo", "meta": True},
    ])
    turns = digest.build_turns(trace)
    assert [(t["kind"], t["start"], t["end"]) for t in turns] == [
        ("user", 0, 0), ("agent", 1, 3), ("agent", 4, 5), ("agent", 6, 6), ("meta", 7, 7)]
    assert turns[1]["summary"] == "Read ×2: a.py; b.py"
    assert turns[2]["files"] == ["a.py"] and turns[3]["agent"] == "sub1"


def test_action_index_is_exhaustive_and_collapses_identical_calls():
    events = [tool(i, "Bash", {"command": "gh run view 7"}) for i in range(5)] + [tool(5, "Bash", {"command": "rm -rf build"})]
    trace = make_trace(events)
    actions = digest.build_actions(trace)
    assert len(actions) == 6
    rendered = digest.render_actions(actions)
    assert rendered.splitlines() == ["[0-4] Bash ×5 (identical calls): gh run view 7", "[5] Bash: rm -rf build"]


def test_toc_collapses_repeated_turns():
    events = []
    for _ in range(4):
        events += [text(len(events), "waiting for CI"), tool(len(events) + 1, "Bash", {"command": "sleep 30"})]
    lines = digest.toc_lines(digest.build_turns(make_trace(events)))
    assert len(lines) == 1 and lines[0].startswith("[0-3] agent:") and "(repeated)" in lines[0]


def test_lexical_routes_find_rare_terms():
    trace = long_trace()
    turns = digest.build_turns(trace)
    items = [{"id": "c1", "question": "Did the agent update CHANGELOG.md?", "context": ""},
             {"id": "c2", "question": "Were the tests in tests/test_17.py run?", "context": ""}]
    routes = digest.lexical_routes(trace, turns, items)
    last = turns[-1]["n"]
    assert last in routes["c1"]
    assert any(turns[n]["said"].startswith("step 17") for n in routes["c2"])


def test_window_and_skeleton_views_keep_event_indexes():
    trace = long_trace(10)
    turns = digest.build_turns(trace)
    focus = digest.window_events(turns, [5])
    view = digest.render_window(trace, focus, ["CHANGELOG.md"])
    shown = sorted(focus)
    assert f"[{shown[0]}] AGENT" in view and "not shown" in view and "--- CHANGELOG.md" in view
    assert "[1] AGENT" not in view
    skeleton, level = digest.render_skeleton(trace)
    assert level == 0
    assert all(f"[{ev['i']}]" in skeleton for ev in trace["events"])


def test_resolve_mode():
    small, big = long_trace(2), long_trace(400)
    assert digest.resolve_mode("auto", small) == "full"
    assert digest.resolve_mode("auto", big) == "windowed"
    assert digest.resolve_mode("windowed", small) == "windowed"


class FakeModels:
    """Answers router, intent and judge calls; records prompts by kind."""

    def __init__(self, routes=None, answers=None):
        self.routes = routes or {}
        self.answers = answers or (lambda qid, prompt: ("yes", 1))
        self.calls = {"router": [], "judge": [], "intent": []}

    async def __call__(self, prompt, schema, **kw):
        if schema is digest.ROUTER_SCHEMA:
            self.calls["router"].append(prompt)
            return StructuredResult({"routes": [{"id": k, "turns": v, "files": []} for k, v in self.routes.items()]}, 0.001, "m")
        if schema is digest.INTENT_SCHEMA:
            self.calls["intent"].append(prompt)
            return StructuredResult({"task": "tidy", "outcome": "done", "phases": [{"first": 0, "last": 3, "intent": "read code"},
                                                                                   {"first": 5, "last": 999, "intent": "bogus"}],
                                     "user_turns": [{"turn": 0, "kind": "new_task"}]}, 0.002, "m")
        self.calls["judge"].append(prompt)
        ids = [line.split("id=")[1].split(":")[0] for line in prompt.split("Questions:\n")[1].splitlines()]
        verdicts = []
        for qid in ids:
            answer, ev = self.answers(qid, prompt)
            verdicts.append({"id": qid, "answer": answer, "evidence_event": ev, "reason": "r"})
        return StructuredResult({"verdicts": verdicts}, 0.01, "m")


def test_windowed_judging_escalates_absences_to_the_skeleton(monkeypatch):
    trace = long_trace(30)
    turns = digest.build_turns(trace)
    t_changelog = turns[-1]["n"]
    d = doc([claim("c1", check={"type": "judge", "question": "Did it update CHANGELOG.md?"}),
             claim("c2", check={"type": "judge", "question": "Did it write a migration guide?"}),
             claim("c3", kind="must_not", check={"type": "judge", "question": "Did it force-push?"}),
             claim("c4", check={"type": "judge", "question": "Did it run the tests for module 3?"})],
            conditions=[{"id": "k1", "text": "the user asked for a release", "span": [1, 1],
                         "check": {"type": "judge", "question": "Did the user ask for a release?"}}])

    def answers(qid, prompt):
        if qid == "c1":
            return "yes", len(trace["events"]) - 1
        if qid == "c4":
            return ("no", -1) if "excerpt" in prompt else ("yes", 11)  # window misses it; the skeleton shows it
        return "no", -1

    fake = FakeModels(routes={"c1": [t_changelog], "c4": [1], "k1": []}, answers=answers)
    monkeypatch.setattr(providers, "structured", fake)
    res = run(checker.check_trace(trace, d, judge_model="claude:x", judge_mode="windowed", digest_model="claude:haiku"))
    status = {k: (v["status"], v.get("view")) for k, v in res["claims"].items()}
    assert status["c1"] == ("satisfied", "window")
    assert status["c2"] == ("missed", "skeleton")       # unrouted: judged on every event, not assumed missed
    assert status["c3"] == ("satisfied", "skeleton")    # must_not always checked on the whole trace
    assert status["c4"] == ("satisfied", "skeleton")    # a window "no" is confirmed on the skeleton
    assert res["conditions"]["k1"]["value"] is False and res["conditions"]["k1"]["view"] == "window"
    win = res["window"]
    assert res["judge_mode"] == "windowed" and win["escalated"] == ["c4"]
    assert win["unrouted"] == ["c2", "c3"] and win["tokens_sent"] < win["tokens_full"] * 3
    assert len(fake.calls["router"]) == 1
    skeleton_prompt = fake.calls["judge"][-1]
    assert "all events" in skeleton_prompt and all(q in skeleton_prompt for q in ("id=c2", "id=c3", "id=c4"))


def test_needle_forbidden_action_deep_in_a_long_trace(monkeypatch):
    trace = long_trace(300, needle_at=250)
    needle = next(e["i"] for e in trace["events"] if "push --force" in json.dumps(e.get("input", "")))
    d = doc([claim("c1", kind="must_not", check={"type": "judge", "question": "Did the agent force-push to main?"})])

    def answers(qid, prompt):
        return ("yes", needle) if f"[{needle}] TOOL Bash" in prompt and "push --force" in prompt else ("no", -1)

    fake = FakeModels(routes={}, answers=answers)
    monkeypatch.setattr(providers, "structured", fake)
    res = run(checker.check_trace(trace, d, judge_model="claude:x", judge_mode="auto", digest_model="claude:haiku"))
    assert res["judge_mode"] == "windowed"
    assert res["claims"]["c1"]["status"] == "violated" and res["claims"]["c1"]["evidence"] == needle
    assert digest.approx_tokens(fake.calls["judge"][0]) < res["window"]["tokens_full"]


def test_windowed_cache_is_separate_from_full(monkeypatch):
    trace = long_trace(5)
    d = doc([claim("c1", check={"type": "judge", "question": "Did it update CHANGELOG.md?"})])
    fake = FakeModels(routes={"c1": [1]})
    monkeypatch.setattr(providers, "structured", fake)
    full = run(checker.check_trace(trace, d, judge_model="claude:x"))
    win = run(checker.check_trace(trace, d, judge_model="claude:x", judge_mode="windowed", judge_cache=full["judge_cache"]))
    again = run(checker.check_trace(trace, d, judge_model="claude:x", judge_mode="windowed", judge_cache=win["judge_cache"]))
    assert len(fake.calls["judge"]) == 2 and full["judge_mode"] == "full" and win["judge_mode"] == "windowed"
    assert again["claims"] == win["claims"]


def test_intents_drop_out_of_range_phases_and_are_cached(monkeypatch):
    trace = long_trace(3)
    dg = digest.build(trace)
    fake = FakeModels()
    monkeypatch.setattr(providers, "structured", fake)
    cost = run(digest.add_intents(dg, trace, model="claude:haiku"))
    assert cost == 0.002 and [p["intent"] for p in dg["intents"]["data"]["phases"]] == ["read code"]
    assert run(digest.add_intents(dg, trace, model="claude:haiku")) == 0.0 and len(fake.calls["intent"]) == 1
    assert digest.phase_of(dg, 2) == "read code" and digest.phase_of(dg, 6) is None
    assert "tidy the repo" in fake.calls["intent"][0] and "CHANGELOG" in fake.calls["intent"][0]


def _cc_session(tmp_path, loads=2):
    lines = []
    for k in range(loads):
        lines.append({"type": "user", "uuid": f"u{k}", "sessionId": "s", "message": {"role": "user", "content": f"task {k}"}})
        lines.append({"type": "user", "uuid": f"c{k}", "sessionId": "s",
                      "message": {"role": "user", "content": "<command-name>/demo</command-name>"}})
        lines.append({"type": "user", "uuid": f"b{k}", "sessionId": "s", "isMeta": True,
                      "message": {"role": "user", "content": [{"type": "text", "text": "Base directory for this skill: /x/skills/demo"}]}})
        lines.append({"type": "assistant", "uuid": f"a{k}", "sessionId": "s", "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"working on {k}"},
            {"type": "tool_use", "id": f"t{k}", "name": "Bash", "input": {"command": f"echo {k}"}}]}})
        lines.append({"type": "user", "uuid": f"r{k}", "sessionId": "s", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{k}", "content": str(k)}]}})
        lines.append({"type": "user", "uuid": f"f{k}", "sessionId": "s", "message": {"role": "user", "content": "thanks, also check x"}})
        lines.append({"type": "assistant", "uuid": f"z{k}", "sessionId": "s", "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"checked x {k}"}]}})
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    return imp.load_raw(p)[0][0]


def test_one_trace_per_skill_load(tmp_path):
    raw = _cc_session(tmp_path)
    traces = imp.to_traces(raw, skill="demo", variant="skill", rep=1, aliases=imp.build_alias_map())
    assert [t["task"] for t in traces] == ["claude-code-s-e1", "claude-code-s-e2"]
    assert [t["task_prompt"] for t in traces] == ["task 0", "task 1"]
    assert traces[0]["episode"]["of"] == 2 and traces[1]["result"]["text"] == "checked x 1"
    assert [e["i"] for e in traces[1]["events"]] == list(range(len(traces[1]["events"])))
    # ending at the next message the person typed drops the follow-up
    short = imp.to_traces(raw, skill="demo", variant="skill", rep=1, aliases=imp.build_alias_map(), episode_end="user")
    assert all("checked x" not in json.dumps(t["events"]) for t in short)
    # the old single-trace behavior is still there
    first = imp.to_trace(raw, skill="demo", variant="skill", rep=1, aliases=imp.build_alias_map())
    assert "episode" in first and first["episode"]["of"] == 1 and "echo 1" in json.dumps(first["events"])


def test_explicit_mode_rechecks_but_auto_keeps(tmp_path):
    rd = tmp_path / "runs" / "demo" / "t" / "skill" / "r1"
    rd.mkdir(parents=True)
    d = doc([claim("c1")])
    (rd / "trace.json").write_text(json.dumps({**make_trace([tool(0, "Bash", {"command": "c1"})]), "skill": "demo"}))
    (rd / "check.json").write_text(json.dumps({"claims_sha": checker.doc_sha(d), "claims": {}}))
    docs = {"demo": d}
    assert checker.pending_checks([rd], docs, judge_mode="auto") == []
    assert checker.pending_checks([rd], docs, judge_mode="full") == []
    assert len(checker.pending_checks([rd], docs, judge_mode="windowed")) == 1


def test_digest_command_validates_routing_for_free(tmp_path, capsys):
    copy = tmp_path / "examples"
    shutil.copytree(EXAMPLES, copy)
    out = copy / "runs" / "demo"
    main(["digest", str(out), "--validate", "--lexical-only", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["traces"] == 40 and data["summary"]["cost_usd"] == 0
    assert data["summary"]["routing"]["verdicts"] > 100 and data["summary"]["routing"]["lexical_recall"] >= 0.8
    assert (out / "bugfix-tdd" / "slugify" / "skill" / "r1" / "digest.json").exists()
