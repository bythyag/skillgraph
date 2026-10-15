"""Trace digests: find the evidence for each question in a long trace.

A long agent session can run to hundreds of thousands of tokens. One judge call over
all of it is slow and expensive, and the judge's attention thins out. The digest is
a map of the trace, built mostly without a model:

    turns     the trace split at each agent message, user message, or switch to a
              subagent. Each turn lists its actions and the files it touched
    actions   an exhaustive index of tool calls, one line each. Consecutive identical
              calls (polling) collapse into one line with an index range
    intents   optional: one cheap call that never sees the skill. It names the task,
              the phases of work and what each user message was. Shown in the
              dashboard and `show`, never to the judge
    routes    which turns hold the evidence for each judge question: lexical matches
              plus one router call over the turn list

Verdicts are still decided on raw events. A windowed judge call sees the routed turns,
one turn either side, in full. Some answers stand from the window: a step seen done,
a rule seen broken, a condition settled. Absences are another matter: an excerpt can't
show something never happened. So these go to one skeleton call, which shows every
event of the trace with long fields shortened and the routed turns in full:
`must_not` rules, questions the router couldn't place, and window answers of "not
done" or "unclear". The router failing to find something never marks it missed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re

from skillgraph import providers
from skillgraph.providers import LLMError, parse_model

DIGEST_VERSION = 1
JUDGE_MODES = ("full", "windowed", "auto")
AUTO_THRESHOLD_TOKENS = 30_000   # auto mode judges the full trace below this many tokens
WINDOW_BUDGET_TOKENS = 24_000    # most tokens of excerpts in one windowed judge call
SKELETON_BUDGET_TOKENS = 60_000  # the skeleton is shortened further until it fits
FOCUS_BUDGET_TOKENS = 20_000     # most tokens shown in full inside the skeleton call
MAX_ROUTED_TURNS = 8             # per question, before the ±1 window
INTENT_KINDS = ["new_task", "follow_up", "approval", "correction", "question", "other"]


def approx_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _one_line(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def trace_sha(trace: dict) -> str:
    blob = json.dumps({"p": trace.get("task_prompt"), "e": trace["events"], "f": trace.get("files"),
                       "r": (trace.get("result") or {}).get("text")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- actions

def action_target(ev: dict) -> str:
    """The one thing that identifies a tool call: a command, a path, a pattern, a URL."""
    inp = ev.get("input")
    if not isinstance(inp, dict):
        return str(inp or "")
    tool = ev.get("tool", "")
    if tool == "Bash":
        return str(inp.get("command", ""))
    if tool in ("Read", "Write", "Edit"):
        return str(inp.get("file_path", ""))
    if tool in ("Glob", "Grep"):
        return " ".join(str(inp[k]) for k in ("pattern", "path", "glob") if inp.get(k))
    if tool == "Skill":
        return str(inp.get("skill") or inp.get("name") or "")
    if tool in ("Agent", "Task"):
        return str(inp.get("description") or inp.get("prompt") or "")
    for key in ("url", "query", "command", "file_path", "path", "pattern"):
        if inp.get(key):
            return str(inp[key])
    return json.dumps(inp, ensure_ascii=False)


def build_actions(trace: dict) -> list[dict]:
    return [{"i": ev["i"], "tool": ev["tool"], "target": _one_line(action_target(ev), 300),
             "error": bool(ev.get("is_error")), **({"agent": ev["agent"]} if ev.get("agent") else {})}
            for ev in trace["events"] if ev["kind"] == "tool"]


def collapse(actions: list[dict]) -> list[dict]:
    """Consecutive identical calls (same tool, target and agent) become one entry with a count."""
    out: list[dict] = []
    for a in actions:
        last = out[-1] if out else None
        if last and (last["tool"], last["target"], last.get("agent")) == (a["tool"], a["target"], a.get("agent")):
            last["last"], last["count"] = a["i"], last["count"] + 1
            last["error"] = last["error"] or a["error"]
        else:
            out.append({**a, "last": a["i"], "count": 1})
    return out


def render_actions(actions: list[dict]) -> str:
    lines = []
    for a in collapse(actions):
        where = f"[{a['i']}]" if a["count"] == 1 else f"[{a['i']}-{a['last']}]"
        times = "" if a["count"] == 1 else f" ×{a['count']} (identical calls)"
        who = f" ({a['agent']})" if a.get("agent") else ""
        lines.append(f"{where} {a['tool']}{times}{who}: {a['target']}" + (" (error)" if a["error"] else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- turns

def _user_kind(ev: dict) -> str:
    text = ev.get("text", "")
    if ev.get("meta") or text.startswith(("Base directory for this skill", "<command-name>", "<command-message>")):
        return "meta"
    return "user"


def _summarize(acts: list[dict], per_tool: int = 3, width: int = 60) -> str:
    groups: dict[str, list[str]] = {}
    for a in acts:
        groups.setdefault(a["tool"], []).append(a["target"])
    parts = []
    for tool, targets in groups.items():
        uniq = list(dict.fromkeys(targets))
        shown = "; ".join(_one_line(t, width) for t in uniq[:per_tool])
        more = f"; +{len(uniq) - per_tool} more" if len(uniq) > per_tool else ""
        count = f" ×{len(targets)}" if len(targets) > 1 else ""
        parts.append(f"{tool}{count}: {shown}{more}")
    return " | ".join(parts)


def build_turns(trace: dict, actions: list[dict] | None = None) -> list[dict]:
    """Split at every agent message, user message, and switch of (sub)agent."""
    from skillgraph.checker import event_lines

    by_i = {a["i"]: a for a in (actions if actions is not None else build_actions(trace))}
    turns: list[dict] = []
    cur: dict | None = None
    for ev in trace["events"]:
        agent = ev.get("agent")
        if cur is None or ev["kind"] in ("text", "user") or agent != cur["agent"]:
            kind = _user_kind(ev) if ev["kind"] == "user" else "agent"
            said = ev.get("text", "") if ev["kind"] in ("text", "user") else ""
            cur = {"n": len(turns), "start": ev["i"], "end": ev["i"], "agent": agent, "kind": kind,
                   "said": _one_line(said, 300), "actions": [], "files": [], "errors": 0, "tokens": 0}
            turns.append(cur)
        cur["end"] = ev["i"]
        cur["tokens"] += approx_tokens("\n".join(event_lines(ev)))
        if ev["kind"] == "tool":
            cur["actions"].append(ev["i"])
            cur["errors"] += bool(ev.get("is_error"))
            if ev["tool"] in ("Write", "Edit") and (ev.get("input") or {}).get("file_path"):
                path = str(ev["input"]["file_path"])
                if path not in cur["files"]:
                    cur["files"].append(path)
    for t in turns:
        t["summary"] = _summarize([by_i[i] for i in t["actions"] if i in by_i])
    return turns


def toc_lines(turns: list[dict], intents: dict | None = None, said_limit: int = 200, summary_limit: int = 300) -> list[str]:
    """The turn list a router or intent model reads. Runs of identical turns (polling) collapse."""
    lines: list[str] = []
    prev_key, run_start = None, None
    for t in turns:
        key = (t["kind"], t["agent"], t["said"], t["summary"])
        if key == prev_key and t["kind"] == "agent":
            lines[-1] = re.sub(r"^\[(\d+)(?:-\d+)?\]", f"[{run_start}-{t['n']}]", lines[-1])
            if "(repeated)" not in lines[-1]:
                lines[-1] += " (repeated)"
            continue
        prev_key, run_start = key, t["n"]
        who = {"user": "user", "meta": "injected"}.get(t["kind"], f"subagent {t['agent']}" if t["agent"] else "agent")
        said = f'"{_one_line(t["said"], said_limit)}"' if t["said"] else ""
        acts = _one_line(t["summary"], summary_limit) if t["summary"] else ""
        err = f" ({t['errors']} errors)" if t["errors"] else ""
        lines.append(f"[{t['n']}] {who}: " + " | ".join(x for x in (said, acts) if x) + err)
    return lines


def build(trace: dict) -> dict:
    """The deterministic part of a digest (no model calls)."""
    from skillgraph.checker import render_trace

    actions = build_actions(trace)
    turns = build_turns(trace, actions)
    return {
        "version": DIGEST_VERSION,
        "trace_sha": trace_sha(trace),
        "episode": trace.get("episode"),
        "turns": turns,
        "actions": actions,
        "stats": {
            "events": len(trace["events"]),
            "tool_calls": len(actions),
            "turns": len(turns),
            "agent_messages": sum(e["kind"] == "text" for e in trace["events"]),
            "collapsed_actions": len(actions) - len(collapse(actions)),
            "tokens_full": approx_tokens(render_trace(trace)),
            "tokens_skeleton": approx_tokens(render_skeleton(trace)[0]),
        },
        "intents": None,
        "routes": {},
    }


def load_or_build(path, trace: dict) -> dict:
    """digest.json from `path` when it was built from this exact trace, else a fresh one."""
    from pathlib import Path

    p = Path(path)
    if p.exists():
        try:
            d = json.loads(p.read_text())
            if d.get("version") == DIGEST_VERSION and d.get("trace_sha") == trace_sha(trace):
                return d
        except ValueError:
            pass
    return build(trace)


# ---------------------------------------------------------------- intents (skill-blind)

INTENT_SYSTEM = (
    "You describe what an AI agent did in a run, from a table of contents of its turns. "
    "Describe only what the turns show. Don't judge the work."
)

INTENT_PROMPT = (
    "Name the task the user asked for. Split the run into phases of consecutive turns and give each "
    "phase an intent of at most 8 words, starting with a verb. Classify each user turn: new_task "
    "(asks for something new), follow_up (adds to or changes the current task), approval (says yes "
    "or go ahead), correction (says something was wrong), question, or other. Give the outcome in "
    "one sentence."
)

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {"type": "string"},
        "outcome": {"type": "string"},
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"first": {"type": "integer"}, "last": {"type": "integer"}, "intent": {"type": "string"}},
                "required": ["first", "last", "intent"],
                "additionalProperties": False,
            },
        },
        "user_turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"turn": {"type": "integer"}, "kind": {"type": "string", "enum": INTENT_KINDS}},
                "required": ["turn", "kind"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["task", "outcome", "phases", "user_turns"],
    "additionalProperties": False,
}


def _model_name(model) -> str:
    ref = parse_model(model)
    return str(ref)


async def add_intents(digest: dict, trace: dict, *, model, force: bool = False) -> float:
    """Fill digest["intents"]; returns the cost (0 when cached)."""
    have = digest.get("intents")
    if have and have.get("model") == _model_name(model) and have.get("prompt_version") == DIGEST_VERSION and not force:
        return 0.0
    toc = "\n".join(toc_lines(digest["turns"]))
    prompt = (f"<run>\nTASK PROMPT:\n{_one_line(trace.get('task_prompt', ''), 2000)}\n\nTURNS:\n{toc}\n</run>\n\n"
              f"{INTENT_PROMPT}")
    res = await providers.structured(prompt, INTENT_SCHEMA, system=INTENT_SYSTEM, model=model)
    n = len(digest["turns"])
    data = res.data
    data["phases"] = [p for p in data.get("phases", []) if 0 <= p["first"] <= p["last"] < max(n, 1)]
    data["user_turns"] = [u for u in data.get("user_turns", []) if 0 <= u["turn"] < n]
    digest["intents"] = {"model": _model_name(model), "prompt_version": DIGEST_VERSION,
                         "cost_usd": round(res.cost_usd, 5), "data": data}
    return res.cost_usd


def phase_of(digest: dict, turn: int) -> str | None:
    for p in ((digest.get("intents") or {}).get("data") or {}).get("phases", []):
        if p["first"] <= turn <= p["last"]:
            return p["intent"]
    return None


# ---------------------------------------------------------------- routing

ROUTER_SYSTEM = (
    "You point to where the evidence is in an AI agent's run. You see a table of contents of the "
    "run, not the run itself. Favor recall: list every turn that could hold the evidence."
)

ROUTER_PROMPT = (
    "For each question, list the turns whose events could answer it: where the action, its output, "
    "or a statement about it would appear. Also list the final files that could answer it. If nothing "
    "in the run relates to the question, return empty lists. List at most 8 turns per question, the "
    "closest to the evidence first."
)

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "routes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "turns": {"type": "array", "items": {"type": "integer"}},
                    "files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "turns", "files"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["routes"],
    "additionalProperties": False,
}

_STOP = set("""a an and are as at be been but by can could did do does done each for from had has have how if in into is
it its make made may must not of on or should so such than that the their them then there these they this those to
under use used using was were what when where which while who will with without would you your any all also only other
some more most very just after before during agent run step file files sure every""".split())


def _terms(text: str) -> dict[str, float]:
    """Search terms from a question: quoted and backticked spans, paths and flags count double."""
    terms: dict[str, float] = {}
    for m in re.findall(r"`([^`]{2,60})`|\"([^\"]{2,40})\"|'([^']{2,40})'", text):
        span = next(x for x in m if x).strip().lower()
        if span:
            terms[span] = 2.0
    for tok in re.findall(r"[\w./@:-]*[/.][\w./@:-]+|--?[a-z][\w-]+|\b[a-z]+_[\w]+\b", text.lower()):
        tok = tok.strip(".:,")
        if len(tok) >= 3 and not re.fullmatch(r"[\d.]+", tok):
            terms.setdefault(tok, 2.0)
    for word in re.findall(r"[a-z][a-z0-9-]{3,}", text.lower()):
        if word not in _STOP:
            terms.setdefault(word, 1.0)
    return terms


def _haystacks(trace: dict, turns: list[dict]) -> list[str]:
    by_i = {ev["i"]: ev for ev in trace["events"]}
    out = []
    for t in turns:
        parts = [t["said"]]
        for i in range(t["start"], t["end"] + 1):
            ev = by_i.get(i)
            if not ev:
                continue
            if ev["kind"] == "tool":
                parts.append(json.dumps(ev.get("input"), ensure_ascii=False)[:3000])
                parts.append(str(ev.get("output") or "")[:2000])
            else:
                parts.append(ev.get("text", "")[:3000])
        out.append(" ".join(parts).lower())
    return out


def lexical_routes(trace: dict, turns: list[dict], items: list[dict], top: int = 4) -> dict[str, list[int]]:
    """Turns sharing rare terms with each question. Terms found in more than a quarter of
    the turns carry no weight (they are everywhere, so they point nowhere)."""
    hay = _haystacks(trace, turns)
    n = max(1, len(hay))
    routes: dict[str, list[int]] = {}
    df_cache: dict[str, int] = {}
    for it in items:
        terms = _terms(f"{it['question']} {it.get('context', '')}")
        scores = [0.0] * len(hay)
        for term, boost in terms.items():
            if term not in df_cache:
                df_cache[term] = sum(term in h for h in hay)
            df = df_cache[term]
            if not df or (df / n > 0.25 and n >= 8):
                continue
            weight = boost * (1.0 + math.log(n / df))
            for k, h in enumerate(hay):
                if term in h:
                    scores[k] += weight
        ranked = sorted((k for k, s in enumerate(scores) if s >= 2.0), key=lambda k: -scores[k])
        routes[it["id"]] = sorted(turns[k]["n"] for k in ranked[:top])
    return routes


def _route_key(items: list[dict], model) -> str:
    blob = json.dumps([_model_name(model) if model else None, DIGEST_VERSION,
                       [[it["id"], it["question"], it.get("context", "")] for it in items]])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


async def route(digest: dict, trace: dict, items: list[dict], *, model=None) -> tuple[dict, float]:
    """{id: {"turns", "files", "lexical", "llm"}} for each item, and the router's cost.
    Without a model, only lexical routes are used."""
    key = _route_key(items, model)
    cached = digest.setdefault("routes", {}).get(key)
    if cached:
        return cached["items"], 0.0
    turns = digest["turns"]
    lexical = lexical_routes(trace, turns, items)
    llm: dict[str, dict] = {}
    cost = 0.0
    if model is not None and turns:
        files = ", ".join(sorted(trace.get("files") or {})) or "(none)"
        toc = "\n".join(toc_lines(turns))
        questions = "\n".join(f"- id={it['id']}: {it['question']}" + (f"  (instruction: {it['context']})" if it.get("context") else "")
                              for it in items)
        prompt = (f"<run>\nTASK PROMPT:\n{_one_line(trace.get('task_prompt', ''), 2000)}\n\nTURNS:\n{toc}\n\n"
                  f"FINAL FILES: {files}\n</run>\n\n{ROUTER_PROMPT}\n\nQuestions:\n{questions}")
        res = await providers.structured(prompt, ROUTER_SCHEMA, system=ROUTER_SYSTEM, model=model)
        cost = res.cost_usd
        valid_turns = {t["n"] for t in turns}
        for r in res.data.get("routes", []):
            llm[r["id"]] = {"turns": [n for n in r["turns"] if n in valid_turns][:MAX_ROUTED_TURNS],
                            "files": [f for f in r["files"] if f in (trace.get("files") or {})]}
    out = {}
    for it in items:
        lex = lexical.get(it["id"], [])
        mod = llm.get(it["id"], {"turns": [], "files": []})
        text = f"{it['question']} {it.get('context', '')}"
        lex_files = [p for p in (trace.get("files") or {}) if p in text or p.rsplit("/", 1)[-1] in text]
        out[it["id"]] = {
            "turns": sorted(set(lex) | set(mod["turns"])),
            "files": sorted(set(lex_files) | set(mod["files"])),
            "lexical": lex,
            "llm": mod["turns"],
        }
    digest["routes"] = {**{k: v for k, v in list(digest["routes"].items())[-3:]},
                        key: {"model": _model_name(model) if model else None, "items": out, "cost_usd": round(cost, 5)}}
    return out, cost


# ---------------------------------------------------------------- views of the trace

WINDOW_NOTE = (
    "This trace is an excerpt: the events around the parts that matter for these questions are shown "
    "in full, and lines starting with … stand for events that are not shown. Answer yes only when the "
    "shown events support it. If they don't settle a question, answer unclear, not no."
)

SKELETON_NOTE = (
    "This trace shows every event of the run. Some events are shown in full; the others have long "
    "inputs, outputs and messages shortened, so answer unclear if a shortened field would decide the "
    "question."
)

# (input/field limit, output limit, agent text limit) per skeleton level
_SKELETON_LEVELS = [(300, 160, 400), (200, 0, 200), (120, 0, 0)]


def window_events(turns: list[dict], turn_ids: list[int], pad: int = 1) -> set[int]:
    by_n = {t["n"]: t for t in turns}
    picked = {n + d for n in turn_ids for d in range(-pad, pad + 1) if n + d in by_n}
    return {i for n in picked for i in range(by_n[n]["start"], by_n[n]["end"] + 1)}


def opening_events(turns: list[dict], first_agent_turns: int = 1) -> set[int]:
    """The episode opening: injected and user turns up to and including the first agent turn(s)."""
    out: set[int] = set()
    seen_agent = 0
    for t in turns:
        out |= set(range(t["start"], t["end"] + 1))
        if t["kind"] == "agent":
            seen_agent += 1
            if seen_agent >= first_agent_turns:
                break
    return out


def _gap(events: list[dict]) -> str:
    tools = sum(e["kind"] == "tool" for e in events)
    msgs = sum(e["kind"] == "text" for e in events)
    other = len(events) - tools - msgs
    bits = [f"{tools} tool calls" if tools else "", f"{msgs} agent messages" if msgs else "",
            f"{other} injected/user messages" if other else ""]
    return f"… [events {events[0]['i']}-{events[-1]['i']} not shown: {', '.join(b for b in bits if b)}] …"


def _files_block(trace: dict, files: list[str] | None, limit: int) -> str:
    all_files = trace.get("files") or {}
    shown = list(all_files) if files is None else [f for f in files if f in all_files]
    blocks = []
    for path in shown:
        content = all_files[path]
        body = "(binary or large)" if content is None else (content if len(content) <= limit else content[:limit] + " …[truncated]")
        blocks.append(f"--- {path}\n{body}")
    rest = [p for p in all_files if p not in shown]
    if rest:
        blocks.append("(other final files, contents not shown: " + ", ".join(rest) + ")")
    return "\n".join(blocks)


def _wrap(trace: dict, events_block: str, files_block: str, header: str) -> str:
    git = trace.get("git", {})
    return (
        f"TASK PROMPT:\n{trace['task_prompt']}\n\n"
        f"{header}\n" + events_block + "\n\n"
        f"FINAL ANSWER:\n{(trace.get('result') or {}).get('text', '')}\n\n"
        f"FINAL FILES:\n" + files_block + "\n\n"
        f"GIT: tags={git.get('tags')} status={git.get('status')}\nlog:\n" + "\n".join(git.get("log", []))
    )


def render_window(trace: dict, focus: set[int], files: list[str] | None) -> str:
    """Events in `focus` in full; each run of other events becomes one … line."""
    from skillgraph.checker import event_lines

    lines, gap = [], []
    for ev in trace["events"]:
        if ev["i"] in focus:
            if gap:
                lines.append(_gap(gap))
                gap = []
            lines.extend(event_lines(ev))
        else:
            gap.append(ev)
    if gap:
        lines.append(_gap(gap))
    return _wrap(trace, "\n".join(lines), _files_block(trace, files, 3000), "EVENTS (excerpt):")


def render_skeleton(trace: dict, focus: set[int] | None = None, files: list[str] | None = None) -> tuple[str, int]:
    """Every event: those in `focus` in full, the rest shortened. Returns (text, level used)."""
    from skillgraph.checker import event_lines

    focus = focus or set()
    text, used = "", 0
    for level, (field, out, said) in enumerate(_SKELETON_LEVELS):
        used = level
        lines = []
        for ev in trace["events"]:
            if ev["i"] in focus:
                lines.extend(event_lines(ev))
            elif ev["kind"] == "text" and not said:
                lines.append(f"[{ev['i']}] AGENT: (message omitted)")
            else:
                lines.extend(event_lines(ev, field_limit=field, output_limit=out, text_limit=said, user_limit=min(200, field)))
        text = _wrap(trace, "\n".join(lines), _files_block(trace, files, 1500),
                     "EVENTS (all events; long fields shortened except where shown in full):")
        if approx_tokens(text) <= SKELETON_BUDGET_TOKENS:
            break
    return text, used


def resolve_mode(mode: str, trace: dict) -> str:
    if mode not in JUDGE_MODES:
        raise ValueError(f"unknown judge mode {mode!r}; use one of {', '.join(JUDGE_MODES)}")
    if mode != "auto":
        return mode
    from skillgraph.checker import render_trace

    return "windowed" if approx_tokens(render_trace(trace)) > AUTO_THRESHOLD_TOKENS else "full"


# ---------------------------------------------------------------- windowed judging

def _pack(items: list[dict], windows: dict[str, set[int]], tokens_of) -> list[list[dict]]:
    """Group items in trace order; a group grows while its union window fits the budget."""
    order = sorted(items, key=lambda it: (min(windows[it["id"]]) if windows[it["id"]] else 0, it["id"]))
    batches: list[list[dict]] = []
    cur: list[dict] = []
    cur_window: set[int] = set()
    for it in order:
        merged = cur_window | windows[it["id"]]
        if cur and tokens_of(merged) > WINDOW_BUDGET_TOKENS:
            batches.append(cur)
            cur, merged = [], set(windows[it["id"]])
        cur.append(it)
        cur_window = merged
    if cur:
        batches.append(cur)
    return batches


async def judge_windowed(trace: dict, digest: dict, items: list[dict], kinds: dict[str, str], *,
                         judge_model, judge_effort: str | None = None, router_model=None) -> tuple[dict, float, dict]:
    """Verdicts for `items` from routed windows and one skeleton call.
    kinds maps claim ids to their kind; ids not in it are conditions."""
    from skillgraph.checker import event_lines, judge

    turns = digest["turns"]
    routes, router_cost = await route(digest, trace, items, model=router_model)
    token_of = {ev["i"]: approx_tokens("\n".join(event_lines(ev))) for ev in trace["events"]}

    def tokens_of(events: set[int]) -> int:
        return sum(token_of.get(i, 0) for i in events)

    opening = opening_events(turns)
    windows: dict[str, set[int]] = {}
    files: dict[str, list[str]] = {}
    windowed, skeleton = [], []
    for it in items:
        r = routes.get(it["id"], {"turns": [], "files": []})
        win = window_events(turns, r["turns"])
        is_condition = it["id"] not in kinds
        if is_condition:
            win |= opening
        windows[it["id"]], files[it["id"]] = win, r["files"]
        if kinds.get(it["id"]) == "must_not" or not (r["turns"] or r["files"] or is_condition):
            skeleton.append(it)
        else:
            windowed.append(it)

    verdicts: dict[str, dict] = {}
    cost = router_cost
    calls = 0
    sent = 0

    async def call(batch: list[dict], text: str, note: str) -> dict:
        nonlocal cost, calls, sent
        calls += 1
        sent += approx_tokens(text)
        try:
            got, c = await judge(trace, batch, model=judge_model, effort=judge_effort, rendered=text, note=note)
        except LLMError as exc:
            return {it["id"]: {"answer": "unclear", "evidence_event": -1, "reason": str(exc)[:200]} for it in batch}
        cost += c
        return got

    batches = _pack(windowed, windows, tokens_of)
    texts = []
    for batch in batches:
        focus = set().union(*(windows[it["id"]] for it in batch))
        batch_files = sorted(set().union(*(files[it["id"]] for it in batch)))
        texts.append(render_window(trace, focus, batch_files))
    results = await asyncio.gather(*(call(b, t, WINDOW_NOTE) for b, t in zip(batches, texts, strict=True)))

    escalated = []
    for batch, got in zip(batches, results, strict=True):
        for it in batch:
            v = got.get(it["id"])
            kind = kinds.get(it["id"])
            settled = v is not None and (v["answer"] == "yes" or (kind is None and v["answer"] == "no"))
            if settled:
                verdicts[it["id"]] = {**v, "view": "window"}
            else:
                escalated.append(it["id"])
                skeleton.append(it)

    skeleton_level = None
    if skeleton:
        focus: set[int] = set()
        for it in sorted(skeleton, key=lambda x: len(windows[x["id"]])):
            if tokens_of(focus | windows[it["id"]]) <= FOCUS_BUDGET_TOKENS:
                focus |= windows[it["id"]]
        sk_files = sorted(set().union(*(files[it["id"]] for it in skeleton))) or None
        text, skeleton_level = render_skeleton(trace, focus, sk_files)
        got = await call(skeleton, text, SKELETON_NOTE)
        for it in skeleton:
            if it["id"] in got:
                verdicts[it["id"]] = {**got[it["id"]], "view": "skeleton"}

    n_events = len(trace["events"])
    for v in verdicts.values():
        if not (0 <= v.get("evidence_event", -1) < n_events):
            v["evidence_event"] = -1

    routed_turns = {n for it in items for n in routes.get(it["id"], {}).get("turns", [])}
    off_skill = [t["n"] for t in turns if t["kind"] == "agent" and t["actions"] and t["n"] not in routed_turns]
    from skillgraph.checker import render_trace

    info = {
        "mode": "windowed",
        "calls": calls,
        "window_batches": len(batches),
        "skeleton_level": skeleton_level,
        "tokens_full": approx_tokens(render_trace(trace)),
        "tokens_sent": sent,
        "router_model": _model_name(router_model) if router_model else None,
        "router_cost_usd": round(router_cost, 5),
        "routes": {it["id"]: routes.get(it["id"], {}).get("turns", []) for it in items},
        "unrouted": [it["id"] for it in items if not routes.get(it["id"], {}).get("turns")
                     and not routes.get(it["id"], {}).get("files") and it["id"] in kinds],
        "escalated": escalated,
        "off_skill_turns": off_skill,
    }
    return verdicts, cost, info


# ---------------------------------------------------------------- validation

def flat_results(check: dict) -> dict[str, dict]:
    """Claims and conditions of a check.json in one dict; reference entries get the ref: prefix."""
    out: dict[str, dict] = {}
    out.update(check.get("conditions", {}))
    out.update(check.get("claims", {}))
    ref = check.get("reference") or {}
    for part in ("conditions", "claims"):
        out.update({f"ref:{k}": v for k, v in ref.get(part, {}).items()})
    return out


def routing_recall(digest: dict, routes: dict, check: dict, condition_ids: set[str]) -> list[dict]:
    """For each judge verdict that cites an event: is that event inside the window the
    routes would send to the judge? (Conditions also get the episode opening.)"""
    turns = digest["turns"]
    opening = opening_events(turns)
    rows = []
    for cid, res in flat_results(check).items():
        if res.get("method") != "judge" or res.get("evidence") is None or cid not in routes:
            continue
        r = routes[cid]
        extra = opening if cid in condition_ids else set()
        ev = res["evidence"]
        rows.append({
            "id": cid,
            "evidence": ev,
            "status": res.get("status", res.get("value")),
            "hit": ev in (window_events(turns, r["turns"]) | extra),
            "lexical_hit": ev in (window_events(turns, r["lexical"]) | extra),
            "llm_hit": ev in (window_events(turns, r["llm"]) | extra),
            "routed": bool(r["turns"]),
        })
    return rows
