import asyncio
import json

import pytest

from skillgraph.providers import AgentRequest, harness
from skillgraph.providers.tools import ToolBox, ToolError, discover_skills, parse_frontmatter

SKILL = """---
name: notes
description: Write meeting notes in the house format.
---
Always write NOTES.md with a Decisions heading.
"""


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\nVALUE = 1\n")
    sk = tmp_path / ".claude" / "skills" / "notes"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(SKILL)
    return tmp_path


def test_frontmatter_and_discovery(ws):
    assert parse_frontmatter(SKILL)["name"] == "notes"
    assert set(discover_skills(ws, ["notes"])) == {"notes"}
    assert discover_skills(ws, []) == {}


def test_toolbox_file_tools(ws):
    box = ToolBox(ws, discover_skills(ws, ["notes"]))
    assert "     2\tVALUE = 1" in box.run("Read", {"file_path": "src/app.py"})
    assert box.run("Write", {"file_path": "out/a.txt", "content": "x\ny"}).startswith("Created out/a.txt")
    box.run("Edit", {"file_path": "src/app.py", "old_string": "VALUE = 1", "new_string": "VALUE = 2"})
    assert "VALUE = 2" in (ws / "src" / "app.py").read_text()
    assert "src/app.py" in box.run("Glob", {"pattern": "**/*.py"})
    assert "src/app.py:2: VALUE = 2" in box.run("Grep", {"pattern": "VALUE"})
    assert "Base directory for this skill" in box.run("Skill", {"skill": "notes"})
    with pytest.raises(ToolError):
        box.run("Read", {"file_path": "../outside.txt"})
    with pytest.raises(ToolError):
        box.run("Edit", {"file_path": "src/app.py", "old_string": "nope", "new_string": "x"})


def test_bash_strips_secrets_and_reports_exit_codes(ws, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    box = ToolBox(ws, {})
    assert box.run("Bash", {"command": "echo ${OPENROUTER_API_KEY:-unset}"}) == "unset"
    with pytest.raises(ToolError, match="Exit code 3"):
        box.run("Bash", {"command": "echo oops; exit 3"})
    assert "Skill" not in [t["function"]["name"] for t in box.schemas()]


def scripted(*turns):
    """A fake chat function that replays assistant messages and records requests."""
    seen = []

    async def chat(messages, tools):
        seen.append(json.loads(json.dumps(messages)))
        msg = turns[len(seen) - 1]
        return {"choices": [{"message": msg}], "usage": {"cost": 0.01}}

    return chat, seen


def call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_harness_runs_tools_and_records_trace(ws):
    chat, seen = scripted(
        {"role": "assistant", "content": "Writing notes.", "tool_calls": [
            call("a", "Write", {"file_path": "NOTES.md", "content": "## Decisions\n"}),
            call("b", "Bash", {"command": "git push"}),
            call("c", "Read", "not-an-object")]},
        {"role": "assistant", "content": "Done.", "reasoning_details": [{"type": "reasoning.text", "text": "..."}]},
    )
    deny = lambda tool, args: "no pushing" if "push" in json.dumps(args) else None  # noqa: E731
    req = AgentRequest(workspace=ws, prompt="Summarize the meeting.", model="m", skills=["notes"], invoke="notes", deny=deny)
    out = asyncio.run(harness.run(req, chat))

    assert (ws / "NOTES.md").exists()
    kinds = [(e["kind"], e.get("tool")) for e in out.events]
    assert kinds == [("user", None), ("text", None), ("tool", "Write"), ("tool", "Bash"), ("tool", "Read"), ("text", None)]
    assert out.events[3]["is_error"] and out.blocked[0]["reason"] == "no pushing"
    assert out.events[4]["is_error"] and "Invalid tool arguments" in out.events[4]["output"]
    assert out.result["subtype"] == "success" and out.result["num_turns"] == 2 and out.result["cost_usd"] == 0.02
    # invoked skill arrives before the task; tool results go back with their call ids
    assert "Always write NOTES.md" in seen[0][1]["content"] and "ARGUMENTS: Summarize" in seen[0][1]["content"]
    assert [m["tool_call_id"] for m in seen[1] if m["role"] == "tool"] == ["a", "b", "c"]
    assert "notes: Write meeting notes" in seen[0][0]["content"]


def test_harness_stops_at_max_turns(ws):
    loop_msg = {"role": "assistant", "content": "", "tool_calls": [call("x", "Bash", {"command": "true"})]}
    chat, _ = scripted(*[loop_msg] * 5)
    req = AgentRequest(workspace=ws, prompt="spin", model="m", skills=[], max_turns=3)
    out = asyncio.run(harness.run(req, chat))
    assert out.result["subtype"] == "error_max_turns" and out.result["num_turns"] == 3
