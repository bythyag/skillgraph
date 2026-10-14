"""Which model plays which role.

skillgraph uses three models, each chosen independently:

    agent      runs the tasks (the model whose skill adherence you are measuring)
    extractor  compiles SKILL.md into claims
    judge      grades the claims that need judgment

Resolution order, first match wins: command-line flag, environment variable
(SKILLGRAPH_AGENT_MODEL, SKILLGRAPH_EXTRACTOR_MODEL, SKILLGRAPH_JUDGE_MODEL),
the suite's `models:` block, then the defaults below.
"""

from __future__ import annotations

import os

from skillgraph.providers import ModelRef, parse_model

ROLES = ("agent", "extractor", "judge")
DEFAULTS = {
    "agent": "claude:claude-opus-5-5",
    "extractor": "claude:claude-opus-5-5",
    "judge": "claude:claude-opus-5-5",
}
ENV = {role: f"SKILLGRAPH_{role.upper()}_MODEL" for role in ROLES}

# Claude models the Agent SDK accepts (aliases like "sonnet" also work).
CLAUDE_MODELS = {
    "claude-fable-5-1": "most capable; slowest and most expensive",
    "claude-opus-5-5": "default; strong agent and grader",
    "claude-sonnet-5-5": "fast and capable; good agent or judge for large suites",
    "claude-haiku-5-5": "cheapest; good for smoke tests",
}


def resolve(role: str, cli: str | None = None, suite_models: dict | None = None) -> ModelRef:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    spec = cli or os.environ.get(ENV[role]) or (suite_models or {}).get(role) or DEFAULTS[role]
    return parse_model(spec)


def describe(suite_models: dict | None = None, **cli: str | None) -> dict[str, tuple[ModelRef, str]]:
    """Resolved model per role and where it came from (for `doctor`)."""
    out = {}
    for role in ROLES:
        if cli.get(role):
            out[role] = (parse_model(cli[role]), "flag")
        elif os.environ.get(ENV[role]):
            out[role] = (parse_model(os.environ[ENV[role]]), ENV[role])
        elif (suite_models or {}).get(role):
            out[role] = (parse_model(suite_models[role]), "suite")
        else:
            out[role] = (parse_model(DEFAULTS[role]), "default")
    return out
