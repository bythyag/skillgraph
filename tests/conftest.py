from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def tool(i, name, inp, output="", is_error=False):
    return {"i": i, "kind": "tool", "id": f"t{i}", "tool": name, "input": inp, "output": output, "is_error": is_error}


def text(i, body):
    return {"i": i, "kind": "text", "text": body}


def make_trace(events, files=None, prompt="do the task"):
    return {"task_prompt": prompt, "events": events, "files": files or {}, "result": {"text": "done"},
            "git": {"tags": [], "status": [], "log": []}, "skill_loaded": True}


def claim(cid, kind="step", check=None, after=None, when=None, span=(1, 1), text="an instruction"):
    return {"id": cid, "kind": kind, "text": text, "span": list(span), "after": after or [], "when": when,
            "check": check or {"type": "tool", "tools": ["Bash"], "input_any": [cid], "output_any": []}}


def doc(claims, conditions=None, skill="demo"):
    return {"skill": skill, "source_sha256": "x", "classification": "procedural", "summary": "",
            "conditions": conditions or [], "claims": claims}


@pytest.fixture
def examples():
    return EXAMPLES


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    """Tests never reach a real provider."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    for role in ("AGENT", "EXTRACTOR", "JUDGE"):
        monkeypatch.delenv(f"SKILLGRAPH_{role}_MODEL", raising=False)
