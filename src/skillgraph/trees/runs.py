"""Import run trees (agent traces from other tools) as skillgraph traces.

Built-in formats:
    claude-code     Claude Code session transcripts (~/.claude/projects/<project>/<session>.jsonl),
                    including subagent transcripts in <session>/subagents/
    otel            OpenTelemetry spans (OTLP JSON or a list of spans) with GenAI or
                    OpenInference attributes
    langsmith       LangSmith runs (a list, JSONL, or nested child_runs)
    openai-agents   OpenAI Agents SDK spans (span_data.type = function / generation)
    skillgraph      traces written by `skillgraph run`
    <file>.yaml     a declarative mapping for anything else (see mapping.py)

Every format becomes the same trace.json the runner writes, so `check` and `report`
work unchanged. Tool names are mapped onto the names claims use (Bash, Read, Write,
Edit, Glob, Grep, Skill); the original name is kept as `raw_tool`. Tree structure is
kept per event as `parent`, `depth` and `agent`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from skillgraph.providers.base import clip
from skillgraph.trees.mapping import as_input, as_text, get_path, matches, validate_mapping

FORMATS = ("claude-code", "otel", "langsmith", "openai-agents", "skillgraph")

# Canonical tool name -> other names for the same capability (compared case-insensitively).
TOOL_ALIASES = {
    "Bash": ["bash", "shell", "sh", "run_shell_command", "execute_command", "run_command", "terminal", "exec",
             "run_terminal_cmd", "local_shell", "execute_bash", "command", "powershell"],
    "Read": ["read", "read_file", "readfile", "view", "view_file", "open_file", "cat", "get_file_contents",
             "read_text_file"],
    "Write": ["write", "write_file", "writefile", "create_file", "save_file", "write_to_file"],
    "Edit": ["edit", "edit_file", "multiedit", "str_replace", "replace", "replace_in_file", "apply_patch",
             "patch", "apply_diff", "search_replace"],
    "Glob": ["glob", "list_files", "find_files", "list_dir", "list_directory", "ls", "file_search"],
    "Grep": ["grep", "search", "search_files", "ripgrep", "rg", "grep_search", "codebase_search", "find_in_files"],
    "Skill": ["skill", "load_skill", "use_skill", "activate_skill"],
}
# Anthropic's text editor tool does several things, picked by its `command` field.
EDITOR_TOOLS = {"str_replace_editor", "str_replace_based_edit_tool", "text_editor"}
EDITOR_COMMANDS = {"view": "Read", "create": "Write", "str_replace": "Edit", "insert": "Edit", "undo_edit": "Edit"}


@dataclass
class RawEvent:
    kind: str  # tool | text | user
    order: int = 0
    ts: float | None = None
    id: str | None = None
    parent: str | None = None
    depth: int = 0
    agent: str | None = None
    tool: str = ""
    input: Any = None
    output: Any = None
    is_error: bool = False
    text: str = ""
    meta: bool = False  # injected context (e.g. a loaded skill), not typed by a person


@dataclass
class RawTrace:
    label: str
    source: str
    format: str
    events: list[RawEvent] = field(default_factory=list)
    prompt: str = ""
    model: str = ""
    cwd: str | None = None
    final_text: str = ""
    cost_usd: float | None = None
    skill_loaded: bool | None = None  # known from the source (skillgraph traces); else detected from events
    variant: dict | None = None       # kept when re-importing skillgraph traces


# ---------------------------------------------------------------- small helpers

def _ts(value: Any) -> float | None:
    """Seconds since the epoch from epoch numbers (s, ms, µs, ns) or ISO 8601 strings."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1e9 if v > 1e17 else v / 1e6 if v > 1e14 else v / 1e3 if v > 1e11 else v
    s = str(value).strip()
    if s.isdigit():
        return _ts(int(s))
    # Python 3.10's fromisoformat is strict: normalize Z, +0000, and fractions of any length.
    s = re.sub(r"[Zz]$", "+00:00", s)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    s = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], s, count=1)
    try:
        return datetime.fromisoformat(s.replace(" ", "T", 1) if "T" not in s else s).timestamp()
    except ValueError:
        return None


