"""Draft eval tasks from a reference tree: one task per root-to-leaf path.

Each task gets a realistic prompt, a fixture whose contents make the path's decisions
come out the intended way, and success checks. Tasks are written to a separate
`suite.generated.yaml` (plus fixtures/generated/<task>/) for review; nothing in the
hand-written suite changes. Generated setup.sh scripts run on your machine when the
suite runs, so read them first.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import yaml

from skillgraph import providers
from skillgraph.config import DEFAULTS
from skillgraph.trees.reference import all_paths, path_id

TASK_SYSTEM = (
    "You design evaluation tasks for AI coding agents. Each task is a realistic request plus a small "
    "starting directory, built so that a specific path through a process is the right one to take."
)

TASK_PROMPT = """Design one evaluation task that exercises this path through the "{ref_name}" process.

<process>
{process}
</process>

<path>
{path}
</path>

<skill name="{skill}">
{skill_md}
</skill>

Write:
- id: short kebab-case.
- prompt: what a real user would type. Don't restate the process steps or name the decisions; the agent should have to work them out from the situation.
- files: the starting files (paths relative to the workspace, full contents). Make the facts behind every decision on the path come out as the path says (for example, include a commit message with BREAKING CHANGE if the path takes that branch). Keep it small.
- setup_sh: bash that runs once in the fresh workspace before the agent starts, for state files can't express (git history, tags, permissions). Use `git init -q` and explicit `git add <file>`; never `git add -A`. Leave it "" when files are enough. It must not touch anything outside the workspace or use the network.
- success: 1-3 checks that the task got done, using only these forms: {{"type": "file", "path": ...}}, {{"type": "contains", "path": ..., "pattern": <regex>}}, {{"type": "unchanged", "path": ...}}, {{"type": "cmd", "cmd": <shell command that exits 0 on success>}}. Fill unused fields with "".
- notes: one sentence on what this task tests."""

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "prompt": {"type": "string"},
        "files": {"type": "array", "items": {
            "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"], "additionalProperties": False}},
        "setup_sh": {"type": "string"},
        "success": {"type": "array", "items": {
            "type": "object",
            "properties": {"type": {"type": "string", "enum": ["file", "contains", "unchanged", "cmd"]},
                           "path": {"type": "string"}, "pattern": {"type": "string"}, "cmd": {"type": "string"}},
            "required": ["type", "path", "pattern", "cmd"], "additionalProperties": False}},
        "notes": {"type": "string"},
    },
    "required": ["id", "prompt", "files", "setup_sh", "success", "notes"],
    "additionalProperties": False,
}


def describe_path(ref: dict, path: list[tuple[str, str | None]]) -> str:
    lines = []
    for n, (nid, label) in enumerate(path, 1):
        node = ref["nodes"][nid]
        if label is not None:
            lines.append(f"{n}. decision {nid}: {node['text']} -> {label}")
        else:
            lines.append(f"{n}. {node['kind']} {nid}: {node['text']}")
    return "\n".join(lines)


def describe_process(ref: dict) -> str:
    lines = []
    for nid, n in ref["nodes"].items():
        if n["kind"] == "decision":
            lines.append(f"- {nid} (decision): {n['text']} [" + ", ".join(f"{b['label']} -> {b.get('next')}" for b in n["branches"]) + "]")
        else:
            lines.append(f"- {nid} ({n['kind']}): {n['text']}" + (f" -> {n['next']}" if n.get("next") else ""))
    lines += [f"- rule {c['id']}: {c['text']}" for c in ref["constraints"]]
    return "\n".join(lines)


def _success(checks: list[dict]) -> list[dict]:
    out = []
    for c in checks:
        if c["type"] == "file" and c["path"]:
            out.append({"file": c["path"]})
        elif c["type"] == "contains" and c["path"] and c["pattern"]:
            out.append({"file": c["path"], "contains": c["pattern"]})
        elif c["type"] == "unchanged" and c["path"]:
            out.append({"unchanged": c["path"]})
        elif c["type"] == "cmd" and c["cmd"]:
            out.append({"cmd": c["cmd"]})
    return out


_RISKY = [
    (r"(?<![\w.])/(?!dev/null)[\w.]", "uses an absolute path"),
    (r"(^|\s)(cd|pushd)\s+(\.\.|~|/)", "leaves the workspace"),
    (r"~/|\$HOME", "touches the home directory"),
    (r"\b(curl|wget|ssh|scp|nc|pip|npm|brew|apt(-get)?)\b", "uses the network or installs software"),
    (r"\bsudo\b|\bchmod\s+-R\s+777|\brm\s+-rf\s+[/~]", "runs a dangerous command"),
]


def risky(script: str) -> list[str]:
    """Reasons a generated setup.sh needs a careful look before it runs."""
    import re

    return [msg for pattern, msg in _RISKY if re.search(pattern, script or "", re.M)]


def _safe_rel(path: str) -> Path | None:
    """A relative path inside the fixture, or None. Refuses .claude/ (Claude Code would load
    settings and hooks from it during the run), .git/, and setup.sh (written separately)."""
    p = Path(path)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return None
    if p.parts[0] in (".claude", ".git") or p.as_posix() == "setup.sh":
        return None
    return p


async def generate(ref: dict, *, skill, suite_dir: Path, out_file: Path, only: list[int] | None = None,
                   model: str = DEFAULTS["extractor"], concurrency: int = 4) -> tuple[Path, list[dict]]:
    """Write draft tasks for the selected paths; returns (suite file, task summaries)."""
    paths = all_paths(ref)
    chosen = [(i, p) for i, p in enumerate(paths) if only is None or i in only]
    skill_md = (skill.dir / "SKILL.md").read_text()
    sem = asyncio.Semaphore(concurrency)

    async def one(i, path):
        async with sem:
            res = await providers.structured(
                TASK_PROMPT.format(ref_name=ref["name"], process=describe_process(ref), path=describe_path(ref, path),
                                   skill=skill.name, skill_md=skill_md),
                TASK_SCHEMA, system=TASK_SYSTEM, model=model)
        return i, path, res.data, res.cost_usd

    results = await asyncio.gather(*(one(i, p) for i, p in chosen))
    fixtures_root = suite_dir / "fixtures" / "generated"
    tasks, summaries, used = [], [], set()
    for i, path, data, cost in results:
        tid = f"{path_id(path)}"[:60]
        while tid in used:
            tid += "-b"
        used.add(tid)
        fx = fixtures_root / tid
        fx.mkdir(parents=True, exist_ok=True)
        written, refused = [], []
        for f in data["files"]:
            rel = _safe_rel(f["path"])
            if rel is None:
                refused.append(f["path"])
                continue
            (fx / rel).parent.mkdir(parents=True, exist_ok=True)
            (fx / rel).write_text(f["content"])
            written.append(rel.as_posix())
        script = data["setup_sh"].strip()
        if script:
            if not script.startswith("set -e"):
                script = "set -e\n" + script
            (fx / "setup.sh").write_text(script + "\n")
            written.append("setup.sh")
        if not written:
            (fx / ".keep").write_text("")
        tasks.append({"id": tid, "fixture": os.path.relpath(fx, out_file.parent), "prompt": data["prompt"].strip(),
                      "success": _success(data["success"]), "path": i,
                      "notes": data["notes"]})
        warnings = risky(script) + [f"refused to write {p} (outside the fixture, or under .claude/ or .git/)" for p in refused]
        summaries.append({"id": tid, "path": i, "files": written, "cost_usd": round(cost, 4), "notes": data["notes"],
                          "warnings": warnings})

    rel = lambda p: os.path.relpath(p, out_file.parent)  # noqa: E731
    entry = {"path": rel(skill.dir), "claims": rel(skill.claims_path)}
    if skill.reference_path:
        entry["reference"] = rel(skill.reference_path)
    entry["tasks"] = tasks
    header = ("# Draft tasks generated from the reference tree, one per path. Review prompts, fixtures,\n"
              "# and especially setup.sh scripts before running: they execute on this machine.\n")
    out_file.write_text(header + yaml.safe_dump({"skills": [entry]}, sort_keys=False, allow_unicode=True, width=100))
    return out_file, summaries
