"""Types shared by every model provider."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROVIDERS = ("claude", "openrouter")


class LLMError(RuntimeError):
    """A model call failed after retries (bad credentials, no route, unparseable output)."""


@dataclass(frozen=True)
class ModelRef:
    """A model on a provider, written `provider:model` (e.g. `openrouter:openai/gpt-6.1-sol`)."""

    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def parse_model(spec: str | ModelRef) -> ModelRef:
    """Parse `provider:model`. Without a known prefix, `claude-*` ids go to Claude and
    `vendor/model` ids go to OpenRouter."""
    if isinstance(spec, ModelRef):
        return spec
    spec = spec.strip()
    head, sep, rest = spec.partition(":")
    if sep and head in PROVIDERS:
        if not rest:
            raise ValueError(f"model spec {spec!r} names a provider but no model")
        return ModelRef(head, rest)
    if "/" in spec:
        return ModelRef("openrouter", spec)
    return ModelRef("claude", spec)


@dataclass
class StructuredResult:
    data: Any
    cost_usd: float
    model: str


# Called before each tool call; returns a reason to block it, or None to allow it.
DenyFn = Callable[[str, dict], "str | None"]


@dataclass
class AgentRequest:
    workspace: Path
    prompt: str
    model: str
    skills: list[str]  # skills the agent may load ([] = none installed)
    invoke: str | None = None  # load this skill up front, like typing /name
    effort: str | None = None
    max_turns: int = 40
    budget_usd: float = 3.0
    deny: DenyFn | None = None


@dataclass
class AgentResult:
    """Provider-neutral record of one agent run.

    events: [{"i", "kind": "tool", "tool", "input", "output", "is_error"} |
             {"i", "kind": "text" | "user", "text"}]
    result: {"subtype", "is_error", "num_turns", "duration_ms", "cost_usd", "text"}
    """

    events: list[dict] = field(default_factory=list)
    init: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    error: str | None = None
    blocked: list[dict] = field(default_factory=list)


MAX_TEXT = 4000


def clip(value, limit: int = MAX_TEXT):
    """Truncate long strings anywhere inside a JSON-like value."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"... [{len(value) - limit} more chars]"
    if isinstance(value, dict):
        return {k: clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [clip(v, limit) for v in value]
    return value


# ---------------------------------------------------------------- JSON helpers

def extract_json(text: str) -> Any:
    """Parse the first JSON object in a model reply, tolerating code fences and prose."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def validate(data: Any, schema: dict, path: str = "$") -> list[str]:
    """Check data against the JSON Schema subset skillgraph uses (object, array,
    string, integer, boolean, enum, required). Returns a list of problems."""
    problems: list[str] = []
    kind = schema.get("type")
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
    }
    if kind in checks and not checks[kind](data):
        return [f"{path}: expected {kind}, got {type(data).__name__}"]
    if "enum" in schema and data not in schema["enum"]:
        problems.append(f"{path}: {data!r} not in {schema['enum']}")
    if kind == "object":
        for key in schema.get("required", []):
            if key not in data:
                problems.append(f"{path}: missing {key!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in data:
                problems.extend(validate(data[key], sub, f"{path}.{key}"))
    elif kind == "array" and "items" in schema:
        for i, item in enumerate(data):
            problems.extend(validate(item, schema["items"], f"{path}[{i}]"))
    return problems