_REMINDER = re.compile(r"\s*<system-reminder>.*?</system-reminder>\s*", re.S)


def _strip_reminders(text: str) -> str:
    """Claude Code appends <system-reminder> blocks to tool results; they aren't tool output."""
    return _REMINDER.sub("\n", text).strip() if "<system-reminder>" in text else text


def _read_records(path: Path) -> list[Any]:
    text = path.read_text()
    if path.suffix == ".jsonl" or (text.lstrip()[:1] == "{" and "\n{" in text and not _is_json(text)):
        out = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- format detection

def detect_format(path: Path) -> str:
    if path.is_dir():
        if any(path.glob("*.jsonl")):
            return "claude-code"
        raise ValueError(f"can't detect a trace format for directory {path}")
    records = _read_records(path)[:200]
    for r in records:
        if not isinstance(r, dict):
            continue
        if "resourceSpans" in r or "traceId" in r or "spanId" in r or "trace_id" in r and "span_id" in r:
            return "otel"
        if r.get("type") in ("user", "assistant") and "message" in r and ("uuid" in r or "sessionId" in r):
            return "claude-code"
        if "span_data" in r or (isinstance(r.get("data"), list) and r["data"] and isinstance(r["data"][0], dict)
                                and "span_data" in r["data"][0]):
            return "openai-agents"
        if "run_type" in r or (isinstance(r.get("runs"), list)):
            return "langsmith"
        if "events" in r and "variant" in r and "task_prompt" in r:
            return "skillgraph"
        if isinstance(r.get("spans"), list):
            return "otel"
    raise ValueError(f"can't detect the trace format of {path}; pass --format or a mapping file")


def expand_sources(sources: Iterable[str | Path]) -> list[Path]:
    """Directories of Claude Code transcripts expand to their *.jsonl sessions."""
    out = []
    for s in sources:
        p = Path(s).expanduser()
        if p.is_dir() and any(p.glob("*.jsonl")):
            out.extend(sorted(p.glob("*.jsonl")))
        else:
            out.append(p)
    return out


# ---------------------------------------------------------------- claude code

def _cc_entries(path: Path, agent: str | None, depth: int, start_order: int) -> tuple[list[RawEvent], dict]:
    events: list[RawEvent] = []
    info: dict = {"prompt": "", "model": "", "cwd": None, "final": ""}
    order = start_order
    for entry in _read_records(path):
        if not isinstance(entry, dict) or entry.get("type") not in ("user", "assistant"):
            continue
        info["cwd"] = info["cwd"] or entry.get("cwd")
        ts = _ts(entry.get("timestamp"))
        msg = entry.get("message") or {}
        content = msg.get("content")
        is_meta = bool(entry.get("isMeta"))
        if entry["type"] == "assistant":
            info["model"] = info["model"] or msg.get("model", "")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    events.append(RawEvent("text", order, ts, agent=agent, depth=depth, text=block["text"]))
                    info["final"] = block["text"]
                elif block.get("type") == "tool_use":
                    events.append(RawEvent("tool", order, ts, id=block.get("id"), agent=agent, depth=depth,
                                           tool=block.get("name", ""), input=block.get("input") or {}))
                order += 1
        else:
            if isinstance(content, str):
                if content.strip():
                    events.append(RawEvent("user", order, ts, agent=agent, depth=depth, text=content, meta=is_meta))
                    if not is_meta and not info["prompt"] and not content.startswith("<"):
                        info["prompt"] = content
                order += 1
                continue
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    for ev in reversed(events):
                        if ev.kind == "tool" and ev.id == block.get("tool_use_id"):
                            ev.output = _strip_reminders(as_text(block.get("content")))
                            ev.is_error = bool(block.get("is_error"))
                            break
                elif block.get("type") == "text" and block.get("text", "").strip():
                    text = block["text"]
                    events.append(RawEvent("user", order, ts, agent=agent, depth=depth, text=text,
                                           meta=is_meta or text.startswith("Base directory for this skill")))
                    if not is_meta and not info["prompt"] and not text.startswith(("<", "Base directory")):
                        info["prompt"] = text
                order += 1
    return events, info


