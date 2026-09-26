# Install and run

[← Back to README](../README.md)

## Prerequisites

- **Python 3.11+**
- **Git**
- **OpenCode 1.x** connected to ChatGPT and Z.ai (the default engine). Live-checked with OpenCode **1.18.31**; OpenCode 2.x is not supported.
- **macOS or Linux**. Windows needs WSL because the inherited process and lock mechanisms use POSIX APIs.
- Optional: Codex CLI for `--engine codex` and the Figma path. Installation does not change Codex or OpenCode settings.

Native process supervision uses the `psutil` runtime dependency. Works against any
committed Git workspace; no IdleCampus files or services are required.

## Install

Run directly from this checkout:

```sh
python3 /path/to/autocode/tools/autocode.py "Build a greeting CLI" \
  --workspace /path/to/project --reasoning-effort high
```

Install the command once with `pipx` to invoke it from any project:

```sh
pipx install --editable /path/to/autocode
```

Or install into your own virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/autocode "Build a greeting CLI" --workspace /path/to/project
```

### Sanity check

Confirm the install resolved the CLI entry points:

```sh
autocode --help
autopilot --help
```

## The normal invocation

From inside any committed Git project:

```sh
autocode "Your rough idea"
```

Or target another committed Git workspace:

```sh
autocode "Your rough idea" --workspace /path/to/project
```

That starts joint requirements planning: a Requirements Gatherer clarifies the
outcome, a Planner drafts a task DAG, and a Plan Reviewer challenges it. You approve
the plan before any Builder starts. See [Workflow](workflow.md).

## Advanced entry points

Use the whole workflow through `autopilot`, or invoke one unit at its saved boundary:

```sh
autopilot "Build a greeting CLI" --workspace /path/to/project --chat

# Or invoke individual units at their saved boundaries:
autoplanner "Build a greeting CLI" --workspace /path/to/project --chat
# After approval, this stops before any Builder starts.
# Use the workspace and run directory printed by the planner:
autocode-build --workspace /path/to/run-workspace --run-dir /path/to/run --no-chat
autoreview --workspace /path/to/run-workspace --run-dir /path/to/run --no-chat
# If review requests rework, diagnose it without launching a Builder:
autoresolver --workspace /path/to/run-workspace --run-dir /path/to/run --no-chat
# Let Autopilot continue through any remaining build/review cycles:
autopilot --workspace /path/to/run-workspace --run-dir /path/to/run --no-chat
```

Each unit command stops successfully before dispatching another unit.
`autocode --unit autoplanner|autocode|autoreview|autoresolver` provides the same
selection; omitting it runs all units.

## Engine and planning flags

New runs use joint Requirements Planner/Plan Reviewer work by default. An approved
three-role OpenCode run can add Planner (GLM) work at a clean execution boundary with
`--joint-planning --resume-paused`. Its approved work and existing sessions remain;
the Planner joins the next brief revision.

Native Codex, including Figma runs, supports `--engine codex --joint-planning`:
requirements gathering, planning, and plan review run in separate read-only Codex
sessions using the existing ChatGPT login. The three routes inherit the saved
planning model unless explicitly selected with `--requirements-model`, `--glm-model`,
and `--plan-reviewer-model` (bare GPT names). Adding joint planning to a saved Codex
run at a clean execution or discovery boundary backs up the checkpoint, retains the
work and existing sessions, and restarts at requirements gathering. The reviewed plan
needs fresh approval before further implementation. Saved runs retain their engine
and limits.

## Default provider

New runs use OpenCode unless you choose otherwise. `--provider <name>` picks the
tool for one run. To change the default for every new run and for the dashboard,
set `AUTOCODE_PROVIDER=kilocode` or add this to `~/.config/autocode/config.toml`:

```toml
default_provider = "kilocode"
```

A saved run keeps the provider it started with, and `--engine codex` runs still
use Codex. `autocode-dashboard --provider <name>` overrides the default for the
dashboard; its model pickers list that tool's models.

See also: [CLI](cli.md) · [Providers](providers.md) · [Models](models.md)
