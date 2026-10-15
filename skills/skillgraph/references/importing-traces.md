# Importing run trees

`skillgraph import` turns agent traces from other tools into skillgraph runs, so real
sessions can be graded against a skill (and its reference process) with the same
`check` and `report` as test runs.

```bash
skillgraph import <files or dirs> --suite evals/<skill>/suite.yaml --skill <skill> --out runs/<skill>-prod
skillgraph check runs/<skill>-prod
skillgraph report runs/<skill>-prod
```

Always start with `--dry-run`. It parses and prints the number of traces, how many
loaded the skill, and which tool names didn't map, without writing anything.

## Built-in formats (`--format auto` detects them)

| format | what to pass | notes |
|---|---|---|
| claude-code | `~/.claude/projects/<project>/` or one `<session>.jsonl` | subagent transcripts in `<session>/subagents/` are attached under the tool call that spawned them |
| otel | OTLP JSON (`resourceSpans`) or a JSON list of spans | GenAI conventions (`gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `gen_ai.tool.call.arguments/result`) and OpenInference (`openinference.span.kind=TOOL`, `tool.name`, `input.value`, `output.value`) |
| langsmith | a JSON list of runs, JSONL, or `{"runs": [...]}`; nested `child_runs` work | tool runs are `run_type: tool`; llm runs give the agent's text |
| openai-agents | spans as a JSON list, JSONL, or `{"data": [...]}` | `span_data.type: function` are tool calls; `generation` spans give text |
| skillgraph | `trace.json` files from `skillgraph run` | re-import into another run set |

Claude Code transcripts contain everything the user typed and every file the agent
read. Ask before importing someone else's sessions, and keep imported run
directories out of version control.

## What import does to each trace

1. **Segments at the skill load.** Events before the agent loaded the skill are dropped
   (a `Skill` tool call, a `/skill-name` command, or a read of `skills/<name>/SKILL.md`).
   The user message just before the load becomes the task prompt. `--whole-session`
   keeps everything.
   A session that loads the skill several times becomes one trace per load (an
   *episode*), named `<session>-e1`, `-e2`, and so on. An episode ends at the next load of
   the skill; `--episode-end user` also ends it at the next message the person typed, which
   suits sessions where one person asks for several unrelated things. `--episodes first`
   keeps the old behavior: one trace from the first load to the end of the session.
2. **Skips traces that never loaded the skill**, unless you pass `--include-unloaded`.
   Then they are imported as `baseline` runs. These are different tasks from the
   skill runs, so treat with/without comparisons on imported data as rough.
3. **Maps tool names** onto the names claims use: Bash, Read, Write, Edit, Glob, Grep,
   Skill. `execute_command`, `run_shell_command`, `read_file`, `str_replace_editor`
   (by its `command` field), `mcp__*__read_file` and many others are built in. The
   original name is kept as `raw_tool`.
4. **Normalizes inputs**: `cmd`/`script` -> `command`, `path`/`file` -> `file_path`,
   `old_str` -> `old_string`. The original fields stay.
5. **Rebuilds final files** from Write calls, and from Edit calls applied to content seen
   through Write or Read. Files the trace never shows in full are `null`, so `file`
   checks on them miss. Pass a judge question instead if that matters.
6. **Keeps the tree**: every event has `parent`, `depth` and `agent` when the source had them.

## Long sessions

A real session can run to hundreds of thousands of tokens. `check` judges traces over
about 30,000 tokens in *windowed* mode (`--judge-mode auto`, the default): a cheap digest
model points each question at the turns that hold its evidence, and the judge reads those
turns in full. `must_not` rules, and anything the window can't settle, are judged on a
skeleton of the whole trace (every event, with long fields shortened). A missing step
is never inferred from the router failing to find it. Verdicts record which view decided
them (`window` or `skeleton`).

```bash
skillgraph digest runs/x                      # turns, tool calls, tokens, what auto would pick (free)
skillgraph digest runs/x --validate --lexical-only   # how often routing finds the evidence (free)
skillgraph check runs/x --judge-mode windowed --intents --yes   # also name each run's phases
skillgraph show runs/x/<skill>/<task>/skill/r1 --turns
skillgraph dashboard runs/x                   # browse it: timeline by turn, off-skill turns, results
```

`--judge-mode full` keeps one call over the whole trace; an explicit mode redoes checks
made in the other mode. The digest model (`--digest-model`, `SKILLGRAPH_DIGEST_MODEL`,
default Claude Haiku) never grades, and the intent call never sees the skill.

## Unmapped tools

The dry run lists tool names that didn't map. If any of them run shell commands or
touch files, map them:

```yaml
# tool-map.yaml
run_in_sandbox: Bash
fs_write: Write
```

```bash
skillgraph import traces/ --suite S --skill X --out runs/prod --tool-map tool-map.yaml --dry-run
```

Tools that do something else (web search, deploy) can stay unmapped; claims that name
them still match by their raw name.

## Formats without a built-in importer: write a mapping

When `--format auto` fails, look at a few records, then write a mapping file and pass
it as `--format mapping.yaml`. Keys:

```yaml
name: my-agent                 # label for the traces
format: json                   # json (one document) or jsonl (one record per line)
records: data.steps            # path to the list of records inside a json document
children: children             # optional: key holding nested child records
group_by: session_id           # optional: one trace per distinct value
order_by: started_at           # optional: sort key (ISO time, epoch s/ms/ns, or number)
id: step_id                    # optional: record id (for parent links)
parent: parent_id              # optional: parent record id
tool_call:                     # required: which records are tool calls
  when: {type: tool}           # path -> value, list of values, or "*" (present)
  name: tool.name
  input: tool.arguments        # JSON strings are decoded
  output: tool.result
  error: tool.error            # optional; truthy means the call failed
text:                          # optional: the agent's messages
  when: {type: message, role: assistant}
  value: content
prompt:                        # optional: the user's request
  when: {type: message, role: user}
  value: content
tools: {exec: Bash}            # optional extra tool-name aliases
```

Paths are dot-separated keys, `[0]` indexes, `[*]` to map over a list, and
`["dotted.key"]` for keys containing dots, e.g. `attributes["gen_ai.tool.name"]`.

Iterate with `--dry-run` until the summary shows the tool calls you expect and nothing
important is unmapped. Then save the mapping next to the suite so the next import uses it.

## Limits

- Imported runs have no fixtures, so suite success checks don't apply.
- Without a "without the skill" twin of each session, verdicts are mostly
  followed/ignored. For working/inert, run the suite's own tasks too.
- Production traces often truncate tool outputs; checks on `output_any` can miss.
