"""`skillgraph show RUN_DIR`: one run as a readable transcript, with each claim's
result printed next to the event that decided it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

STATUS_MARK = {"satisfied": "✓", "missed": "✗", "violated": "⊘", "out_of_order": "⇄", "n/a": "·", "unclear": "?"}
COLORS = {"satisfied": "32", "missed": "31", "violated": "31", "out_of_order": "33", "n/a": "2", "unclear": "33"}


def _color(text: str, code: str, on: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if on else text


def _clip(text: str, n: int) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _one_line(value) -> str:
    if isinstance(value, dict):
        for key in ("command", "file_path", "pattern", "skill", "prompt"):
            if key in value:
                return str(value[key])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render(run_dir: Path, *, width: int = 160, full: bool = False, color: bool | None = None) -> str:
    run_dir = Path(run_dir)
    tp = run_dir / "trace.json"
    if not tp.exists():
        raise FileNotFoundError(f"{tp} not found; pass a run directory like runs/x/<skill>/<task>/<variant>/r1")
    trace = json.loads(tp.read_text())
    check = json.loads((run_dir / "check.json").read_text()) if (run_dir / "check.json").exists() else {}
    on = sys.stdout.isatty() if color is None else color
    limit = 10_000 if full else width

    by_event: dict[int, list[tuple[str, dict]]] = {}
    for scope, results in (("", check.get("claims", {})), ("ref:", check.get("reference", {}).get("claims", {}))):
        for cid, r in results.items():
            if r.get("evidence") is not None:
                by_event.setdefault(r["evidence"], []).append((scope + cid, r))

    res = trace.get("result") or {}
    lines = [
        _color(f"{trace['skill']} / {trace['task']} / {trace['variant']['name']} / r{trace['rep']}", "1", on),
        f"model {trace.get('model')}  ·  {res.get('num_turns')} turns  ·  "
        f"{sum(e['kind'] == 'tool' for e in trace['events'])} tool calls  ·  "
        + (f"${res['cost_usd']:.3f}" if res.get("cost_usd") is not None else "cost unknown")
        + (f"  ·  ERROR {trace['error']}" if trace.get("error") else ""),
        "",
        _color("prompt", "1", on) + f"  {_clip(trace.get('task_prompt', ''), limit)}",
        "",
    ]
    for ev in trace["events"]:
        indent = "  " * int(ev.get("depth") or 0)
        tag = f"[{ev['i']:>3}]"
        if ev["kind"] == "tool":
            name = ev["tool"] + (f" ({ev['raw_tool']})" if ev.get("raw_tool") and ev["raw_tool"] != ev["tool"] else "")
            err = _color(" error", "31", on) if ev.get("is_error") else ""
            lines.append(f"{tag} {indent}{_color(name, '36', on)}{err}  {_clip(_one_line(ev.get('input')), limit)}")
            if ev.get("output"):
                lines.append(f"      {indent}  -> {_clip(str(ev['output']).replace(chr(10), ' ⏎ '), limit)}")
        elif ev["kind"] == "text":
            lines.append(f"{tag} {indent}{_color('agent', '35', on)}  {_clip(ev['text'].replace(chr(10), ' '), limit)}")
        else:
            label = "skill loaded" if ev.get("meta") or ev["text"].startswith(("/", "Base directory")) else "user"
            lines.append(f"{tag} {indent}{_color(label, '2', on)}  {_clip(ev['text'].replace(chr(10), ' '), limit)}")
        for cid, r in by_event.get(ev["i"], []):
            mark = _color(f"{STATUS_MARK.get(r['status'], '?')} {cid} {r['status']}", COLORS.get(r["status"], "0"), on)
            lines.append(f"      {indent}  {mark}")

    if res.get("text"):
        lines += ["", _color("final answer", "1", on), _clip(res["text"], limit * 4)]
    if trace.get("success"):
        lines += ["", _color("success checks", "1", on)]
        lines += [f"  {'✓' if ok else '✗'} {name}" for name, ok in trace["success"].items()]

    for title, results in (("skill claims", check.get("claims", {})), ("reference steps", check.get("reference", {}).get("claims", {}))):
        if not results:
            continue
        lines += ["", _color(title, "1", on)]
        for cid, r in sorted(results.items(), key=lambda kv: (kv[1]["status"] == "satisfied", kv[0])):
            where = f"event {r['evidence']}" if r.get("evidence") is not None else r.get("method", "")
            reason = f"  {_clip(r['reason'], limit)}" if r.get("reason") else ""
            lines.append("  " + _color(f"{STATUS_MARK.get(r['status'], '?')} {cid:<28} {r['status']:<12}", COLORS.get(r["status"], "0"), on)
                         + f" {where}{reason}")
    if not check:
        lines += ["", "not checked yet: run `skillgraph check` on the run directory"]
    return "\n".join(lines) + "\n"
