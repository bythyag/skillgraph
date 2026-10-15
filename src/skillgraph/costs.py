"""Cost estimates before anything is spent, and the confirmation gate.

Estimates come from the most specific source available: the average cost of earlier
runs with the same model in the run directory, then OpenRouter's public per-token
prices with typical token counts, then rough per-model defaults. They are estimates;
`--max-total-usd` is the hard limit.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from skillgraph.providers import ModelRef

# Rough USD per call when there's no history: (agent run, judge call, extractor call).
CLAUDE_DEFAULTS = {
    "fable": (2.50, 0.60, 0.80),
    "opus": (1.00, 0.30, 0.30),
    "sonnet": (0.50, 0.15, 0.15),
    "haiku": (0.12, 0.03, 0.04),
}
UNKNOWN = (0.50, 0.10, 0.15)
# Typical tokens per call (input, output) for pricing OpenRouter models from their rate card.
TOKENS = {"agent": (60_000, 4_000), "judge": (15_000, 1_500), "extract": (6_000, 4_000)}


@dataclass
class Line:
    what: str
    count: int
    each: float
    basis: str

    @property
    def total(self) -> float:
        return self.count * self.each


@dataclass
class Estimate:
    lines: list[Line] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(line.total for line in self.lines)

    def add(self, what: str, count: int, each: float, basis: str) -> None:
        if count:
            self.lines.append(Line(what, count, each, basis))

    def render(self) -> str:
        if not self.lines:
            return "nothing to run (every result already exists)"
        rows = [f"  {ln.count:>4} x {ln.what:<26} ≈ ${ln.each:6.2f} each = ${ln.total:7.2f}   ({ln.basis})" for ln in self.lines]
        return "\n".join(rows + [f"  estimated total ≈ ${self.total:.2f} at API prices"])


def _family(model: str) -> str | None:
    for fam in CLAUDE_DEFAULTS:
        if fam in model.lower():
            return fam
    return None


_rate_card: dict | None = None


def _openrouter_price(model: str, role: str) -> float | None:
    """Price one call from OpenRouter's public model list (no key needed)."""
    global _rate_card
    if _rate_card is None:
        _rate_card = {}
        try:
            import httpx

            from skillgraph.providers.openrouter import base_url
            resp = httpx.get(f"{base_url()}/models", timeout=10)
            for m in resp.json().get("data", []):
                p = m.get("pricing") or {}
                _rate_card[m["id"]] = (float(p.get("prompt") or 0), float(p.get("completion") or 0))
        except Exception:
            _rate_card = {}
    price = _rate_card.get(model)
    if not price:
        return None
    tin, tout = TOKENS[role]
    return price[0] * tin + price[1] * tout


def per_call(ref: ModelRef, role: str) -> tuple[float, str]:
    """(USD per call, where the number came from) for role agent | judge | extract."""
    idx = {"agent": 0, "judge": 1, "extract": 2}[role]
    if ref.provider == "openrouter":
        price = _openrouter_price(ref.model, role)
        if price is not None:
            return price, "OpenRouter rate card, typical token counts"
    fam = _family(ref.model)
    if fam:
        return CLAUDE_DEFAULTS[fam][idx], f"typical {fam} cost"
    return UNKNOWN[idx], "rough guess for an unknown model"


def history(run_dirs: list[Path], agent: ModelRef, judge: ModelRef) -> tuple[float | None, float | None, int]:
    """Average agent-run and judge cost from earlier runs with the same models."""
    agent_costs, judge_costs = [], []
    for out in run_dirs:
        if not out.exists():
            continue
        for tp in out.glob("*/*/*/r*/trace.json"):
            try:
                t = json.loads(tp.read_text())
            except ValueError:
                continue
            same = t.get("model") in (str(agent), agent.model)
            cost = (t.get("result") or {}).get("cost_usd")
            if same and cost:
                agent_costs.append(cost)
            cp = tp.with_name("check.json")
            if same and cp.exists():
                try:
                    jc = json.loads(cp.read_text()).get("judge_cost_usd")
                except ValueError:
                    jc = None
                if jc:
                    judge_costs.append(jc)
    avg = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    return avg(agent_costs), avg(judge_costs), len(agent_costs)


def estimate(*, agent_runs: int, agent: ModelRef, judge: ModelRef, history_dirs: list[Path],
             judge_calls: int | None = None, extract_calls: int = 0, extractor: ModelRef | None = None,
             digest_calls: int = 0, digest: ModelRef | None = None) -> Estimate:
    est = Estimate()
    hist_agent, hist_judge, n = history(history_dirs, agent, judge)
    if hist_agent is not None:
        est.add(f"agent runs ({agent.model})", agent_runs, hist_agent, f"average of {n} earlier runs")
    else:
        each, basis = per_call(agent, "agent")
        est.add(f"agent runs ({agent.model})", agent_runs, each, basis)
    calls = agent_runs if judge_calls is None else judge_calls
    if hist_judge is not None:
        est.add(f"judge calls ({judge.model})", calls, hist_judge, "average of earlier checks")
    else:
        each, basis = per_call(judge, "judge")
        est.add(f"judge calls ({judge.model})", calls, each, basis)
    if extract_calls and extractor is not None:
        each, basis = per_call(extractor, "extract")
        est.add(f"extract/align calls ({extractor.model})", extract_calls, each, basis)
    if digest_calls and digest is not None:
        each, basis = per_call(digest, "judge")  # a router or intent call reads about as much as a judge call
        est.add(f"digest calls ({digest.model})", digest_calls, each, basis)
    return est


class Declined(Exception):
    pass


def confirm(est: Estimate, *, yes: bool, max_total_usd: float | None, what: str = "this") -> None:
    """Print the estimate; stop unless the user agrees. Raises Declined."""
    print(est.render(), flush=True)
    if not est.lines:
        return
    if max_total_usd is not None and est.total > max_total_usd:
        raise Declined(f"the estimate (${est.total:.2f}) is over --max-total-usd ${max_total_usd:.2f}")
    if yes:
        return
    if not sys.stdin.isatty():
        raise Declined("pass --yes to run without a prompt (for scripts and CI)")
    answer = input(f"Run {what}? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise Declined("cancelled")
