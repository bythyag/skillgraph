import pytest

from skillgraph import config
from skillgraph.providers import ModelRef, parse_model
from skillgraph.providers.base import extract_json, validate


@pytest.mark.parametrize("spec, expected", [
    ("claude:claude-opus-5-5", ModelRef("claude", "claude-opus-5-5")),
    ("openrouter:openai/gpt-6.1-sol", ModelRef("openrouter", "openai/gpt-6.1-sol")),
    ("openrouter:meta-llama/llama-3.3-70b-instruct:free", ModelRef("openrouter", "meta-llama/llama-3.3-70b-instruct:free")),
    ("claude-sonnet-5-5", ModelRef("claude", "claude-sonnet-5-5")),
    ("google/gemini-3.8-flash", ModelRef("openrouter", "google/gemini-3.8-flash")),
    ("sonnet", ModelRef("claude", "sonnet")),
])
def test_parse_model(spec, expected):
    assert parse_model(spec) == expected
    assert str(parse_model(str(expected))) == str(expected)


def test_parse_model_rejects_empty_model():
    with pytest.raises(ValueError):
        parse_model("openrouter:")


def test_resolution_order(monkeypatch):
    suite = {"judge": "openrouter:google/gemini-3.8-flash"}
    assert str(config.resolve("judge", None, suite)) == "openrouter:google/gemini-3.8-flash"
    assert str(config.resolve("judge", None, {})) == config.DEFAULTS["judge"]
    monkeypatch.setenv("SKILLGRAPH_JUDGE_MODEL", "claude:claude-haiku-5-5")
    assert str(config.resolve("judge", None, suite)) == "claude:claude-haiku-5-5"
    assert str(config.resolve("judge", "openrouter:x/y", suite)) == "openrouter:x/y"
    sources = {role: src for role, (_, src) in config.describe(suite, agent="claude:claude-fable-5-1").items()}
    assert sources == {"agent": "flag", "extractor": "default", "judge": "SKILLGRAPH_JUDGE_MODEL"}


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('result: {"a": {"b": [1, 2]}} trailing') == {"a": {"b": [1, 2]}}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_validate_subset():
    schema = {"type": "object", "required": ["k", "n"], "properties": {
        "k": {"type": "string", "enum": ["a", "b"]},
        "n": {"type": "integer"},
        "xs": {"type": "array", "items": {"type": "boolean"}},
    }}
    assert validate({"k": "a", "n": 1, "xs": [True]}, schema) == []
    problems = validate({"k": "c", "n": True, "xs": [1]}, schema)
    assert len(problems) == 3
    assert validate({"k": "a"}, schema) == ["$: missing 'n'"]
