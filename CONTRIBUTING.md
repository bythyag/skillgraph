# Contributing

Thanks for helping. Bug reports with a `skillgraph doctor` output and a small repro are the
most useful contribution; pull requests are welcome too.

## Set up

```bash
uv sync --all-extras        # creates .venv with the package, the Claude extra, pytest and ruff
uv run pytest -q            # offline: every provider is mocked, nothing calls a model
uv run ruff check src tests
```

## Ground rules

- **Tests never touch the network or a paid model.** Mock `skillgraph.providers.structured`,
  script the harness's chat function, or use `httpx.MockTransport` (see `tests/test_openrouter.py`).
- **Prompts and schemas are part of the spec.** If you change the extractor, judge, harness,
  reference conversion or compilation, coverage alignment, or task generation prompts or
  schemas, update `skills/skillgraph/references/build-spec.md` so it quotes the new text.
  `tests/test_spec.py` fails until you do.
- **Keep run data out of commits.** `runs/` is ignored. `examples/runs/demo` is a scrubbed
  run set regenerated with `scripts/export_demo.py`; don't hand-edit it.
- **User-visible changes** go in `CHANGELOG.md` and, where relevant, the README.
- Match the surrounding code: small functions, type hints, comments only where the code
  can't say it.

## Running against real models

Live runs cost money. Use cheap models while developing, for example:

```bash
export SKILLGRAPH_AGENT_MODEL=claude:claude-haiku-5-5
export SKILLGRAPH_JUDGE_MODEL=claude:claude-haiku-5-5
uv run skillgraph estimate examples/suite.yaml --runs 1
uv run skillgraph run examples/suite.yaml --out runs/dev --skills release-notes --tasks minor-release --runs 1 --yes
```

## Layout

See the "Development" section of the README and `docs/design.md`.
