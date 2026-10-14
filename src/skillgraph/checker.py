"""Check a run trace against a claims doc.

Mechanical checks (tool calls, final files) run first. Everything that needs
judgment goes to one structured judge call per trace. Branch claims whose
condition did not hold are marked n/a, and `after` edges are checked using the
event index of each claim's evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from skillgraph import providers
from skillgraph.config import DEFAULTS
from skillgraph.providers import LLMError, parse_model
from skillgraph.schema import doc_sha, save_json

STATUSES = ["satisfied", "missed", "violated", "out_of_order", "n/a", "unclear"]


# ---------------------------------------------------------------- mechanical checks

def _search_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I | re.S) for p in patterns)


def match_tool(check: dict, event: dict) -> bool:
    if check["tools"] and event.get("tool") not in check["tools"]:
        return False
    if check["input_any"]:
        blob = json.dumps(event.get("input", {}), ensure_ascii=False)
        # match against both the JSON form and the raw command/path, so regexes need not handle escaping
        raw = " ".join(str(v) for v in (event.get("input") or {}).values() if isinstance(v, str))
        if not (_search_any(check["input_any"], blob) or _search_any(check["input_any"], raw)):
            return False
    if check["output_any"] and not _search_any(check["output_any"], event.get("output") or ""):
        return False
    return True


def _glob_regex(glob: str) -> re.Pattern:
    """Glob to regex where `*` and `?` stay within one path segment and `**/` spans any depth."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        elif glob[i] == "[" and "]" in glob[i + 1:]:
            j = glob.index("]", i + 1)
            out.append("[" + glob[i + 1:j].replace("!", "^", 1) + "]")
            i = j + 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _file_matches(glob: str, path: str) -> bool:
    return bool(_glob_regex(glob).match(path))


def _mentions(text: str, path: str) -> bool:
    """`path` appears in `text` as a whole path (so `a.md` doesn't match `data.md`)."""
    return re.search(rf"(?:^|[\s/\"'=<>]){re.escape(path)}(?:$|[\s\"';&|<>)])", text) is not None


def _file_evidence(trace: dict, path: str) -> int | None:
    """Index of the last event that wrote `path`, or None when no event shows it being
    written (then the claim has no position and ordering checks skip it)."""
    idx = None
    for ev in trace["events"]:
        if ev["kind"] != "tool" or ev["tool"] not in ("Write", "Edit", "Bash"):
            continue
        inp = ev.get("input") or {}
        target = str(inp.get("file_path", "")) + " " + str(inp.get("command", ""))
        if _mentions(target, path):
            idx = ev["i"]
    return idx


def eval_mechanical(check: dict, trace: dict) -> tuple[bool, int | None]:
    """Returns (matched, evidence event index)."""
    if check["type"] == "tool":
        for ev in trace["events"]:
            if ev["kind"] == "tool" and match_tool(check, ev):
                return True, ev["i"]
        return False, None
    if check["type"] == "file":
        for path, content in trace["files"].items():
            if not _file_matches(check["path_glob"], path):
                continue
            if check["content_any"] and not (content and _search_any(check["content_any"], content)):
                continue
            return True, _file_evidence(trace, path)
        return False, None
    raise ValueError("judge checks are not mechanical")


# ---------------------------------------------------------------- judge

JUDGE_SYSTEM = (
    "You grade whether an AI agent followed specific instructions, using only the evidence in "
    "its execution trace and final files. You are strict about evidence: if the trace does not "
    "show something happened, it did not happen. Answer 'unclear' only when the evidence is "
    "genuinely ambiguous."
)

JUDGE_INSTRUCTIONS = (
    "Answer each question about this run with yes, no, or unclear. For evidence_event give the "
    "[index] of the event that best supports your answer, or -1 if none (for example when the "
    "evidence is a final file or the absence of an action). Keep reasons to one sentence."
)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "answer": {"type": "string", "enum": ["yes", "no", "unclear"]},
                    "evidence_event": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "answer", "evidence_event", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def render_trace(trace: dict, field_limit: int = 1200, file_limit: int = 3000) -> str:
    def clip(s: str, n: int) -> str:
        return s if len(s) <= n else s[:n] + " …[truncated]"

    lines = []
    for ev in trace["events"]:
        if ev["kind"] == "tool":
            inp = json.dumps(ev["input"], ensure_ascii=False)
            lines.append(f"[{ev['i']}] TOOL {ev['tool']} input={clip(inp, field_limit)}")
            if ev.get("output"):
                err = " (error)" if ev.get("is_error") else ""
                lines.append(f"      output{err}: {clip(ev['output'], field_limit)}")
        elif ev["kind"] == "text":
            lines.append(f"[{ev['i']}] AGENT: {clip(ev['text'], field_limit)}")
        else:
            lines.append(f"[{ev['i']}] INJECTED: {clip(ev['text'], 300)}")
    files = []
    for path, content in trace["files"].items():
        body = "(binary or large)" if content is None else clip(content, file_limit)
        files.append(f"--- {path}\n{body}")
    git = trace.get("git", {})
    return (
        f"TASK PROMPT:\n{trace['task_prompt']}\n\n"
        f"EVENTS:\n" + "\n".join(lines) + "\n\n"
        f"FINAL ANSWER:\n{(trace.get('result') or {}).get('text', '')}\n\n"
        f"FINAL FILES:\n" + "\n".join(files) + "\n\n"
        f"GIT: tags={git.get('tags')} status={git.get('status')}\nlog:\n" + "\n".join(git.get("log", []))
    )


