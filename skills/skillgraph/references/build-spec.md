# skillgraph build specification

This document specifies skillgraph completely enough to rebuild it from an empty
directory. Where it quotes prompts, JSON schemas, or formats, copy them exactly:
the tool's behavior depends on them. Everything else (internal function names,
code style) is up to the builder as long as the observable behavior matches.

## 1. What skillgraph does

skillgraph evaluates an agent skill (a `SKILL.md`, optionally with bundled
`scripts/`, `references/`, `assets/`) line by line:

1. **extract**: one structured LLM call compiles SKILL.md into *claims*: atomic,
   checkable instructions, each anchored to a 1-based line span, with optional
   ordering edges and branch conditions. The result is a JSON *lockfile* that a
   person reviews and commits.
2. **lint**: static findings over the claim graph and the skill text.
3. **run**: for each task in a suite, copy a fixture into a fresh git workspace and
   run an agent with the skill installed (`skill`), without it (`baseline`), or
   with line spans deleted (`ablate-L<a>-<b>`). Record a flat trace.
4. **check**: grade every claim in every trace. Mechanical checks first, then one
   judge call per trace for the rest.
5. **ablate**: pick spans automatically, rerun with them removed, grade.
6. **report**: one self-contained HTML page per run set.

The output that matters is a verdict per claim, from its follow rate with and
without the skill (threshold 0.5):

| with skill | without skill | verdict |
|---|---|---|
| followed | followed | inert |
| followed | not followed | working |
| not followed | not followed | ignored |
| not followed | followed | interfering |
| followed | no baseline data | followed |
| no applicable runs | any | untested |

## 2. Repository layout

```
pyproject.toml            hatchling; src layout; console script skillgraph = skillgraph.cli:main
README.md
src/skillgraph/
  __init__.py             __version__
  __main__.py             from skillgraph.cli import main; main()
  cli.py                  argparse CLI (section 12)
  config.py               model roles and resolution (section 4)
  schema.py               claims schema, normalization, hashing, JSON io (section 5)
  extract.py              extractor prompt and call (section 6)
  graph.py                NetworkX graph, lint, Cypher/GraphML export (section 7)
  runner.py               suites, variants, workspaces, run matrix, enforcement (section 8)
  checker.py              mechanical checks, judge, branch/order logic, cache (section 9)
  digest.py               turns, action index, intents, routing, windowed judging (section 9.1)
  aggregate.py            rates, verdicts, ablation analysis (section 10)
  report.py + templates/report.html   HTML report (section 11)
  scaffold.py             init + install-skill
  costs.py                estimates and the spend gate (section 12.1)
  summary.py, diff.py, show.py   result commands (section 12.2)
  dashboard/              __init__.py (local server, JSON API, cache, static export) + static/ (index.html,
                          core.js, pages.js, trace.js, app.js, app.css, tokens.css, favicon.svg) (section 11.1)
  trees/                  mapping.py, runs.py (import), reference.py, coverage.py, taskgen.py (section 13)
  providers/
    __init__.py           dispatch: structured(), run_agent()
    base.py               ModelRef, parse_model, AgentRequest, AgentResult, StructuredResult, LLMError, clip, extract_json, validate
    claude.py             Claude Agent SDK provider
    openrouter.py         OpenRouter provider (HTTP via httpx)
    harness.py            agent loop for OpenAI-compatible chat APIs
    tools.py              Read/Write/Edit/Bash/Glob/Grep/Skill for the harness
skills/skillgraph/        the agent skill (SKILL.md + references/); packaged into the wheel as skillgraph/_skill
examples/                 suite.yaml, skills/, fixtures/, claims/ (three sample workflow skills)
tests/                    pytest, no network
docs/design.md            rationale and prior art
examples/runs/demo/       a scrubbed, graded run set (no workspaces) for free demos and tests
design/                   the dashboard's design system, made with OpenDesigner: DESIGN.md, PRODUCT.md and
                          opendesigner/ (state, decisions, DTCG tokens); static/tokens.css is its CSS export
scripts/validate_digest.py  checks windowed judging against full-trace verdicts on long traces (section 9.1)
scripts/export_demo.py    regenerates it, rewriting workspace paths to /workspace, home dirs to ~, the login name to
                          "user", and dropping MCP tool names; fails if a local path survives
.github/                  ci.yml (ruff + pytest on 3.10-3.14, then build the wheel and use it from a clean venv),
                          dependabot, issue and PR templates
LICENSE (MIT), CONTRIBUTING.md, SECURITY.md, CHANGELOG.md, CITATION.cff, Dockerfile, .dockerignore
```

Dependencies: `httpx>=0.27`, `networkx>=3.3`, `pyyaml>=6.0`; optional extra
`claude = ["claude-agent-sdk>=0.2.165"]`; dev group `pytest>=8`, `ruff>=0.6`. Python >= 3.10.
Version is single-sourced from `src/skillgraph/__init__.py` (`dynamic = ["version"]`).
In `pyproject.toml`, force-include `skills/skillgraph` as `skillgraph/_skill` in
the wheel.

## 3. Suite format (`suite.yaml`)

```yaml
models:                       # optional
  agent: claude:claude-opus-5-5
  extractor: claude:claude-opus-5-5
  judge: openrouter:google/gemini-3.8-flash
skills:
  - path: skills/csv-report                 # directory with SKILL.md, relative to the suite file
    claims: claims/csv-report.claims.json   # lockfile path (default claims/<name>.claims.json)
    reference: references/sop.ref.yaml     # optional reference tree (section 13)
    tasks:
      - id: broken-data
        fixture: fixtures/sales-broken      # copied into the workspace
        prompt: Create the sales report for data/sales.csv.
        success:
          - file: ERRORS.md                 # exists
          - file: RELEASE_NOTES.md          # exists and matches (case-insensitive, multiline)
            contains: v1\.5\.0
          - unchanged: data/sales.csv       # byte-identical to the fixture copy
          - cmd: python3 -m unittest        # shell command exits 0 within 120 s
```

The skill name is the `name:` in SKILL.md frontmatter, else the directory name.
Success check labels: `file <path>`, `file <path> ~ /<regex>/`, `unchanged <path>`,
`cmd <command>`, or an explicit `name:`.

## 4. Models and providers

A model is written `provider:model`. Providers: `claude`, `openrouter`.
`parse_model`: if the text before the first `:` is a known provider, split there
(OpenRouter ids may contain further colons, e.g. `:free`). Otherwise, a spec
containing `/` is OpenRouter and anything else is Claude.

