"""Workspace tools for the built-in agent harness.

Names and input fields mirror Claude Code's (Read, Write, Edit, Bash, Glob, Grep,
Skill), so a claims file extracted once checks traces from either provider.
File tools are confined to the workspace. Bash is not sandboxed: like any coding
agent, it runs commands on your machine with the workspace as its directory.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

OUTPUT_LIMIT = 30_000
SECRET_ENV = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}


class ToolError(Exception):
    pass


@dataclass
class SkillInfo:
    name: str
    description: str
    dir: Path

    @property
    def body(self) -> str:
        text = (self.dir / "SKILL.md").read_text()
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                text = text[end + 4:]
        return text.strip()


def parse_frontmatter(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    for line in text[3:end if end > 0 else None].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not line.startswith(" "):
            meta[key.strip()] = value.strip().strip("'\"")
    return meta


def discover_skills(workspace: Path, allowed: list[str]) -> dict[str, SkillInfo]:
    found = {}
    for md in sorted((workspace / ".claude" / "skills").glob("*/SKILL.md")):
        meta = parse_frontmatter(md.read_text())
        name = meta.get("name") or md.parent.name
        if name in allowed:
            found[name] = SkillInfo(name, meta.get("description", ""), md.parent)
    return found


def _fn(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
    }}


class ToolBox:
    def __init__(self, workspace: Path, skills: dict[str, SkillInfo]):
        self.ws = workspace.resolve()
        self.skills = skills

    # ------------------------------------------------------------ schemas

    def schemas(self) -> list[dict]:
        s = {"type": "string"}
        tools = [
            _fn("Read", "Read a text file. Returns lines prefixed with line numbers.",
                {"file_path": {**s, "description": "Path to the file, absolute or relative to the working directory"},
                 "offset": {"type": "integer", "description": "1-based line to start from"},
                 "limit": {"type": "integer", "description": "Number of lines to read (default 2000)"}},
                ["file_path"]),
            _fn("Write", "Create or overwrite a file with the given content.",
                {"file_path": s, "content": s}, ["file_path", "content"]),
            _fn("Edit", "Replace an exact string in a file. old_string must match exactly once unless replace_all is true.",
                {"file_path": s, "old_string": s, "new_string": s, "replace_all": {"type": "boolean"}},
                ["file_path", "old_string", "new_string"]),
            _fn("Bash", "Run a shell command in the working directory and return its output.",
                {"command": s, "timeout": {"type": "integer", "description": "Seconds before the command is stopped (default 120)"}},
                ["command"]),
            _fn("Glob", "List files matching a glob pattern such as **/*.py.",
                {"pattern": s, "path": {**s, "description": "Directory to search (default: working directory)"}},
                ["pattern"]),
            _fn("Grep", "Search file contents with a regular expression. Returns path:line: text matches.",
                {"pattern": s, "path": s, "glob": {**s, "description": "Only search files matching this glob"},
                 "case_insensitive": {"type": "boolean"}},
                ["pattern"]),
        ]
        if self.skills:
            tools.append(_fn("Skill", "Load a skill's instructions by name. Do this before starting a task that matches the skill.",
                             {"skill": {"type": "string", "enum": sorted(self.skills)}}, ["skill"]))
        return tools

    # ------------------------------------------------------------ dispatch

    def run(self, name: str, args: dict) -> str:
        handlers = {"Read": self._read, "Write": self._write, "Edit": self._edit, "Bash": self._bash,
                    "Glob": self._glob, "Grep": self._grep}
        if self.skills:
            handlers["Skill"] = self._skill
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"Unknown tool: {name}")
        try:
            out = handler(**args)
        except UnicodeDecodeError:
            raise ToolError("That file isn't text (it couldn't be decoded as UTF-8)") from None
        except OSError as exc:  # a directory where a file was expected, permissions, ...
            raise ToolError(f"{type(exc).__name__}: {exc.strerror or exc}") from None
        return out if len(out) <= OUTPUT_LIMIT else out[:OUTPUT_LIMIT] + f"\n... [output truncated, {len(out) - OUTPUT_LIMIT} more chars]"

    def _path(self, file_path: str) -> Path:
        p = Path(file_path)
        p = (p if p.is_absolute() else self.ws / p).resolve()
        if p != self.ws and self.ws not in p.parents:
            raise ToolError(f"{file_path} is outside the working directory")
        return p

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.ws).as_posix()

    # ------------------------------------------------------------ tools

    def _read(self, file_path: str, offset: int = 1, limit: int = 2000) -> str:
        p = self._path(file_path)
        if not p.is_file():
            raise ToolError(f"File does not exist: {file_path}")
        lines = p.read_text(errors="replace").splitlines()
        try:
            start, limit = max(1, int(offset or 1)), max(1, int(limit or 2000))
        except (TypeError, ValueError):
            raise ToolError("offset and limit must be numbers") from None
        chunk = lines[start - 1:start - 1 + limit]
        if not chunk:
            return "(empty file)" if not lines else f"(no lines at offset {start}; file has {len(lines)})"
        return "\n".join(f"{n:6d}\t{line}" for n, line in enumerate(chunk, start))

    def _write(self, file_path: str, content: str) -> str:
        p = self._path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content)
        return f"{'Updated' if existed else 'Created'} {self._rel(p)} ({len(content.splitlines())} lines)"

    def _edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        p = self._path(file_path)
        if not p.is_file():
            raise ToolError(f"File does not exist: {file_path}")
        if not old_string:
            raise ToolError("old_string is empty; use Write to replace a whole file")
        text = p.read_text()
        count = text.count(old_string)
        if count == 0:
            raise ToolError("old_string not found in file")
        if count > 1 and not replace_all:
            raise ToolError(f"old_string matches {count} times; add context or set replace_all")
        p.write_text(text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1))
        return f"Edited {self._rel(p)} ({count if replace_all else 1} replacement{'s' if replace_all and count > 1 else ''})"

    def _bash(self, command: str, timeout: int = 120) -> str:
        env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
        try:
            proc = subprocess.run(command, shell=True, cwd=self.ws, env=env, capture_output=True, text=True,
                                  timeout=max(1, min(int(timeout or 120), 600)))
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {timeout}s") from None
        out = (proc.stdout + ("\n" + proc.stderr if proc.stderr else "")).strip()
        if proc.returncode != 0:
            raise ToolError(f"Exit code {proc.returncode}\n{out}")
        return out or "(no output)"

    def _iter_files(self, base: Path):
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                yield Path(root) / f

    def _glob(self, pattern: str, path: str = ".") -> str:
        base = self._path(path)
        hits = [self._rel(p) for p in self._iter_files(base)
                if fnmatch.fnmatch(p.relative_to(base).as_posix(), pattern)
                or (pattern.startswith("**/") and fnmatch.fnmatch(p.relative_to(base).as_posix(), pattern[3:]))]
        return "\n".join(hits[:500]) or "No files found"

    def _grep(self, pattern: str, path: str = ".", glob: str = "", case_insensitive: bool = False) -> str:
        try:
            rx = re.compile(pattern, re.I if case_insensitive else 0)
        except re.error as exc:
            raise ToolError(f"Invalid regex: {exc}") from None
        base = self._path(path)
        files = [base] if base.is_file() else self._iter_files(base)
        hits = []
        for p in files:
            if glob and not fnmatch.fnmatch(p.name, glob):
                continue
            try:
                for n, line in enumerate(p.read_text().splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{self._rel(p)}:{n}: {line[:300]}")
                        if len(hits) >= 300:
                            return "\n".join(hits) + "\n... [more matches omitted]"
            except (UnicodeDecodeError, OSError):
                continue
        return "\n".join(hits) or "No matches found"

    def _skill(self, skill: str) -> str:
        info = self.skills.get(skill)
        if info is None:
            raise ToolError(f"Unknown skill: {skill}")
        return skill_prompt(info)


def skill_prompt(info: SkillInfo, args: str = "") -> str:
    """What the agent sees when a skill loads (mirrors Claude Code's wording)."""
    text = f"Base directory for this skill: {info.dir}\n\n{info.body}"
    return text + (f"\n\nARGUMENTS: {args}" if args else "")
