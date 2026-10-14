# Security

## What skillgraph runs on your machine

skillgraph evaluates skills by letting an AI agent act. Know what that means before you
point it at a skill you didn't write:

- **Agent runs execute shell commands on the host**, inside a temporary workspace under the
  run directory, with your user's permissions. There is no sandbox.
  - Claude models run inside Claude Code (via the Claude Agent SDK) with Bash allowed and no
    permission prompts. The agent can read anything your user can, and Claude Code runs with
    the credentials it needs (your login or `ANTHROPIC_API_KEY`).
  - OpenRouter models run in skillgraph's own harness. File tools refuse paths outside the
    workspace and API keys are removed from the shell environment, but `Bash` can still
    reach the rest of the machine.
- **Fixture `setup.sh` scripts and `cmd:` success checks** run with `bash` in the workspace,
  with API keys removed from their environment.
- **`skillgraph tasks` writes model-generated fixtures and `setup.sh` scripts.** It refuses
  paths outside the fixture and under `.claude/` or `.git/`, and flags scripts that use
  absolute paths, the network, or `sudo`. Read every generated script before running it.
- **A skill or fixture can carry Claude Code project settings** (`.claude/settings.json`,
  hooks). skillgraph only installs the skill under `.claude/skills/`, but a fixture you copy
  in is used as-is.

To evaluate a skill from an untrusted source, run skillgraph inside the container described
in the README ("Run untrusted skills in a container"), with only the API key it needs.

## Data in run directories and reports

Traces, `check.json` files, and HTML reports contain prompts, tool inputs and outputs, file
contents, and absolute paths. Imported Claude Code transcripts contain everything typed in
those sessions. Treat run directories like logs: keep them out of version control (`runs/` is
ignored by default) and review a report before sharing it.

## Reporting a vulnerability

Please report security problems privately through
[GitHub security advisories](https://github.com/bythyag/skillgraph/security/advisories/new),
not as public issues. Include the command, the skill or fixture involved, and what happened.