Four roles, resolved independently: `agent`, `extractor`, `judge`, `digest`. Order: CLI flag
> env var `SKILLGRAPH_<ROLE>_MODEL` > suite `models:` > default (`claude:claude-opus-5-5` for agent,
extractor and judge; `claude:claude-haiku-5-5` for digest, a cheap helper that routes judge questions to turns
and names a run's phases, and never grades).

Provider interface:

- `structured(prompt, schema, *, system, model, effort) -> StructuredResult(data, cost_usd, model)`:
  one tool-less call whose result validates against `schema`. Raise `LLMError` on failure.
- `run_agent(AgentRequest) -> AgentResult`. `AgentRequest` fields: `workspace`, `prompt`,
  `model`, `skills` (names the agent may load; empty for baseline), `invoke` (skill to
  load up front, or None), `effort`, `max_turns` (40), `budget_usd` (3.0), `deny`
  (callable `(tool, input) -> reason | None`). `AgentResult`: `events`, `init`, `result`,
  `error`, `blocked`.

### 4.1 Claude provider (Claude Agent SDK)

Import `claude_agent_sdk` lazily; if missing, raise `LLMError` telling the user to
install the `claude` extra. Authentication is the SDK's: `ANTHROPIC_API_KEY`, else
the local Claude Code login.

- structured: `ClaudeAgentOptions(system_prompt=system, tools=[], setting_sources=[],
  max_turns=4, model=model, effort=effort, output_format={"type": "json_schema", "schema": schema})`;
  read `ResultMessage.structured_output`; drain the stream fully (don't return inside
  the `async for`); 2 attempts; accumulate `total_cost_usd`.
- run_agent: tools `["Read","Write","Edit","Bash","Glob","Grep"]` plus `"Skill"` when
  `skills` is non-empty; `allowed_tools` = same list; `permission_mode="dontAsk"`;
  `setting_sources=["project"]` (only the workspace's `.claude/`); `skills=list(req.skills)`;
  `system_prompt={"type":"preset","preset":"claude_code"}`; `model`, `effort`,
  `max_turns`, `max_budget_usd`; and isolation from the user's account: `disallowed_tools=["mcp__*"]`,
  `mcp_servers={}`, `strict_mcp_config=True`, `env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}` (`tools=` and
  `setting_sources` don't cover MCP servers or claude.ai connectors; structured calls use the same isolation).
  When `deny` is set, register a `PreToolUse`
  `HookMatcher` whose callback returns
  `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}`
  and records `{"tool","input","reason"}` in `blocked`. Prompt is `"/<invoke> <prompt>"`
  when invoking. Map the stream: `SystemMessage(init)` -> `init` = `{model, claude_code_version, tools (without
  mcp__ names), mcp_tools_visible (count), skills (= req.skills), harness: "claude-code"}`; `TextBlock` ->
  text event; `ToolUseBlock` -> tool event (`subagent: true` if `parent_tool_use_id`);
  `ToolResultBlock` fills the matching event's `output`/`is_error`; string
  `UserMessage` -> user event; `ResultMessage` -> `result`. Catch exceptions into `error`.

### 4.2 OpenRouter provider

Base URL `OPENROUTER_BASE_URL` (default `https://openrouter.ai/api/v1`), key
`OPENROUTER_API_KEY` (missing key -> `LLMError` with a link to create one). Headers:
`Authorization: Bearer <key>`, `X-Title: skillgraph`. `POST /chat/completions`
with up to 4 attempts, exponential backoff from 2 s, retrying on network errors
and HTTP 408/429/500/502/503/504; other non-200 statuses raise immediately. A 200
whose body has `error` and no `choices` counts as retryable. Cost comes from
`usage.cost` (always present). Effort maps to `"reasoning": {"effort": effort}`.
Make the httpx transport injectable for tests.

- structured: first try `response_format: {"type":"json_schema","json_schema":{"name":"result","strict":true,"schema":schema}}`
  with `"provider": {"require_parameters": true}`; parse with `extract_json`, check
  with `validate`. On any failure, fall back to a plain request whose system prompt
  appends `Reply with only a JSON object that matches this JSON Schema, with no prose and no code fences:\n<schema json>`;
  up to 2 tries, sending the validation problems back as a user turn
  (`That JSON doesn't match the schema: <problems>. Reply again with corrected JSON only.`).
- run_agent: harness (4.3) with a chat function that sends `tools`, `tool_choice: "auto"`,
  `provider.require_parameters: true`, and reasoning effort.
- `list_models()`: `GET /models` (public), returns `data`.

`extract_json(text)`: use the first fenced ```json block if present, then
`json.JSONDecoder().raw_decode` from the first `{`. `validate(data, schema)`:
supports `type` object/array/string/integer/boolean, `enum`, `required`,
`properties`, `items`; returns a list of problem strings.

### 4.3 Harness (OpenAI-compatible agent loop)

System prompt (format with `cwd`, `platform` = `"<system> <machine>"`, `today` ISO
date, and `skills`):

```
You are an autonomous coding agent working in a local project directory.

Working directory: {cwd}
Platform: {platform}
Today's date: {today}
The directory is a git repository.

Use the tools to inspect files, change them, and run commands. Relative paths resolve against the working directory. Keep working until the task is done, then reply with a short summary of what you did. Nobody can answer questions during the run, so make reasonable assumptions and mention them in the summary.{skills}
```

When skills are installed, `skills` is:

```
Skills are packaged instructions for particular kinds of tasks. When a task matches a skill's description, call the Skill tool with its name before you start, then follow its instructions. A skill's files live in its base directory.
<available_skills>
{items}
</available_skills>
```

with `items` = one `- <name>: <description>` line per skill.

Skills are discovered from `<workspace>/.claude/skills/*/SKILL.md` (frontmatter
`name`, `description`), filtered to `req.skills`. A skill loads as the text
`Base directory for this skill: <abs dir>\n\n<SKILL.md body without frontmatter>`.
When `invoke` names an installed skill, the first user message is
`<command-name>/<name></command-name>\n<loaded skill text>\n\nARGUMENTS: <prompt>`,
and a `user` event records `/<name>\n<loaded text>` (clipped to 1500 chars);
otherwise the user message is the prompt.

Loop: stop with `error_max_turns` at `max_turns`, `error_max_budget_usd` when
accumulated cost reaches the budget, `success` when a reply has no tool calls.
Append each assistant message with `content`, plus `tool_calls`,
`reasoning_details`, and `reasoning` when present, unmodified (reasoning models
need them back). Record non-empty text as a text event. For each tool call: parse
`arguments` as a JSON object (failure -> tool result `Invalid tool arguments: ...`,
error); call `deny` (a reason -> result `Blocked: <reason>`, error, recorded in
`blocked`); otherwise run the tool in a thread (ToolError -> its message as an
error result; TypeError -> `Invalid arguments for <tool>: ...`). Send each result
back as `{"role":"tool","tool_call_id":id,"content":result}`. `init` =
`{"model","skills","tools","harness":"skillgraph"}`.

Tools (OpenAI function schemas, `additionalProperties: false`), all paths relative
to the workspace or absolute; file tools refuse paths outside the workspace;
outputs over 30,000 chars are truncated. Dispatch through an explicit name -> method table (never `getattr`).
`UnicodeDecodeError` and `OSError` from a tool become tool errors; the harness turns any other exception into an error
result for that call, so one bad call never ends the run:

| tool | parameters | behavior |
|---|---|---|
| Read | file_path, offset?, limit? (2000) | lines formatted `f"{n:6d}\t{line}"`; offset/limit coerced with `int()` |
| Write | file_path, content | creates parents; `Created/Updated <rel> (<n> lines)` |
| Edit | file_path, old_string, new_string, replace_all? | old_string must be non-empty and match exactly once unless replace_all |
| Bash | command, timeout? (120, max 600) | shell in workspace; env without OPENROUTER_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, OPENAI_API_KEY; nonzero exit -> error `Exit code <n>\n<output>` |
| Glob | pattern, path? | fnmatch over relative paths (`**/x` also matches `x`), skip .git/__pycache__/node_modules/.venv, max 500 |
| Grep | pattern, path?, glob?, case_insensitive? | `path:line: text`, max 300 matches |
| Skill | skill (enum of installed names) | only offered when skills are installed |

## 5. Claims lockfile

Extraction JSON schema (structured output; every field required, unused ones `""` or `[]`):

```json
{
  "type": "object",
  "properties": {
    "classification": {
      "type": "string",
      "enum": [
        "procedural",
        "reference",
        "mixed"
      ]
    },
    "summary": {
      "type": "string"
    },
    "conditions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "text": {
            "type": "string"
          },
          "line_start": {
            "type": "integer"
          },
          "line_end": {
            "type": "integer"
          },
          "check": {
            "type": "object",
            "properties": {
              "type": {
                "type": "string",
                "enum": [
                  "tool",
                  "file",
                  "judge"
                ]
              },
              "tools": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "input_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "output_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "path_glob": {
                "type": "string"
              },
              "content_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "question": {
                "type": "string"
              }
            },
            "required": [
              "type",
              "tools",
              "input_any",
              "output_any",
              "path_glob",
              "content_any",
              "question"
            ],
            "additionalProperties": false
          }
        },
        "required": [
          "id",
          "text",
          "line_start",
          "line_end",
          "check"
        ],
        "additionalProperties": false
      }
    },
    "claims": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "kind": {
            "type": "string",
            "enum": [
              "step",
              "must_not",
              "must_read",
              "artifact"
            ]
          },
          "text": {
            "type": "string"
          },
          "line_start": {
            "type": "integer"
          },
          "line_end": {
            "type": "integer"
          },
          "after": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "when_cond": {
            "type": "string"
          },
          "when_value": {
            "type": "boolean"
          },
          "check": {
            "type": "object",
            "properties": {
              "type": {
                "type": "string",
                "enum": [
                  "tool",
                  "file",
                  "judge"
                ]
              },
              "tools": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "input_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "output_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "path_glob": {
                "type": "string"
              },
              "content_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "question": {
                "type": "string"
              }
            },
            "required": [
              "type",
              "tools",
              "input_any",
              "output_any",
              "path_glob",
              "content_any",
              "question"
            ],
            "additionalProperties": false
          }
        },
        "required": [
          "id",
          "kind",
          "text",
          "line_start",
          "line_end",
          "after",
          "when_cond",
          "when_value",
          "check"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "classification",
    "summary",
    "conditions",
    "claims"
  ],
  "additionalProperties": false
}
```

Normalized lockfile (what is saved):

```
{
  "skill": "<name>",
  "source_sha256": "<sha256 of SKILL.md text>",
  "classification": "procedural" | "reference" | "mixed",
  "summary": "...",
  "conditions": [{"id": "k1", "text": "...", "span": [a, b], "check": Check}],
  "claims": [{"id": "c1", "kind": "step|must_not|must_read|artifact", "text": "...", "span": [a, b],
              "after": ["c0"], "when": {"cond": "k1", "value": true} | null, "check": Check}],
  "extracted": {"model": "<provider:model>", "at": "<iso>", "cost_usd": 0.0},
  "review_notes": ["optional free text added by reviewers"]
}
Check = {"type": "tool", "tools": [...], "input_any": [...], "output_any": [...]}
      | {"type": "file", "path_glob": "...", "content_any": [...]}
      | {"type": "judge", "question": "..."}
```

Normalization: clamp spans into `[1, n_lines]` with `b >= a`; uniquify ids; an
invalid regex is replaced with `re.escape` of itself plus a warning; unknown
`when_cond` -> unconditional plus a warning; unknown kinds -> `step`; drop `after`
refs that are unknown or self-references (warning); empty judge question -> warning.
`doc_sha` = first 16 hex chars of sha256 over `json.dumps({"conditions","claims"}, sort_keys=True)`;
check results store it so a lockfile edit triggers re-checking. A lockfile is
stale when `source_sha256` differs from the current SKILL.md.

## 6. Extractor

System prompt:

```
You compile agent skill files (SKILL.md) into span-anchored, checkable claims for an evaluation harness. You are precise about line numbers, you never invent instructions the text does not contain, and you prefer mechanical checks over subjective ones.
```

User prompt (format `name`, `bundle` = newline-separated bundled file paths
relative to the skill dir excluding SKILL.md and `__pycache__`, or `(none)`,
`numbered` = SKILL.md lines formatted `f"{i:4d}| {line}"`):

```
Compile this skill into checkable claims.

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
- Bundled scripts are run from the skill's own directory, often by absolute path, so match on the file name (for example "validate\\.py"), not the full path.
- Agents often chain shell commands in one Bash call (`cd x && a; b | c`), so don't anchor command regexes to the start or end of the input.
- Use judge for quality, judgment, or content that a regex cannot capture. Don't force a regex onto a fuzzy instruction.