def import_claude_code(path: Path) -> list[RawTrace]:
    events, info = _cc_entries(path, None, 0, 0)
    # Subagents live next to the session: <session>/subagents/agent-<id>.jsonl
    sub_dir = path.with_suffix("") / "subagents"
    order = len(events) + 1
    for sub in sorted(sub_dir.glob("agent-*.jsonl")) if sub_dir.is_dir() else []:
        agent_id = sub.stem.removeprefix("agent-")
        parent = next((e.id for e in events if e.kind == "tool" and agent_id in str(e.output or "")), None)
        sub_events, _ = _cc_entries(sub, agent_id, 1, order)
        for e in sub_events:
            e.parent = e.parent or parent
        events.extend(sub_events)
        order += len(sub_events) + 1
    return [RawTrace(label=path.stem, source=str(path), format="claude-code", events=events,
                     prompt=info["prompt"], model=info["model"], cwd=info["cwd"], final_text=info["final"])]


# ---------------------------------------------------------------- opentelemetry

def _otel_value(v: Any) -> Any:
    if not isinstance(v, dict):
        return v
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in v:
            return v[key]
    if "arrayValue" in v:
        return [_otel_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _otel_value(kv.get("value")) for kv in v["kvlistValue"].get("values", [])}
    return v


def _otel_attrs(span: dict) -> dict:
    attrs = span.get("attributes") or {}
    if isinstance(attrs, list):
        return {a.get("key"): _otel_value(a.get("value")) for a in attrs if isinstance(a, dict)}
    return dict(attrs)


def _otel_spans(records: list[Any]) -> list[dict]:
    spans = []
    for r in records:
        if not isinstance(r, dict):
            continue
        if "resourceSpans" in r:
            for rs in r["resourceSpans"] or []:
                for ss in rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []:
                    spans.extend(ss.get("spans") or [])
        elif isinstance(r.get("spans"), list):
            spans.extend(r["spans"])
        elif "spanId" in r or "span_id" in r:
            spans.append(r)
    return spans


def _otel_span_id(s: dict):
    return s.get("spanId") or s.get("span_id") or (s.get("context") or {}).get("span_id")


def _otel_parent_id(s: dict):
    return s.get("parentSpanId") or s.get("parent_span_id") or s.get("parent_id")


def _chain_depth(item: dict, ids: dict, own_id, parent_id, limit: int = 50) -> int:
    """How many ancestors `item` has in `ids`; stops on cycles and at `limit`."""
    depth, seen, cur = 0, {own_id(item)}, item
    while depth < limit:
        parent = ids.get(parent_id(cur))
        if parent is None or own_id(parent) in seen:
            break
        seen.add(own_id(parent))
        cur, depth = parent, depth + 1
    return depth


