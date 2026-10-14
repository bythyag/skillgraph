"""Model providers. Every model is named `provider:model`:

    claude:claude-opus-5-5                 Claude via the Claude Agent SDK (Claude Code harness)
    openrouter:anthropic/claude-sonnet-5.5 any OpenRouter model (skillgraph's own harness)
"""

from __future__ import annotations

from skillgraph.providers.base import (
    PROVIDERS,
    AgentRequest,
    AgentResult,
    LLMError,
    ModelRef,
    StructuredResult,
    parse_model,
)

__all__ = [
    "PROVIDERS", "AgentRequest", "AgentResult", "LLMError", "ModelRef", "StructuredResult",
    "parse_model", "structured", "run_agent",
]


def _module(provider: str):
    if provider == "claude":
        from skillgraph.providers import claude
        return claude
    if provider == "openrouter":
        from skillgraph.providers import openrouter
        return openrouter
    raise LLMError(f"unknown provider {provider!r}; use one of {', '.join(PROVIDERS)}")


async def structured(prompt: str, schema: dict, *, system: str, model: str | ModelRef,
                     effort: str | None = None) -> StructuredResult:
    """One tool-less call that returns JSON matching `schema`."""
    ref = parse_model(model)
    return await _module(ref.provider).structured(prompt, schema, system=system, model=ref.model, effort=effort)


async def run_agent(req: AgentRequest, model: str | ModelRef) -> AgentResult:
    """Run the agent under test. `req.model` is overwritten with the provider's model id."""
    ref = parse_model(model)
    req.model = ref.model
    return await _module(ref.provider).run_agent(req)