async def judge(trace: dict, items: list[dict], *, model: str, effort: str | None = None) -> tuple[dict, float]:
    """items: [{"id", "question", "context"}] -> ({id: verdict}, cost)"""
    questions = "\n".join(
        f"- id={it['id']}: {it['question']}" + (f"  (instruction: {it['context']})" if it.get("context") else "")
        for it in items
    )
    prompt = f"<trace>\n{render_trace(trace)}\n</trace>\n\n{JUDGE_INSTRUCTIONS}\n\nQuestions:\n{questions}"
    res = await providers.structured(prompt, JUDGE_SCHEMA, system=JUDGE_SYSTEM, model=model, effort=effort)
    return {v["id"]: v for v in res.data["verdicts"]}, res.cost_usd


# ---------------------------------------------------------------- check a trace

def _judge_key(items: list[dict], model: str, effort: str | None) -> str:
    ref = parse_model(model)
    name = ref.model if ref.provider == "claude" else str(ref)  # bare Claude ids keep older caches valid
    head = [name] if effort is None else [name, effort]
    blob = json.dumps(head + [[it["id"], it["question"], it.get("context", "")] for it in items], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _item_key(item: dict, model: str, effort: str | None) -> str:
    """Cache key for one judge question; the ref: prefix doesn't change the question."""
    ref = parse_model(model)
    name = ref.model if ref.provider == "claude" else str(ref)
    blob = json.dumps([name, effort, item["id"].removeprefix("ref:"), item["question"], item.get("context", "")])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


async def check_trace(trace: dict, doc: dict, *, judge_model: str = DEFAULTS["judge"],
                      judge_effort: str | None = None, judge_cache: dict | None = None) -> dict:
    conditions: dict[str, dict] = {}
    claims: dict[str, dict] = {}
    pending: list[dict] = []

    for k in doc["conditions"]:
        if k["check"]["type"] != "judge":
            hit, idx = eval_mechanical(k["check"], trace)
            if hit:
                conditions[k["id"]] = {"value": True, "method": k["check"]["type"], "evidence": idx}
                continue
        # Not observed mechanically (or subjective): ask the judge whether it held.
        question = k["check"].get("question") or f"During this run, did this condition hold: {k['text']}?"
        pending.append({"id": k["id"], "question": question, "context": k["text"]})

    for c in doc["claims"]:
        chk = c["check"]
        if chk["type"] == "judge":
            pending.append({"id": c["id"], "question": chk["question"], "context": c["text"]})
            continue
        hit, idx = eval_mechanical(chk, trace)
        if c["kind"] == "must_not":
            status = "violated" if hit else "satisfied"
        else:
            status = "satisfied" if hit else "missed"
        claims[c["id"]] = {"status": status, "method": chk["type"], "evidence": idx, "reason": ""}

    # Judge verdicts depend only on the trace and the question, so they are cached per
    # question: edits to ordering edges or mechanical checks, or adding a reference,
    # only pay for questions that are new.
    judge_cost = 0.0
    cache = judge_cache or {}
    items_cache: dict = dict(cache.get("items") or {})
    verdicts: dict = {}
    for subset in (pending, [it for it in pending if not it["id"].startswith("ref:")]):
        legacy = cache.get(_judge_key(subset, judge_model, judge_effort)) if subset else None
        if legacy and "verdicts" in legacy:  # older caches stored one entry per batch
            share = legacy.get("cost_usd", 0.0) / max(1, len(subset))
            for it in subset:
                if it["id"] in legacy["verdicts"]:
                    items_cache.setdefault(_item_key(it, judge_model, judge_effort),
                                           {"verdict": legacy["verdicts"][it["id"]], "cost_usd": share})
            break
    todo = []
    for it in pending:
        hit = items_cache.get(_item_key(it, judge_model, judge_effort))
        if hit:
            verdicts[it["id"]] = hit["verdict"]
            judge_cost += hit.get("cost_usd", 0.0)  # cost of the original call
        else:
            todo.append(it)
    if todo and trace["events"]:
        try:
            fresh, cost = await judge(trace, todo, model=judge_model, effort=judge_effort)
            judge_cost += cost
            for it in todo:
                if it["id"] in fresh:
                    verdicts[it["id"]] = fresh[it["id"]]
                    items_cache[_item_key(it, judge_model, judge_effort)] = {
                        "verdict": fresh[it["id"]], "cost_usd": round(cost / len(todo), 5)}
        except LLMError as exc:
            for it in todo:
                verdicts[it["id"]] = {"answer": "unclear", "evidence_event": -1, "reason": str(exc)[:200]}
    elif todo:
        for it in todo:
            verdicts[it["id"]] = {"answer": "no", "evidence_event": -1, "reason": "empty trace"}
    live = {_item_key(it, judge_model, judge_effort) for it in pending}
    cache_out = {"items": {k: v for k, v in items_cache.items() if k in live}}

    kinds = {c["id"]: c["kind"] for c in doc["claims"]}
    for it in pending:
        v = verdicts.get(it["id"], {"answer": "unclear", "evidence_event": -1, "reason": "no verdict returned"})
        ev = v["evidence_event"] if v["evidence_event"] >= 0 else None
        if it["id"] in kinds:
            ans = v["answer"]
            if ans == "unclear":
                status = "unclear"
            elif kinds[it["id"]] == "must_not":
                status = "violated" if ans == "yes" else "satisfied"
            else:
                status = "satisfied" if ans == "yes" else "missed"
            claims[it["id"]] = {"status": status, "method": "judge", "evidence": ev, "reason": v["reason"]}
        else:
            value = {"yes": True, "no": False}.get(v["answer"])
            conditions[it["id"]] = {"value": value, "method": "judge", "evidence": ev, "reason": v["reason"]}

    # Branches: claims on the side that did not happen don't apply. `when_all` (nested
    # branches) needs every gate to hold; any gate that didn't hold makes the claim n/a.
    for c in doc["claims"]:
        gates = c.get("when_all") or ([c["when"]] if c.get("when") else [])
        if not gates:
            continue
        values = [(g, conditions.get(g["cond"], {}).get("value")) for g in gates]
        failed = next(((g, v) for g, v in values if v is not None and v != g["value"]), None)
        if failed:
            claims[c["id"]] = {"status": "n/a", "method": "branch", "evidence": None,
                               "reason": f"{failed[0]['cond']} was {failed[1]}"}
        elif any(v is None for _, v in values):
            claims[c["id"]]["status"] = "unclear"
            claims[c["id"]]["reason"] = "branch condition unknown; " + claims[c["id"]]["reason"]

    # Ordering: a claim satisfied before something it must follow is out of order.
    for c in doc["claims"]:
        mine = claims[c["id"]]
        if mine["status"] != "satisfied" or mine["evidence"] is None or c["kind"] == "must_not":
            continue
        for a in c["after"]:
            pred = claims.get(a, {})
            if pred.get("status") in ("satisfied", "out_of_order") and pred.get("evidence") is not None \
                    and pred["evidence"] > mine["evidence"]:
                mine["status"] = "out_of_order"
                mine["reason"] = f"happened at event {mine['evidence']}, before {a} (event {pred['evidence']})"
                break

    return {
        "claims_sha": doc_sha(doc),
        "skill_loaded": trace.get("skill_loaded"),
        "conditions": conditions,
        "claims": claims,
        "judge_items": len(pending),
        "judge_cost_usd": round(judge_cost, 4),
        "judge_cache": cache_out,
    }


def pending_checks(run_dirs: list[Path], docs: dict[str, dict], *, ref_docs: dict[str, dict] | None = None,
                   force: bool = False) -> list[tuple]:
    """(run_dir, trace, merged doc, cache) for every trace without an up-to-date check.json."""
    from skillgraph.trees.reference import merge_docs

    todo = []
    for rd in run_dirs:
        tp, cp = rd / "trace.json", rd / "check.json"
        if not tp.exists():
            continue
        trace = json.loads(tp.read_text())
        if trace["skill"] not in docs:
            continue
        doc = merge_docs(docs[trace["skill"]], (ref_docs or {}).get(trace["skill"]))
        prev = json.loads(cp.read_text()) if cp.exists() else {}
        if not force and prev.get("claims_sha") == doc_sha(doc):
            continue
        todo.append((rd, trace, doc, None if force else prev.get("judge_cache")))
    return todo


async def check_run_dirs(run_dirs: list[Path], docs: dict[str, dict], *, judge_model: str,
                         judge_effort: str | None = None, concurrency: int = 4, force: bool = False,
                         ref_docs: dict[str, dict] | None = None) -> int:
    """Check every run dir that has a trace and no up-to-date check.json. When a skill has a
    reference tree, its claims are graded in the same judge call and stored under "reference"."""
    import asyncio

    from skillgraph.trees.reference import split_result

    sem = asyncio.Semaphore(concurrency)
    todo = pending_checks(run_dirs, docs, ref_docs=ref_docs, force=force)

    async def go(rd, trace, doc, cache):
        async with sem:
            result = await check_trace(trace, doc, judge_model=judge_model, judge_effort=judge_effort, judge_cache=cache)
        result = split_result(result)
        save_json(rd / "check.json", result)
        counts = {}
        for r in result["claims"].values():
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        ref_note = ""
        if "reference" in result:
            done = sum(r["status"] == "satisfied" for r in result["reference"]["claims"].values())
            ref_note = f"; reference {done}/{len(result['reference']['claims'])} satisfied"
        print(f"  checked {rd.relative_to(rd.parents[3])}: {counts}{ref_note}", flush=True)

    if todo:
        print(f"checking {len(todo)} traces (judge model {judge_model})", flush=True)
    await asyncio.gather(*(go(*t) for t in todo))
    return len(todo)
