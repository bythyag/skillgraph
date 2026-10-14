"""Render a run set as one self-contained HTML report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from skillgraph.aggregate import QUADRANTS, aggregate_skill, collect
from skillgraph.runner import load_runset
from skillgraph.schema import load_claims
from skillgraph.trees.reference import load_reference

TEMPLATE = Path(__file__).parent / "templates" / "report.html"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
</head>
<body>
{body}
</body>
</html>
"""


def build_data(out: Path) -> dict:
    meta = load_runset(out)
    rows = collect(out)
    skills = []
    for name, info in meta["skills"].items():
        claims_path = Path(info["claims"])
        if not claims_path.exists() or not any(r["trace"]["skill"] == name and r["check"] for r in rows):
            continue
        ref = ref_doc = cov = None
        if info.get("reference") and Path(info["reference"]).exists():
            ref = load_reference(info["reference"])
            ref_claims = claims_path.with_name(f"{name}.reference.claims.json")
            cov_path = claims_path.with_name(f"{name}.coverage.json")
            ref_doc = load_claims(ref_claims) if ref_claims.exists() else None
            cov = json.loads(cov_path.read_text()) if cov_path.exists() else None
        skills.append(aggregate_skill(name, load_claims(claims_path), Path(info["dir"]), rows,
                                      ref=ref, ref_doc=ref_doc, coverage=cov))
    return {
        "runset": out.resolve().name,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "model": meta.get("model"),
        "trigger": meta.get("trigger"),
        "quadrants": QUADRANTS,
        "skills": skills,
    }


def render(data: dict, *, fragment: bool = False) -> str:
    # Escape every "<" so no data (a SKILL.md line, a judge reason) can end or confuse the <script> block.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    body = TEMPLATE.read_text().replace("/*__SKILLGRAPH_DATA__*/null", payload)
    return body if fragment else PAGE.format(body=body)


def write_report(out: Path, path: Path, *, fragment: bool = False) -> Path:
    path.write_text(render(build_data(out), fragment=fragment))
    return path