classification: "procedural" (mostly an ordered workflow), "reference" (mostly knowledge, lookups, or style rules), or "mixed".
summary: one sentence on what the skill makes the agent do.
Use ids c1, c2, ... for claims and k1, k2, ... for conditions.
```

## 7. Graph and lint

NetworkX `DiGraph`: nodes are conditions (`node_type="condition"`) and claims
(`node_type="claim"`, `kind`); edges `precedes` (a -> b for each `after` a of b)
and `branch` (cond -> claim, `value`). `checkability` = share of claims whose check
is not `judge`.

Lint findings `{severity, rule, message, span, ids}`, sorted error < warn < info then by line:

- `order-cycle` (error): each simple cycle among `precedes` edges.
- `unused-condition` (warn): a condition gating nothing.
- `one-sided-branch` (info): a condition with claims on only one side.
- `subjective-condition` (warn): a condition whose check is `judge`.
- `no-done-criterion` (info): one finding listing every step/artifact claim checked only by a judge.
- `no-ordering` (info): a procedural skill with no `after` edges.
- `dangling-file` (error): a SKILL.md line references `(scripts|references|assets|templates|examples)/<path>` that doesn't exist in the skill dir (regex `(?<![\w/.-])((?:scripts|references|assets|templates|examples)/[\w./-]+\w)`).
- `no-claims` (info): an instruction block with no claim or condition span. Blocks: after frontmatter, consecutive non-blank lines, split at headings, code fences, and each list item.

Exports: Cypher (`MERGE` Skill, Condition, Claim nodes with text, line_start,
line_end, check type, and check_spec JSON; relationships HAS_CLAIM, PRECEDES,
GATES {value}) and GraphML (spans as `"a-b"` strings).

## 8. Runner

Workspace for `<out>/<skill>/<task>/<variant>/r<rep>/ws`:

1. Copy the fixture (a missing directory is an error). Warn when it contains `.claude/settings*.json` or `.claude/hooks`.
   If it contains `setup.sh`, run `bash setup.sh` in the workspace (timeout 300 s) with the clean environment
   (`os.environ` minus OPENROUTER_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, OPENAI_API_KEY, plus fixed git
   author/committer `Fixture Bot` / `fixture@example.invalid`), then delete it. A failure here becomes the trace's
   `error` (`FixtureError: fixture setup.sh failed (exit N): <stderr>`); the agent doesn't run and the matrix continues.
2. If there is no `.git`, `git init`, add all, commit `initial import`. Set local
   `user.name`/`user.email`. Append `.claude/` to `.git/info/exclude`.
3. Unless the variant is `baseline`, copy the skill dir to
   `ws/.claude/skills/<name>` (ignore `__pycache__`); for `ablate` variants delete
   the removed 1-based line ranges from that copy of SKILL.md.

Variants: `skill`, `baseline`, `ablate-L<a>-<b>` (require 1 <= a <= b). Trigger `invoke` (default) loads
the skill up front; `auto` lets the agent decide.

Writing a new `trace.json` first deletes the run's `check.json`. Success `cmd:` checks also use the clean environment.

`--enforce`: build `deny` from `must_not` claims with a `tool` check and no
`output_any`; it returns `Blocked by a skill rule (<id>): <text>` when the call
matches.

Trace (`trace.json`): `skill, task, task_prompt, variant {name, kind, remove}, rep,
model (provider:model), trigger, enforce, init, events, blocked, result, error,
wall_s, skill_loaded, warnings, files, git {tags, log (15 oneline), status (porcelain)},
success {label: bool}`. `files` snapshots every workspace file outside
`.git .claude __pycache__ node_modules .venv`: text up to 20,000 chars, `null` for
binary or files over 500 KB. `skill_loaded`: a Skill tool call naming the skill,
a user event containing the name or `Base directory for this skill`, a tool input
containing `skills/<name>/`, or trigger `invoke`.

`runset.json` in the run directory: `{"suite", "model", "trigger", "skills": {name: {"suite", "dir", "claims", "reference"?}}}`
with paths relative to the run directory; resolve them against it when loading. Saving merges: `suite` keeps its first
value and each saved suite adds or replaces its own skills, so a hand-written and a generated suite can share a run
directory. `ablate` loads each skill's own suite.

Planning (`plan_jobs`) lists `(skill, task, variant, rep, run_dir)` for runs still to do: unknown `--skills` or
`--tasks` names are errors; runs with a `trace.json` are skipped unless `--force`, or `--retry-errors` and the trace
has an `error`. Run matrix: asyncio semaphore for `--concurrency` (default 4); print one progress line per run plus
its warnings. With `max_total_usd`, a run isn't started once the summed `cost_usd` reaches it (report how many were
skipped). A provider exception becomes the trace's `error`; it never aborts the matrix.

## 9. Checker

Mechanical:
- `tool`: an event matches when its tool is in `tools` (or `tools` is empty), any
  `input_any` regex matches either the JSON-serialized input or the space-joined
  string values of the input, and any `output_any` regex matches the output. Flags
  `re.I | re.S`. Evidence = first matching event index.
- `file`: a final file whose path fully matches `path_glob` (`*` and `?` stay within one path segment, `**/` spans
  any depth including none, `[...]` classes) and whose content matches any `content_any`. Evidence = the last
  Write/Edit/Bash event whose `file_path` or `command` mentions the path as a whole path (bounded by start, end,
  whitespace, quotes, `/`, `=`, `<`, `>`, `;`, `&`, `|`, `)`), else `None`: a claim with no position is skipped by
  ordering checks.

Per trace:
1. Conditions with a mechanical check that matches -> true. All others go to the judge with their judge question, or `During this run, did this condition hold: <text>?`.
2. Claims with mechanical checks: `must_not` -> violated if matched else satisfied; other kinds -> satisfied if matched else missed. Judge claims go to the judge.
3. One judge call for all pending items (skip if none; an empty trace answers "no"). In windowed mode the
   pending items go to `digest.judge_windowed` instead (section 9.1).
4. Judge answers: for claims, `unclear` -> unclear; `must_not` yes -> violated, no -> satisfied; others yes -> satisfied, no -> missed. For conditions yes -> true, no -> false, unclear -> unknown.
5. Branches: gates are `when_all` if present, else `when`. If any gate's condition is known and differs from its value -> `n/a`; else if any gate's condition is unknown -> unclear.
6. Order: a satisfied, non-must_not claim whose evidence index is earlier than the evidence of any satisfied/out_of_order `after` claim becomes `out_of_order`.

Judge system prompt:

```
You grade whether an AI agent followed specific instructions, using only the evidence in its execution trace and final files. You are strict about evidence: if the trace does not show something happened, it did not happen. Answer 'unclear' only when the evidence is genuinely ambiguous.
```

Judge user prompt: `<trace>\n<rendered trace>\n</trace>\n\n<instructions>\n\nQuestions:\n<one line per item>`
(in windowed mode the rendered trace is a window or the skeleton, and the instructions end with a space and that
view's note),
where each item line is `- id=<id>: <question>  (instruction: <claim or condition text>)` and instructions are:

```
Answer each question about this run with yes, no, or unclear. For evidence_event give the [index] of the event that best supports your answer, or -1 if none (for example when the evidence is a final file or the absence of an action). Keep reasons to one sentence.
```

Rendered trace: `TASK PROMPT`, then `EVENTS` (`[i] TOOL <name> input=<json>` with
`      output[ (error)]: ...`, `[i] AGENT: ...`, `[i] INJECTED: ...`; fields clipped
at 1200 chars, injected text at 300), `FINAL ANSWER`, `FINAL FILES` (`--- path`
plus content clipped at 3000), and `GIT: tags=... status=...` plus the log.

Judge schema:

```json
{
  "type": "object",
  "properties": {
    "verdicts": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "answer": {
            "type": "string",
            "enum": [
              "yes",
              "no",
              "unclear"
            ]
          },
          "evidence_event": {
            "type": "integer"
          },
          "reason": {
            "type": "string"
          }
        },
        "required": [
          "id",
          "answer",
          "evidence_event",
          "reason"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "verdicts"
  ],
  "additionalProperties": false
}
```

Cache, per question: key = first 16 hex of sha256 over `json.dumps([model_name, effort, id without a "ref:" prefix, question, context])`,
where `model_name` is the bare model id for Claude and `provider:model` otherwise. `check.json["judge_cache"] =
{"items": {key: {"verdict", "cost_usd"}}}` keeps the entries for the current questions; only uncached questions go to the
judge (cost is split evenly across a call's questions), and reuse is skipped with `--force`. Older batch caches (key over
`[model_name, (effort if set)] + [[id, question, context] ...]` with `{"verdicts", "cost_usd"}`) are read for all pending
items, or for the non-`ref:` subset, and converted.

`check.json`: `{claims_sha, skill_loaded, judge_mode, conditions {id: {value, method, evidence, reason, view?}},
claims {id: {status, method, evidence, reason, view?}}, judge_items, judge_cost_usd, judge_cache, window?, digest_cost_usd?,
reference? {claims, conditions}}`. `judge_mode` is `full` or `windowed` (older files without it count as full); `view`
is `window` or `skeleton` for windowed verdicts. Windowed verdicts are cached under keys that add the mode after the
effort (`[model_name, effort, "windowed", id, question, context]`), so they never mix with full-trace verdicts.
`render_trace` is built from `event_lines(ev, field_limit=1200, output_limit=None, text_limit=None, user_limit=300)`:
`output_limit` and `text_limit` default to `field_limit`, an output limit of 0 drops the output line (an error then shows
`      output (error)`).
Statuses: satisfied, missed, violated, out_of_order, n/a, unclear.

### 9.1 Digests and windowed judging (`digest.py`)

Long traces (a real session can reach hundreds of thousands of tokens) are judged on the parts that hold the evidence.
Verdicts are still decided on raw events; summaries are never shown to the judge.

`--judge-mode`: `full` (one call over the whole rendered trace), `windowed`, or `auto` (the default on the CLI: windowed
when the full render is over 30,000 tokens, else full). Tokens are estimated as `(len(text) + 3) // 4`. Under `auto`,
existing checks are kept whatever mode made them; an explicit `full` or `windowed` redoes checks made in the other mode.

**Digest** (deterministic; `digest.json` next to the trace, rebuilt when `trace_sha` changes; version 1):
- `trace_sha`: first 16 hex of sha256 over the JSON of `{p: task_prompt, e: events, f: files, r: result.text}` (sorted keys).
- `actions`: one entry per tool event `{i, tool, target, error, agent?}`; target = Bash command, Read/Write/Edit
  `file_path`, Glob/Grep pattern + path + glob, Skill name, Agent/Task description or prompt, else the first of url,
  query, command, file_path, path, pattern, else the input JSON; whitespace collapsed, clipped to 300 characters.
  Consecutive actions with the same tool, target and agent collapse into one line `[a-b] Tool ×n (identical calls): target`.
