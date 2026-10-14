"""The claims file: a skill compiled into span-anchored, checkable claims.

A claims file is a lockfile. It is generated once by `extract`, reviewed by a
person, committed next to the suite, and regenerated when SKILL.md changes
(the stored source hash tells you when it is stale).

Shape (after normalization):

    {
      "skill": "csv-report",
      "source_sha256": "...",
      "classification": "procedural" | "reference" | "mixed",
      "summary": "...",
      "conditions": [{"id": "k1", "text": ..., "span": [a, b], "check": Check}],
      "claims": [{
          "id": "c1",
          "kind": "step" | "must_not" | "must_read" | "artifact",
          "text": ...,
          "span": [a, b],            # 1-based SKILL.md line range
          "after": ["c0"],           # must happen after these claims
          "when": {"cond": "k1", "value": true} | null,
          "check": Check,
      }],
    }

    Check = {"type": "tool", "tools": [...], "input_any": [...], "output_any": [...]}
          | {"type": "file", "path_glob": "...", "content_any": [...]}
          | {"type": "judge", "question": "..."}
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

CLAIM_KINDS = ["step", "must_not", "must_read", "artifact"]
CHECK_TYPES = ["tool", "file", "judge"]

_str_list = {"type": "array", "items": {"type": "string"}}

# Structured-output schemas are flat and fully required; unused fields are "" or [].
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": CHECK_TYPES},
        "tools": _str_list,
        "input_any": _str_list,
        "output_any": _str_list,
        "path_glob": {"type": "string"},
        "content_any": _str_list,
        "question": {"type": "string"},
    },
    "required": ["type", "tools", "input_any", "output_any", "path_glob", "content_any", "question"],
    "additionalProperties": False,
}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": ["procedural", "reference", "mixed"]},
        "summary": {"type": "string"},
        "conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "line_start": {"type": "integer"},
                    "line_end": {"type": "integer"},
                    "check": CHECK_SCHEMA,
                },
                "required": ["id", "text", "line_start", "line_end", "check"],
                "additionalProperties": False,
            },
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string", "enum": CLAIM_KINDS},
                    "text": {"type": "string"},
                    "line_start": {"type": "integer"},
                    "line_end": {"type": "integer"},
                    "after": _str_list,
                    "when_cond": {"type": "string"},
                    "when_value": {"type": "boolean"},
                    "check": CHECK_SCHEMA,
                },
                "required": [
                    "id", "kind", "text", "line_start", "line_end",
                    "after", "when_cond", "when_value", "check",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["classification", "summary", "conditions", "claims"],
    "additionalProperties": False,
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def doc_sha(doc: dict) -> str:
    """Hash of the parts of a claims doc that affect checking."""
    core = {"conditions": doc["conditions"], "claims": doc["claims"]}
    return sha256_text(json.dumps(core, sort_keys=True))[:16]


def _valid_regexes(patterns: list[str], warnings: list[str], where: str) -> list[str]:
    out = []
    for p in patterns:
        if not p:
            continue
        try:
            re.compile(p)
            out.append(p)
        except re.error as exc:
            warnings.append(f"{where}: invalid regex {p!r} ({exc}); escaped it")
            out.append(re.escape(p))
    return out


def normalize_check(raw: dict, warnings: list[str], where: str) -> dict:
    kind = raw.get("type")
    if kind == "tool":
        return {
            "type": "tool",
            "tools": [t for t in raw.get("tools", []) if t],
            "input_any": _valid_regexes(raw.get("input_any", []), warnings, where),
            "output_any": _valid_regexes(raw.get("output_any", []), warnings, where),
        }
    if kind == "file":
        return {
            "type": "file",
            "path_glob": raw.get("path_glob") or "*",
            "content_any": _valid_regexes(raw.get("content_any", []), warnings, where),
        }
    question = raw.get("question") or ""
    if not question:
        warnings.append(f"{where}: judge check without a question")
    return {"type": "judge", "question": question}


def _span(item: dict, n_lines: int) -> list[int]:
    a = max(1, min(int(item.get("line_start", 1)), n_lines))
    b = max(a, min(int(item.get("line_end", a)), n_lines))
    return [a, b]


def normalize(raw: dict, *, skill: str, source_text: str) -> tuple[dict, list[str]]:
    """Turn the extractor's flat output into a clean claims doc. Returns (doc, warnings)."""
    warnings: list[str] = []
    n_lines = max(1, len(source_text.splitlines()))

    conditions = []
    seen: set[str] = set()
    for i, c in enumerate(raw.get("conditions", []), 1):
        cid = c.get("id") or f"k{i}"
        if cid in seen:
            cid = f"k{i}_{len(seen)}"
        seen.add(cid)
        conditions.append({
            "id": cid,
            "text": c.get("text", "").strip(),
            "span": _span(c, n_lines),
            "check": normalize_check(c.get("check", {}), warnings, cid),
        })
    cond_ids = {c["id"] for c in conditions}

    claims = []
    for i, c in enumerate(raw.get("claims", []), 1):
        cid = c.get("id") or f"c{i}"
        if cid in seen:
            cid = f"c{i}_{len(seen)}"
        seen.add(cid)
        when = None
        if c.get("when_cond"):
            if c["when_cond"] in cond_ids:
                when = {"cond": c["when_cond"], "value": bool(c.get("when_value", True))}
            else:
                warnings.append(f"{cid}: unknown condition {c['when_cond']!r}; treated as unconditional")
        kind = c.get("kind") if c.get("kind") in CLAIM_KINDS else "step"
        claims.append({
            "id": cid,
            "kind": kind,
            "text": c.get("text", "").strip(),
            "span": _span(c, n_lines),
            "after": list(dict.fromkeys(a for a in c.get("after", []) if a)),
            "when": when,
            "check": normalize_check(c.get("check", {}), warnings, cid),
        })

    claim_ids = {c["id"] for c in claims}
    for c in claims:
        unknown = [a for a in c["after"] if a not in claim_ids or a == c["id"]]
        if unknown:
            warnings.append(f"{c['id']}: dropped unknown 'after' refs {unknown}")
            c["after"] = [a for a in c["after"] if a not in unknown]

    doc = {
        "skill": skill,
        "source_sha256": sha256_text(source_text),
        "classification": raw.get("classification", "mixed"),
        "summary": raw.get("summary", ""),
        "conditions": conditions,
        "claims": claims,
    }
    return doc, warnings


def load_claims(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def save_json(path: str | Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def is_stale(doc: dict, skill_md: str) -> bool:
    return doc.get("source_sha256") != sha256_text(skill_md)
