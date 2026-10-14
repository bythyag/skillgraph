"""The build spec quotes prompts and schemas verbatim; keep it in sync with the code."""

import json

from conftest import ROOT

from skillgraph import checker, extract, schema
from skillgraph.providers import harness
from skillgraph.trees import coverage, reference, taskgen

SPEC = (ROOT / "skills" / "skillgraph" / "references" / "build-spec.md").read_text()


def test_spec_quotes_prompts_and_schemas():
    for text in (extract.SYSTEM, extract.PROMPT, checker.JUDGE_SYSTEM, checker.JUDGE_INSTRUCTIONS,
                 harness.SYSTEM, harness.SKILLS.strip("\n"),
                 json.dumps(schema.EXTRACTION_SCHEMA, indent=2), json.dumps(checker.JUDGE_SCHEMA, indent=2),
                 reference.CONVERT_SYSTEM, reference.CONVERT_PROMPT, json.dumps(reference.REFERENCE_SCHEMA, indent=2),
                 reference.COMPILE_SYSTEM, reference.COMPILE_PROMPT, json.dumps(reference.COMPILE_SCHEMA, indent=2),
                 coverage.ALIGN_SYSTEM, coverage.ALIGN_PROMPT, json.dumps(coverage.ALIGN_SCHEMA, indent=2),
                 taskgen.TASK_SYSTEM, taskgen.TASK_PROMPT, json.dumps(taskgen.TASK_SCHEMA, indent=2)):
        assert text in SPEC, f"build-spec.md is out of date; it no longer contains: {text[:80]!r}"


def test_skill_frontmatter():
    md = (ROOT / "skills" / "skillgraph" / "SKILL.md").read_text()
    head = md.split("---")[1]
    assert "name: skillgraph" in head and "description:" in head
    for ref in ("references/build-spec.md", "references/writing-suites.md", "references/importing-traces.md",
                "references/reference-trees.md"):
        assert ref in md and (ROOT / "skills" / "skillgraph" / ref).exists()
