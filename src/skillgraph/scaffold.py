"""`skillgraph init` (scaffold an eval suite for a skill) and `install-skill`."""

from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path

from skillgraph.extract import skill_name

SUITE_TEMPLATE = """\
# skillgraph suite for the `{name}` skill.
# Each task copies its fixture into a fresh git workspace, runs the agent on the
# prompt (with the skill, without it, and with parts removed), then runs the
# success checks inside that workspace. See the skillgraph README for details.

# models:                                   # optional; flags and env vars override these
#   agent: claude:claude-opus-5-5           # the model whose skill use you are measuring
#   extractor: claude:claude-opus-5-5       # compiles SKILL.md into claims
#   judge: openrouter:google/gemini-3.8-flash  # grades the claims that need judgment

skills:
  - path: {skill_path}
    claims: claims/{name}.claims.json      # lockfile written by `skillgraph extract`
    tasks:
      - id: example
        fixture: fixtures/example          # files the agent starts with; add setup.sh to script git history
        prompt: >
          TODO: write a request a real user would make that this skill should handle.
        success:                           # checks that the task itself got done
          - file: TODO_OUTPUT.md           # exists (add `contains: <regex>` to check content)
        # - unchanged: data/input.csv      # byte-identical to the fixture
        # - cmd: python3 -m unittest       # exits 0
"""

FIXTURE_README = """\
Files in this directory are copied into the agent's workspace before each run.

Put whatever the task needs here: source files, data, configs. If the task needs
git history (tags, commits), add a `setup.sh` that builds it; it runs once inside
the fresh workspace and is deleted before the agent starts.

Delete this README once the fixture has real content, or the agent will see it.
"""


def init_suite(skill_dir: Path, out_dir: Path, force: bool = False) -> Path:
    skill_dir = skill_dir.resolve()
    md = skill_dir / "SKILL.md"
    if not md.exists():
        raise FileNotFoundError(f"{md} not found; point init at the skill's directory")
    suite = out_dir / "suite.yaml"
    if suite.exists() and not force:
        raise FileExistsError(f"{suite} already exists (use --force to overwrite)")
    name = skill_name(skill_dir, md.read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = os.path.relpath(skill_dir, out_dir.resolve())
    skill_path = skill_dir.as_posix() if rel.startswith(os.path.join("..", "..", "..")) else Path(rel).as_posix()
    suite.write_text(SUITE_TEMPLATE.format(name=name, skill_path=f'"{skill_path}"'))
    fixture = out_dir / "fixtures" / "example"
    fixture.mkdir(parents=True, exist_ok=True)
    if not any(fixture.iterdir()):
        (fixture / "README.md").write_text(FIXTURE_README)
    (out_dir / "claims").mkdir(exist_ok=True)
    return suite


def bundled_skill_dir() -> Path:
    """The skillgraph agent skill: packaged copy in a wheel, or skills/ in a checkout."""
    packaged = resources.files("skillgraph") / "_skill"
    if packaged.is_dir():
        return Path(str(packaged))
    checkout = Path(__file__).resolve().parents[2] / "skills" / "skillgraph"
    if (checkout / "SKILL.md").exists():
        return checkout
    raise FileNotFoundError("couldn't find the bundled skillgraph skill")


def install_skill(target_root: Path, force: bool = False) -> Path:
    """Copy the skillgraph skill into <target_root>/.claude/skills/skillgraph."""
    dst = target_root / ".claude" / "skills" / "skillgraph"
    if dst.exists():
        if not force:
            raise FileExistsError(f"{dst} already exists (use --force to replace it)")
        shutil.rmtree(dst)
    shutil.copytree(bundled_skill_dir(), dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst
