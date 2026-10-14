"""Any model on OpenRouter (or another OpenAI-compatible endpoint via OPENROUTER_BASE_URL).

Structured calls use `response_format: json_schema` routed only to endpoints that
support it, and fall back to "reply with JSON" plus validation when a model can't.
Agent runs use skillgraph's own harness (see harness.py).
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx

from skillgraph.providers import harness
from skillgraph.providers.base import AgentRequest, AgentResult, LLMError, StructuredResult, extract_json, validate

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
RETRY_DELAY = 2.0
# Tests swap in an httpx.MockTransport here.
TRANSPORT: httpx.AsyncBaseTransport | None = None


def base_url() -> str:
    return os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise LLMError("OPENROUTER_API_KEY is not set. Create a key at https://openrouter.ai/keys and export it.")
    return key


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
        "X-Title": "skillgraph",
    }


async def chat(body: dict, *, attempts: int = 4, timeout: float = 300.0) -> dict:
    """POST /chat/completions with retries on rate limits and transient errors."""
    body = {**body, "usage": {"include": True}}  # cost per call (always returned now; older behavior needed this)
    delay = RETRY_DELAY
    last = ""
    async with httpx.AsyncClient(timeout=timeout, transport=TRANSPORT) as client:
        for attempt in range(attempts):
            try:
                resp = await client.post(f"{base_url()}/chat/completions", headers=_headers(), json=body)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data and not data.get("choices"):
                        last = f"provider error: {json.dumps(data['error'])[:400]}"
                    else:
                        return data
                elif resp.status_code in RETRY_STATUS:
                    last = f"HTTP {resp.status_code}: {resp.text[:400]}"
                else:
                    raise LLMError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:600]}")
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
                delay *= 2
    raise LLMError(f"OpenRouter request failed after {attempts} attempts: {last}")


def _reasoning(effort: str | None) -> dict:
    return {"reasoning": {"effort": effort}} if effort else {}


async def structured(prompt: str, schema: dict, *, system: str, model: str, effort: str | None = None) -> StructuredResult:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    cost = 0.0
    problems: list[str] = []

    # 1) Native structured outputs, only on endpoints that support them.
    try:
        data = await chat({
            "model": model, "messages": messages, **_reasoning(effort),
            "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}},
            "provider": {"require_parameters": True},
        })
        cost += float((data.get("usage") or {}).get("cost") or 0.0)
        parsed = extract_json(data["choices"][0]["message"].get("content") or "")
        problems = validate(parsed, schema)
        if not problems:
            return StructuredResult(parsed, cost, f"openrouter:{model}")
    except (LLMError, ValueError, KeyError, IndexError) as exc:
        problems = [str(exc)[:300]]

    # 2) Plain JSON reply for models without structured-output support.
    fallback = [
        {"role": "system", "content": f"{system}\n\nReply with only a JSON object that matches this JSON Schema, "
                                      f"with no prose and no code fences:\n{json.dumps(schema)}"},
        {"role": "user", "content": prompt},
    ]
    for _ in range(2):
        data = await chat({"model": model, "messages": fallback, **_reasoning(effort)})
        cost += float((data.get("usage") or {}).get("cost") or 0.0)
        reply = data["choices"][0]["message"].get("content") or ""
        try:
            parsed = extract_json(reply)
            problems = validate(parsed, schema)
        except ValueError as exc:
            problems = [str(exc)]
        if not problems:
            return StructuredResult(parsed, cost, f"openrouter:{model}")
        fallback += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": "That JSON doesn't match the schema: " + "; ".join(problems[:8]) + ". Reply again with corrected JSON only."}]
    raise LLMError(f"{model} did not return valid JSON: {'; '.join(problems[:5])}")


async def run_agent(req: AgentRequest) -> AgentResult:
    async def call(messages: list[dict], tools: list[dict]) -> dict:
        return await chat({
            "model": req.model, "messages": messages, "tools": tools, "tool_choice": "auto",
            "provider": {"require_parameters": True}, **_reasoning(req.effort),
        })

    try:
        api_key()
    except LLMError as exc:
        return AgentResult(error=str(exc), result={"subtype": "error", "is_error": True, "num_turns": 0,
                                                   "duration_ms": 0, "cost_usd": 0.0, "text": ""})
    return await harness.run(req, call)


async def list_models() -> list[dict]:
    """Public model catalog: id, context length, pricing, supported parameters."""
    async with httpx.AsyncClient(timeout=60, transport=TRANSPORT) as client:
        resp = await client.get(f"{base_url()}/models")
        resp.raise_for_status()
        return resp.json().get("data", [])
