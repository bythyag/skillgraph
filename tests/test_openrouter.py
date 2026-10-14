import asyncio
import json

import httpx
import pytest

from skillgraph import providers
from skillgraph.providers import AgentRequest, LLMError, openrouter

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


def reply(content=None, tool_calls=None, cost=0.001):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"cost": cost}})


@pytest.fixture
def mock(monkeypatch):
    """Route OpenRouter calls to a scripted handler; returns the list of request bodies."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(openrouter, "RETRY_DELAY", 0)
    requests: list[dict] = []

    def install(handler):
        def wrapped(request: httpx.Request):
            assert request.headers["authorization"] == "Bearer test-key"
            body = json.loads(request.content)
            requests.append(body)
            return handler(body, len(requests))
        monkeypatch.setattr(openrouter, "TRANSPORT", httpx.MockTransport(wrapped))
        return requests

    return install


def test_structured_native(mock):
    reqs = mock(lambda body, n: reply('{"ok": true}'))
    res = asyncio.run(providers.structured("q", SCHEMA, system="s", model="openrouter:vendor/model", effort="low"))
    assert res.data == {"ok": True} and res.model == "openrouter:vendor/model"
    body = reqs[0]
    assert body["model"] == "vendor/model" and body["reasoning"] == {"effort": "low"}
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["provider"] == {"require_parameters": True}


def test_structured_falls_back_and_repairs(mock):
    def handler(body, n):
        if n == 1:
            return httpx.Response(404, text='{"error": "No endpoints support response_format"}')
        if n == 2:
            return reply('Here you go: {"ok": "yes"}')
        return reply('{"ok": false}')
    reqs = mock(handler)
    res = asyncio.run(providers.structured("q", SCHEMA, system="s", model="openrouter:vendor/model"))
    assert res.data == {"ok": False}
    assert "response_format" not in reqs[1] and "JSON Schema" in reqs[1]["messages"][0]["content"]
    assert "doesn't match the schema" in reqs[2]["messages"][-1]["content"]


def test_retries_rate_limits_then_gives_up(mock):
    reqs = mock(lambda body, n: httpx.Response(429, text="slow down"))
    with pytest.raises(LLMError, match="after 4 attempts"):
        asyncio.run(openrouter.chat({"model": "m", "messages": []}))
    assert len(reqs) == 4


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        asyncio.run(providers.structured("q", SCHEMA, system="s", model="openrouter:a/b"))
    out = asyncio.run(openrouter.run_agent(AgentRequest(workspace=None, prompt="x", model="a/b", skills=[])))
    assert "OPENROUTER_API_KEY" in out.error


def test_agent_run_end_to_end(mock, tmp_path):
    def handler(body, n):
        assert body["tools"] and body["tool_choice"] == "auto"
        if n == 1:
            return reply("Creating it.", [{"id": "1", "type": "function",
                                           "function": {"name": "Write", "arguments": json.dumps({"file_path": "hello.txt", "content": "hi"})}}])
        return reply("Created hello.txt.")
    mock(handler)
    out = asyncio.run(providers.run_agent(AgentRequest(workspace=tmp_path, prompt="make hello.txt", model="", skills=[]),
                                          "openrouter:vendor/model"))
    assert (tmp_path / "hello.txt").read_text() == "hi"
    assert out.init["harness"] == "skillgraph" and out.result["text"] == "Created hello.txt."
    assert [e.get("tool") for e in out.events if e["kind"] == "tool"] == ["Write"]
