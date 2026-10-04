# CLI reference

[← Back to README](../README.md)

This page collects the entry points and the most-used flags. Role/model selection
is in [Models](models.md); provider setup is in [Providers](providers.md).

## Entry points

| Command | What it does |
| --- | --- |
| `autocode "Your rough idea"` | The normal entry point. Runs the full plan → approve → build → validate → complete loop (or stops at the next required checkpoint). |
| `autopilot` | Deterministic workflow controller. Same loop as `autocode`, and the controller behind the dashboard and macOS app. |
| `autoplanner` | Planning only. Stops before any Builder starts. |
| `autocode-build` | Implementation only, from a saved run directory. |
| `autoreview` | Independent validation and completion-owner review. |
| `autoresolver` | Read-only diagnosis of reviewer-requested rework. |
| `autocode ui` / `autocode-ui` | Figma design (and optional `--build` handoff to implementation). |
| `autocode tasks` / `autocode-tasks` | Run a multi-lane task flow file. |
| `autocode components` / `autocode-components` | Build the components of an architecture record in parallel and combine them (see [Task lanes](task-lanes.md#building-components-of-an-architecture-in-parallel)). |
| `autocode program plan\|derive\|run\|status` / `autocode-program` | Plan a large requirement, derive a workstream manifest from the approved plan, run workstreams in parallel worktrees merged onto an integration branch (see [Programs](program.md)). |
| `autocode-dashboard` | Local browser dashboard. |
| `autocode --unit autoplanner\|autocode\|autoreview\|autoresolver` | Select one unit; omitting `--unit` runs all. |
| `autocode compare-baseline` | Compare Vitest failure evidence (see [Execution](execution.md#baseline-comparison)). |
| `autocode clean-worktrees [--yes]` | List, then with `--yes` remove, task worktrees whose runs are complete and whose branch holds their work; records are archived and branches kept (see [Task lanes](task-lanes.md#when-a-task-finishes)). |
| `autocode --version` | Print the installed version and, when run from a checkout, its commit. |
| `autocode models [--provider NAME] [--workspace PATH] [--json]` | List the models your plans offer, grouped by plan (subscription or pay per token) and tier (cheap worker, strong judge, Resolver only), and check every role's default route. Suggests a replacement for any default you cannot use; exits 1 when one is missing (see [Models](models.md#when-a-model-is-not-in-your-plans)). |
| `autocode doctor [--workspace PATH] [--engine opencode\|codex] [--json]` | Check Python, psutil, Git, each engine (OpenCode must be 1.x; Codex must be logged in) and that the workspace is a Git repository with a commit. Prints the fix for anything missing; exits 1 when not ready. Passes when any engine is ready, unless `--engine` names one. Never reads credentials. |
| `autocode registry location\|list\|import` | Registry API (see [Registry API](registry-api.md)). |
| `autocode intervention submit\|inspect` | Queued interventions (see [Interventions](interventions.md)). |

## Common flags

### Targeting a run

| Flag | Meaning |
| --- | --- |
| `--workspace /path` | Committed Git workspace to work in. Defaults to the current directory. |
| `--run-dir /path` | Resume a specific saved run. Always pair with the same `--workspace`. |
| `--in-place` | Start a new task in the selected checkout instead of a fresh worktree. Only one run's agents work in a checkout at a time; a second run exits with status 2 and changes nothing (see [Task lanes](task-lanes.md#multiple-tasks-in-one-project)). |
| `--workflow build\|bugfix\|review\|design\|discuss` | Name the kind of job instead of having the recognizer read it from the request. Also accepted by a saved run whose recognizer has not run yet. A run whose job is already decided keeps it: start a new run to change it (see [Workflow](workflow.md)). |
| `--status` | Read-only status, including `milestone_checkpoint`, `interventions`, `active_stage.activity`. |
| `--dry-run` | Read-only preview; never emits an accepted handoff. |

### Conversation and approval

| Flag | Meaning |
| --- | --- |
| `--chat` | Interactive chat mode (default in a terminal). |
| `--no-chat` | One command per turn (default for non-interactive). |
| `--answer 'Q1=…'` | Answer a requirements question (repeatable). Requires the current `--resolver-token` shown by Resolver. |
| `--feedback '…'` | Send a correction; returns to discovery and requires fresh approval. With `--adaptive-planning`, feedback on a plan shown for approval goes to the Planner, which revises it. |
| `--follow-up '…'` | Say the next thing to a finished run ("Fix them." after a review): the run recognizes the new job and continues in the same run directory. |
| `--delegate Q1` | Accept a question's proposed default. Requires the current `--resolver-token` shown by Resolver. |
| `--delegate-all --review-token 'r3:<hash>'` | Delegate every pending question marked `delegable` with a proposed default, on the exact displayed revision. Refuses the whole call if any question lacks a default, is not delegable, has a protected or missing category (cost, quota, permission, external side effect, requested outcome), or asks about a rejected assumption. Never approves; invalidates any existing approval. |
| `--reject-assumption A1 --review-token 'r3:<hash>'` | Reject a structured assumption from the displayed requirements handoff (repeatable). A stale token, or a handoff refreshed since display, is refused. Never approves; invalidates any existing approval. |
| `--show-goal` | Display the current contract/revision. |
| `--approve-goal 'r3:<hash>'` | Approve the exact displayed revision. |
| `--edit-goal body.json` | Load a full contract body as a new draft revision. |
| `--approve-review C1 --review-token '…'` | Record a human-review decision for criterion `C1`. |
| `--investigator-model MODEL`, `--investigator-reasoning-effort LEVEL` | Pin the stuck-stage Investigator's model for this run (default, at high: Claude Opus 5.5 in `kilocode` runs, otherwise GPT-6 Sol, or GLM 5.3 when the stuck stage runs on Sol). A `provider/model` id runs it through OpenCode. See [Workflow](workflow.md#when-a-stage-stops-making-progress). |
| `--resolver-response provide_information --resolver-request ID --resolver-token '…'` | Answer an Resolver operational request with corrective information. `--resolver-response` requires both `--resolver-request` and `--resolver-token`; the response itself authorizes no retry, approval or budget change. |

### Execution and recovery

| Flag | Meaning |
| --- | --- |
| `--resume-paused` | Acknowledge an operational pause and continue. Does not approve a draft, and does not restore a spent recovery allowance. |
| `--diagnose-failed-stage` | With `--resume-paused`, request bounded read-only diagnosis of a recorded repeated Builder report failure. Alternative to `--retry-failed-stage`; not a permission or budget override. |
| `--grant-recovery N` | With `--resume-paused`, authorize N more automatic timeout recoveries for a run paused at `PAUSED_TIMEOUT_RECOVERY` after its cause was fixed. Audited as a `recovery_grant` user event; recovery history is retained. |
| `--planning-review-call-limit N` | At a reconciled planning-budget pause, save a total allowance for the current cycle. `0` disables the cap for this and future cycles while preserving usage history; it can also be saved at a requested pause or after abandoning a stopped stage. No model launch or approval; resume separately. |
| `--pause-after-stage` | Stop at the next saved boundary. |
| `--retry-builder M2` | With `--resume-paused`, authorize one retry of the exhausted current serial milestone or stopped parallel members. Keeps failure history, model routes and verification gates; all workers must be stopped. |
| `--abandon-stage '001/terra-01'` | Archive a stopped attempt, keep partial edits and logs. |
| `--retry-report ATTEMPT_ID` | With `--resume-paused`, request fresh Tester evidence after an exhausted rejected report with an exact attempt ID; saved source and evidence pins must still match. |
| `--accept-transport-change` | Resume a transport-change pause after route checks. |
| `--max-parallel-builders N` | Concurrency limit for independent milestone Builders. |
| `--milestone-checkpoints` / `--request-milestone-checkpoints` | Enable milestone checkpoints (idle boundary / queued). |
| `--max-milestone-seconds N` | Milestone active-time budget (default 5400; `0` disables). |
| `--max-milestone-replans N` | Changed-approach replan limit (default 1). |
| `--max-findings-per-task N` | Cap open findings bundled into one REWORK task. |
| `--max-idle-seconds` / `--max-tool-seconds` / `--max-stage-seconds` | Watchdog limits (new-run defaults `300` / `1800` / `3600`; `0` disables). |
| `--max-seconds N` | Total active provider time for the run (new-run default `43200`, 12 hours; `0` disables). Checked at stage boundaries. |
| `--no-progress-limit N` | Unchanged-batch limit (`0` disables; never disables the 3-recovery ceiling). |
| `--max-iterations N` | Optional total iteration ceiling; new runs default to unlimited, and resumes retain their saved limit. |
| `--test-command CMD` | The project's test suite command for runner-owned regression proof (default: detected). Correct a saved command with `--resume-paused` at a reconciled pause before the Tester or combined checkpoint; see [Bug fixes](workflow.md#bug-fixes). |
| `--regression-command CMD` | A command that runs only the fix's new or changed tests (default: derived). Saved corrections require the same pre-validation `--resume-paused` boundary as `--test-command`. |

### Engine, provider, and models

| Flag | Meaning |
| --- | --- |
| `--engine opencode\|codex` | Engine for the run. OpenCode is the default; other tools join as providers (see [Providers](providers.md)). |
| `--provider <name>` | External tool registered via TOML (see [Providers](providers.md#add-a-tool)). |
| `--joint-planning` | Add joint Requirements / Plan Reviewer work. |
| `--adaptive-planning` / `--no-adaptive-planning` | New runs plan as deep as the job needs by default (joint planning on the default flow): a clear build request skips the Requirements stage, and a Plan Reviewer with no blocking concern approves the draft. `--no-adaptive-planning` keeps the fixed sequence; `--adaptive-planning` insists. See [Adaptive planning](adaptive-planning.md). |
| `--builder-strong-model MODEL` | Stronger model for the Builder's second attempt. |
| `--requirements-model`, `--glm-model`, `--plan-reviewer-model` | Planning-role model overrides (bare GPT names). |
| `--astra-model`, `--terra-model`, `--sol-model`, `--completion-model` | Execution-role model overrides (`provider/model` IDs). |
| `--<role>-provider` | Per-role Codex provider override (Responses API). |
| `--reasoning-effort`, `--<role>-reasoning-effort` | Shared / per-role reasoning effort (`low`, `medium`, `high`, `xhigh`, …). |
| `--migrate-only` | Run the opt-in legacy migration and stop (see [Testing](testing.md#legacy-migration--opt-in-only)). |

### Programs

| Flag | Meaning |
| --- | --- |
| `program run MANIFEST --max-parallel N` | Concurrent workstreams (default 2). |
| `program run MANIFEST --authorize-deployment` | Allow `deployment` workstreams to start or resume; their runs still need plan approval. Descriptor generation is ordinary `code`. |
| `program run MANIFEST --retry-workstream ID` | Explicitly retry a failed workstream in its existing worktree/checkpoint, without bypassing child gates. Repeat for multiple failed workstreams. |
| `program run MANIFEST --dry-run` | Validate and preview without creating branches or worktrees. |
| `program derive --run-dir RUN --output program.json` | Write the manifest from an approved plan; refuses unapproved plans. |

Unrecognized `program run` flags (for example `--engine`, model overrides) are passed through to every child code run.

Program manifests support `code`, `integration`, and `deployment`. UI workstreams are
deferred until the UI runner supports checkpoint recovery; use `autocode ui` separately.

### Figma / UI

| Flag | Meaning |
| --- | --- |
| `--build` | Hand an accepted Figma result to the implementation runner. |
| `--figma-file <url>` | Refine or implement from a specific Figma file. |
| `--ui-run /path` | Import a completed design run. |
| `--figma-review human` | Require a human visual approval during implementation. |
| `--max-plan-reworks`, `--max-reworks` | Review-loop limits (`none`, `0`, or a number). |

## Unattended callers (agents)

`autocode-unattended` (or `scripts/autocode-unattended` from a checkout) runs AutoCode
for another agent without letting that agent make the operator's decisions. It takes
AutoCode's own arguments but refuses every decision or recovery flag (`--answer`,
`--delegate*`, `--approve-*`, `--resume-paused`, `--retry-*`, `--feedback`, `--follow-up`,
`--accept-completion`, …, including abbreviations) and the `intervention`, `tasks`,
`ui`, `program`, `registry`, `capture` and `compare-baseline` subcommands. It forces `--no-chat`
with no stdin, and when AutoCode stops it prints `--status` and tells the caller to
report and stop. Exit codes are AutoCode's.

When a run completes, the wrapper prints the command to analyze it:
`autocode-unattended --analyze --run-dir RUN [--out DIR]`. That launches no stage; it
reads the saved run and reports the outcome, each acceptance criterion with its
recorded status and evidence, findings, stages (role, time, exit, tokens, report
path), cost by role (model calls, seconds and tokens per role, with report-format
repair calls counted separately), and the code changes against the task's base commit,
including new untracked files, plus a summary of the run's always-on [activity log](execution.md#activity-log).
`--out` saves `analysis.md`, the full `changes.diff` and a copy of `activity.jsonl`.

To lock a Claude Code agent to it, launch the agent from a copy of
[`examples/agent-operator`](../examples/agent-operator): its `.claude/settings.json`
allows only `autocode-unattended` and read-only tools, denies edits and other
AutoCode or provider commands, and uses `dontAsk` so anything else is refused
without a prompt. Its `CLAUDE.md` has the agent report stops verbatim and, after completion,
analyze the work read-only. Keep that directory outside the target workspace.

## Exit codes

- **0** — an action was saved, or the task completed.
- **2** — user input, pause, or error. Inspect `--status`; do not rely on the exit code alone.

See also: [Install](install.md) · [Workflow](workflow.md) · [Execution](execution.md)

## Exact tool output

Use `--tool-output-mode raw|conservative` for AutoCode capture/file-read display.
`autocode output read FILE` returns exact sections with retained originals;
`autocode output retrieve SHA256 --raw` recovers exact bytes. See
[exact output transport](exact-output.md) for options, recovery and measurement
limits. Native provider tools keep their existing behavior.

A failed read-only workflow job exposes an exact `retry_job` action in status.
After inspecting the archived attempt, retry with `--resume-paused
--retry-failed-stage --job-retry-token TOKEN` using its current
`needs.job_retry_token`. Plain resume does not repeat the job. Unrestored source
or changed source/model/limits requires a fresh authorized run.
