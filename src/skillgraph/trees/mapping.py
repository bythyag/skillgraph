"""Small helpers for reading arbitrary trace JSON: path lookup, filters, value coercion,
and declarative mappings for formats without a built-in importer.

Path syntax: dot-separated keys, `[0]` indexes, `[*]` to map over a list, and
`["dotted.key"]` for keys that contain dots. Examples:

    span_data.name
    inputs.messages[0].content
    attributes["gen_ai.tool.name"]
    outputs.generations[*][0].text
"""

from __future__ import annotations

import json
import re
from typing import Any

_TOKEN = re.compile(r'\["((?:[^"\\]|\\.)*)"\]|\[(-?\d+|\*)\]|([^.\[\]]+)')


def _segments(path: str) -> list[str | int]:
    segs: list[str | int] = []
    for quoted, index, key in _TOKEN.findall(path or ""):
        if quoted:
            segs.append(quoted)
        elif index:
            segs.append("*" if index == "*" else int(index))
        elif key:
            segs.append(key)
    return segs


def get_path(obj: Any, path: str) -> Any:
    """Resolve a path; returns None when any segment is missing. `[*]` yields a list."""
    if not path:
        return obj
    cur: list[Any] = [obj]
    spread = False
    for seg in _segments(path):
        nxt: list[Any] = []
        for value in cur:
            value = parse_json_string(value)
            if seg == "*":
                if isinstance(value, list):
                    nxt.extend(value)
                    spread = True
            elif isinstance(seg, int):
                if isinstance(value, list) and -len(value) <= seg < len(value):
                    nxt.append(value[seg])
            elif isinstance(value, dict) and seg in value:
                nxt.append(value[seg])
        cur = nxt
        if not cur:
            return None
    return cur if spread else cur[0]


def parse_json_string(value: Any) -> Any:
    """Decode strings that hold JSON objects or arrays (common for tool arguments)."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in "{[" and s[-1:] in "}]":
            try:
                return json.loads(s)
            except ValueError:
                return value
    return value


def matches(record: Any, when: dict | None) -> bool:
    """`when` maps paths to an expected value, a list of allowed values, or "*" (present)."""
    for path, expected in (when or {}).items():
        actual = get_path(record, path)
        if expected == "*":
            if actual is None:
                return False
            continue
        allowed = expected if isinstance(expected, list) else [expected]
        if actual is None or str(actual).lower() not in {str(a).lower() for a in allowed}:
            return False
    return True


def as_text(value: Any) -> str:
    """Best-effort readable text from strings, content-block lists, or message objects."""
    value = parse_json_string(value)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [as_text(v) for v in value]
        return "\n".join(p for p in parts if p)
    if isinstance(value, dict):
        for key in ("text", "content", "output", "result", "message", "value"):
            if key in value and value[key] not in (None, ""):
                return as_text(value[key])
        if "parts" in value:
            return as_text(value["parts"])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def as_input(value: Any) -> dict:
    """Tool input as a dict: decode JSON strings, wrap scalars."""
    value = parse_json_string(value)
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {"input": value}


MAPPING_KEYS = {"format", "records", "children", "group_by", "order_by", "id", "parent", "session",
                "tool_call", "text", "prompt", "model", "tools", "name"}


def validate_mapping(spec: dict) -> list[str]:
    problems = []
    unknown = set(spec) - MAPPING_KEYS
    if unknown:
        problems.append(f"unknown keys: {sorted(unknown)}")
    if spec.get("format", "json") not in ("json", "jsonl"):
        problems.append("format must be json or jsonl")
    tc = spec.get("tool_call")
    if not isinstance(tc, dict) or not tc.get("name"):
        problems.append("tool_call.name is required (path to the tool name)")
    for section in ("tool_call", "text", "prompt"):
        block = spec.get(section)
        if block is not None and not isinstance(block, dict):
            problems.append(f"{section} must be a mapping")
        elif isinstance(block, dict) and "when" in block and not isinstance(block["when"], dict):
            problems.append(f"{section}.when must map paths to values")
    return problems