- `turns`: a new turn starts at every agent text event, every user event, and whenever the event's `agent` differs from the
  turn's. Turn = `{n, start, end, agent, kind (user | meta | agent), said (first text, clipped to 300), actions (event
  indexes), files (Write/Edit paths), errors, tokens, summary}`. User events are `meta` when marked meta or starting with
  `Base directory for this skill`, `<command-name>` or `<command-message>`. `summary` groups the turn's actions by tool:
  `Tool ×n: target; target; +k more` (3 unique targets of 60 characters per tool), joined with ` | `.
- Table of contents (what the router and intent model read): one line per turn, `[n] who: "said" | summary (k errors)`,
  who = user, injected, agent or `subagent <id>`; consecutive identical agent turns collapse to `[a-b] ... (repeated)`.
- `stats`: events, tool_calls, turns, agent_messages, collapsed_actions, tokens_full, tokens_skeleton.

**Intents** (optional, `check --intents` or `digest --intents`; one call with the digest model; it never sees the skill).
Prompt: `<run>\nTASK PROMPT:\n<prompt, clipped to 2000>\n\nTURNS:\n<table of contents>\n</run>\n\n<intent prompt>`.
Phases outside the turn range and user turns that don't exist are dropped. Stored as `intents {model, prompt_version,
cost_usd, data}`; reused while the model and prompt version match.

Intent system prompt:

```
You describe what an AI agent did in a run, from a table of contents of its turns. Describe only what the turns show. Don't judge the work.
```

Intent prompt:

```
Name the task the user asked for. Split the run into phases of consecutive turns and give each phase an intent of at most 8 words, starting with a verb. Classify each user turn: new_task (asks for something new), follow_up (adds to or changes the current task), approval (says yes or go ahead), correction (says something was wrong), question, or other. Give the outcome in one sentence.
```

Intent schema:

```json
{
  "type": "object",
  "properties": {
    "task": {
      "type": "string"
    },
    "outcome": {
      "type": "string"
    },
    "phases": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "first": {
            "type": "integer"
          },
          "last": {
            "type": "integer"
          },
          "intent": {
            "type": "string"
          }
        },
        "required": [
          "first",
          "last",
          "intent"
        ],
        "additionalProperties": false
      }
    },
    "user_turns": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "turn": {
            "type": "integer"
          },
          "kind": {
            "type": "string",
            "enum": [
              "new_task",
              "follow_up",
              "approval",
              "correction",
              "question",
              "other"
            ]
          }
        },
        "required": [
          "turn",
          "kind"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "task",
    "outcome",
    "phases",
    "user_turns"
  ],
  "additionalProperties": false
}
```

**Routing** (per trace, for the pending judge items):
- Lexical (free): search terms from the question and instruction text: quoted or backticked spans and path-, flag- or
  identifier-like tokens weigh 2, other words of 4+ letters (minus stop words) weigh 1. Each turn's haystack is its
  said text plus every event's input JSON (3000 chars), output (2000) and text (3000), lowercased. A term scores
  `weight * (1 + ln(N / df))` in each turn containing it, where N is the number of turns and df the number containing
  the term; terms found in more than a quarter of the turns score nothing once N >= 8. Turns scoring >= 2 route, the
  top 4.
- Router call (digest model): `<run>\nTASK PROMPT:\n<prompt, clipped to 2000>\n\nTURNS:\n<table of contents>\n\nFINAL FILES:
  <paths>\n</run>\n\n<router prompt>\n\nQuestions:\n<one line per item, as for the judge>`. Unknown turns and files are
  dropped; at most 8 turns per item.
- A route is the union of both, plus final files whose path or basename appears in the item text. Routes are cached in
  `digest.json` under the first 16 hex of sha256 over `[router model, version, [[id, question, context], ...]]`
  (the last four keys are kept).

Router system prompt:

```
You point to where the evidence is in an AI agent's run. You see a table of contents of the run, not the run itself. Favor recall: list every turn that could hold the evidence.
```

Router prompt:

```
For each question, list the turns whose events could answer it: where the action, its output, or a statement about it would appear. Also list the final files that could answer it. If nothing in the run relates to the question, return empty lists. List at most 8 turns per question, the closest to the evidence first.
```

Router schema:

```json
{
  "type": "object",
  "properties": {
    "routes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "turns": {
            "type": "array",
            "items": {
              "type": "integer"
            }
          },
          "files": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "id",
          "turns",
          "files"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "routes"
  ],
  "additionalProperties": false
}
```

**Views** (built from `event_lines`, so indexes are the trace's own):
- Window: the routed turns plus one turn either side, in full; each run of other events becomes
  `… [events a-b not shown: k tool calls, m agent messages, u injected/user messages] …`. The header is
  `EVENTS (excerpt):`; final files are the routed ones (3000 chars each), other paths listed by name.
- Skeleton: every event; events in focus are in full, the others shortened with (input, output, agent text) limits
  (300, 160, 400), then (200, 0, 200), then (120, 0, 0) (agent text replaced by `(message omitted)`) until the text fits
  60,000 tokens. Header `EVENTS (all events; long fields shortened except where shown in full):`; all final files at
  1500 chars unless routes named some.
- Both keep `TASK PROMPT`, `FINAL ANSWER` and `GIT` as in the full render.

**Judging:**
1. Conditions always get the episode opening (every turn up to and including the first agent turn) added to their window.
2. `must_not` claims, and claims with no routed turn or file, go to the skeleton call. Everything else is packed into
   window calls in trace order; a call grows while its union window stays within 24,000 tokens.
3. Window calls run concurrently with the window note. A window answer stands when it is `yes`, or when it is `no` for
   a condition. Any other answer for a claim (`no` or `unclear`: an excerpt can't show that something never happened)
   escalates the item to the skeleton call.
4. One skeleton call (with the skeleton note) for every remaining item; its focus is the union of those items' windows,
   smallest first, while it stays within 20,000 tokens.
5. `evidence_event` outside the trace becomes -1. The judge cost is the sum of all calls plus the router call.
6. `check.json["window"]`: `{mode, calls, window_batches, skeleton_level, tokens_full, tokens_sent, router_model,
   router_cost_usd, routes {id: [turns]}, unrouted [claim ids], escalated [ids], off_skill_turns}`; off-skill turns are
   agent turns with actions that no question was routed to.

Window note:

```
This trace is an excerpt: the events around the parts that matter for these questions are shown in full, and lines starting with … stand for events that are not shown. Answer yes only when the shown events support it. If they don't settle a question, answer unclear, not no.
```

Skeleton note:

```
This trace shows every event of the run. Some events are shown in full; the others have long inputs, outputs and messages shortened, so answer unclear if a shortened field would decide the question.
```

`skillgraph digest DIR [--run PATH] [--intents] [--validate [--lexical-only]] [--digest-model S] [--force] [--json]`
writes `digest.json` for every trace and prints events, turns, tool calls, full and skeleton tokens, and what `auto`
would pick. `--validate` routes the judge questions whose full-mode verdict cites an event and reports routing recall:
the share of cited events inside the routed windows (opening included for conditions), for lexical routes, router
routes, and both. Paid calls (intents, router) go through the spend gate.

`scripts/validate_digest.py` embeds sampled demo runs in about 90,000 tokens of agent work over the repository's own
source (a message, a Read of 50-130 lines, sometimes a Grep or `wc -l`), interleaved before each turn, and judges
them windowed. It reports agreement with the demo's full-trace verdicts, mechanical agreement (noise must not trigger
tool checks), routing recall, needle detection (an input-CSV edit and a deleted test spliced into the noise must
come out violated), tokens sent and cost, and optionally compares a full-mode judgment of one long trace.

## 10. Aggregation

Follow rate per claim and variant = satisfied / (satisfied + missed + violated +
out_of_order); n/a and unclear excluded; `None` when nothing applied. Verdict per
section 1 from the `skill` and `baseline` rates. `failure_mode` = the most common of
missed/violated/out_of_order in skill runs. `miss_notes`: up to 6
`<task>/r<rep>: <reason>` strings from skill runs that weren't satisfied or n/a.
Condition `held` per variant = {true count, n known}. Success rate per variant =
share of runs where every success check passed. `weak` = the skill rate exists and fewer than 3 runs applied in the
skill variant or (when present) the baseline. `stale_checks` = runs whose `check.json` `claims_sha` differs from the
current merged skill+reference doc.

Ablation analysis per `ablate-*` variant: affected claims = spans overlapping the
removed range; effects = affected claims plus any claim whose rate moved by >= 0.5;
verdict `load-bearing` if an affected claim dropped by >= 0.25, else
`removal-helped` if task success rose, else `no-effect`. Sort ablations by line.

Automatic ablation picks (`--top N` each, skipping overlapping spans): working
claims with the largest skill-minus-baseline gap (positive control), inert claims
with the highest baseline rate, ignored claims with the lowest skill rate.

## 11. Report

`report.html`: the template with `/*__SKILLGRAPH_DATA__*/null` replaced by the JSON
data, with every `<` escaped as `\u003c`. Use system fonts only (no external font requests). When not a fragment, wrap in a minimal
`<!doctype html>` page. Data: `{runset, generated, model, trigger, quadrants
{verdict: description}, skills: [aggregate]}`.

Page, per skill tab (deep-linkable as `#<skill>`):
- warning bars when SKILL.md changed since extraction and when `stale_checks` > 0;
- summary pills (classification, counts, runs, estimated cost) and four tiles: task
  success with the skill, without it, verdict counts, share checked mechanically;
- SKILL.md with line numbers, each line tinted by the most severe verdict of the
  claims on it (interfering > ignored > working > followed > inert > untested),
  claim-id chips at each claim's first line, dimmed lines that yield no claims;
- a sticky inspector for the selected claim or condition: text, kind, lines, order,
  branch, check spec, follow-rate bars per variant, miss notes;
- a Cytoscape.js graph (cdnjs `cytoscape/3.30.2`) in branch lanes: one column per
  branch ("Always", "If kN holds", "If kN doesn't hold"), claims in line order,
  condition diamonds above their lanes, arrows for `after` edges bent around
  intermediate nodes, colored by verdict, shapes by kind;
- a claims table with a with-vs-without dumbbell per claim, the runs behind each rate, and "(weak)" on weak verdicts;
- ablation cards (removed text, verdict, effects, task success);
- a run matrix (rows = runs, columns = claims, glyph + color per status);
- lint findings with links to lines.
Insert all model- and user-derived text with `textContent`. Support light and dark
themes and phone widths.

### 11.1 Dashboard (`dashboard/`)

`skillgraph dashboard DIR [DIR ...] [--port 8765] [--no-open] [--export OUT]` serves a read-only app on 127.0.0.1
(port 0 picks a free one) or writes a static copy. It never calls a model. The product spec is
`docs/dashboard-prd.md` (journeys, page specs, copy, acceptance criteria).

Run sets get ids from their folder names (non-word characters become `-`; duplicates get `-2`, `-3`). API, GET only,
JSON with `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`:
- `/api/sets`: `[{id, name, model, trigger, skills, runs, checked, tasks, has_reference, has_baseline, imported, updated}]`.
- `/api/set/<id>`: `{id, data (report.build_data), summary (summary.summarize), runs, error}`. Each aggregate claim also has
  `results`: `[{path, task, variant, rep, status, reason, evidence, method, view}]` for every graded run, and each
  aggregate run has `ref_statuses`. `runs` has one row per trace: path, skill, task, variant, rep, model, prompt (160
  chars), success, all_success, skill_loaded, error, imported, events, tool_calls, messages, turns, tokens (digest
  `tokens_full` when the digest is current, else the full render), wall_s, duration_ms, blocked, cost_usd, judge_cost_usd,
  checked, statuses (counts), failed (ids missed, violated or out of order), unclear, reference (counts), judge_mode,
  tokens_sent, episode, task_intent. If report data can't be built, `error` says why and the run rows still load.