def import_otel(path: Path) -> list[RawTrace]:
    by_trace: dict[str, list[dict]] = {}
    for s in _otel_spans(_read_records(path)):
        tid = s.get("traceId") or s.get("trace_id") or (s.get("context") or {}).get("trace_id") or "trace"
        by_trace.setdefault(tid, []).append(s)
    traces = []
    for tid, spans in by_trace.items():
        sid, pid = _otel_span_id, _otel_parent_id
        ids = {sid(s): s for s in spans}
        depths = {sid(s): _chain_depth(s, ids, sid, pid) for s in spans}

        def depth(s, depths=depths):
            return depths[_otel_span_id(s)]

        events, prompt, model, final = [], "", "", ""
        roots = [s for s in spans if pid(s) not in ids]
        for root in roots:
            a = _otel_attrs(root)
            prompt = prompt or as_text(a.get("input.value") or a.get("gen_ai.input.messages") or "")
            final = final or as_text(a.get("output.value") or a.get("gen_ai.output.messages") or "")
        for n, s in enumerate(spans):
            a = _otel_attrs(s)
            model = model or a.get("gen_ai.request.model") or a.get("llm.model_name") or ""
            kind = str(a.get("openinference.span.kind", "")).upper()
            op = a.get("gen_ai.operation.name", "")
            ts = _ts(s.get("startTimeUnixNano") or s.get("start_time") or s.get("startTime"))
            status = s.get("status") or {}
            is_error = str(status.get("code", "")).upper() in ("2", "STATUS_CODE_ERROR", "ERROR")
            common = dict(order=n, ts=ts, id=sid(s), parent=pid(s), depth=depth(s), agent=a.get("gen_ai.agent.name"))
            if op == "execute_tool" or kind == "TOOL" or "gen_ai.tool.name" in a:
                name = a.get("gen_ai.tool.name") or a.get("tool.name") or re.sub(r"^execute_tool\s*", "", s.get("name", ""))
                args = a.get("gen_ai.tool.call.arguments") or a.get("input.value") or a.get("tool_call.function.arguments")
                result = a.get("gen_ai.tool.call.result") or a.get("output.value")
                events.append(RawEvent("tool", tool=name, input=as_input(args), output=as_text(result),
                                       is_error=is_error, **common))
            elif kind == "LLM" or op in ("chat", "text_completion", "generate_content"):
                text = as_text(a.get("gen_ai.output.messages") or a.get("output.value") or "")
                if text.strip():
                    events.append(RawEvent("text", text=text, **common))
        traces.append(RawTrace(label=str(tid)[:16], source=str(path), format="otel", events=events,
                               prompt=prompt, model=str(model), final_text=final))
    return traces


# ---------------------------------------------------------------- langsmith

def _flatten_runs(runs: list[dict], parent: str | None = None) -> list[dict]:
    out = []
    for r in runs:
        r = dict(r)
        if parent and not r.get("parent_run_id"):
            r["parent_run_id"] = parent
        children = r.pop("child_runs", None) or []
        out.append(r)
        out.extend(_flatten_runs(children, str(r.get("id"))))
    return out


def import_langsmith(path: Path) -> list[RawTrace]:
    records = []
    for r in _read_records(path):
        if isinstance(r, dict) and isinstance(r.get("runs"), list):
            records.extend(r["runs"])
        elif isinstance(r, dict):
            records.append(r)
    runs = _flatten_runs(records)
    by_trace: dict[str, list[dict]] = {}
    for r in runs:
        by_trace.setdefault(str(r.get("trace_id") or "trace"), []).append(r)
    traces = []
    for tid, rs in by_trace.items():
        ids = {str(r.get("id")): r for r in rs}
        rs.sort(key=lambda r: (str(r.get("dotted_order") or ""), _ts(r.get("start_time")) or 0))
        run_id, parent_id = (lambda r: str(r.get("id"))), (lambda r: str(r.get("parent_run_id")))

        def depth(r, ids=ids, run_id=run_id, parent_id=parent_id):
            return _chain_depth(r, ids, run_id, parent_id)

        events, model = [], ""
        root = next((r for r in rs if str(r.get("parent_run_id")) not in ids), rs[0] if rs else {})
        for n, r in enumerate(rs):
            common = dict(order=n, ts=_ts(r.get("start_time")), id=str(r.get("id")),
                          parent=str(r.get("parent_run_id")) if r.get("parent_run_id") else None, depth=depth(r))
            outputs = r.get("outputs") or {}
            if r.get("run_type") == "tool":
                inputs = r.get("inputs") or {}
                inp = inputs.get("input", inputs) if isinstance(inputs, dict) and len(inputs) == 1 else inputs
                out = outputs.get("output", outputs) if isinstance(outputs, dict) and "output" in outputs else outputs
                events.append(RawEvent("tool", tool=r.get("name", ""), input=as_input(inp), output=as_text(out),
                                       is_error=bool(r.get("error")), **common))
            elif r.get("run_type") == "llm":
                model = model or str(get_path(r, "extra.invocation_params.model") or get_path(r, "extra.metadata.ls_model_name") or "")
                gen = get_path(outputs, "generations[0][0].text") or get_path(outputs, "generations[0][0].message.kwargs.content")
                text = as_text(gen if gen is not None else outputs)
                if text.strip():
                    events.append(RawEvent("text", text=text, **common))
        traces.append(RawTrace(label=str(tid)[:16], source=str(path), format="langsmith", events=events,
                               prompt=as_text((root.get("inputs") or {}).get("input") or root.get("inputs") or ""),
                               model=model, final_text=as_text((root.get("outputs") or {}).get("output") or "")))
    return traces


