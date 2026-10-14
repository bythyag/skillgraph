"""Claude through the Claude Agent SDK.

The SDK drives a local Claude Code binary, so agent runs use the real Claude Code
harness (its system prompt, tools, and skill loading). It authenticates like the
CLI: ANTHROPIC_API_KEY if set, otherwise the Claude account you logged in with.
"""

from __future__ import annotations

import time

from skillgraph.providers.base import AgentRequest, AgentResult, LLMError, StructuredResult, clip

AGENT_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

# Keep runs isolated from the user's account: no MCP servers, including claude.ai connectors
# (Gmail, Drive, ...), which `tools=` and `setting_sources` don't cover.
ISOLATION = dict(disallowed_tools=["mcp__*"], mcp_servers={}, strict_mcp_config=True,
                 env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"})


def _sdk():
    try:
        import claude_agent_sdk
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise LLMError(
            "The Claude provider needs the Claude Agent SDK: pip install 'skillgraph[claude]' "
            "(or use an openrouter:<model> spec)."
        ) from exc
    return claude_agent_sdk


async def structured(prompt: str, schema: dict, *, system: str, model: str, effort: str | None = None,
                     attempts: int = 2) -> StructuredResult:
    sdk = _sdk()
    options = sdk.ClaudeAgentOptions(
        system_prompt=system,
        tools=[],
        setting_sources=[],
        max_turns=4,  # structured output can take an extra turn to validate
        model=model,
        effort=effort,
        output_format={"type": "json_schema", "schema": schema},
        **ISOLATION,
    )
    last_error, total_cost = "no result", 0.0
    for _ in range(attempts):
        data = None
        try:
            # Drain the stream instead of returning mid-iteration, so the SDK's generator closes cleanly.
            async for message in sdk.query(prompt=prompt, options=options):
                if isinstance(message, sdk.ResultMessage):
                    total_cost += message.total_cost_usd or 0.0
                    if message.structured_output is not None:
                        data = message.structured_output
                    else:
                        last_error = f"{message.subtype}: {(message.result or '')[:300]}"
        except Exception as exc:  # the SDK raises after an error result
            last_error = f"{type(exc).__name__}: {exc}"
        if data is not None:
            return StructuredResult(data, total_cost, f"claude:{model}")
    raise LLMError(f"Claude structured call failed: {last_error}")


def _result_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)


async def run_agent(req: AgentRequest) -> AgentResult:
    sdk = _sdk()
    out = AgentResult()
    tools = AGENT_TOOLS + (["Skill"] if req.skills else [])

    hooks = None
    if req.deny:
        async def pre_tool(input_data, tool_use_id, context):
            tool, tool_input = input_data.get("tool_name", ""), input_data.get("tool_input", {})
            reason = req.deny(tool, tool_input)
            if reason is None:
                return {}
            out.blocked.append({"tool": tool, "input": clip(tool_input, 500), "reason": reason})
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        hooks = {"PreToolUse": [sdk.HookMatcher(hooks=[pre_tool])]}

    options = sdk.ClaudeAgentOptions(
        cwd=str(req.workspace),
        tools=tools,
        allowed_tools=tools,
        permission_mode="dontAsk",
        setting_sources=["project"],  # only the workspace's .claude/: no personal skills or settings
        skills=list(req.skills),
        system_prompt={"type": "preset", "preset": "claude_code"},
        model=req.model,
        effort=req.effort,
        max_turns=req.max_turns,
        max_budget_usd=req.budget_usd,
        hooks=hooks,
        **ISOLATION,
    )
    prompt = f"/{req.invoke} {req.prompt}" if req.invoke else req.prompt

    by_id: dict[str, dict] = {}
    events = out.events
    started = time.time()
    try:
        async for msg in sdk.query(prompt=prompt, options=options):
            if isinstance(msg, sdk.SystemMessage) and msg.subtype == "init":
                tools_seen = msg.data.get("tools") or []
                # Record only what the run may use; connector names are personal and stay out of traces.
                out.init = {"model": msg.data.get("model"), "claude_code_version": msg.data.get("claude_code_version"),
                            "tools": [t for t in tools_seen if not t.startswith("mcp__")],
                            "mcp_tools_visible": sum(t.startswith("mcp__") for t in tools_seen),
                            "skills": list(req.skills), "harness": "claude-code"}
            elif isinstance(msg, sdk.AssistantMessage):
                for block in msg.content:
                    if isinstance(block, sdk.TextBlock) and block.text.strip():
                        events.append({"i": len(events), "kind": "text", "text": clip(block.text)})
                    elif isinstance(block, sdk.ToolUseBlock):
                        ev = {"i": len(events), "kind": "tool", "id": block.id, "tool": block.name,
                              "input": clip(block.input), "output": None, "is_error": None}
                        if msg.parent_tool_use_id:
                            ev["subagent"] = True
                        events.append(ev)
                        by_id[block.id] = ev
            elif isinstance(msg, sdk.UserMessage):
                if isinstance(msg.content, str):
                    if msg.content.strip():
                        events.append({"i": len(events), "kind": "user", "text": clip(msg.content, 1500)})
                    continue
                for block in msg.content:
                    if isinstance(block, sdk.ToolResultBlock):
                        ev = by_id.get(block.tool_use_id)
                        if ev is not None:
                            ev["output"] = clip(_result_text(block.content))
                            ev["is_error"] = bool(block.is_error)
                    elif isinstance(block, sdk.TextBlock) and block.text.strip():
                        events.append({"i": len(events), "kind": "user", "text": clip(block.text, 1500)})
            elif isinstance(msg, sdk.ResultMessage):
                out.result = {
                    "subtype": msg.subtype, "is_error": msg.is_error, "num_turns": msg.num_turns,
                    "duration_ms": msg.duration_ms, "cost_usd": msg.total_cost_usd,
                    "text": clip(msg.result or "", 3000),
                }
    except Exception as exc:
        out.error = f"{type(exc).__name__}: {exc}"[:1000]
    if not out.result:
        out.result = {"subtype": "error", "is_error": True, "num_turns": 0,
                      "duration_ms": int((time.time() - started) * 1000), "cost_usd": 0.0, "text": ""}
    return out
