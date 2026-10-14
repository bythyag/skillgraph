"""Compile a SKILL.md into a claims doc with one structured Claude call."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from skillgraph import providers
from skillgraph.config import DEFAULTS
from skillgraph.providers import ModelRef, parse_model
from skillgraph.schema import EXTRACTION_SCHEMA, normalize

SYSTEM = (
    "You compile agent skill files (SKILL.md) into span-anchored, checkable claims for an "
    "evaluation harness. You are precise about line numbers, you never invent instructions "
    "the text does not contain, and you prefer mechanical checks over subjective ones."
)

PROMPT = """Compile this skill into checkable claims.

<skill name="{name}">
<bundled_files>
{bundle}
</bundled_files>
<skill_md>
{numbered}
</skill_md>
</skill>

How the harness uses your output
- An agent (Claude Code) runs a task with this skill loaded. Its trace is a list of events: tool calls (tool name, JSON input, text output) and assistant text. After the run, the harness also sees the files left in the working directory.
- Each claim is checked against the trace. "tool" and "file" checks run mechanically; "judge" checks go to an LLM grader that reads the trace.
- Results are mapped back to the SKILL.md lines in each span, so spans must be tight and accurate.

Claims
- One claim per atomic, independently checkable instruction. Split compound sentences ("validate X, then write Y") into separate claims. Skip frontmatter, headings, and background prose that asks for nothing.
- kind:
  - step: an action the agent should take.
  - must_not: a prohibition ("never", "don't", "do not"). Its check describes the FORBIDDEN behavior, so a match means a violation.
  - must_read: an instruction to read a specific file or reference.
  - artifact: a required output (a file, a section, a format) checkable in the final workspace or answer.
- line_start / line_end: 1-based line numbers from the numbered listing, covering only the lines that state this instruction.
- after: ids of claims the text says must happen before this one ("first", "before", "then", "finally", or a numbered sequence where the order clearly matters). An ordering edge is a testable requirement, so leave it empty when the text does not state or clearly imply an order. When an instruction names its own anchor ("read X before writing the report"), add exactly that edge (on the report claim) and no edge from its list position.
- when_cond / when_value: if the claim applies on only one side of a branch, set when_cond to the condition id and when_value to true (applies when the condition holds) or false (applies otherwise). For unconditional claims use when_cond "" and when_value true.

Conditions
- Create one condition per branch point ("if validation fails", "if there are breaking changes") and give it a check that tells the harness whether the condition held during the run. Claims on both sides of the branch should reference it.
- Phrase each condition as a fact about the task's inputs or environment ("the input CSV contains invalid rows"), not about what the agent observed ("validate.py reported errors"). The harness also runs the task without the skill, where the agent may never run the step that would reveal the condition; the grader can still judge the fact from the trace and the files. Use a judge check unless a tool output reliably reveals the fact in every run.

Checks (fill every field; use "" or [] for the ones a type does not use)
- type "tool": matches one tool call. tools = tool names such as ["Bash"], ["Read", "Bash"], ["Write", "Edit"] ([] = any tool). input_any = Python regexes matched case-insensitively against the JSON-serialized tool input; the call matches if ANY regex matches ([] = no constraint). output_any = regexes against the tool's text output ([] = no constraint). The claim holds if any call matches.
- type "file": checks the final working directory. path_glob = glob relative to it ("report.md", "**/*.md"). content_any = regexes, any of which must match the file content ([] = existence only).
- type "judge": question = a yes/no question a grader can answer from the trace and final files. Phrase it so "yes" means the instruction was followed; for must_not, "yes" means the forbidden thing happened.
- Prefer tool/file checks whenever the instruction names a concrete command, script, file, or path. Write regexes that accept reasonable equivalents (absolute vs relative paths, python vs python3, extra flags) without matching unrelated calls. Reads can happen through Read or Bash (cat, head, sed), so list both.
- Bundled scripts are run from the skill's own directory, often by absolute path, so match on the file name (for example "validate\\\\.py"), not the full path.
- Agents often chain shell commands in one Bash call (`cd x && a; b | c`), so don't anchor command regexes to the start or end of the input.
- Use judge for quality, judgment, or content that a regex cannot capture. Don't force a regex onto a fuzzy instruction.

classification: "procedural" (mostly an ordered workflow), "reference" (mostly knowledge, lookups, or style rules), or "mixed".
summary: one sentence on what the skill makes the agent do.
Use ids c1, c2, ... for claims and k1, k2, ... for conditions."""


def number_lines(text: str) -> str:
    return "\n".join(f"{i:4d}| {line}" for i, line in enumerate(text.splitlines(), 1))


def bundle_files(skill_dir: Path) -> list[str]:
    files = []
    for p in sorted(skill_dir.rglob("*")):
        if p.is_file() and p.name != "SKILL.md" and "__pycache__" not in p.parts:
            files.append(p.relative_to(skill_dir).as_posix())
    return files


def skill_name(skill_dir: Path, skill_md: str) -> str:
    for line in skill_md.splitlines()[:20]:
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return skill_dir.name


async def extract(skill_dir: str | Path, *, model: str | ModelRef = DEFAULTS["extractor"],
                  effort: str | None = None) -> tuple[dict, list[str]]:
    skill_dir = Path(skill_dir)
    source = (skill_dir / "SKILL.md").read_text()
    name = skill_name(skill_dir, source)
    prompt = PROMPT.format(
        name=name,
        bundle="\n".join(bundle_files(skill_dir)) or "(none)",
        numbered=number_lines(source),
    )
    ref = parse_model(model)
    result = await providers.structured(prompt, EXTRACTION_SCHEMA, system=SYSTEM, model=ref, effort=effort)
    doc, warnings = normalize(result.data, skill=name, source_text=source)
    doc["extracted"] = {
        "model": str(ref),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cost_usd": round(result.cost_usd, 4),
    }
    return doc, warnings