# ---------------------------------------------------------------- openai agents sdk

def import_openai_agents(path: Path) -> list[RawTrace]:
    spans = []
    for r in _read_records(path):
        if isinstance(r, dict) and isinstance(r.get("data"), list):
            spans.extend(x for x in r["data"] if isinstance(x, dict))
        elif isinstance(r, dict) and "span_data" in r:
            spans.append(r)
    by_trace: dict[str, list[dict]] = {}
    for s in spans:
        by_trace.setdefault(str(s.get("trace_id") or "trace"), []).append(s)
    traces = []
    for tid, ss in by_trace.items():
        ids = {s.get("id"): s for s in ss}
        ss.sort(key=lambda s: _ts(s.get("started_at")) or 0)

        def depth(s, ids=ids):
            return _chain_depth(s, ids, lambda x: x.get("id"), lambda x: x.get("parent_id"))

        events, model, final, prompt = [], "", "", ""
        for n, s in enumerate(ss):
            d = s.get("span_data") or {}
            common = dict(order=n, ts=_ts(s.get("started_at")), id=s.get("id"), parent=s.get("parent_id"), depth=depth(s),
                          agent=None)
            if d.get("type") == "function":
                events.append(RawEvent("tool", tool=d.get("name", ""), input=as_input(d.get("input")),
                                       output=as_text(d.get("output")), is_error=bool(s.get("error")), **common))
            elif d.get("type") in ("generation", "response"):
                model = model or str(d.get("model") or get_path(d, "response.model") or "")
                text = as_text(d.get("output") if d.get("type") == "generation" else get_path(d, "response.output_text"))
                if text.strip():
                    events.append(RawEvent("text", text=text, **common))
                    final = text
                prompt = prompt or as_text(get_path(d, "input[0].content") or "")
        traces.append(RawTrace(label=str(tid)[:16], source=str(path), format="openai-agents", events=events,
                               prompt=prompt, model=model, final_text=final))
    return traces


# ---------------------------------------------------------------- skillgraph passthrough

def import_skillgraph(path: Path) -> list[RawTrace]:
    t = json.loads(path.read_text())
    events = [RawEvent(e["kind"], order=e["i"], id=e.get("id"), parent=e.get("parent"), depth=e.get("depth", 0),
                       agent=e.get("agent"), tool=e.get("tool", ""), input=e.get("input"), output=e.get("output"),
                       is_error=bool(e.get("is_error")), text=e.get("text", "")) for e in t["events"]]
    return [RawTrace(label=f"{t['task']}-{t['variant']['name']}-r{t['rep']}", source=str(path), format="skillgraph",
                     events=events, prompt=t.get("task_prompt", ""), model=t.get("model", ""),
                     final_text=(t.get("result") or {}).get("text", ""), cost_usd=(t.get("result") or {}).get("cost_usd"),
                     skill_loaded=t.get("skill_loaded"), variant=t.get("variant"))]


# ---------------------------------------------------------------- declarative mapping

