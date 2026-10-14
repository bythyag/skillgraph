"""A small coding-agent loop for OpenAI-compatible chat APIs (used for OpenRouter models).

It gives the model the same tool names Claude Code uses, lists installed skills in
the system prompt, loads a skill through a Skill tool (or up front when invoked by
name), and records the run in skillgraph's provider-neutral trace format.
"""

from __future__ import annotations

import asyncio
import json
import platform
import time
from collections.abc import Awaitable, Callable
from datetime import date

from skillgraph.providers.base import AgentRequest, AgentResult, LLMError, clip
from skillgraph.providers.tools import ToolBox, ToolError, discover_skills, skill_prompt

# chat(messages, tools) -> OpenAI-style response dict (choices[0].message, usage.cost)
ChatFn = Callable[[list[dict], list[dict]], Awaitable[dict]]

SYSTEM = """You are an autonomous coding agent working in a local project directory.

Working directory: {cwd}
Platform: {platform}
Today's date: {today}
The directory is a git repository.

Use the tools to inspect files, change them, and run commands. Relative paths resolve against the working directory. Keep working until the task is done, then reply with a short summary of what you did. Nobody can answer questions during the run, so make reasonable assumptions and mention them in the summary.{skills}"""

SKILLS = """

Skills are packaged instructions for particular kinds of tasks. When a task matches a skill's description, call the Skill tool with its name before you start, then follow its instructions. A skill's files live in its base directory.
<available_skills>
{items}
</available_skills>"""


async def run(req: AgentRequest, chat: ChatFn) -> AgentResult:
    out = AgentResult()
    skills = discover_skills(req.workspace, req.skills)
    box = ToolBox(req.workspace, skills)
    schemas = box.schemas()
    out.init = {"model": req.model, "skills": sorted(skills), "tools": [t["function"]["name"] for t in schemas],
                "harness": "skillgraph"}

    listing = SKILLS.format(items="\n".join(f"- {s.name}: {s.description}" for s in skills.values())) if skills else ""
    messages: list[dict] = [{"role": "system", "content": SYSTEM.format(
        cwd=box.ws, platform=f"{platform.system()} {platform.machine()}", today=date.today().isoformat(), skills=listing)}]

    events = out.events
    if req.invoke and req.invoke in skills:
        # Same effect as typing /skill-name: the instructions arrive before the task.
        loaded = skill_prompt(skills[req.invoke])
        events.append({"i": 0, "kind": "user", "text": clip(f"/{req.invoke}\n{loaded}", 1500)})
        messages.append({"role": "user", "content": f"<command-name>/{req.invoke}</command-name>\n{loaded}\n\nARGUMENTS: {req.prompt}"})
    else:
        messages.append({"role": "user", "content": req.prompt})

    started, cost, turns, final_text = time.time(), 0.0, 0, ""
    subtype = "success"
    try:
        while True:
            if turns >= req.max_turns:
                subtype = "error_max_turns"
                break
            if cost >= req.budget_usd:
                subtype = "error_max_budget_usd"
                break
            resp = await chat(messages, schemas)
            turns += 1
            cost += float((resp.get("usage") or {}).get("cost") or 0.0)
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            text = msg.get("content") or ""
            calls = msg.get("tool_calls") or []

            # Echo the assistant turn back unmodified (reasoning_details included) so
            # reasoning models keep their chain across tool calls.
            assistant = {"role": "assistant", "content": text}
            for key in ("tool_calls", "reasoning_details", "reasoning"):
                if msg.get(key):
                    assistant[key] = msg[key]
            messages.append(assistant)

            if text.strip():
                events.append({"i": len(events), "kind": "text", "text": clip(text)})
                final_text = text
            if not calls:
                break

            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except ValueError as exc:
                    args, parse_error = {}, f"Invalid tool arguments: {exc}"
                else:
                    parse_error = None
                ev = {"i": len(events), "kind": "tool", "id": call.get("id"), "tool": name,
                      "input": clip(args), "output": None, "is_error": None}
                events.append(ev)

                reason = req.deny(name, args) if (req.deny and not parse_error) else None
                if parse_error:
                    result, is_error = parse_error, True
                elif reason:
                    out.blocked.append({"tool": name, "input": clip(args, 500), "reason": reason})
                    result, is_error = f"Blocked: {reason}", True
                else:
                    try:
                        result, is_error = await asyncio.to_thread(box.run, name, args), False
                    except ToolError as exc:
                        result, is_error = str(exc), True
                    except TypeError as exc:  # wrong or missing arguments
                        result, is_error = f"Invalid arguments for {name}: {exc}", True
                    except Exception as exc:  # one bad call must not lose the whole run
                        result, is_error = f"{name} failed: {type(exc).__name__}: {exc}", True
                ev["output"], ev["is_error"] = clip(result), is_error
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": result})
    except LLMError as exc:
        out.error = str(exc)[:1000]
        subtype = "error_during_execution"

    out.result = {
        "subtype": subtype, "is_error": subtype != "success", "num_turns": turns,
        "duration_ms": int((time.time() - started) * 1000), "cost_usd": round(cost, 6),
        "text": clip(final_text, 3000),
    }
    return out
