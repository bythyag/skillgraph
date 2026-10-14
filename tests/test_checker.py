import asyncio

from conftest import claim, doc, make_trace, text, tool

from skillgraph import checker
from skillgraph.providers import StructuredResult


def run(coro):
    return asyncio.run(coro)


def test_match_tool_checks_name_input_and_output():
    chk = {"type": "tool", "tools": ["Bash"], "input_any": [r"validate\.py"], "output_any": ["OK"]}
    assert checker.match_tool(chk, tool(0, "Bash", {"command": "python3 /x/validate.py a.csv"}, "OK"))
    assert not checker.match_tool(chk, tool(0, "Bash", {"command": "python3 validate.py"}, "ERROR"))
    assert not checker.match_tool(chk, tool(0, "Read", {"file_path": "validate.py"}, "OK"))
    # raw strings match even when JSON escaping would get in the way
    path_chk = {"type": "tool", "tools": [], "input_any": [r"C:\\data"], "output_any": []}
    assert checker.match_tool(path_chk, tool(0, "Read", {"file_path": "C:\\data\\x"}))


def test_file_checks_use_final_files_and_last_write():
    trace = make_trace([tool(0, "Write", {"file_path": "/ws/report.md", "content": "v1"}),
                        tool(1, "Edit", {"file_path": "/ws/report.md"})],
                       files={"report.md": "| Region | Revenue |", "sub/notes.md": "x"})
    assert checker.eval_mechanical({"type": "file", "path_glob": "report.md", "content_any": ["Region"]}, trace) == (True, 1)
    assert checker.eval_mechanical({"type": "file", "path_glob": "**/*.md", "content_any": []}, trace)[0]
    assert not checker.eval_mechanical({"type": "file", "path_glob": "report.md", "content_any": ["Top product"]}, trace)[0]


def test_mechanical_statuses_branches_and_order():
    d = doc(
        [claim("c1"),                                                   # satisfied
         claim("c2", after=["c3"]),                                     # out of order
         claim("c3"),
         claim("c4", kind="must_not"),                                  # violated
         claim("c5", kind="must_not"),                                  # respected
         claim("c6", when={"cond": "k1", "value": True}),               # branch not taken
         claim("c7", when={"cond": "k1", "value": False}),              # missed on the taken branch
         claim("c8", kind="artifact", check={"type": "file", "path_glob": "out.md", "content_any": []})],
        conditions=[{"id": "k1", "text": "input broken", "span": [1, 1],
                     "check": {"type": "tool", "tools": ["Bash"], "input_any": ["validate"], "output_any": ["ERROR"]}}],
    )
    trace = make_trace([tool(0, "Bash", {"command": "c1"}), tool(1, "Bash", {"command": "c2"}),
                        tool(2, "Bash", {"command": "c3 c4"}), text(3, "done")], files={"out.md": "ok"})

    async def no_judge(*a, **k):
        raise AssertionError("judge should not be called")

    orig = checker.providers.structured
    checker.providers.structured = no_judge
    try:
        # k1 isn't observed mechanically, so it goes to the judge: patch that path.
        async def judge(prompt, schema, **kw):
            return StructuredResult({"verdicts": [{"id": "k1", "answer": "no", "evidence_event": -1, "reason": "valid"}]}, 0.01, "m")
        checker.providers.structured = judge
        res = run(checker.check_trace(trace, d, judge_model="claude:x"))
    finally:
        checker.providers.structured = orig
    status = {k: v["status"] for k, v in res["claims"].items()}
    assert status == {"c1": "satisfied", "c2": "out_of_order", "c3": "satisfied", "c4": "violated",
                      "c5": "satisfied", "c6": "n/a", "c7": "missed", "c8": "satisfied"}
    assert res["conditions"]["k1"]["value"] is False
    assert res["judge_cost_usd"] == 0.01


def test_judge_verdicts_are_cached():
    d = doc([claim("c1", check={"type": "judge", "question": "Was it tidy?"}),
             claim("c2", kind="must_not", check={"type": "judge", "question": "Did it delete tests?"})])
    trace = make_trace([text(0, "working")])
    calls = []

    async def judge(prompt, schema, **kw):
        calls.append(prompt)
        return StructuredResult({"verdicts": [
            {"id": "c1", "answer": "yes", "evidence_event": 0, "reason": "tidy"},
            {"id": "c2", "answer": "yes", "evidence_event": 0, "reason": "deleted"}]}, 0.2, "m")

    orig = checker.providers.structured
    checker.providers.structured = judge
    try:
        first = run(checker.check_trace(trace, d, judge_model="openrouter:x/y"))
        second = run(checker.check_trace(trace, d, judge_model="openrouter:x/y", judge_cache=first["judge_cache"]))
    finally:
        checker.providers.structured = orig
    assert len(calls) == 1
    assert first["claims"]["c1"]["status"] == "satisfied" and first["claims"]["c2"]["status"] == "violated"
    assert second["claims"] == first["claims"] and second["judge_cost_usd"] == 0.2


def test_render_trace_mentions_events_and_files():
    out = checker.render_trace(make_trace([tool(0, "Bash", {"command": "ls"}, "a.txt")], files={"a.txt": "hello"}))
    assert "[0] TOOL Bash" in out and "--- a.txt" in out and "hello" in out


def test_nested_gates_need_every_condition():
    gates = [{"cond": "outer", "value": True}, {"cond": "inner", "value": True}]
    d = doc([claim("c1", when={"cond": "inner", "value": True}), {**claim("c2", when={"cond": "inner", "value": True}), "when_all": gates}],
            conditions=[{"id": "outer", "text": "o", "span": [1, 1], "check": {"type": "tool", "tools": [], "input_any": ["never-matches"], "output_any": []}},
                        {"id": "inner", "text": "i", "span": [1, 1], "check": {"type": "tool", "tools": ["Bash"], "input_any": ["c1|c2"], "output_any": []}}])
    trace = make_trace([tool(0, "Bash", {"command": "c1 c2"})])

    async def judge(prompt, schema, **kw):  # outer isn't seen mechanically, so the judge rules it false
        return StructuredResult({"verdicts": [{"id": "outer", "answer": "no", "evidence_event": -1, "reason": "x"}]}, 0, "m")

    orig = checker.providers.structured
    checker.providers.structured = judge
    try:
        res = run(checker.check_trace(trace, d, judge_model="claude:x"))
    finally:
        checker.providers.structured = orig
    assert res["claims"]["c1"]["status"] == "satisfied"   # only gated on inner
    assert res["claims"]["c2"]["status"] == "n/a"         # outer gate failed
