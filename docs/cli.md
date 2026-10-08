# CLI reference

[← Back to README](../README.md)

This page collects the entry points and the most-used flags. Role/model selection
is in [Models](models.md); provider setup is in [Providers](providers.md).

## Entry points

| Command | What it does |
| --- | --- |
| `autocode "Your rough idea"` | The normal entry point. Runs the full plan → approve → build → validate → complete loop (or stops at the next required checkpoint). |
| `autocode resume` | Continue the unfinished run of this project or task worktree (see [Which run a command acts on](#which-run-a-command-acts-on)). Never starts a new task. On a paused or blocked run (`PAUSED_*`, `BLOCKED_*`, `*_REWORK_REQUIRED`, `RESOLVER_PENDING`) it also acknowledges the stop, as `--resume-paused` does, so the run goes on: no new budget or recovery allowance, though the per-cycle report-repair and resolver attempt counts restart. A design conflict (`PAUSED_DESIGN_CONFLICT`) is only shown until you edit the design and pass `--resume-paused`. Its companions (`--retry-failed-stage`, `--grant-recovery N`, ...) need no `--resume-paused` after it. Plain `autocode` with no task relaunches a running run the same way but only shows a stop. |
| `autocode status` | The same as `autocode --status`: read-only status of that run. Both words are commands wherever they stand among the options; a task whose whole text is `resume` or `status` goes after `--` (`autocode -- status`). |
| `autopilot` | Deterministic workflow controller. Same loop as `autocode`, and the controller behind the dashboard and macOS app. |
| `autoplanner` | Planning only. Stops before any Builder starts. |
| `autocode-build` | Implementation only, from a saved run directory. |
| `autoreview` | Independent validation and completion-owner review. |
| `autoresolver` | Read-only diagnosis of reviewer-requested rework. |
| `autocode ui` / `autocode-ui` | Figma design (and optional `--build` handoff to implementation). |
| `autocode tasks` / `autocode-tasks` | Run a multi-lane task flow file. |
| `autocode components` / `autocode-components` | Build the components of an architecture record in parallel and combine them (see [Task lanes](task-lanes.md#building-components-of-an-architecture-in-parallel)); with `--integrate TARGET --run-local`, also start the combined system with Docker Compose and run its smoke check (see [Running the combined system locally](task-lanes.md#running-the-combined-system-locally)). |
| `autocode program plan\|derive\|show\|approve\|run\|status\|request-change\|resolve-change` / `autocode-program` | Plan a large requirement, derive a workstream manifest from the approved plan, read and approve that manifest as the program agreement by exact token, run workstreams in parallel worktrees merged onto an integration branch, and raise or reject interface change requests (see [Programs](program.md)). |
| `autocode-dashboard` | Local browser dashboard. |
| `autocode --unit autoplanner\|autocode\|autoreview\|autoresolver` | Select one unit; omitting `--unit` runs all. |
| `autocode compare-baseline` | Compare Vitest failure evidence (see [Execution](execution.md#baseline-comparison)). |
| `autocode clean-worktrees [--yes]` | List, then with `--yes` remove, task worktrees whose runs are complete and whose branch holds their work; records are archived and branches kept (see [Task lanes](task-lanes.md#when-a-task-finishes)). |
| `autocode --version` | Print the installed version and, when run from a checkout, its commit. |
| `autocode models [--provider NAME] [--workspace PATH] [--json]` | List the models your plans offer, grouped by plan (subscription or pay per token) and tier (cheap worker, strong judge, Resolver only), and check every role's default route. Suggests a replacement for any default you cannot use; exits 1 when one is missing (see [Models](models.md#when-a-model-is-not-in-your-plans)). |
| `autocode doctor [--workspace PATH] [--engine opencode\|codex] [--json]` | Check Python, psutil, Git, each engine (OpenCode must be 1.x; Codex must be logged in) and that the workspace is a Git repository with a commit. Prints the fix for anything missing; exits 1 when not ready. Passes only when the engine a new run uses is ready, resolved as a run resolves it: `--engine codex` is Codex; otherwise (no `--engine`, or `--engine opencode`) the provider `AUTOCODE_PROVIDER` or `default_provider` in `~/.config/autocode/config.toml` names, else OpenCode. That provider must answer and list every role's default model (`opencode models`, as `autocode models` checks). `AUTOCODE_PROVIDER=codex` names a provider config, not the Codex engine. A ready Codex alone does not pass; doctor names `--engine codex` as the single-login alternative. Runs without psutil and reports it missing. Never reads credentials. |
| `autocode registry location\|list\|import` | Registry API (see [Registry API](registry-api.md)). |
| `autocode intervention submit\|inspect` | Queued interventions (see [Interventions](interventions.md)). |

## Common flags

### Targeting a run

| Flag | Meaning |
| --- | --- |
| `--workspace /path` | Committed Git workspace to work in. Defaults to the current directory. Without a task or `--run-dir`, where AutoCode looks for the run to act on. |
| `--run-dir /path` | Act on this saved run, from any directory. Without `--workspace`, the run's own checkout is used. |
| `--in-place` | Start a new task in the selected checkout instead of a fresh worktree. Uncommitted and untracked files there count as the code the task starts from (see [Workflow](workflow.md)). Only one run's agents work in a checkout at a time; a second run exits with status 2 and changes nothing (see [Task lanes](task-lanes.md#multiple-tasks-in-one-project)). |
| `--workflow build\|bugfix\|review\|design\|discuss` | Name the kind of job instead of having the recognizer read it from the request. Also accepted by a saved run whose recognizer has not run yet. A run whose job is already decided keeps it: start a new run to change it (see [Workflow](workflow.md)). |
| `--status` | Read-only status, including `milestone_checkpoint`, `interventions`, `active_stage.activity`. |
| `--dry-run` | Read-only preview; never emits an accepted handoff. |

### Which run a command acts on

A command with no task and no `--run-dir` acts on a saved run found from the
`--workspace` directory: the run directory you are in; otherwise the runs of the task
worktree you are in; otherwise the project's in-place runs and the runs of all its task
worktrees. It names the run on stderr (`Using the saved run RUN (STATUS, ...)`) and goes on
as if you had passed `--run-dir RUN`.

| Command | Run it takes |
| --- | --- |
| `autocode`, `autocode resume`, `--resume-paused` and its `--retry-*` companions, `--unit` | The only unfinished run. A finished run (`TASK_COMPLETE`, or stopped) is never relaunched this way, and a run that `autocode program` or `autocode tasks` drives is left to that command. |
| `--status`, `--dry-run`, `autocode status` | The only unfinished run; with none, the latest finished one. |
| `--show-goal`, `--answer`, `--approve-goal`, `--feedback` and the other user actions | The only unfinished run, preferring one no program or task flow drives. These save the run, so they never pick a finished one. |
| `--follow-up` | The most recently completed run, preferring one no program or task flow drives. Refused when a run started after it finished has not finished: name the run with `--run-dir`. |

With several unfinished runs the command changes nothing, exits 2 and lists them with the
`--run-dir` command for each. With no run it says where it looked; with only finished
runs, a bare `autocode` names the latest one instead of relaunching it. Inside a run
directory whose `state.json` is missing or cannot be used, or one `autocode clean-worktrees`
archived, it refuses rather than pick another run.
A task, or a new-run option (`--in-place`, `--figma-file`, `--figma-manifest`,
`--figma-review`, `--ui-run`, `--builder-strong-model`, `--conversation-handoff`), starts
a new run instead; `autocode resume` never does.

### Conversation and approval

| Flag | Meaning |
| --- | --- |
| `--chat` | Interactive chat mode (default in a terminal). |
| `--no-chat` | One command per turn (default for non-interactive). |
| `--answer 'Q1=…'` | Answer a question the run is waiting on (repeatable; the status view's `needs.kind` is `answer`). Requires the current `--resolver-token` shown by Resolver. A finished run waits on none: the questions in its report take `--follow-up` (see [Waiting or finished](#waiting-or-finished)). |
| `--answer route-sol=MODEL` | At a quota stop (`PAUSED_BUDGET`) or a content-filter refusal (`PAUSED_CONTENT_FILTER`), name the model the stopped role continues on; the only operational question `--answer` takes. Requires the current `--resolver-token`. The model must suit the role's engine, be listed by OpenCode on OpenCode runs and keep the cross-model rule; otherwise the run stays paused with the question open. Sets the stopped attempt aside as `--abandon-stage` does and records a `route_assignment`; continue with `--resume-paused`. The resume-flag form is `--abandon-stage ATTEMPT`, then `--resume-paused --sol-model MODEL`. A stopped workflow job (`needs.kind` `retry_job` with `needs.route`) takes `--answer route-ROLE=MODEL --job-retry-token TOKEN` instead: no resolver token, no `--abandon-stage`, no `--ROLE-model` flag; it issues a new job retry token, so continue with `--resume-paused --retry-failed-stage --job-retry-token NEW_TOKEN` ([Waiting or finished](#waiting-or-finished)). See [Models](models.md#when-a-roles-quota-runs-out). |
| `--feedback '…'` | Send a correction; returns to discovery and requires fresh approval. With `--adaptive-planning`, feedback on a plan shown for approval goes to the Planner, which revises it. |
| `--follow-up '…'` | Say the next thing to a finished run ("Fix them." after a review): the run recognizes the new job and continues in the same run directory. Finished runs only (`TASK_COMPLETE`); any other run exits 2 unchanged. After a design review, the reply revises that review: the Architect keeps every concern under its id, keeps a settled one as resolved with what settled it, and adds the revision to `revisions` in `review/design-review.json` ([Replying to a design review](#replying-to-a-design-review)). After a design turn, "Build it." builds that design as approved: it is checked against the code first (`check_design`). The message is saved as feedback that planning can cite for requested contract changes; the new plan still needs approval. |
| `--delegate Q1` | Accept a question's proposed default. Requires the current `--resolver-token` shown by Resolver. |
| `--delegate-all --review-token 'r3:<hash>'` | Delegate every pending question marked `delegable` with a proposed default, on the exact displayed revision. Refuses the whole call if any question lacks a default, is not delegable, has a protected or missing category (cost, quota, permission, external side effect, requested outcome), or asks about a rejected assumption. Never approves; invalidates any existing approval. |
| `--reject-assumption A1 --review-token 'r3:<hash>'` | Reject a structured assumption from the displayed requirements handoff (repeatable). A stale token, or a handoff refreshed since display, is refused. Never approves; invalidates any existing approval. |
| `--show-goal` | Display the current contract/revision. At the approval stop it also says what approving authorizes and what the token locks, and ends with a summary of the decision (outcome, permissions, criteria and how each is checked, what passing proves), the limits in effect and the exact approve command ([sample](workflow.md#conversation-and-approval)). |
| `--approve-goal 'r3:<hash>'` | Approve the exact displayed revision. |
| `--edit-goal body.json` | Load a full contract body as a new draft revision. |
| `--approve-review C1 --review-token '…'` | Record a human-review decision for criterion `C1`. |
| `--investigator-model MODEL`, `--investigator-reasoning-effort LEVEL` | Pin the stuck-stage Investigator's model for this run (default, at high: Claude Opus 5.5 in `kilocode` runs, otherwise GPT-6 Sol, or GLM 5.3 when the stuck stage runs on Sol). A `provider/model` id runs it through OpenCode. See [Workflow](workflow.md#when-a-stage-stops-making-progress). |
| `--resolver-response provide_information --resolver-request ID --resolver-token '…'` | Answer an Resolver operational request with corrective information. `--resolver-response` requires both `--resolver-request` and `--resolver-token`; the response itself authorizes no retry, approval or budget change. The next `autocode resume` has Resolver re-evaluate it once: the run either continues through the normal admission checks or stays paused, naming the exact control it needs where the CLI has one ([Execution](execution.md)). |
| `--close-finding ID --close-reason '…'` | Close an open reviewer finding as your own decision (repeatable), for example a duplicate of a problem you already settled. Records who closed it and why, and launches no agent. Closing every finding a validation-only stop asked about answers that stop, so the next `--resume-paused` continues. |

#### Waiting or finished

`--follow-up` continues only a finished run (`TASK_COMPLETE`). A waiting, paused or blocked run
refuses it (exit 2, state unchanged): answer it (`--answer`/`--delegate` with `--resolver-token`),
approve or correct it (`--approve-goal`/`--feedback`), or resume it. Jobs that report without
waiting for a reply (design review, discussion, code review) complete with their questions or
findings in their report; you reply to them with `--follow-up`. After a design turn, "Build it."
builds that design as approved (checked against the code first, no requirements gathering or
questions, plan approval still required). The build gets a plan of its own: the design turn's
plan (which wrote no code), with whatever earlier turns agreed that it carried, and its open
non-blocking findings are archived in the run, not revised; the design is now the requirements.
After a design review instead, building the design it approved revises the plan in force. A follow-up builds as approved only the design its previous turn wrote, or the design a
design review approved; any other document is planned from requirements as usual.

| The run is | Status view | Say the next thing with | Through `TaskRun` |
| --- | --- | --- | --- |
| finished | `done` is true, `needs` is null | `--follow-up TEXT` | `follow_up(text)` |
| waiting for you | `needs.kind` is `answer`, `approve_plan`, `review` or `planning_budget` | `--answer`/`--delegate` with `--resolver-token`, `--approve-goal`, `--approve-review`, `--feedback` | `answer`, `approve_plan`, `approve_review`, `feedback` |
| stopped | `needs.kind` is `resume` | `autocode resume`, once the cause in `stop_reason` is resolved (`--resume-paused` after editing the design at `PAUSED_DESIGN_CONFLICT`) | `resume_paused()` |
| stopped in a workflow job (Reviewer, Architect, Analyst, Investigator) | `needs.kind` is `retry_job`, or `recover_source` for an attempt with no saved source identity | `--resume-paused --retry-failed-stage --job-retry-token TOKEN` after inspecting `needs.archive`; with `needs.route` (quota or a content-filter refusal), first `--answer route-ROLE=MODEL --job-retry-token TOKEN` to run it on another model, then the retry with the new token; for `recover_source`, a new run ([Task-run interface](task-run.md#failed-workflow-jobs)) | `retry_job(token)`; with `needs.route`, `assign_model(role, model)` first |
| waiting on another run | `needs.kind` is `dependency` | `--receive-dependency MANIFEST` once that run delivers | `receive_dependency(manifest)` |

The wrong one is refused with exit 2 and the run left as it was. `--follow-up` on an unfinished
run lists the alternatives (answer, approve or correct, resume). On a finished run, `--answer`,
`--delegate` or `--delegate-all` says: "This run is finished and waits for no answer; reply to
the questions in its report with --follow-up TEXT", and `--feedback`, `--edit-goal` or
`--reject-assumption` says it would reopen the run without a new turn. Without `--run-dir`,
`--follow-up` is also refused when a run started after the latest finished one has not finished:
the message lists that run, and the command that continues the finished one anyway. Through
`TaskRun` these raise `TaskRunError`.

#### Replying to a design review

A design review never waits for its questions: the run completes with them in
`review/design-review.json`, and you answer with `--follow-up` ("Ordering is per-domain."). The
Architect is asked to keep a concern that is a problem only under one answer to its question
advisory until you answer. A reply recognized as design makes the Architect revise the same
review instead of writing a new one. It is asked to:

- keep every earlier concern under its id, open or resolved, and to drop or renumber none;
- keep a concern the reply settles with `status` `resolved` and a `resolution` saying what
  settled it (an answer can also make a concern blocking), and to add a concern only for a
  problem the reply exposes;
- drop the questions the reply answered and keep the others under their ids.

The runner refuses a revision that leaves out an earlier concern id, uses an id twice, resolves
a concern without a resolution or resolves one raised in that revision, or whose verdict is not
`request_changes` exactly when an open concern is blocking. A reply that names a different
design gets a fresh review (revision 1); a reply asking for a new design hands it to the build
pipeline, as a first request would.

`revision` is the review's number, and `revisions` keeps one entry per review: the message that
prompted it (`said`, null for the run's first review), its feedback receipt (`event_id`), the
verdict, and the ids of its open blocking, open advisory and resolved concerns and of its
questions. The report you reply to must be the one the Architect wrote: if
`review/design-review.json` was edited since, `--follow-up` exits 2 with "changed since the
Architect's review; restore it or start a new run".

### Execution and recovery

`autocode resume` can replace `--resume-paused` alongside a recovery companion such
as `--grant-recovery N`, `--retry-failed-stage`, or an explicit budget change. This
also works when AutoResolver has published the operational pause as
`WAITING_FOR_USER`, provided its saved request is still valid. Bare `resume`
does not acknowledge that published request; ordinary questions and approvals
still require their own actions. Recovery eligibility and token checks are unchanged.

| Flag | Meaning |
| --- | --- |
| `--resume-paused` | Acknowledge an operational pause and continue. Does not approve a draft, and does not restore a spent recovery allowance. `autocode resume` implies it at `PAUSED_*` (not `PAUSED_DESIGN_CONFLICT`), `BLOCKED_*`, `*_REWORK_REQUIRED` and `RESOLVER_PENDING`, never with a user action or `--resolver-response`; with a recovery companion, also at a verified operational pause published as `WAITING_FOR_USER`. |
| `--diagnose-failed-stage` | With `--resume-paused`, request bounded read-only diagnosis of a recorded repeated Builder report failure. Alternative to `--retry-failed-stage`; not a permission or budget override. |
| `--grant-recovery N` | With `--resume-paused`, authorize N more automatic timeout recoveries for a run paused at `PAUSED_TIMEOUT_RECOVERY` after its cause was fixed. Audited as a `recovery_grant` user event; recovery history is retained. |
| `--planning-review-call-limit N` | At a reconciled planning-budget pause, save a total allowance for the current cycle. `0` disables the cap for this and future cycles while preserving usage history; it can also be saved at a requested pause or after abandoning a stopped stage. No model launch or approval; resume separately. |
| `--pause-after-stage` | Stop at the next saved boundary. |
| `--verbose` / `--no-verbose` | Stream each stage's live model activity (tools started and finished, new model text, one bounded line each) to stderr as `Role/model: …`. On by default; `--no-verbose` or `AUTOCODE_VERBOSE=0` turns it off. Not saved with the run: each command that runs stages reads it afresh. The full stream is always kept in the stage's `iterations/NNN/<stage>-NN.jsonl`. |
| `--retry-builder M2` | With `--resume-paused`, authorize one retry of the exhausted current serial milestone or stopped parallel members (not a member its provider's content filter refused: answer its `route-terra` question; once that request was answered with information or left paused, `--retry-builder` asks the question again and launches nothing). Keeps failure history, model routes and verification gates; all workers must be stopped. |
| `--abandon-stage '001/terra-01'` | Archive a stopped attempt, keep partial edits and logs. |
| `--retry-report ATTEMPT_ID` | With `--resume-paused`, request fresh Tester evidence after report repair or repeated-failure limits stop a rejected report, using the exact attempt ID status names; saved source and evidence pins must still match. After a source edit, `--resume-paused` validates the current source instead. |
| `--accept-transport-change` | Resume a transport-change pause after route checks. |
| `--accept-source-edit` | With `--resume-paused`, hand a paused repair the source edited while it was stopped. The resolution's source revision becomes the current snapshot, and a recovery packet bound to the replaced source stays on disk but is detached from the request. The approved contract, task, budget, proof and evidence pins stay. It refuses any other change, and source the Resolver wrote. |
| `--max-parallel-builders N` | Concurrency limit for independent milestone Builders. |
| `--milestone-checkpoints` / `--request-milestone-checkpoints` | Enable milestone checkpoints (idle boundary / queued). |
| `--max-milestone-seconds N` | Milestone active-time budget (default 5400; `0` disables). |
| `--max-milestone-replans N` | Changed-approach replan limit (default 1). |
| `--max-findings-per-task N` | Cap open findings bundled into one REWORK task. |
| `--max-idle-seconds` / `--max-tool-seconds` / `--max-stage-seconds` | Watchdog limits (new-run defaults `300` / `1800` / `3600`; `0` disables). |
| `--max-seconds N` | Total active provider time for the run (new-run default `43200`, 12 hours; `0` disables). Checked at stage boundaries. |
| `--no-progress-limit N` | Unchanged-batch limit (new-run default `3`; `0` disables the cap, never the 3-recovery ceiling). With `--resume-paused`, an N above the retained count, or `0`, acknowledges a `PAUSED_NO_PROGRESS` request, also when N is already saved; it never acknowledges another cause's pause. When the limit caused the pause, its advice names this flag and the retained count, not `--resolver-response`: information alone never acknowledges it. |
| `--max-iterations N` | Optional total iteration ceiling; new runs default to unlimited, and resumes retain their saved limit. |
| `--test-command CMD` | The project's test suite command for runner-owned regression proof (default: detected). Correct a saved command with `--resume-paused` at a reconciled pause before the Tester or combined checkpoint; see [Bug fixes](workflow.md#bug-fixes). |
| `--base-patch PATH` | Bug fixes whose only regression test needs a hook or variable the fix adds: a patch that adds only that instrumentation to the original code, so the test can run and fail there. Pinned by hash, may not change test files, and its edits must occur at the corresponding original source locations in the final change; every proof that uses it asks the Tester and Completion Reviewer to check it changes no behavior. Set it when the run starts, or with `--resume-paused` at a stop before the Tester or a completion check. |
| `--regression-command CMD` | A command that runs only the fix's new or changed tests (default: derived). Saved corrections require the same pre-validation `--resume-paused` boundary as `--test-command`. |

### Engine, provider, and models

| Flag | Meaning |
| --- | --- |
| `--engine opencode\|codex` | Engine for the run. OpenCode is the default; other tools join as providers (see [Providers](providers.md)). |
| `--provider <name>` | External tool registered via TOML (see [Providers](providers.md#add-a-tool)). |
| `--allow-uncontained-tools` | Built-in OpenCode runs only (including a Codex run whose `--investigator-model` is an OpenCode `provider/model`). Without it, a run is refused before any stage when the kernel tool boundary cannot exist here (it needs macOS `sandbox-exec` and OpenCode 1.18.33). With it, the Builder, Validator and other non-planning stages run with OpenCode's own permission checks only, no kernel containment. Accepted on a new run or a resume (including a `PAUSED_TOOL_CONTAINMENT` run); saved with the run and recorded as a user event, so later resumes need not repeat it. The status view shows `tool_containment`. See [Execution](execution.md#native-tool-containment). |
| `--joint-planning` | Add joint Requirements / Plan Reviewer work. |
| `--adaptive-planning` / `--no-adaptive-planning` | New runs plan as deep as the job needs by default (joint planning on the default flow): a clear build request skips the Requirements stage, and a Plan Reviewer with no blocking concern approves the draft. `--no-adaptive-planning` keeps the fixed sequence; `--adaptive-planning` insists. See [Adaptive planning](adaptive-planning.md). |
| `--builder-strong-model MODEL` | New run: the model for the Builder's stronger attempt after its ordinary retry (default `openai/gpt-6-sol`, or `strong_model` under `[builder_retry]` in the provider config; see [Models](models.md#builder-retry-policy)). Refused when it is the checker model. |
| `--single-model MODEL` | New run: use one model for every role, including reviewers; explicitly relaxes cross-model verification for single-subscription accounts and implies joint planning. |
| `--requirements-model`, `--glm-model`, `--plan-reviewer-model` | Planning-role model overrides (bare GPT names). |
| `--astra-model`, `--terra-model`, `--sol-model`, `--completion-model` | Execution-role model overrides (`provider/model` IDs). |
| `--<role>-provider` | Per-role Codex provider override (Responses API). |
| `--reasoning-effort`, `--<role>-reasoning-effort` | Shared / per-role reasoning effort (`low`, `medium`, `high`, `xhigh`, …). |
| `--migrate-only` | Run the opt-in legacy migration and stop (see [Testing](testing.md#legacy-migration--opt-in-only)). |

### Programs

| Command or flag | Meaning |
| --- | --- |
| `program plan BRIEF --workspace DIR` | Plan the request with the ordinary planning unit (`--unit autoplanner`, in place) and a program preamble. `--engine` and unrecognized flags go to that run. |
| `program derive --run-dir RUN --output program.json` | Write the manifest from the run's `approved_contract`; refuses unapproved plans. `--workspace` names the plan run's project, `--name` the program; without `--output` it prints the manifest. |
| `program show MANIFEST --workspace DIR` | Print the program agreement and the exact token (`a<revision>:<digest>`) that approves its pending revision, or the approved revision. Saves nothing. |
| `program approve MANIFEST --workspace DIR --token TOKEN` | Approve that agreement revision; refuses any other token. No workstream starts before the first approval, and every later revision is approved the same way. |
| `program run MANIFEST --max-parallel N` | Concurrent workstreams (default 2). |
| `program run MANIFEST --authorize-deployment` | Allow `deployment` workstreams to start or resume; their runs still need plan approval. Descriptor generation is ordinary `code`. |
| `program run MANIFEST --retry-workstream ID` | Explicitly retry a failed workstream in its existing worktree/checkpoint, without bypassing child gates. Repeat for multiple failed workstreams. |
| `program run MANIFEST --check-timeout S` | Seconds each cumulative check may take after a merge (default 900). |
| `program run MANIFEST --engine codex\|opencode` | Engine for the workstream runs it starts; a workstream's own `engine` wins. |
| `program run MANIFEST --dry-run` | Validate and preview without creating branches or worktrees. |
| `program status MANIFEST --workspace DIR` | The same summary as `run`, read from the saved state and each unfinished child's status view, without launching or saving anything. |
| `program request-change MANIFEST --workspace DIR --interface ID --by WORKSTREAM --reason TEXT [--proposal TEXT]` | Open a change request (`CR-N`) on a shared interface; its producer, its consumers and the final check (for an interface with no producer, every workstream) neither start, resume nor merge while it is open, and the program is not `COMPLETE` while any request is open. |
| `program resolve-change MANIFEST --workspace DIR --request CR-N --reject --reason TEXT` | Reject an open change request. Accepting one is an approved agreement revision that publishes the interface's next version. |

Every program command except `derive` takes `--workspace` (default: the current
directory), the root of the project's Git checkout. Unrecognized `program run` flags
(for example model overrides) are passed through, with `--workflow build`, to every
workstream run that pass starts; a resumed run keeps its saved settings. `program run`
exits 0 only when the program is `COMPLETE`.

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
`--accept-completion`, `--close-finding`, `--close-reason`, `--resolver-response`, …, including
abbreviations), the command word `resume` (on a paused or blocked run it stands for
`--resume-paused`; a bare relaunch still continues a run that is not paused)
and the `intervention`, `tasks`, `ui`, `program`, `registry`, `capture` and `compare-baseline`
subcommands. It forces `--no-chat` with no stdin, and when AutoCode stops it prints
`--status` and tells the caller to report and stop. Exit codes are AutoCode's.

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
blocks retry until the exact original source is restored. The CLI verifies file
bytes, modes and Git HEAD even when a capture artifact is missing; changed
model/limits still invalidate the retry. An older attempt with no saved original
identity exposes `recover_source` and an explanation instead of a retry action.
Inspect its archive and current changes before starting a new run.

A job its provider's content filter refused (`PAUSED_CONTENT_FILTER`) or that ran
out of quota (`PAUSED_BUDGET`) pauses the same way, and its stop names the cause
and the model. The same model is likely to refuse again, so `needs.route` carries
the job's model question. `--job-retry-token` is accepted without `--resume-paused
--retry-failed-stage` only together with that answer:
`--answer route-ROLE=MODEL --job-retry-token TOKEN` checks the model the way a launch
would, records a `route_assignment` and issues a new token for the new model. The
old token stops working, and the exact retry with the new token is the only way on:
`--resume-paused --retry-failed-stage --job-retry-token NEW_TOKEN`. After a refusal,
until a model is named, the status view's `needs.action` is that answer and the
recovery card offers no retry, since the exact retry would replay the refused model. The Architect,
Analyst and Investigator have no `--ROLE-model` flag, so this answer is how they
move. The answer changes only that model: given with a limit or another role's
model, with or without its token, it is refused and nothing is saved. It issues a
new token, so it is given on its own: next to `--resume-paused --retry-failed-stage`
it is refused before anything is read or saved. At that stop `--abandon-stage` is
refused, and a `--ROLE-model` flag for the stopped role is refused with this advice
instead of being saved (saving it would make the retry stale); only a flag that
puts back the model the retry is bound to is saved. The same flag is refused while
the job's attempt is still uncertain, including with `--abandon-stage` in the same
command: run `--abandon-stage ATTEMPT` alone, then answer with the token it shows.
After a quota stop, the unchanged retry with the shown token also works once the
quota resets. A stuck-stage Investigator (`investigate_stuck`) keeps only the
exact retry: its route is rebuilt on every launch, and an answer naming a model for
it is refused with that reason. A job stop saved before stopped jobs could take
another model keeps only the exact retry too; the refusal names its cause, reading
the archived attempt's provider errors for a quota stop saved then.
