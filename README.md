# Autocode

A conversation-first runner for multi-agent coding. Describe a rough idea, approve a
plan, and Autocode coordinates Requirements Gathering → Planning → Review →
Implementation → Validation → Completion across separate model sessions — with you
in control at every decision point.

Runs on top of [OpenCode](https://opencode.ai) 1.x (default) or [Codex](https://github.com/openai/codex),
in any committed Git workspace.

## Why Autocode

- **Roles do one job.** Requirements, planning, building, validation, and completion
  are separate model sessions with separate budgets — not one agent grading its own homework.
- **You approve the plan.** Implementation starts only after you explicitly approve
  the displayed brief. A writer's self-assessment never authorizes completion.
- **Evidence-based completion.** Green tests and changed files are not completion.
  Independent validation of every acceptance criterion is.
- **Crash-safe and resumable.** Checkpoints, atomic state writes, worktree isolation,
  and bounded recovery. Interrupt it; resume it; nothing gets silently replayed.
- **Honest status.** Stale state is labeled stale. Failures pause and explain. It
  never quietly spends your budget on a dead end.

Compared to "just use one coding agent": Autocode adds process — plan review,
independent validation, explicit approval gates, and recovery — in exchange for
trustworthy completion. That trade is worth it for work you need to be able to rely
on. See [Reliability priorities](RELIABILITY.md) for the project's current focus.

## How it works

```text
You → Requirements Gatherer: rough idea → saved requirements report
Requirements Gatherer → Planner: draft task DAG
Planner → Plan Reviewer → Planner revision → Plan Reviewer final → your approval

Autopilot → AutoCode task scheduler → independent Builders → combined validation

Builder → Validator → Completion Owner
   ↑                       |
   └──────── REWORK ───────┘
```

| Role | Job |
| --- | --- |
| **Requirements Gatherer** | Clarify outcome, scope, constraints, definition of done |
| **Planner** | Draft the task DAG and technical approach |
| **Plan Reviewer** | Challenge the draft; owns final planning decisions |
| **Builder** | Implement one bounded task |
| **Validator** | Independently inspect and test the combined code |
| **Completion Owner** | Propose `COMPLETE` / `REWORK` / `CONTINUE` / `BLOCKED` |

`autopilot` is the deterministic controller that wires these together: it dispatches
units, coordinates recovery, pauses for your approval, and enforces the completion
gate. Each role's model and reasoning level is configurable. Details:
[Workflow](docs/workflow.md) · [Models and escalation](docs/models.md).

## Quickstart

### Prerequisites

Python 3.11+, Git, and **OpenCode 1.x** connected to ChatGPT and Z.ai
(live-checked with 1.18.31). macOS/Linux; Windows needs WSL.
Full details: [Install](docs/install.md).

### Install

```sh
# Run from a checkout:
python3 /path/to/autocode/tools/autocode.py "Your rough idea" --workspace /path/to/project

# Or install a command once:
pipx install --editable /path/to/autocode
```

### Run

From inside any committed Git project:

```sh
autocode "Your rough idea"
```

You'll get a Requirements Planner conversation that clarifies the idea, a Plan
Reviewer that challenges the draft, and then a plan you must approve before any
code is written. From there the build → validate → complete loop runs within
your configured limits.

### What you'll see

1. **DISCOVERING** — answer a few focused questions about scope and definition of done.
2. **AWAITING_GOAL_APPROVAL** — review the displayed brief; type feedback or `y` to approve.
3. **EXECUTING** — Builders implement, a Validator checks the combined code independently.
4. **COMPLETE** — every acceptance criterion has passing evidence, or a clear rework/blocked status.

Status at any time: `autocode --status`. Resume after an interruption:
`autocode --resume-paused`.

## Commands at a glance

| Command | Purpose |
| --- | --- |
| `autocode "Your rough idea"` | The normal full loop |
| `autopilot` | Same loop, and the controller behind the dashboard/macOS app |
| `autoplanner` / `autocode-build` / `autoreview` / `autoresolver` | Individual stages at their saved boundaries |
| `autocode ui "…"` | Figma design (→ `--build` hands off to implementation) |
| `autocode tasks flow.json` | Multi-lane task flows |
| `autocode-dashboard --port 8767` | Local browser dashboard |
| `autocode --status` / `--resume-paused` / `--approve-goal` | Checkpoint, resume, approve |

Full flag reference: [CLI](docs/cli.md).

## Feature guides

| Guide | What's in it |
| --- | --- |
| [Install and run](docs/install.md) | Prerequisites, install paths, engine flags |
| [Workflow](docs/workflow.md) | Planning, approval, Autopilot units, handoffs |
| [Models and escalation](docs/models.md) | Role model overrides, escalation ladders, retries |
| [Providers](docs/providers.md) | OpenCode and Codex adapters, custom tools, KiloCode |
| [Execution and completion](docs/execution.md) | Parallel Builders, milestones, findings, timeouts, completion gate |
| [CLI reference](docs/cli.md) | Every entry point and flag |
| [Browser dashboard](docs/dashboard.md) | Local web UI for planning, approvals, monitoring |
| [macOS app](docs/macos-app.md) | Native wrapper around the dashboard |
| [Figma design](docs/figma.md) | Design → review → implementation via Figma |
| [Task lanes](docs/task-lanes.md) | Multi-task flows and worktree isolation |
| [Registry API](docs/registry-api.md) | Run discovery for browser clients |
| [Interventions](docs/interventions.md) | Queued feedback and pause requests |
| [Testing](docs/testing.md) | Test suites, evidence rules, legacy migration |

## Project layout

```text
tools/                 runner, units, providers, schemas (Python)
  units/               autoplanner / autocode / autoreview / autoresolver
  dashboard/           browser dashboard (Python + JS)
  providers/configs/   bundled tool TOMLs (OpenCode, KiloCode, …)
macos-app/             native macOS host for the dashboard
docs/                  detailed guides (this README links out to them)
audits/                recorded verification runs
RELIABILITY.md         current development priorities
VALIDATION.md          measured results and remaining limits
```

## Development status

The stated priority is **reliable completion of agreed work** — completion,
recovery, trustworthy status, and clear requests for human input. New features
require an identified user need and an explicit scope decision; competitor feature
parity is not a reason to expand scope.

Claims of dependable project completion require live trials, not just fixture tests;
three bounded real-model cases are still outstanding. See
[RELIABILITY.md](RELIABILITY.md) for the criteria and
[VALIDATION.md](VALIDATION.md) for what has been measured so far.

## Tests

```sh
python3 -m unittest tools/test_escalation.py tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py tools/test_opencode.py tools/test_process.py
```

The suite uses isolated Git fixtures and a deterministic fake provider — it proves
runner behavior, not model quality. More: [Testing](docs/testing.md).

## License

See the repository for license terms.
