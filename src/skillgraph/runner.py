"""Run the agent on suite tasks with the skill, without it, or with parts of it removed,
and record a provider-neutral trace of what it did."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from skillgraph import providers
from skillgraph.checker import match_tool
from skillgraph.config import DEFAULTS
from skillgraph.extract import skill_name
from skillgraph.providers import AgentRequest, ModelRef, parse_model
from skillgraph.providers.tools import SECRET_ENV
from skillgraph.schema import load_claims, save_json

SNAPSHOT_SKIP = {".git", ".claude", "__pycache__", "node_modules", ".venv"}
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture Bot", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture Bot", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


# ---------------------------------------------------------------- suite config

@dataclass
class Skill:
    name: str
    dir: Path
    claims_path: Path
    tasks: list[dict]
    reference_path: Path | None = None  # optional reference tree (SOP) for this skill

    def claims(self) -> dict:
        return load_claims(self.claims_path)

    @property
    def ref_claims_path(self) -> Path:
        return self.claims_path.with_name(f"{self.name}.reference.claims.json")

    @property
    def coverage_path(self) -> Path:
        return self.claims_path.with_name(f"{self.name}.coverage.json")


@dataclass
class Suite:
    path: Path
    skills: list[Skill]
    models: dict = field(default_factory=dict)

    def skill(self, name: str) -> Skill:
        for s in self.skills:
            if s.name == name:
                return s
        raise KeyError(f"no skill named {name!r} in {self.path}")


def load_suite(path: str | Path) -> Suite:
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text()) or {}
    base = path.parent
    skills = []
    for s in raw.get("skills", []):
        sdir = (base / s["path"]).resolve()
        if not (sdir / "SKILL.md").exists():
            raise FileNotFoundError(f"{sdir}/SKILL.md not found (from {path})")
        name = skill_name(sdir, (sdir / "SKILL.md").read_text())
        claims = (base / s.get("claims", f"claims/{name}.claims.json")).resolve()
        reference = (base / s["reference"]).resolve() if s.get("reference") else None
        tasks = []
        for t in s.get("tasks", []):
            t = dict(t)
            t["fixture"] = str((base / t["fixture"]).resolve())
            tasks.append(t)
        skills.append(Skill(name, sdir, claims, tasks, reference))
    return Suite(path, skills, raw.get("models") or {})


# ---------------------------------------------------------------- run sets

def save_runset(out: Path, suite: Suite, agent: ModelRef | str, trigger: str) -> None:
    """runset.json links a run directory to its suites. Paths are stored relative to the
    run directory so a repo with its runs can move. Several suites (for example a
    hand-written one and a generated one) can share a run directory: skills merge."""
    out = out.resolve()
    rel = lambda p: os.path.relpath(p, out)  # noqa: E731
    meta_path = out / "runset.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.setdefault("suite", rel(suite.path))
    meta.update({"model": str(agent), "trigger": trigger})
    skills = meta.setdefault("skills", {})
    for s in suite.skills:
        skills[s.name] = {"suite": rel(suite.path), "dir": rel(s.dir), "claims": rel(s.claims_path),
                          **({"reference": rel(s.reference_path)} if s.reference_path else {})}
    save_json(meta_path, meta)


def load_runset(out: Path) -> dict:
    out = out.resolve()
    path = out / "runset.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `skillgraph run` first")
    meta = json.loads(path.read_text())
    meta["suite"] = str((out / meta["suite"]).resolve())
    for info in meta["skills"].values():
        for key in ("suite", "dir", "claims", "reference"):
            if info.get(key):
                info[key] = str((out / info[key]).resolve())
    return meta


# ---------------------------------------------------------------- variants

@dataclass
class Variant:
    name: str
    kind: str  # "skill" | "baseline" | "ablate"
    remove: list[list[int]] = field(default_factory=list)  # 1-based inclusive line ranges

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "remove": self.remove}

    @staticmethod
    def parse(name: str) -> Variant:
        if name in ("skill", "baseline"):
            return Variant(name, name)
        m = re.fullmatch(r"ablate-L(\d+)-(\d+)", name)
        if m:
            return Variant.ablate(int(m[1]), int(m[2]))
        raise ValueError(f"unknown variant {name!r} (use skill, baseline, or ablate-L<a>-<b>)")

    @staticmethod
    def ablate(a: int, b: int) -> Variant:
        if a < 1 or b < a:
            raise ValueError(f"bad line range {a}-{b}: lines start at 1 and the end can't come before the start")
        return Variant(f"ablate-L{a}-{b}", "ablate", [[a, b]])


def ablate_text(text: str, remove: list[list[int]]) -> str:
    drop = {ln for a, b in remove for ln in range(a, b + 1)}
    lines = text.splitlines(keepends=True)
    return "".join(line for i, line in enumerate(lines, 1) if i not in drop)


# ---------------------------------------------------------------- workspace

def clean_env() -> dict:
    """The environment for fixture scripts and success commands: no API keys."""
    return {**{k: v for k, v in os.environ.items() if k not in SECRET_ENV}, **GIT_ENV}


def _git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=ws, env=clean_env(), capture_output=True, text=True).stdout


class FixtureError(RuntimeError):
    pass


def prepare_workspace(ws: Path, task: dict, skill: Skill, variant: Variant) -> list[str]:
    """Fresh git workspace from the fixture; the skill is installed unless this is the baseline.
    Returns warnings about the fixture."""
    if ws.exists():
        shutil.rmtree(ws)
    if not Path(task["fixture"]).is_dir():
        raise FixtureError(f"fixture directory {task['fixture']} doesn't exist")
    shutil.copytree(task["fixture"], ws)
    warnings = []
    claude_dir = ws / ".claude"
    if claude_dir.exists() and any(claude_dir.glob("settings*.json")) or (claude_dir / "hooks").exists():
        warnings.append("the fixture ships Claude Code project settings or hooks (.claude/), which apply to the agent run")
    setup = ws / "setup.sh"
    if setup.exists():
        try:
            proc = subprocess.run(["bash", "setup.sh"], cwd=ws, env=clean_env(), capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise FixtureError("fixture setup.sh timed out after 300s") from None
        setup.unlink()
        if proc.returncode != 0:
            raise FixtureError(f"fixture setup.sh failed (exit {proc.returncode}): {proc.stderr[-500:].strip()}")
    if not (ws / ".git").exists():
        _git(ws, "init", "-q")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-qm", "initial import")
    _git(ws, "config", "user.name", GIT_ENV["GIT_AUTHOR_NAME"])
    _git(ws, "config", "user.email", GIT_ENV["GIT_AUTHOR_EMAIL"])
    with open(ws / ".git" / "info" / "exclude", "a") as fh:
        fh.write("\n.claude/\n")
    if variant.kind != "baseline":
        dst = ws / ".claude" / "skills" / skill.name
        shutil.copytree(skill.dir, dst, ignore=shutil.ignore_patterns("__pycache__"))
        if variant.remove:
            md = dst / "SKILL.md"
            md.write_text(ablate_text(md.read_text(), variant.remove))
    return warnings


def snapshot_files(ws: Path, limit: int = 20000) -> dict[str, str | None]:
    files: dict[str, str | None] = {}
    for p in sorted(ws.rglob("*")):
        rel = p.relative_to(ws)
        if any(part in SNAPSHOT_SKIP for part in rel.parts) or not p.is_file():
            continue
        try:
            files[rel.as_posix()] = p.read_text()[:limit] if p.stat().st_size < 500_000 else None
        except (UnicodeDecodeError, OSError):
            files[rel.as_posix()] = None
    return files


def run_success_checks(ws: Path, task: dict) -> dict:
    results = {}
    fixture = Path(task["fixture"])
    for chk in task.get("success", []):
        if "file" in chk:
            p = ws / chk["file"]
            ok = p.exists()
            if ok and "contains" in chk:
                ok = re.search(chk["contains"], p.read_text(errors="replace"), re.I | re.M) is not None
            label = f"file {chk['file']}" + (f" ~ /{chk['contains']}/" if "contains" in chk else "")
        elif "unchanged" in chk:
            a, b = ws / chk["unchanged"], fixture / chk["unchanged"]
            ok = a.exists() and b.exists() and a.read_bytes() == b.read_bytes()
            label = f"unchanged {chk['unchanged']}"
        elif "cmd" in chk:
            try:
                proc = subprocess.run(chk["cmd"], shell=True, cwd=ws, env=clean_env(), capture_output=True, text=True, timeout=120)
                ok = proc.returncode == 0
            except subprocess.TimeoutExpired:
                ok = False
            label = f"cmd {chk['cmd']}"
        else:
            continue
        results[chk.get("name", label)] = ok
    return results


# ---------------------------------------------------------------- enforcement

def make_deny(doc: dict):
    """Deny function for mechanically checkable must_not rules (used by --enforce).
    Returns (deny_fn, number_of_rules)."""
    rules = [c for c in doc["claims"]
             if c["kind"] == "must_not" and c["check"]["type"] == "tool" and not c["check"]["output_any"]]

    def deny(tool: str, tool_input: dict) -> str | None:
        event = {"tool": tool, "input": tool_input, "output": ""}
        for c in rules:
            if match_tool(c["check"], event):
                return f"Blocked by a skill rule ({c['id']}): {c['text']}"
        return None

    return deny, len(rules)


# ---------------------------------------------------------------- runs

@dataclass
class RunConfig:
    agent: ModelRef = field(default_factory=lambda: parse_model(DEFAULTS["agent"]))
    effort: str | None = None
    trigger: str = "invoke"  # "invoke": load the skill by name up front; "auto": the agent decides
    max_turns: int = 40
    budget_usd: float = 3.0
    enforce: bool = False


async def run_one(skill: Skill, task: dict, variant: Variant, rep: int, run_dir: Path, cfg: RunConfig) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "check.json").unlink(missing_ok=True)  # grades of an earlier trace no longer apply
    ws = run_dir / "ws"
    try:
        warnings = prepare_workspace(ws, task, skill, variant)
        setup_error = None
    except (FixtureError, OSError) as exc:
        warnings, setup_error = [], f"{type(exc).__name__}: {exc}"[:1000]

    with_skill = variant.kind != "baseline"
    deny = None
    if cfg.enforce and with_skill and skill.claims_path.exists():
        deny, n_rules = make_deny(skill.claims())
        deny = deny if n_rules else None

    started = time.time()
    req = AgentRequest(
        workspace=ws, prompt=task["prompt"].strip(), model=cfg.agent.model,
        skills=[skill.name] if with_skill else [],
        invoke=skill.name if (with_skill and cfg.trigger == "invoke") else None,
        effort=cfg.effort, max_turns=cfg.max_turns, budget_usd=cfg.budget_usd, deny=deny,
    )
    if setup_error:
        res = providers.AgentResult(error=setup_error)
    else:
        try:
            res = await providers.run_agent(req, cfg.agent)
        except Exception as exc:  # a provider bug shouldn't take down the whole matrix
            res = providers.AgentResult(error=f"{type(exc).__name__}: {exc}"[:1000])

    trace = {
        "skill": skill.name,
        "task": task["id"],
        "task_prompt": task["prompt"].strip(),
        "variant": variant.to_dict(),
        "rep": rep,
        "model": str(cfg.agent),
        "trigger": cfg.trigger if with_skill else None,
        "enforce": deny is not None,
        "init": res.init,
        "events": res.events,
        "blocked": res.blocked,
        "result": res.result,
        "error": res.error,
        "wall_s": round(time.time() - started, 1),
        "skill_loaded": with_skill and not setup_error and _skill_loaded(res.events, skill.name, cfg.trigger),
        "warnings": warnings,
        "files": snapshot_files(ws) if ws.exists() else {},
        "git": {
            "tags": _git(ws, "tag", "-l").split(),
            "log": _git(ws, "log", "--oneline", "-n", "15").splitlines(),
            "status": _git(ws, "status", "--porcelain").splitlines(),
        } if (ws / ".git").exists() else {"tags": [], "log": [], "status": []},
        "success": run_success_checks(ws, task) if not setup_error else {},
    }
    save_json(run_dir / "trace.json", trace)
    return trace


def _skill_loaded(events: list[dict], name: str, trigger: str) -> bool:
    for ev in events:
        if ev["kind"] == "tool" and ev["tool"] == "Skill" and name in json.dumps(ev["input"]):
            return True
        if ev["kind"] == "user" and (name in ev["text"] or "Base directory for this skill" in ev["text"]):
            return True
        if ev["kind"] == "tool" and f"skills/{name}/" in json.dumps(ev["input"]):
            return True
    # Claude Code injects an invoked skill without a tool call or a streamed message,
    # so an invoked run counts as loaded.
    return trigger == "invoke"


def run_dir_for(out: Path, skill: str, task: str, variant: str, rep: int) -> Path:
    return out / skill / task / variant / f"r{rep}"


def plan_jobs(suite: Suite, out: Path, *, variants: list[Variant], runs: int, skills: list[str] | None = None,
              tasks: list[str] | None = None, force: bool = False, retry_errors: bool = False) -> list[tuple]:
    """(skill, task, variant, rep, run_dir) for every run that still needs doing."""
    known_skills = {s.name for s in suite.skills}
    unknown = [n for n in skills or [] if n not in known_skills]
    if unknown:
        raise ValueError(f"no skill named {', '.join(unknown)} in {suite.path} (skills: {', '.join(sorted(known_skills))})")
    known_tasks = {t["id"] for s in suite.skills if not skills or s.name in skills for t in s.tasks}
    unknown = [t for t in tasks or [] if t not in known_tasks]
    if unknown:
        raise ValueError(f"no task named {', '.join(unknown)} (tasks: {', '.join(sorted(known_tasks))})")
    jobs = []
    for s in suite.skills:
        if skills and s.name not in skills:
            continue
        for t in s.tasks:
            if tasks and t["id"] not in tasks:
                continue
            for v in variants:
                for rep in range(1, runs + 1):
                    rd = run_dir_for(out, s.name, t["id"], v.name, rep)
                    tp = rd / "trace.json"
                    if tp.exists() and not force:
                        if not retry_errors:
                            continue
                        try:
                            if not json.loads(tp.read_text()).get("error"):
                                continue
                        except ValueError:
                            pass
                    jobs.append((s, t, v, rep, rd))
    return jobs


async def run_matrix(
    suite: Suite,
    out: Path,
    *,
    variants: list[Variant],
    runs: int,
    cfg: RunConfig,
    skills: list[str] | None = None,
    tasks: list[str] | None = None,
    concurrency: int = 4,
    force: bool = False,
    retry_errors: bool = False,
    max_total_usd: float | None = None,
    jobs: list[tuple] | None = None,
) -> list[Path]:
    if jobs is None:
        jobs = plan_jobs(suite, out, variants=variants, runs=runs, skills=skills, tasks=tasks,
                         force=force, retry_errors=retry_errors)
    out.mkdir(parents=True, exist_ok=True)
    save_runset(out, suite, cfg.agent, cfg.trigger)

    sem = asyncio.Semaphore(concurrency)
    done, spent, skipped = 0, 0.0, 0

    async def go(job):
        nonlocal done, spent, skipped
        s, t, v, rep, rd = job
        async with sem:
            if max_total_usd is not None and spent >= max_total_usd:
                skipped += 1
                return None
            trace = await run_one(s, t, v, rep, rd, cfg)
        cost = (trace["result"] or {}).get("cost_usd") or 0
        spent += cost
        done += 1
        ok = all(trace["success"].values()) if trace["success"] else None
        status = "ERROR " + trace["error"][:100] if trace["error"] else f"success={ok}"
        print(f"  [{done}/{len(jobs)}] {s.name}/{t['id']}/{v.name}/r{rep}: "
              f"{sum(e['kind'] == 'tool' for e in trace['events'])} tool calls, "
              f"{trace['wall_s']}s, ${cost:.3f}, {status}", flush=True)
        for w in trace.get("warnings", []):
            print(f"      warning: {w}", flush=True)
        return rd

    if jobs:
        print(f"running {len(jobs)} agent runs (concurrency {concurrency}, agent {cfg.agent})", flush=True)
    dirs = [d for d in await asyncio.gather(*(go(j) for j in jobs)) if d is not None]
    if skipped:
        print(f"stopped early: spent ${spent:.2f}, at the --max-total-usd limit; {skipped} runs not started "
              f"(rerun the same command later to continue)", flush=True)
    return dirs
