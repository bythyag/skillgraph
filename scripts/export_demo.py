"""Export a run directory as a shareable demo: traces and checks without workspaces,
with local paths and account details scrubbed.

    uv run python scripts/export_demo.py runs/demo examples/runs/demo --suite examples/suite.yaml

What gets scrubbed, in every string of trace.json, check.json and runset.json:
  - workspace paths (<anything>/runs/<set>/<skill>/<task>/<variant>/rN/ws) -> /workspace
  - home directories (/Users/<name>, /home/<name>) -> ~
  - temp directories (/private/tmp/..., /tmp/...) -> /tmp/...
  - your login name as a whole word (owner columns of `ls -l`, prompts) -> user
  - MCP tool names from trace["init"] (claude.ai connector names are account details)
The script fails if anything that looks like a local path survives.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import sys
from pathlib import Path

WORKSPACE = re.compile(r"(?<![\w.~-])/[^\n\"'`]*?/runs/[^/\n\"']+/[^/\n\"']+/[^/\n\"']+/[^/\n\"']+/r\d+/ws(?=[/\s\"'`),;:]|$)")
HOME = re.compile(r"/(?:Users|home)/[^/\s\"'`]+")
TMP = re.compile(r"/private/tmp/")
USER = re.compile(rf"\b{re.escape(getpass.getuser())}\b")
LEAK = re.compile(rf"/(?:Users|home)/|scratch-workspaces|claude_ai_|/private/|\b{re.escape(getpass.getuser())}\b")


def scrub_text(text: str) -> str:
    text = WORKSPACE.sub("/workspace", text)
    text = HOME.sub("~", text)
    text = TMP.sub("/tmp/", text)
    return USER.sub("user", text)


def scrub(value):
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {scrub_text(k) if isinstance(k, str) else k: scrub(v) for k, v in value.items()}
    return value


def scrub_trace(trace: dict) -> dict:
    init = dict(trace.get("init") or {})
    tools = init.get("tools") or []
    if any(t.startswith("mcp__") for t in tools):
        init["mcp_tools_visible"] = sum(t.startswith("mcp__") for t in tools)
        init["tools"] = [t for t in tools if not t.startswith("mcp__")]
    if "skills" in init:
        init["skills"] = [trace["skill"]] if trace.get("variant", {}).get("kind") != "baseline" else []
    return scrub({**trace, "init": init})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--suite", type=Path, required=True, help="the suite the runs came from (for relative paths)")
    args = ap.parse_args()

    src, dst = args.src.resolve(), args.dst.resolve()
    if dst.exists():
        shutil.rmtree(dst)
    count = 0
    for tp in sorted(src.glob("*/*/*/r*/trace.json")):
        rel = tp.parent.relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        (dst / rel / "trace.json").write_text(json.dumps(scrub_trace(json.loads(tp.read_text())), indent=2, ensure_ascii=False) + "\n")
        cp = tp.with_name("check.json")
        if cp.exists():
            (dst / rel / "check.json").write_text(json.dumps(scrub(json.loads(cp.read_text())), indent=2, ensure_ascii=False) + "\n")
        count += 1

    meta = json.loads((src / "runset.json").read_text())
    base = args.suite.resolve().parent

    def relocate(p: str) -> str:
        """Point at the same file under the suite's folder, relative to the exported run dir."""
        absolute = (src / p).resolve()
        try:
            return os.path.relpath(base / absolute.relative_to(base), dst)
        except ValueError:
            raise SystemExit(f"{absolute} isn't under the suite's folder {base}") from None

    meta["suite"] = relocate(meta["suite"])
    for info in meta["skills"].values():
        for key in ("suite", "dir", "claims", "reference"):
            if info.get(key):
                info[key] = relocate(info[key])
    (dst / "runset.json").write_text(json.dumps(scrub(meta), indent=2) + "\n")

    leaks = [(f, m.group(0)) for f in dst.rglob("*.json") for m in LEAK.finditer(f.read_text())]
    if leaks:
        for f, hit in leaks[:20]:
            print(f"possible leak in {f.relative_to(dst)}: {hit!r}", file=sys.stderr)
        return 1
    print(f"exported {count} runs to {args.dst} (no workspaces, paths scrubbed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