- `/api/run/<id>?path=<skill>/<task>/<variant>/r<N>`: `{path, trace, check, digest (without routes), turn_of_event,
  claims {id: {kind, text, span, check, after, when}}, conditions {id: {text, span}}, reference {id: {kind, text}}}`.
  The path must match `^[\w.@+-]+/[\w.@+-]+/[\w.@+-]+/r\d+$`, contain no `.` or `..` segment, and resolve inside the
  run set (400 / 404).
- `/api/diff/<a>/<b>`: `diff.compare`. `/report/<id>`: the HTML report.
- Responses are cached in memory per run set, keyed on the (path, mtime, size) of runset.json, every trace, check and
  digest, and the skill's SKILL.md, claims, reference claims and coverage files; any change recomputes.
- `/` and `/static/<name>` serve only index.html, core.js, pages.js, trace.js, app.js, app.css, tokens.css and
  favicon.svg, with `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src
  'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'`.
- Requests whose `Host` is not `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` get 403 (DNS rebinding).

Export writes index.html (asset paths made relative, `<script src="data.js">` before the first script), the assets,
`data.js` (`window.SKILLGRAPH_DATA = {<api path>: <response>}` for sets, every set, every run and every ordered pair of
sets, with `<` escaped) and `report-<id>.html` per run set with checked runs. It opens from disk.

The app is plain scripts (no build step, no dependencies) sharing `window.SG`: core.js (API access, the `h()` DOM helper
(text only, never HTML strings), icons, copy, badges, scores, bars, tables, tabs, drawer, help popovers, and
`recommend()`), pages.js, trace.js (the run page) and app.js (shell and router). Layout: a left sidebar (Overview,
Recommendations, Skills with one entry per skill, Runs, Compare; footer Glossary, claim graph report, theme), a top bar
(run-set switcher, breadcrumbs, glossary). The sidebar is open at >= 1100px, a 56px icon rail that expands on hover
or focus from 768px, and a slide-in drawer behind a menu button below 768px.

Routes (hash; filters and selections are query parameters, so every view can be bookmarked and works in an export):
`#/s/<set>` overview; `/recommendations?skill=&action=`; `/skills`; `/skill/<name>?tab=lines|claims|conditions|
ablations|reference|lint&verdict=&line=&claim=&kind=&check=&q=&sort=&dir=&diag=&step=`; `/claim/<skill>/<id>`;
`/runs?skill=&task=&variant=&outcome=passed|failed|unknown&status=&judge=&claim=&q=&problems=1&sort=&dir=&group=task`;
`/run/<path>?event=&claim=&tab=detail|scores|prompt|answer|files|info&status=&q=`; `#/compare/<a>/<b>?skill=`
(`#/diff/...` redirects); `#/glossary`. Filter changes use `history.replaceState`, so they don't add history entries.

Pages:
- Overview: run-set meta, four linked tiles (task success with the skill, linking to failed skill runs, plus the rate
  without; lines that work; needs attention = high-priority recommendations; confidence = weak verdicts and stale
  results), one row per skill with a health sentence, verdict bar and legend (each count links to the filtered claims),
  success, cost, reference coverage, warnings and its recommendations, then the top three recommendations.
  Health sentence, first rule that matches (W = working + followed, I = inert, G = ignored, F = interfering, n = claims):
  no baseline: "{W} of {n} rules followed in {runs} runs; {k} run(s) broke a rule"; G+F = 0 and W/n >= 0.5: "Does real
  work: {W} of {n} lines change behavior." plus "Task success {without} without → {with} with."; I/n >= 0.6: "Mostly
  redundant: {I} of {n} lines describe what the agent does anyway." plus "{G} line(s) ignored." when G; G+F > 0: "{G+F}
  line(s) need attention: {G} ignored, {F} interfering. {W} of {n} lines work."; else "{W} working, {I} inert,
  {untested} untested of {n} lines."
- Recommendations (`recommend(S)`, pure, no model calls), per skill: interfering claims (high, Investigate); ignored
  claims (high, or medium when weak; Rewrite, with advice by kind and check type and up to 3 miss notes); reference
  steps diagnosed gap or conflict (high) or partial (medium); ignored reference steps (high); lint errors (high, Fix);
  skill runs that failed their success checks (medium, Check task); ablations where removal helped (medium, Consider
  cutting); untested claims (medium, Add a task) and untested reference paths (medium); lint warnings (low); inert
  non-`must_not` claims grouped into line ranges within 2 lines of each other, only with baseline runs (low, Consider
  cutting, with the ablation verdict that covers them); weak verdicts (low, Add runs). Sorted by priority, then impact,
  then line. Each item: priority, action, skill, lines, quoted SKILL.md text, why, what to do, links.
- Skills: one row per skill. Skill: header (meta, summary, stale notices, health sentence, verdict counts linking to
  the Claims tab) and tabs: Lines (SKILL.md with each line's left edge colored by its most severe claim verdict,
  ignored > interfering > working > followed > inert > untested, lint errors underlined; clicking a line filters the
  claim cards; verdict filters; under 768px a Text | Claims switch), Claims (sortable table: verdict, id, kind, lines,
  text, with and without scores linking to the runs, scores per ablation labelled by the lines removed, check type,
  weak and failure mode; filters by verdict, kind, check, search), Conditions, Ablations (cards with the removed text,
  verdict, success with and without, effect table), Reference (coverage sentence, diagnosis filters, steps table with
  expandable rows, paths with tested tags, findings), Lint.
- Claim (drawer over the Skill page, Esc closes it; also a page): verdict, kind, lines, weak; the claim text; the
  verdict explained in plain words; score chips per variant linking to the runs; the SKILL.md lines with one line of
  context; how it is checked; every run's result with reason and evidence (row opens the run at that event); the
  suggested change from `recommend()`.
- Runs: filters (skill, task, variant, outcome, has status, judge mode, claim, search, only problems, group by task)
  shown as removable chips, a count, and a sortable table (task with intent or prompt, skill, variant, the selected
  claim's status per run, a status bar with the failed claim ids, outcome, tool calls and tokens, judge mode and tokens
  sent, agent and judge cost). With `claim=`, a notice names the claim.
- Run (trace viewer): header with meta, a score strip (status counts linking to the Scores tab, task outcome, judge
  mode, window calls and tokens, episode, warnings) and the task and outcome from intents. Left: a tree of phases
  (from intents) → turns → events. Turn rows show who, action count, errors, off-skill, the claims routed to it,
  evidence dots coloured by result, and the agent's message; runs of consecutive off-skill turns collapse into one row;
  events render when their turn opens. Filter by text, tool, or only evidence. Right: tabs Detail (the selected event's
  input and output, or a turn's summary, with "Decided here": every result whose evidence is that event, with reason,
  method and view), Scores (all claim, condition and reference results by status, each linking to its event), Prompt,
  Answer, Files (reconstructed files, success checks, git), Info. Keys: j / k or arrows move, Enter opens a turn, /
  filters. Under 900px a Timeline | Detail switch replaces the two panes.
- Compare: before and after pickers, swap, a verdict (Improved, Regressed, Mixed, No change in verdicts) with per-skill
  counts, then per skill: success, cost, runs and line counts before → after, change chips, changed claims first and
  unchanged ones behind "Show n unchanged"; a row opens the claim in the after set (the before set when removed).
- Glossary: verdicts, results, reference diagnoses, ablation verdicts and other terms, with what to do.

The look follows `design/DESIGN.md` (OpenDesigner): compact density, flat surfaces with a faint ring, 6px controls and
8px cards, one accent (`#2375d4`) for selection, links and focus; verdicts use the success, warning, danger and info
ramps and always carry a word or glyph. Every value in app.css is a `--ds-*` token. No page scrolls sideways at 375px;
tables scroll inside their wrapper.

## 12. CLI

```
skillgraph doctor [SUITE] [--ping] [--agent-model S] [--extractor-model S] [--judge-model S] [--digest-model S]
skillgraph models [--search T] [--limit 40] [--all]
skillgraph init SKILL_DIR [--dir evals/<name>] [--force]
skillgraph install-skill [--user | --project DIR] [--force]
skillgraph extract SUITE [--skills a,b] [--force] [--extractor-model S] [--extractor-effort E]
skillgraph lint SUITE [--skills a,b]                         # exit 1 on errors
skillgraph graph SUITE --skill NAME [--format cypher|graphml|json] [-o PATH]
skillgraph estimate SUITE [--out DIR] [--top 1] [run, agent, extractor, judge options]   # spends nothing
skillgraph run SUITE --out DIR [--variants skill,baseline] [--skills] [--tasks] [--trigger invoke|auto]
               [--enforce] [--force] [--retry-errors] [--agent-model S] [--effort E] [--runs 2] [--concurrency 4]
               [--max-turns 40] [--budget 3.0] [-y/--yes] [--max-total-usd USD]
skillgraph check DIR [--judge-model S] [--judge-effort E] [--judge-mode auto|full|windowed] [--digest-model S] [--intents]
               [--concurrency 4] [--force] [-y] [--max-total-usd USD]
skillgraph ablate DIR [--top 1] [--lines a-b,c,...] [--skills] [agent, judge, run, spend options]
skillgraph report DIR [-o PATH]
skillgraph import SOURCES --suite S --skill NAME --out DIR [--format F] [--tool-map FILE] [--whole-session]
               [--episodes all|first] [--episode-end load|user] [--include-unloaded] [--dry-run]
skillgraph reference SOURCE [-o OUT] [--llm]
skillgraph coverage SUITE [--skills a,b] [--force]                    # exit 1 on conflicts
skillgraph tasks SUITE --skill NAME [--untested-in DIR | --paths 0,2] [-o OUT]
skillgraph pipeline SUITE --out DIR [--strict] [all of the above options]   # one cost confirmation, then extract, lint, coverage (if any reference), run, check, ablate (unless --top 0), report
skillgraph summary DIR [--json] [--junit FILE] [--fail-on LIST]
skillgraph diff BEFORE AFTER [--skills a,b] [--json | --markdown]
skillgraph show RUN_DIR [--full] [--turns] [--no-color]
skillgraph digest DIR [--run PATH] [--intents] [--validate [--lexical-only]] [--digest-model S] [--force] [--json] [-y]
skillgraph dashboard DIR [DIR ...] [--port 8765] [--no-open] [--export OUT]
```

