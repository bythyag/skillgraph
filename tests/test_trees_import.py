import json

import pytest

from skillgraph.trees import runs as imp
from skillgraph.trees.mapping import as_text, get_path, matches

ALIASES = imp.build_alias_map()


def jl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def entry(kind, ts, content, **kw):
    return {"type": kind, "uuid": f"u{ts}", "sessionId": "s1", "timestamp": f"2026-10-14T10:00:{ts:02d}Z",
            "cwd": "/proj", "message": {"role": kind, "content": content, **({"model": "claude-x"} if kind == "assistant" else {})},
            **kw}


@pytest.fixture
def cc_session(tmp_path):
    main = tmp_path / "abc12345-0000.jsonl"
    jl(main, [
        {"type": "queue-operation", "sessionId": "s1"},
        entry("user", 1, "Prepare the release notes"),
        entry("assistant", 2, [{"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "release-notes"}}]),
        entry("user", 3, [{"type": "tool_result", "tool_use_id": "t1", "content": "Launching skill: release-notes"}]),
        entry("user", 4, [{"type": "text", "text": "Base directory for this skill: /proj/.claude/skills/release-notes\n\n# Release notes"}], isMeta=True),
        entry("assistant", 5, [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "git describe --tags --abbrev=0"}}]),
        entry("user", 6, [{"type": "tool_result", "tool_use_id": "t2", "content": "v1.4.0"}]),
        entry("assistant", 7, [{"type": "tool_use", "id": "t3", "name": "Agent", "input": {"prompt": "write notes"}}]),
        entry("user", 11, [{"type": "tool_result", "tool_use_id": "t3", "content": [{"type": "text", "text": "done. agentId: sub1"}]}]),
        entry("assistant", 12, [{"type": "text", "text": "Release notes are ready."}]),
    ])
    sub = tmp_path / "abc12345-0000" / "subagents"
    sub.mkdir(parents=True)
    jl(sub / "agent-sub1.jsonl", [
        entry("assistant", 8, [{"type": "tool_use", "id": "s1", "name": "Write",
                                "input": {"file_path": "/proj/RELEASE_NOTES.md", "content": "## v1.5.0\n"}}]),
        entry("user", 9, [{"type": "tool_result", "tool_use_id": "s1", "content": "ok"}]),
    ])
    return main


def test_claude_code_transcript_becomes_a_trace(cc_session, tmp_path):
    assert imp.detect_format(cc_session) == "claude-code"
    assert imp.expand_sources([tmp_path]) == [cc_session]
    raws, extra = imp.load_raw(cc_session)
    t = imp.to_trace(raws[0], skill="release-notes", variant="skill", rep=1, aliases=ALIASES)
    assert t["skill_loaded"] and t["task_prompt"] == "Prepare the release notes"
    tools = [(e["tool"], e.get("depth", 0)) for e in t["events"] if e["kind"] == "tool"]
    assert tools == [("Skill", 0), ("Bash", 0), ("Agent", 0), ("Write", 1)]
    write = next(e for e in t["events"] if e.get("tool") == "Write")
    assert write["parent"] == "t3" and write["agent"] == "sub1"
    assert t["files"] == {"RELEASE_NOTES.md": "## v1.5.0\n"}
    assert t["model"] == "claude-x" and t["result"]["text"] == "Release notes are ready."
    whole = imp.to_trace(raws[0], skill="release-notes", variant="skill", rep=1, aliases=ALIASES, from_skill_load=False)
    assert len(whole["events"]) > len(t["events"])
    other = imp.to_trace(raws[0], skill="csv-report", variant="skill", rep=1, aliases=ALIASES)
    assert not other["skill_loaded"]


def test_otel_genai_and_openinference(tmp_path):
    def attrs(**kv):
        return [{"key": k, "value": {"stringValue": v}} for k, v in kv.items()]
    doc = {"resourceSpans": [{"scopeSpans": [{"spans": [
        {"traceId": "T1", "spanId": "a", "name": "agent", "startTimeUnixNano": "1000000000",
         "attributes": attrs(**{"openinference.span.kind": "AGENT", "input.value": "Draft the notes", "output.value": "Done"})},
        {"traceId": "T1", "spanId": "b", "parentSpanId": "a", "name": "execute_tool run_shell_command",
         "startTimeUnixNano": "2000000000",
         "attributes": attrs(**{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "run_shell_command",
                                "gen_ai.tool.call.arguments": json.dumps({"cmd": "git log v1.4.0..HEAD"}),
                                "gen_ai.tool.call.result": "abc feat: x"})},
        {"traceId": "T1", "spanId": "c", "parentSpanId": "b", "name": "read", "startTimeUnixNano": "3000000000",
         "status": {"code": "STATUS_CODE_ERROR"},
         "attributes": attrs(**{"openinference.span.kind": "TOOL", "tool.name": "read_file",
                                "input.value": json.dumps({"path": "CHANGELOG.md"})})},
    ]}]}]}
    p = tmp_path / "otel.json"
    p.write_text(json.dumps(doc))
    assert imp.detect_format(p) == "otel"
    raw = imp.load_raw(p)[0][0]
    t = imp.to_trace(raw, skill="x", variant="skill", rep=1, aliases=ALIASES, from_skill_load=False)
    bash, read = [e for e in t["events"] if e["kind"] == "tool"]
    assert (bash["tool"], bash["raw_tool"], bash["input"]["command"], bash["depth"]) == ("Bash", "run_shell_command", "git log v1.4.0..HEAD", 1)
    assert (read["tool"], read["input"]["file_path"], read["is_error"], read["depth"]) == ("Read", "CHANGELOG.md", True, 2)
    assert t["task_prompt"] == "Draft the notes" and t["result"]["text"] == "Done"


def test_langsmith_nested_runs(tmp_path):
    runs = [{"id": "r1", "trace_id": "tr", "run_type": "chain", "name": "agent", "inputs": {"input": "release it"},
             "outputs": {"output": "ok"}, "start_time": "2026-10-14T10:00:00",
             "child_runs": [{"id": "r2", "trace_id": "tr", "run_type": "tool", "name": "execute_command",
                             "inputs": {"cmd": "git push origin main"}, "outputs": {"output": "pushed"},
                             "start_time": "2026-10-14T10:00:01"}]}]
    p = tmp_path / "runs.json"
    p.write_text(json.dumps(runs))
    assert imp.detect_format(p) == "langsmith"
    t = imp.to_trace(imp.load_raw(p)[0][0], skill="x", variant="skill", rep=1, aliases=ALIASES, from_skill_load=False)
    ev = t["events"][0]
    assert (ev["tool"], ev["input"]["command"], ev["output"], ev["parent"], ev["depth"]) == ("Bash", "git push origin main", "pushed", "r1", 1)


def test_openai_agents_spans(tmp_path):
    spans = [{"object": "trace.span", "id": "sp1", "trace_id": "tr", "parent_id": None, "started_at": "2026-10-14T10:00:00Z",
              "span_data": {"type": "function", "name": "shell", "input": "{\"command\": \"ls\"}", "output": "a.txt"}},
             {"object": "trace.span", "id": "sp2", "trace_id": "tr", "parent_id": None, "started_at": "2026-10-14T10:00:01Z",
              "span_data": {"type": "generation", "output": [{"role": "assistant", "content": "Listed files."}]}}]
    p = tmp_path / "spans.jsonl"
    jl(p, spans)
    assert imp.detect_format(p) == "openai-agents"
    t = imp.to_trace(imp.load_raw(p)[0][0], skill="x", variant="skill", rep=1, aliases=ALIASES, from_skill_load=False)
    assert [(e["kind"], e.get("tool")) for e in t["events"]] == [("tool", "Bash"), ("text", None)]


def test_mapping_file_for_unknown_formats(tmp_path):
    p = tmp_path / "custom.json"
    p.write_text(json.dumps({"steps": [
        {"id": 1, "kind": "call", "fn": "exec", "args": {"cmd": "git tag v2"}, "out": "", "at": 1},
        {"id": 2, "kind": "say", "msg": "Tagged.", "at": 2},
        {"id": 3, "kind": "user", "msg": "tag the release", "at": 0}]}))
    spec = tmp_path / "map.yaml"
    spec.write_text("records: steps\norder_by: at\ntool_call: {when: {kind: call}, name: fn, input: args, output: out}\n"
                    "text: {when: {kind: say}, value: msg}\nprompt: {when: {kind: user}, value: msg}\ntools: {exec: Bash}\n")
    raws, extra = imp.load_raw(p, str(spec))
    t = imp.to_trace(raws[0], skill="x", variant="skill", rep=1, aliases=imp.build_alias_map(extra), from_skill_load=False)
    assert t["events"][0]["tool"] == "Bash" and t["events"][0]["input"]["command"] == "git tag v2"
    assert t["task_prompt"] == "tag the release"
    with pytest.raises(ValueError, match="tool_call.name"):
        imp.import_with_mapping(p, {"records": "steps", "tool_call": {}})


def test_tool_names_inputs_and_file_reconstruction():
    assert imp.canonical_tool("str_replace_based_edit_tool", {"command": "view"}, ALIASES) == "Read"
    assert imp.canonical_tool("str_replace_editor", {"command": "create"}, ALIASES) == "Write"
    assert imp.canonical_tool("mcp__fs__read_file", {}, ALIASES) == "Read"
    assert imp.canonical_tool("deploy", {}, ALIASES) == "deploy"
    events = [
        {"i": 0, "kind": "tool", "tool": "Read", "input": {"file_path": "/w/a.py"}, "output": "     1\tx = 1\n     2\ty = 2"},
        {"i": 1, "kind": "tool", "tool": "Edit", "input": {"file_path": "/w/a.py", "old_string": "y = 2", "new_string": "y = 3"}, "output": ""},
        {"i": 2, "kind": "tool", "tool": "Edit", "input": {"file_path": "/w/b.py", "old_string": "q", "new_string": "r"}, "output": ""},
    ]
    assert imp.reconstruct_files(events, "/w") == {"a.py": "x = 1\ny = 3", "b.py": None}


def test_mapping_helpers():
    doc = {"a": {"b.c": [1, {"d": "x"}]}, "s": '{"k": [1, 2]}'}
    assert get_path(doc, 'a["b.c"][1].d') == "x"
    assert get_path(doc, "s.k[*]") == [1, 2]
    assert get_path(doc, "a.missing") is None
    assert matches({"t": "Tool"}, {"t": ["tool", "fn"]}) and not matches({}, {"t": "*"})
    assert as_text([{"type": "text", "text": "hi"}, {"content": "there"}]) == "hi\nthere"


def test_reading_skill_md_counts_as_loading_but_writing_does_not():
    def ev(i, tool, inp):
        return {"i": i, "kind": "tool", "tool": tool, "input": inp, "output": ""}
    wrote = [ev(0, "Write", {"file_path": "/p/.claude/skills/notes/SKILL.md", "content": "x"}),
             ev(1, "Bash", {"command": "git add skills/notes/SKILL.md"})]
    assert imp._skill_load_index(wrote, "notes") is None
    assert imp._skill_load_index(wrote + [ev(2, "Bash", {"command": "cat skills/notes/SKILL.md"})], "notes") == 2
    assert imp._skill_load_index([ev(0, "Read", {"file_path": "C:\\\\p\\\\skills\\\\notes\\\\SKILL.md"})], "notes") == 0
