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
| `autocode program plan\|derive\|run\|status` / `autocode-program` | Plan a large requirement, derive a workstream manifest from the approved plan, run workstreams in parallel worktrees merged onto an integration branch (see [Programs](program.md)). |
| `autocode-dashboard` | Local browser dashboard. |
| `autocode --unit autoplanner\|autocode\|autoreview\|autoresolver` | Select one unit; omitting `--unit` runs all. |
| `autocode compare-baseline` | Compare Vitest failure evidence (see [Execution](execution.md#baseline-comparison)). |
| `autocode registry location\|list\|import` | Registry API (see [Registry API](registry-api.md)). |
| `autocode intervention submit\|inspect` | Queued interventions (see [Interventions](interventions.md)). |

## Common flags

### Targeting a run

| Flag | Meaning |
| --- | --- |
| `--workspace /path` | Committed Git workspace to work in. Defaults to the current directory. |
| `--run-dir /path` | Resume a specific saved run. Always pair with the same `--workspace`. |
| `--in-place` | Start a new task in the selected checkout instead of a fresh worktree. |
| `--status` | Read-only status, including `milestone_checkpoint`, `interventions`, `active_stage.activity`. |
| `--dry-run` | Read-only preview; never emits an accepted handoff. |

### Conversation and approval

| Flag | Meaning |
| --- | --- |
| `--chat` | Interactive chat mode (default in a terminal). |
| `--no-chat` | One command per turn (default for non-interactive). |
| `--answer 'Q1=…'` | Answer a requirements question (repeatable). |
| `--feedback '…'` | Send a correction; returns to discovery and requires fresh approval. |
| `--delegate Q1` | Accept a question's proposed default. |
| `--show-goal` | Display the current contract/revision. |
| `--approve-goal 'r3:<hash>'` | Approve the exact displayed revision. |
| `--edit-goal body.json` | Load a full contract body as a new draft revision. |
| `--approve-review C1 --review-token '…'` | Record a human-review decision for criterion `C1`. |

### Execution and recovery

| Flag | Meaning |
| --- | --- |
| `--resume-paused` | Acknowledge an operational pause and continue. Does not approve a draft. |
| `--pause-after-stage` | Stop at the next saved boundary. |
| `--retry-builder M2` | Explicitly retry a failed milestone Builder (after all workers stopped). |
| `--abandon-stage '001/terra-01'` | Archive a stopped attempt, keep partial edits and logs. |
| `--accept-transport-change` | Resume a transport-change pause after route checks. |
| `--max-parallel-builders N` | Concurrency limit for independent milestone Builders. |
| `--milestone-checkpoints` / `--request-milestone-checkpoints` | Enable milestone checkpoints (idle boundary / queued). |
| `--max-milestone-seconds N` | Milestone active-time budget (default 5400; `0` disables). |
| `--max-milestone-replans N` | Changed-approach replan limit (default 1). |
| `--max-findings-per-task N` | Cap open findings bundled into one REWORK task. |
| `--max-idle-seconds` / `--max-tool-seconds` / `--max-stage-seconds` | Watchdog limits (defaults `300` / `1800` / `0`). |
| `--no-progress-limit N` | Unchanged-batch limit (`0` disables; never disables the 3-recovery ceiling). |
| `--max-iterations N` | Total iteration ceiling. |

### Engine, provider, and models

| Flag | Meaning |
| --- | --- |
| `--engine opencode\|codex` | Engine for the run. OpenCode is the default. |
| `--provider <name>` | External tool registered via TOML (see [Providers](providers.md#add-a-tool)). |
| `--joint-planning` | Add joint Requirements Planner / Plan Reviewer work. |
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

## Exit codes

- **0** — an action was saved, or the task completed.
- **2** — user input, pause, or error. Inspect `--status`; do not rely on the exit code alone.

See also: [Install](install.md) · [Workflow](workflow.md) · [Execution](execution.md)