`--judge-mode` and `--digest-model` are judge options, so `ablate` and `pipeline` take them too.

`doctor` prints provider status (Claude SDK installed and auth source;
OPENROUTER_API_KEY; git) and each role's resolved model with its source, marks
roles whose provider isn't set up, optionally pings each distinct model, and exits
1 on problems. `models` lists the Claude models and OpenRouter models with tool
calling (hide `:batch` ids), newest first, with $/1M prices and flags for
structured-output support. User-facing errors (missing files, bad specs, LLMError, a declined spend)
exit with a one-line message unless `SKILLGRAPH_DEBUG` is set; `main` exits only with a non-zero code. `--lines` takes
`a-b` or `a`; `tasks --paths` indexes must exist. `pipeline` prints "continuing despite lint errors" unless `--strict`.

### 12.1 Costs and the spend gate (`costs.py`)

Per-call cost, most specific first: the average `result.cost_usd` of earlier traces with the same model in the run
directory (and average `judge_cost_usd` of their checks); for OpenRouter models, the public `/models` rate card times
typical tokens (agent 60k in / 4k out, judge 15k / 1.5k, extract 6k / 4k); else defaults by Claude family (agent, judge,
extract): fable 2.50/0.60/0.80, opus 1.00/0.30/0.30, sonnet 0.50/0.15/0.15, haiku 0.12/0.03/0.04; unknown 0.50/0.10/0.15.
An estimate has lines (what, count, each, basis) and a total. The gate prints it; refuses when the total exceeds
`--max-total-usd`; passes with `--yes`; otherwise asks `[y/N]` on a terminal and refuses without one ("pass --yes").
`run` estimates its planned jobs; `check` its pending traces (an upper bound, cached verdicts are free) plus one
digest call per trace that will be judged windowed and one per trace for `--intents`, priced like a judge call; `ablate` all
planned ablation runs; `pipeline` confirms once for extraction/compile/coverage calls, runs, judging, and ablation
counted at its maximum (3 spans x `--top` x tasks x runs per skill), then runs every step without asking again.

### 12.2 Results commands

- `summary`: condenses report data into `{runset, model, skills: [{skill, classification, runs, cost_usd, success,
  verdicts, claims [{id, kind, text, lines, verdict, weak, skill_rate, baseline_rate, n_skill, n_baseline, failure_mode}],
  failed_runs, lint_errors, ablations, reference {name, coverage, correctness, paths_tested, paths_total, diagnoses,
  steps, findings} | null}]}`. `--fail-on` accepts ignored, interfering, untested (claim verdicts), gap, conflict,
  partial, unreached, ref-ignored (reference diagnoses), lint-error, task-failure (a skill run failed a success check);
  any match exits 1. `--junit` writes one testsuite per skill and one testcase per claim and reference step, failing
  the matched ones and skipping untested ones.
- `diff`: match claims by id when texts are at least 0.75 similar (difflib ratio, case-insensitive), then by best
  similarity >= 0.75 among the rest; unmatched are new/removed. A pair is better when it moves from ignored/interfering to
  working/followed/inert or its skill rate rises by >= 0.25, worse in the opposite case, otherwise same or changed. Also
  compare task success, cost, runs, SKILL.md line counts, and reference coverage/correctness and changed diagnoses.
  Output as text, JSON, or a Markdown table for pull requests.
- `show`: header (skill/task/variant/rep, model, turns, tool calls, cost, error), the prompt, each event indented by
  depth with tool name (and raw name when mapped), input summary (command, file_path, pattern, skill, or prompt) and
  output, the claim and reference results whose evidence is that event, the final answer, success checks, and all claim
  and reference statuses with reasons. ANSI colors only on a terminal. When the trace has a digest with intents, each
  phase's first turn gets a `── <intent> (turns a-b) ──` header (`--turns` marks every turn); the header also shows the
  episode (when a session had several) and, for windowed checks, the judge calls, tokens sent and off-skill turns. Claim
  lines name the view (window or skeleton) that decided them.

## 13. Trees: run trees and reference trees

Two more inputs feed the same checker:

```
        reference tree
        /            \
  coverage        correctness
      /                \
skill graph -adherence- run trees
```

Modules: `src/skillgraph/trees/{mapping,runs,reference,coverage,taskgen}.py`.

### 13.1 Import (run trees)

`skillgraph import SOURCES --suite S --skill NAME --out DIR [--format auto|claude-code|otel|langsmith|openai-agents|skillgraph|<mapping.yaml>] [--tool-map FILE] [--whole-session] [--include-unloaded] [--dry-run] [--force]`.
Directories containing `*.jsonl` expand to their sessions.

Each importer yields `RawTrace{label, source, format, events: [RawEvent], prompt, model, cwd, final_text, cost_usd}`
with `RawEvent{kind tool|text|user, order, ts, id, parent, depth, agent, tool, input, output, is_error, text, meta}`.

Detection (first matching record): `resourceSpans`, `traceId`, `spanId`, `spans` list, or both `trace_id` and `span_id` -> otel;
`type` user/assistant with `message` and `uuid`/`sessionId` -> claude-code; `span_data` (or `data` list of such) -> openai-agents;
`run_type` or `runs` list -> langsmith; `events` + `variant` + `task_prompt` -> skillgraph.

- **claude-code**: JSONL entries of `type` user/assistant. Assistant `message.content` blocks: `text` -> text event,
  `tool_use {id, name, input}` -> tool event. User content: a string -> user event (`isMeta` entries and text starting
  `Base directory for this skill` are meta); `tool_result {tool_use_id, content, is_error}` fills the matching tool event;
  the first non-meta user text not starting with `<` is the prompt. `timestamp` is ISO; `cwd` and `message.model` are kept.
  `<system-reminder>...</system-reminder>` blocks are removed from tool outputs. The label is the full file stem.
  Subagents: `<session path without .jsonl>/subagents/agent-<id>.jsonl`, parsed the same way at depth 1 with `agent=<id>`,
  `parent` = the tool event whose output contains `<id>`.
- **otel**: spans from OTLP JSON or a list; attributes as a list of `{key, value: {stringValue|intValue|doubleValue|boolValue|arrayValue|kvlistValue}}`
  or a dict. Group by trace id. Tool span when `gen_ai.operation.name == execute_tool`, `openinference.span.kind == TOOL`, or
  `gen_ai.tool.name` is present: name from `gen_ai.tool.name` / `tool.name` / the span name minus `execute_tool `; input from
  `gen_ai.tool.call.arguments` / `input.value` / `tool_call.function.arguments`; output from `gen_ai.tool.call.result` / `output.value`;
  error when the status code is 2 / `STATUS_CODE_ERROR` / `ERROR`. LLM spans (`openinference.span.kind == LLM` or a chat
  operation) give text from `gen_ai.output.messages` / `output.value`. Root spans give the prompt (`input.value` /
  `gen_ai.input.messages`) and final text. Depth from the parent chain.
- **langsmith**: runs from a list, JSONL, or `{"runs": [...]}`, with nested `child_runs` flattened (children inherit
  `parent_run_id`). Group by `trace_id`, sort by `dotted_order` then `start_time`. `run_type == tool` -> tool event (a single
  `inputs.input` is unwrapped; `outputs.output` is unwrapped); `run_type == llm` -> text from `outputs.generations[0][0].text`.
- **openai-agents**: spans from a list, JSONL, or `{"data": [...]}`; group by `trace_id`; sort by `started_at`;
  `span_data.type == function` -> tool (`name`, `input` JSON string, `output`); `generation` / `response` -> text.
- **mapping**: a YAML spec with keys `name, format (json|jsonl), records, children, group_by, order_by, id, parent,
  tool_call {when, name, input, output, error} (name required), text {when, value}, prompt {when, value}, tools {raw: canonical}`.
  Paths are dot-separated keys with `[n]`, `[*]`, and `["dotted.key"]`; JSON strings are decoded while walking. `when` maps
  paths to a value, a list of values (case-insensitive string compare), or `"*"` (present).

Normalization (`to_trace`):
1. Sort by timestamp when every event has one, else by document order.
2. Tool names map onto Bash, Read, Write, Edit, Glob, Grep, Skill through a case-insensitive alias table
   (Bash: bash, shell, sh, run_shell_command, execute_command, run_command, terminal, exec, run_terminal_cmd, local_shell,
   execute_bash, command, powershell; Read: read, read_file, readfile, view, view_file, open_file, cat, get_file_contents,
   read_text_file; Write: write, write_file, writefile, create_file, save_file, write_to_file; Edit: edit, edit_file, multiedit,
   str_replace, replace, replace_in_file, apply_patch, patch, apply_diff, search_replace; Glob: glob, list_files, find_files,
   list_dir, list_directory, ls, file_search; Grep: grep, search, search_files, ripgrep, rg, grep_search, codebase_search,
   find_in_files; Skill: skill, load_skill, use_skill, activate_skill), plus mapping/`--tool-map` extras. `mcp__<server>__<tool>`
   maps by its last segment. `str_replace_editor` / `str_replace_based_edit_tool` / `text_editor` map by `input.command`
   (view -> Read, create -> Write, str_replace/insert/undo_edit -> Edit). Keep the original as `raw_tool`.
3. Inputs gain `command` (Bash: from cmd, script, code, commands, input, args), `file_path` (Read/Write/Edit: from path, file,
   filename, filepath, target_file, file_name), `content` (Write: from text, contents, file_text, data), and `old_string` /
   `new_string` (Edit: from old_str / new_str). Originals stay.
4. Skill load = the first of: a Skill tool call whose input mentions the skill; a Read, or a Bash `cat|head|less|more|sed -n|bat`
   of `skills/<name>/SKILL.md`; a user event with `<command-name>/<name></command-name>`; a meta user event starting
   `Base directory for this skill` whose first line names the skill directory. Writing or editing SKILL.md is not a load.
   Unless `--whole-session`, each load starts an episode, and each episode becomes its own trace (`--episodes all`, the
   default; `first` keeps one trace from the first load to the end of the session, the old behavior). An episode ends at
   the next load of the same skill, or with `--episode-end user` also at the next message a person typed (a depth-0,
   non-meta user event not starting `<`, `Base directory`, `[Request interrupted` or `Caveat:`). A slash command and the
   `Base directory` text injected right after it (or after a Skill call) count as one load. Each episode's prompt is the
   last non-meta user text before its load; its events are renumbered from 0; with more than one episode the task gets
   `-e<k>`, the final text is the episode's last depth-0 agent message, cost is unknown, and the trace records
   `episode {index, of, start_event, end_event, session_events, end}`.