def import_with_mapping(path: Path, spec: dict) -> list[RawTrace]:
    problems = validate_mapping(spec)
    if problems:
        raise ValueError("invalid mapping: " + "; ".join(problems))
    if spec.get("format") == "jsonl" or path.suffix == ".jsonl":
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    else:
        doc = json.loads(path.read_text())
        found = get_path(doc, spec["records"]) if spec.get("records") else doc
        records = found if isinstance(found, list) else [found]
    if spec.get("children"):
        def walk(rs, parent=None, depth=0):
            for r in rs:
                if not isinstance(r, dict):
                    continue
                yield r, parent, depth
                yield from walk(get_path(r, spec["children"]) or [], get_path(r, spec.get("id", "id")), depth + 1)
        flat = list(walk(records))
    else:
        flat = [(r, None, 0) for r in records if isinstance(r, dict)]

    groups: dict[str, list] = {}
    for r, parent, d in flat:
        key = str(get_path(r, spec["group_by"])) if spec.get("group_by") else "trace"
        groups.setdefault(key, []).append((r, parent, d))
    tc, tx, pr = spec["tool_call"], spec.get("text"), spec.get("prompt")
    traces = []
    for key, items in groups.items():
        if spec.get("order_by"):
            items.sort(key=lambda it: (_ts(get_path(it[0], spec["order_by"])) or 0))
        events, prompt = [], ""
        for n, (r, parent, d) in enumerate(items):
            common = dict(order=n, ts=_ts(get_path(r, spec["order_by"])) if spec.get("order_by") else None,
                          id=str(get_path(r, spec.get("id", "id"))), depth=d,
                          parent=str(get_path(r, spec["parent"])) if spec.get("parent") and get_path(r, spec["parent"]) else parent)
            if matches(r, tc.get("when")) and get_path(r, tc["name"]):
                events.append(RawEvent("tool", tool=str(get_path(r, tc["name"])), input=as_input(get_path(r, tc.get("input", ""))),
                                       output=as_text(get_path(r, tc["output"])) if tc.get("output") else "",
                                       is_error=bool(get_path(r, tc["error"])) if tc.get("error") else False, **common))
            elif tx and matches(r, tx.get("when")):
                text = as_text(get_path(r, tx.get("value", "")))
                if text.strip():
                    events.append(RawEvent("text", text=text, **common))
            if pr and not prompt and matches(r, pr.get("when")):
                prompt = as_text(get_path(r, pr.get("value", "")))
        traces.append(RawTrace(label=key[:16], source=str(path), format=spec.get("name", "mapping"), events=events,
                               prompt=prompt))
    return traces


IMPORTERS = {
    "claude-code": import_claude_code,
    "otel": import_otel,
    "langsmith": import_langsmith,
    "openai-agents": import_openai_agents,
    "skillgraph": import_skillgraph,
}


def load_raw(path: Path, fmt: str = "auto") -> tuple[list[RawTrace], dict]:
    """Returns (traces, extra tool aliases from a mapping file)."""
    if fmt.endswith((".yaml", ".yml", ".json")) and Path(fmt).exists():
        spec = yaml.safe_load(Path(fmt).read_text())
        return import_with_mapping(path, spec), dict(spec.get("tools") or {})
    fmt = detect_format(path) if fmt == "auto" else fmt
    if fmt not in IMPORTERS:
        raise ValueError(f"unknown format {fmt!r}; use one of {', '.join(FORMATS)} or a mapping .yaml")
    return IMPORTERS[fmt](path), {}


# ---------------------------------------------------------------- normalization

def build_alias_map(extra: dict | None = None) -> dict[str, str]:
    table = {alias.lower(): canon for canon, aliases in TOOL_ALIASES.items() for alias in aliases}
    table.update({canon.lower(): canon for canon in TOOL_ALIASES})
    for raw, canon in (extra or {}).items():
        table[str(raw).lower()] = canon
    return table


def canonical_tool(name: str, inp: dict, aliases: dict[str, str]) -> str:
    base = name.split("__")[-1] if name.startswith("mcp__") else name
    if base.lower() in EDITOR_TOOLS:
        return EDITOR_COMMANDS.get(str(inp.get("command", "")).lower(), "Edit")
    return aliases.get(base.lower(), name)


