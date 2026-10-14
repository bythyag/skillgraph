# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/) (pre-1.0: minor versions may break things).

## [0.3.0] - 2026-10-14

First public release.

### Added
- `skillgraph estimate`: what a pipeline would cost, from earlier runs, OpenRouter's rate card, or model defaults.
- A spend gate: `run`, `check`, `ablate`, and `pipeline` print an estimate and ask before starting
  (`-y/--yes` for scripts), and `--max-total-usd` stops starting runs once the cap is reached.
- `skillgraph summary` (text, `--json`, `--junit`) with `--fail-on` for CI gating.
- `skillgraph diff BEFORE AFTER` to compare run sets claim by claim (`--json`, `--markdown`).
- `skillgraph show RUN_DIR` to read one run as a transcript with claim results inline.
- `--retry-errors` to rerun failed runs; `pipeline --strict` to stop on lint errors.
- Weak-verdict flags when a verdict rests on fewer than 3 runs per variant.
- A report warning when runs were graded against an older claims file.
- `examples/runs/demo`: a scrubbed, already-graded run set, so `summary`, `show`, `diff`, and
  `report` work without spending anything; `scripts/export_demo.py` regenerates it.
- Dockerfile for running untrusted skills in a container.
- LICENSE (MIT), CONTRIBUTING, SECURITY, CITATION, issue and PR templates, Dependabot,
  ruff in CI, a wheel-install CI job, Python 3.14 in the test matrix.

### Changed
- Claude agent, extractor, and judge sessions no longer see MCP servers or claude.ai
  connectors; traces record only the count of MCP tools that were visible.
- Fixture `setup.sh` scripts and `cmd:` success checks run without API keys in their environment.
- `skillgraph tasks` refuses to write generated files under `.claude/` or `.git/`; the runner warns
  when a fixture ships Claude Code settings or hooks.
- The report uses system fonts (no request to Google Fonts).

### Fixed
- Mermaid: `A-->B` without spaces parsed into garbage node ids; ids starting with keywords
  (`classify`, `styleCheck`) dropped lines.
- Reference trees with loops compiled into cyclic ordering rules; branch labels differing only in
  case shared a condition.
- A file check with no visible write made every later step "out of order"; file globs let `*` cross
  directories.
- Any unexpected tool error in the OpenRouter harness lost the whole run; editing a binary file,
  writing to a directory, and empty `old_string` edits are now tool errors.
- `run --force` and `import --force` left the previous `check.json`, so old verdicts stayed.
- A failing fixture `setup.sh` aborted the whole run matrix; it's now that run's error.
- Re-importing skillgraph traces dropped every run that loaded the skill by name; imported Claude
  Code sessions with similar names overwrote each other; microsecond and some ISO timestamps
  didn't parse on Python 3.10; `<system-reminder>` blocks leaked into reconstructed files.
- The report's embedded data now escapes every `<`.
- Validation and clear errors for bad `--lines`, `ablate-L5-3`, unknown `--skills`/`--tasks`,
  and out-of-range `tasks --paths`.

## [0.2.0] - 2026-10-14

### Added
- Model providers: Claude through the Claude Agent SDK and any OpenRouter model through a
  built-in agent harness; independent agent, extractor, and judge models.
- Trees: `import` (Claude Code, OpenTelemetry, LangSmith, OpenAI Agents SDK, mapping files),
  `reference`, `coverage`, `tasks`, and the reference-process panel in the report.
- `doctor`, `models`, `init`, `install-skill`, and the bundled agent skill with a build spec.

## [0.1.0] - 2026-10-14

### Added
- First version: claim extraction, lint, runs with and without the skill, ablation, checking,
  and the HTML report.