5. Files: Write gives full content; Read output (with line-number prefixes stripped) seeds content; Edit applies to known
   content, otherwise the file is `null`. Paths are made relative to `cwd`. Set `files_reconstructed: true`.
6. The trace has `trigger: "observed"`, `variant` skill (or baseline when imported with `--include-unloaded` and never
   loaded; skillgraph traces keep their own `variant` and `skill_loaded`), `task` = `<format>-<label>` (labels: full Claude
   Code file stem, first 16 characters of other trace ids; duplicates in one import get `-2`, `-3`), `rep` 1, empty
   `success` and `git`. Writing a trace deletes that run's `check.json`; existing traces are kept unless `--force`.
   Timestamps: epoch numbers in s, ms, µs, or ns (by magnitude), or ISO 8601 with `Z`, `+0000`, and any fraction length
   normalized before parsing (Python 3.10's `fromisoformat` is strict).
The summary lists traces, how many loaded the skill, tool counts, and unmapped tool names (ignoring known Claude Code tools
such as Agent, Task, WebFetch, WebSearch, TodoWrite, AskUserQuestion, ToolSearch, and `mcp__*`).

### 13.2 Reference trees

Format (YAML; `nodes` may also be a list of `{id, ...}`):

```yaml
name: release-sop
description: Release notes process
source: release-sop.md
root: tag
nodes:
  tag: {kind: action, text: Find the latest release tag, next: any}
  any:
    kind: decision
    text: Are there any commits since the tag?
    branches: [{label: "no", next: nothing}, {label: "yes", next: breaking}]
  nothing: {kind: end, text: Report that there is nothing to release}
constraints:
  - {id: rule-never-create-git-tags, text: Never create git tags or push.}
```

Kinds: action (optional `next`), decision (`branches` of `{label, next}`), end. Optional `lines` on nodes and constraints.
Branch labels are made unique per decision after slugging (a repeat gets ` <n>` appended), so no two branches share a
condition id.
Root defaults to the first node with no incoming edge. Validation: unknown targets, decisions with fewer than two branches,
end nodes with `next`, unreachable nodes, duplicate or clashing constraint ids. `reference_sha` = first 16 hex of sha256 over
`json.dumps({"root","nodes","constraints"}, sort_keys=True)`.

`skillgraph reference SOURCE [-o OUT] [--llm] [--extractor-model S]` writes `<source>.ref.yaml` (with a review header).
- YAML/JSON sources load directly.
- Mermaid (`.mmd`, `.mermaid`, or a ```mermaid block): parse `flowchart`/`graph` lines, skipping lines that start with
  the whole word flowchart, graph, classDef, class, style, linkStyle, subgraph, click, or direction (not followed by `-`
  or a word character, so `classify` and `style-check` are nodes), a bare `end`, and `%%` comments. A node id is
  `[A-Za-z0-9_]` followed by word characters or hyphens that don't start an arrow (`-(?![-.>=])`), so `A-->B` parses. Node shapes: `([..])` and `((..))` terminal, `{{..}}` and `{..}`
  decision, `[[..]]`, `[(..)]`, `[/../]`, `[\..\]`, `[..]`, `(..)`, `>..]` action. Edges: `-->`, `---`, `-.->`, `==>`, `--x`,
  `--o`, `<-->`, with labels as `-->|label|`, `-- label -->`, `== label ==>`, `-. label .->`; chains and `&` groups. A root that
  is terminal with one successor is a start marker and is dropped. Decision shapes become decisions (unlabeled branches take
  the target's text); other nodes with several successors become decisions too, with a warning; terminal leaves are ends.
  Bullets under a `Rules`, `Constraints`, or `Guardrails` heading become constraints with id
  `rule-<first four slug words>`. The first `# ` heading is the description.
- Anything else goes to the extractor model with this system prompt:

```
You turn process documents (SOPs, runbooks, checklists, decision trees) into reference trees for an evaluation harness. You keep the document's meaning and wording, and you never invent steps.
```

and user prompt (`name` = file stem, `numbered` = lines as `f"{i:4d}| {line}"`):

```
Convert this process document into a reference tree.

<document name="{name}">
{numbered}
</document>

A reference tree has:
- actions: things the agent should do, one per node, in the document's wording. Split compound sentences only when the parts can happen independently.
- decisions: points where the process forks. Phrase each as a question about a fact of the situation ("Does any commit contain a breaking change?"), with one branch per outcome. Use short branch labels ("yes", "no", or the case name).
- end nodes: outcomes that finish the process ("Release notes are written").
- constraints: rules that apply throughout rather than at one step ("Never push tags", "Always keep the input file unchanged").

Rules:
- `next` links an action to the step after it; leave it "" for the last step on a path. Decisions use `branches` instead, and actions and end nodes leave `branches` empty.
- Ids are short kebab-case names (`find-tag`, `breaking-change`). root is the first step.
- line_start / line_end point at the 1-based document lines each node comes from.
- If the document is a plain list with no forks, produce a single chain.
- Don't add steps the document doesn't state, even obvious ones.
```

and schema:

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string"
    },
    "description": {
      "type": "string"
    },
    "root": {
      "type": "string"
    },
    "nodes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "kind": {
            "type": "string",
            "enum": [
              "action",
              "decision",
              "end"
            ]
          },
          "text": {
            "type": "string"
          },
          "next": {
            "type": "string"
          },
          "branches": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "label": {
                  "type": "string"
                },
                "next": {
                  "type": "string"
                }
              },
              "required": [
                "label",
                "next"
              ],
              "additionalProperties": false
            }
          },
          "line_start": {
            "type": "integer"
          },
          "line_end": {
            "type": "integer"
          }
        },
        "required": [
          "id",
          "kind",
          "text",
          "next",
          "branches",
          "line_start",
          "line_end"
        ],
        "additionalProperties": false
      }
    },
    "constraints": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "text": {
            "type": "string"
          },
          "line_start": {
            "type": "integer"
          },
          "line_end": {
            "type": "integer"
          }
        },
        "required": [
          "id",
          "text",
          "line_start",
          "line_end"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "name",
    "description",
    "root",
    "nodes",
    "constraints"
  ],
  "additionalProperties": false
}
```

Paths: depth-first root-to-leaf paths of `(node, branch label or None)`; a revisited node ends the path; cap 500.
`path_id` = `path-` + `<decision-slug>-<label-slug>` pairs joined by `-` (or `path-main`). A branch condition id is
`<decision>=<label-slug>`. For each node, its gates are the branch conditions present on every path to it, outermost first.

Compiling (`extract` does this when the skill has `reference:`; output `claims/<skill>.reference.claims.json`, recompiled
when `reference_sha` changes): render the tree one item per line, in depth-first order from the root, as
`[decision id] text`, then `  [branch decision=label] label -> next`, `[action id] text  -> next`, `[end id] text`, and
`[rule id] text` for constraints, numbered `f"{i:4d}| {line}"`. System prompt:

```
You write checks that tell an evaluation harness whether an AI agent's run carried out each step of a reference process. You prefer mechanical checks and never change what a step asks for.
```

User prompt:

```
Write a check for every step and branch condition of this reference process.

<reference name="{name}">
{numbered}
</reference>

The harness runs an agent (with Claude Code tool names: Read, Write, Edit, Bash, Glob, Grep, Skill) and records each tool call (name, JSON input, text output), the agent's text, and the files left in the working directory.

items: one entry per id marked [action], [end] or [rule] above.
- kind: step (an action), artifact (an outcome checkable in the final files or answer), must_not (a prohibition; its check describes the forbidden behavior, so a match is a violation), must_read (reading a specific file).
- check, with every field filled ("" or [] when unused):
  - type "tool": tools = tool names ([] = any), input_any = Python regexes against the JSON tool input (any may match), output_any = regexes against the tool output.
  - type "file": path_glob relative to the working directory, content_any = regexes (any may match; [] = existence).
  - type "judge": question = a yes/no question answerable from the trace, where "yes" means the step was done (for must_not, "yes" means the forbidden thing happened).
- Prefer tool and file checks when the step names a command, file, or exact output; accept reasonable equivalents. Use judge for anything that needs judgment.
- Agents often chain shell commands in one Bash call (`cd x && a; b | c`), so don't anchor command regexes to the start or end of the input.
- [end] items are outcomes. Check the observable result of reaching that state (a file exists, the final answer reports the outcome), not whether the agent used particular words to announce it.