def normalize_input(tool: str, inp: dict) -> dict:
    """Add the field names claims expect (command, file_path) without dropping originals."""
    inp = dict(inp)
    if tool == "Bash" and "command" not in inp:
        for key in ("cmd", "script", "code", "commands", "input", "args"):
            if key in inp:
                v = inp[key]
                inp["command"] = " ".join(map(str, v)) if isinstance(v, list) else str(v)
                break
    if tool in ("Read", "Write", "Edit") and "file_path" not in inp:
        for key in ("path", "file", "filename", "filepath", "target_file", "file_name"):
            if key in inp:
                inp["file_path"] = str(inp[key])
                break
    if tool == "Write" and "content" not in inp:
        for key in ("text", "contents", "file_text", "data"):
            if key in inp:
                inp["content"] = inp[key]
                break
    if tool == "Edit" and "old_string" not in inp:
        if "old_str" in inp:
            inp["old_string"], inp["new_string"] = inp.get("old_str"), inp.get("new_str", "")
    return inp


def _skill_load_index(events: list[dict], skill: str) -> int | None:
    pattern = re.compile(rf"(^|[/\\\s<]){re.escape(skill)}([/\\\s>]|$)")
    for ev in events:
        if ev["kind"] == "tool":
            blob = json.dumps(ev["input"], ensure_ascii=False)
            if ev["tool"] == "Skill" and skill in blob:
                return ev["i"]
            # Frameworks without a Skill tool load skills by reading SKILL.md; writing or
            # editing the file is authoring it, not loading it.
            reads = ev["tool"] == "Read" or (ev["tool"] == "Bash" and re.search(
                r"\b(cat|head|less|more|sed\s+-n|bat)\b[^|;&]*SKILL\.md", str(ev["input"].get("command", ""))))
            if reads and re.search(rf"skills[/\\]+{re.escape(skill)}[/\\]+SKILL\.md", blob):
                return ev["i"]
        elif ev["kind"] == "user":
            text = ev["text"]
            if f"<command-name>/{skill}</command-name>" in text:
                return ev["i"]
            if text.startswith("Base directory for this skill") and pattern.search(text.splitlines()[0]):
                return ev["i"]
    return None


def _rel(path: str, cwd: str | None) -> str:
    p = PurePosixPath(path.replace("\\", "/"))
    if cwd and p.is_absolute():
        try:
            return p.relative_to(PurePosixPath(cwd.replace("\\", "/"))).as_posix()
        except ValueError:
            return p.as_posix()
    return p.as_posix()


def reconstruct_files(events: list[dict], cwd: str | None) -> dict[str, str | None]:
    """Final file contents as far as the trace shows them: Write gives full content,
    Edit applies to content seen through Write or Read; anything else is unknown."""
    files: dict[str, str | None] = {}
    for ev in events:
        if ev["kind"] != "tool" or not isinstance(ev["input"], dict):
            continue
        inp = ev["input"]
        path = inp.get("file_path")
        if not path or ev.get("is_error"):
            continue
        rel = _rel(str(path), cwd)
        if ev["tool"] == "Write" and isinstance(inp.get("content"), str):
            files[rel] = inp["content"]
        elif ev["tool"] == "Read" and rel not in files and isinstance(ev.get("output"), str):
            lines = [re.sub(r"^\s*\d+\t", "", ln, count=1) for ln in _strip_reminders(ev["output"]).splitlines()]
            files[rel] = "\n".join(lines)
        elif ev["tool"] == "Edit":
            old, new = inp.get("old_string"), inp.get("new_string")
            current = files.get(rel)
            if isinstance(current, str) and isinstance(old, str) and old in current:
                files[rel] = current.replace(old, new or "", -1 if inp.get("replace_all") else 1)
            else:
                files[rel] = files.get(rel) if rel in files else None
    return files