conditions: one entry per id marked [branch] above (decision=label). text restates the branch as a fact about the task's inputs or situation ("a commit since the tag contains BREAKING CHANGE"), not as something the agent observed, because the harness also judges runs where the agent skipped the step that would reveal it. Use a judge check unless a tool output reliably reveals the fact.
```

Schema:

```json
{
  "type": "object",
  "properties": {
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "kind": {
            "type": "string",
            "enum": [
              "step",
              "must_not",
              "must_read",
              "artifact"
            ]
          },
          "check": {
            "type": "object",
            "properties": {
              "type": {
                "type": "string",
                "enum": [
                  "tool",
                  "file",
                  "judge"
                ]
              },
              "tools": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "input_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "output_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "path_glob": {
                "type": "string"
              },
              "content_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "question": {
                "type": "string"
              }
            },
            "required": [
              "type",
              "tools",
              "input_any",
              "output_any",
              "path_glob",
              "content_any",
              "question"
            ],
            "additionalProperties": false
          }
        },
        "required": [
          "id",
          "kind",
          "check"
        ],
        "additionalProperties": false
      }
    },
    "conditions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {
            "type": "string"
          },
          "text": {
            "type": "string"
          },
          "check": {
            "type": "object",
            "properties": {
              "type": {
                "type": "string",
                "enum": [
                  "tool",
                  "file",
                  "judge"
                ]
              },
              "tools": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "input_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "output_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "path_glob": {
                "type": "string"
              },
              "content_any": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              },
              "question": {
                "type": "string"
              }
            },
            "required": [
              "type",
              "tools",
              "input_any",
              "output_any",
              "path_glob",
              "content_any",
              "question"
            ],
            "additionalProperties": false
          }
        },
        "required": [
          "id",
          "text",
          "check"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "items",
    "conditions"
  ],
  "additionalProperties": false
}
```

Build the claims doc from the tree, taking only checks and kinds from the model: one condition per branch
(`text`, `check`, `node`, `label`; a missing check becomes the judge question `For this task, is the answer to "<decision>" <label>?`);
one claim per action, end, and rule with id = node id, `after` = nearest non-decision predecessors that the tree orders
before it (pairs in `precedes`, so loops add no back-edges; rules: none),
`when` = innermost gate, and `when_all` = all gates when there is more than one. Items with no check get a judge question
(`Did the agent do this: <text>?`) and a kind of must_not for rules containing never/don't/do not/must not, artifact for
ends, step otherwise. Store `reference`, `reference_sha`, and the claims/conditions.

For checking, merge the reference claims into the skill doc with ids prefixed `ref:` (also inside `after`, `when`, and
`when_all`), check once, and move `ref:` results to `check.json["reference"] = {claims, conditions}`.

### 13.3 Coverage

`skillgraph coverage SUITE [--skills] [--force] [--extractor-model S]` writes `claims/<skill>.coverage.json`, reused until
`reference_sha` or the skill doc's `claims_sha` changes. Exit 1 when any finding is an error. System prompt:

```
You compare a reference process with the instructions in an agent skill. You match by meaning, not wording, and you are strict: a skill line covers a step only if following it would make the agent do that step.
```

User prompt (reference items as `- id [decision] text (branches: label -> next; ...)`, `- id [kind] text (then next)`, and
`- id [rule] text`; skill items as `- kN [condition] text` and `- cN [kind] text (after a, b; only when k is true)`):

```
Align this reference process with the skill's claims.

<reference name="{ref_name}">
{ref_items}
</reference>

<skill name="{skill}">
{skill_items}
</skill>

nodes: one entry per reference id above (steps, decisions, end states, and rules).
- claims: ids of the skill claims (c*) and conditions (k*) that state this step or decision. [] if none.
- relation:
  - covers: the skill tells the agent to do this (or, for a decision, handles this fork).
  - partial: the skill mentions it but more weakly or narrowly (e.g. only one branch of a decision, or a softer wording).
  - conflicts: the skill tells the agent to do something incompatible with this step.
  - missing: nothing in the skill corresponds to it.
- note: one short sentence explaining the match, especially for partial and conflicts.

extra_claims: every skill claim that matches no reference id.
- relation: detail (an implementation detail consistent with the reference, such as an exact command), extra (a step the reference doesn't have), or conflicts (contradicts the reference).
- note: one short sentence.
```

Schema:

```json
{
  "type": "object",
  "properties": {
    "nodes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "ref": {
            "type": "string"
          },
          "claims": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "relation": {
            "type": "string",
            "enum": [
              "covers",
              "partial",
              "conflicts",
              "missing"
            ]
          },
          "note": {
            "type": "string"
          }
        },
        "required": [
          "ref",
          "claims",
          "relation",
          "note"
        ],
        "additionalProperties": false
      }
    },
    "extra_claims": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "claim": {
            "type": "string"
          },
          "relation": {
            "type": "string",
            "enum": [
              "detail",
              "extra",
              "conflicts"
            ]
          },
          "note": {
            "type": "string"
          }
        },
        "required": [
          "claim",
          "relation",
          "note"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "nodes",
    "extra_claims"
  ],
  "additionalProperties": false
}
```

Analysis: drop claim ids that don't exist; covers/partial with no claims becomes missing. Skill claims matched to no reference
item are extras (relation from the model, default extra). A branch is covered when any node gated innermost on it, or its first
node, is covered or partial. Order conflict: the reference has a before b (b reachable from a, not vice versa) while the skill
has a's claim transitively `after` b's claim. Score over non-end items: counts per relation, total, and
coverage = (covers + 0.5 partial) / total. Findings: missing-step (warn, non-end), conflict (error), partial (info),
unhandled-branch (warn), order-conflict (error), skill-extra (info) and skill-conflicts (error).

Diagnosis per reference item, from coverage and the follow rate with the skill (threshold 0.5):
end states: untested (no rate), ok, or unreached; actions and rules: conflict, untested, then missing -> implicit/gap,
partial -> ok/partial, covers -> ok/ignored; decisions: untested unless some reached run (all its gates held) took a branch,
then by the first node of each taken branch (a decision child counts as done): missing -> implicit/gap, otherwise ok/ignored.
A path is tested when some skill run held every branch condition on it.

### 13.4 Task generation

`skillgraph tasks SUITE --skill NAME [--untested-in RUNS_DIR | --paths 0,2] [-o OUT] [--extractor-model S]` drafts one task per
selected path (default: all; `--untested-in` keeps paths no skill run in RUNS_DIR took) and writes `suite.generated.yaml` next
to the suite, with `fixtures/generated/<path_id>/`. System prompt:

```
You design evaluation tasks for AI coding agents. Each task is a realistic request plus a small starting directory, built so that a specific path through a process is the right one to take.
```

User prompt (`process` = one line per node and rule, `path` = numbered steps with the label taken at decisions,
`skill_md` = the SKILL.md text):

```
Design one evaluation task that exercises this path through the "{ref_name}" process.

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
- notes: one sentence on what this task tests.
```

Schema:

```json
{
  "type": "object",
  "properties": {
    "id": {
      "type": "string"
    },
    "prompt": {
      "type": "string"
    },
    "files": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": {
            "type": "string"
          },
          "content": {
            "type": "string"
          }
        },
        "required": [
          "path",
          "content"
        ],
        "additionalProperties": false
      }
    },
    "setup_sh": {
      "type": "string"
    },
    "success": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "type": {
            "type": "string",
            "enum": [
              "file",
              "contains",
              "unchanged",
              "cmd"
            ]
          },
          "path": {
            "type": "string"
          },
          "pattern": {
            "type": "string"
          },
          "cmd": {
            "type": "string"
          }
        },
        "required": [
          "type",
          "path",
          "pattern",
          "cmd"
        ],
        "additionalProperties": false
      }
    },
    "notes": {
      "type": "string"
    }
  },
  "required": [
    "id",
    "prompt",
    "files",
    "setup_sh",
    "success",
    "notes"
  ],
  "additionalProperties": false
}
```

Write files only at safe relative paths (no absolute paths, no `..`, nothing under `.claude/` or `.git/`, and not
`setup.sh`; refused paths are reported as `refused to write <path>`), write `setup.sh` prefixed with `set -e` unless it
already starts with it, convert success checks to suite form, and print `REVIEW setup.sh` warnings when a script uses an
absolute path other than /dev/null, changes directory to `..`, `~` or `/`, touches `~/` or `$HOME`, uses curl, wget, ssh,
scp, nc, pip, npm, brew or apt, or runs sudo, `chmod -R 777`, or `rm -rf` on `/` or `~`.

### 13.5 Report panel

When a skill has a reference, the report adds a **Reference process** panel: four tiles (coverage share with counts, mean
follow rate over reference steps with the skill and without it, paths tested), a diagnosis legend, the reference tree drawn
with Cytoscape's breadthfirst layout from the root (diamonds for decisions, tags for end states, colored by diagnosis, edge
labels for branches), path chips (tested or not), a table (step, coverage relation with clickable claim ids and the note,
rate with, rate without, diagnosis; decisions show how often each branch was taken), and coverage findings. Hints say when
coverage hasn't run, is stale, or runs haven't been graded against the reference.

## 14. Milestones

1. Package skeleton, `providers/base.py`, `config.py`, `schema.py`, tests for model parsing, JSON helpers, normalization.
2. `graph.py` and lint; tests.
3. Providers: Claude, OpenRouter (mock transport tests), tools + harness (scripted chat tests).
4. `extract.py`, `runner.py` (workspace and variant tests on the examples), `checker.py` (mechanical, branch, order, cache tests).
5. `aggregate.py`, `report.py` + template; render test.
6. CLI, scaffold, the agent skill under `skills/skillgraph`, README, examples, CI.
7. Trees: mapping helpers and importers (synthetic transcripts and spans in tests), reference model + Mermaid parser + compile (mocked model), coverage analysis and diagnoses, task generation (mocked model), the report panel, the CLI commands.

8. Digests and windowed judging (mocked router and judge, a needle test), episodes, `digest`; the dashboard server,
   export and app (data API and its new fields, cache invalidation, path and Host checks, export bundle).

## 15. Acceptance checklist

- `uv sync --all-extras && uv run pytest` passes with no network access.
- `skillgraph doctor` reports provider status; `skillgraph models --search sonnet` lists models.
- `skillgraph lint examples/suite.yaml` reports the dangling `references/regions.md` in csv-report and exits 1.
- `skillgraph init <skill>` creates an editable suite; `install-skill --project DIR` installs the agent skill.
- With the user's approval of the cost: `skillgraph run examples/suite.yaml --out runs/smoke --skills release-notes --tasks minor-release --variants skill --runs 1`, then `check` and `report`, produce a report where release-notes claims are mostly satisfied.
- `skillgraph reference examples/references/release-sop.md` parses the Mermaid chart without a model: 9 actions, 3 decisions, 2 end states, 2 rules, 4 paths.
- `skillgraph import <a Claude Code session> --suite examples/suite.yaml --skill release-notes --out /tmp/x --dry-run` summarizes the session without writing anything.
- With the user's approval: `extract` and `coverage` on the examples report the release SOP steps the release-notes skill lacks (the no-commits branch, crediting authors, linking commits).
- `skillgraph summary examples/runs/demo`, `show` on one of its runs, `diff examples/runs/demo examples/runs/demo`, and `report examples/runs/demo` work offline; `summary ... --fail-on gap` exits 1.
- `skillgraph run examples/suite.yaml --out /tmp/x --runs 1` in a non-interactive shell prints an estimate and refuses without `--yes`.
- `skillgraph digest examples/runs/demo --validate --lexical-only` reports routing recall without spending anything.
- `skillgraph dashboard examples/runs/demo --export /tmp/site` writes a site that opens from disk; `skillgraph dashboard
  examples/runs/demo` serves it on 127.0.0.1.
- With the user's approval of the cost: `scripts/validate_digest.py` reports at least 95% agreement with the full-trace
  verdicts, no mechanical changes, and both needles violated.