def to_trace(raw: RawTrace, *, skill: str, variant: str, rep: int, aliases: dict[str, str],
             from_skill_load: bool = True) -> dict:
    """Normalize a RawTrace into skillgraph's trace.json shape."""
    known_ts = [e.ts for e in raw.events if e.ts is not None]
    if len(known_ts) == len(raw.events) and known_ts:
        ordered = sorted(raw.events, key=lambda e: (e.ts, e.order))
    else:
        ordered = sorted(raw.events, key=lambda e: e.order)

    events: list[dict] = []
    for e in ordered:
        if e.kind == "tool":
            inp = as_input(e.input)
            tool = canonical_tool(e.tool, inp, aliases)
            inp = normalize_input(tool, inp)
            ev = {"i": len(events), "kind": "tool", "id": e.id, "tool": tool, "raw_tool": e.tool,
                  "input": clip(inp), "output": clip(as_text(e.output)), "is_error": bool(e.is_error)}
        else:
            ev = {"i": len(events), "kind": e.kind, "text": clip(e.text, 1500 if e.kind == "user" else 4000)}
            if e.meta:
                ev["meta"] = True
        ev.update({k: v for k, v in (("parent", e.parent), ("depth", e.depth), ("agent", e.agent)) if v not in (None, 0)})
        if e.ts is not None:
            ev["ts"] = e.ts
        events.append(ev)

    load = _skill_load_index(events, skill)
    loaded = load is not None or bool(raw.skill_loaded)
    prompt = raw.prompt
    if from_skill_load and load is not None:
        before = [e for e in events[:load] if e["kind"] == "user" and not e.get("meta")]
        if before:
            prompt = before[-1]["text"]
        events = events[load:]
        for n, ev in enumerate(events):
            ev["i"] = n

    duration = None
    stamps = [e["ts"] for e in events if "ts" in e]
    if len(stamps) >= 2:
        duration = int((max(stamps) - min(stamps)) * 1000)
    files = reconstruct_files(events, raw.cwd)
    return {
        "skill": skill,
        "task": f"{raw.format}-{raw.label}",
        "task_prompt": prompt or "(no user prompt in the imported trace)",
        "variant": {"name": variant, "kind": variant, "remove": []},
        "rep": rep,
        "model": raw.model or f"imported:{raw.format}",
        "trigger": "observed",
        "enforce": False,
        "init": {"harness": f"imported:{raw.format}", "source": raw.source},
        "events": events,
        "blocked": [],
        "result": {"subtype": "imported", "is_error": False, "num_turns": sum(e["kind"] == "text" for e in events),
                   "duration_ms": duration, "cost_usd": raw.cost_usd, "text": clip(raw.final_text, 3000)},
        "error": None,
        "wall_s": round(duration / 1000, 1) if duration else None,
        "skill_loaded": loaded,
        "files": files,
        "files_reconstructed": True,
        "git": {"tags": [], "log": [], "status": []},
        "success": {},
    }


# Claude Code tools that aren't file or shell tools; known, so not reported as unmapped.
OTHER_KNOWN_TOOLS = {"Agent", "Task", "WebFetch", "WebSearch", "TodoWrite", "NotebookEdit", "AskUserQuestion",
                     "ToolSearch", "Monitor", "SendMessage", "ExitPlanMode", "EnterPlanMode", "LSP"}


def summarize(traces: list[dict]) -> dict:
    tools: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    canon = set(TOOL_ALIASES) | OTHER_KNOWN_TOOLS
    for t in traces:
        for e in t["events"]:
            if e["kind"] == "tool":
                tools[e["tool"]] = tools.get(e["tool"], 0) + 1
                if e["tool"] not in canon and not e["tool"].startswith("mcp__"):
                    unmapped[e["raw_tool"]] = unmapped.get(e["raw_tool"], 0) + 1
    return {"traces": len(traces), "tool_calls": sum(tools.values()), "tools": tools, "unmapped": unmapped,
            "with_skill": sum(bool(t["skill_loaded"]) for t in traces)}
