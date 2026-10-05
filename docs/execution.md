# Execution and completion

[← Back to README](../README.md)

This page covers independent milestone Builders, milestone checkpoints and acceptance,
open findings, timeouts and recovery, and the completion gate.

## The project's own tests as examples

Every Builder prompt, including each parallel milestone Builder's, carries the opening lines of
the project's own tests, so new tests follow the house style. That style covers the framework,
imports, fixtures, helpers, naming and assertions.

- **Which tests.** `tools/autocode_test_examples.py` picks at most two tracked test files, trying
  each of these in turn:
  1. test files the task itself edits
  2. tests named after a source file it edits (`weeks.py` → `test_weeks.py`)
  3. tests in the same directories

  A test in the task's own language wins a tie. If none of these exist, it takes the shallowest
  test in the repository.
- **How much.** Each excerpt is the first 60 lines, capped at 3,000 characters.
- **Status.** The excerpts are examples to follow, not files to edit.
  `current_task.affected_paths` still decides what the Builder may change.
- **Without tests.** A project with no tracked tests adds nothing to the prompt.

## Independent milestone Builders

New joint runs save `orchestration={enabled:true,max_parallel:2}`. Existing saved
runs retain their saved routing; these defaults do not upgrade them. Set
`--max-parallel-builders N` when creating a run to choose the concurrency limit;
`1` runs serially. Explicitly supplying this option also enables orchestration for
new `--engine codex` runs.

Planner milestones may include optional `affected_paths`: literal,
repository-relative file or directory ownership, such as `src/api.py` or `tests/api`.
These are ownership boundaries, not glob patterns. Absolute paths, parent traversal,
and repository metadata paths such as `.git` and `.autocode` are not allowed.
The Orchestrator selects ready milestones with disjoint ownership and acceptance
criteria, and satisfied explicit dependencies. Missing ownership or dependency
information, overlapping paths, shared criteria, or an unsatisfied dependency
prevents those milestones from running together; when no independent batch can be
formed, execution falls back to a serial Builder.

Each selected milestone gets a fresh Builder subprocess, session, and Git worktree.
The runner combines their patches into the parent workspace only if it still matches
the captured baseline. The Tester checks the combined result before the Completion Reviewer
and required human acceptance gates can authorize completion. Builder success or
patch integration alone does not satisfy those gates. There is no automatic merge
into `master`. Once a batch is integrated, each Builder's checkout and
`autocode/builder-*` branch are removed; its run records stay at the same path
(`.autocode/builders/<batch>/<n>/.autocode/`) for inspection.

Failed workers, stale baselines, or overlapping worker changes pause the run and
retain worktrees and logs for inspection. After inspecting a failed Builder, explicitly
retry it once all workers have stopped (from the project or task worktree; add
`--run-dir RUN` when there are several unfinished runs):

```sh
autocode --resume-paused --retry-builder M2
```

Repeat `--retry-builder` to select additional failed milestones. Successful siblings
are retained rather than rerun. A Builder that needs the stronger model its batch's
checkers run does not pause the run and cannot be retried this way: its milestone is
built serially later (see [Builder retry policy](models.md#builder-retry-policy)).
An explicit retry archives an uncertain stage while preserving its edits and logs.
Report-only repairs and completed-response recovery run automatically through the
existing bounded recovery mechanisms. After a revised
plan is reapproved, child workers and batches from the previous plan are safely
archived once stopped, preserving their work and logs.
Interrupted worktree setup resumes only when its source matches the saved baseline;
an incomplete or manually modified checkout pauses for inspection without overwriting files.

The active batch is saved in
`state.orchestration_batch`, with `id`, `status`, and `workers`; each worker records
`milestone_id`, `status`, `workspace`, and `run_dir`. Integrated batches move to
`state.orchestration_history`, and `deferred` lists the members left to a serial build
(worker status `SERIAL_ESCALATION`). A batch in which every member deferred moves there
with status `DEFERRED` and nothing integrated. The dashboard labels `orchestrator` as
runner-owned and displays saved batch/worker statuses and worktree/log locations.
Those statuses are checkpoint reports, not proof that worker processes are currently
alive.

## Report-only repair

A completed provider response can fail JSON, schema, or evidence validation without
requiring implementation to run again. The runner saves OpenCode's selected terminal
assistant text as `.response.txt` before parsing and the parsed report as `.json`
before schema/evidence validation. These are rejected artifacts, not accepted results.
Tool output and earlier assistant messages are not substitutes for the terminal report.

The rejected artifacts are archived and hash-pinned. Repair receives the complete
report/text inline, its stable path and hash, the exact validation error, and mappings
for artifact paths moved by archival. It must not reconstruct the report by searching
raw JSONL or old prompts. A second repair receives the latest rejected draft paired
with its latest error; the original report and execution receipts remain the immutable
baseline for commands, failures, evidence, and user decisions.

Report sources are limited to 128 KiB each and the runner-supplied repair prompt to
256 KiB, including provider-added schema/instructions. Missing, empty, or oversized
inputs pause as `PAUSED_REPORT_REPAIR_INPUT` before another provider call, without
charging a repair attempt. Full originals remain available for inspection; report
content is never silently truncated to satisfy the limit. Old pending OpenCode
checkpoints with missing report files can materialize their terminal response locally
from intact, pinned events instead of making the model search the log.

Repairs remain read-only, use the existing attempt limit and role/model routing, and
cannot approve plans, rerun tests, replace original execution evidence, or convert
unsupported observations into passing validation.

## Milestone checkpoints and acceptance

New runs enforce a milestone checkpoint in the runner. Each task names an outcome,
affected paths, requirements, acceptance criteria and a validation plan. The Builder can
implement, test and repair within the task; every completed handoff goes to the Tester and
then the Plan Reviewer. A switch to a different milestone requires the Tester's passing evidence for
**all criteria in the current milestone**, current source/contract/task identities,
intact evidence, no blocking findings, and its required human reviews. The findings
that count are its own, a prerequisite milestone's, unscoped ones, and another
milestone's findings recorded only against criteria the two share. Another milestone's
finding recorded against criteria this milestone lacks stays open for that milestone and
for completion, since this milestone's reviewer could not close it. A writer's
self-assessment cannot authorize that switch. Later milestones may still have
`NOT_VERIFIED` results. The full flow may also be `NOT_VERIFIED` with an explanation
while a partial milestone or batch unlocks downstream work; a known flow failure
still blocks acceptance. Full-task completion still requires all contract criteria
and the complete flow to pass on the current source.

After explicit approval of a revised goal, the runner may carry an accepted serial
milestone forward **for scheduling only**. At acceptance it saves a reuse manifest
in the existing `milestone_progress`: the approved contract, exact milestone and
criterion definitions, literal owned paths, all files changed by its recorded
stages, source hashes/modes, and pinned stage snapshots and validation evidence.
The new revision must preserve its criteria text, verification methods, ownership,
`depends_on`, and all other milestone fields exactly. Global outcome, constraints,
permissions and other goal fields must also remain unchanged. Reordering milestones
or changing only an unrelated milestone/criterion can preserve earlier work.

Reuse requires unchanged source bytes/modes and intact evidence, plus reuse of
every prerequisite. Added files under an owned directory count as changes. Missing
task history, legacy acceptance without a manifest, ambiguous paths, symlinks,
submodules, batches and human-review milestones all fall back to revalidation.
This first version supports only one revision hop; it does not chain old evidence
through successive revisions or infer undeclared code dependencies.

The existing approval gate still applies. Reuse records `carried_from` provenance
and a `milestone_carry_forward` audit without rewriting the original validation's
contract hash or granting human approvals. `--status` and role handoffs expose the
compact audit through `milestone_checkpoint.carry_forward`; full manifests remain
in the run's state file. Carried prerequisites are checked again against source and
evidence before scheduling. The runner rejects redundant implementation assignments
for intact carried milestones; an explicit evidence-backed `REWORK` or detected
source/evidence drift revokes scheduling acceptance. Budgets and retry counters are
preserved. Final completion still requires fresh independent validation of every
criterion and the full integration flow on the current code and approved revision.
Deploying this code never retroactively manufactures manifests for existing runs.

Three independent reviews without any new passing criteria require an evidence-backed
`REWORK` with a changed approach or a smaller batch inside the same milestone. One
automatic replan is allowed; another three reviews without progress pause the run.
Changing files, renaming task IDs, or oscillating between previously passing checks
does not reset progress. The runner compares task fields; the Plan Reviewer remains responsible
for judging whether the changed approach is substantively useful.

Milestones have a 5,400-second active-time budget by default. This includes writer,
reviewer and report-repair attempts after the milestone is assigned (or after an
existing run adopts checkpoints). The budget is checked at stage boundaries and
prevents further writing; the Tester and Plan Reviewer can still validate finished work. It does not
replace per-stage timeouts. Use `--max-milestone-seconds N` to change the saved
budget or `0` to disable this time limit. `--status` includes `milestone_checkpoint`
with the current milestone, evidence progress, budget, replans, completed stage hours
per role, and separately estimated elapsed time for any recorded active stage.
These are elapsed stage durations, not billed model-compute hours or proof a recorded
process is still alive. Ordinary stage updates also print milestone time and progress.

The saved milestone replan limit defaults to one changed-approach replan. Set
`--max-milestone-replans 0` on a saved-run resume to allow further evidence-backed
changed-approach cycles within the same approved milestone. This does not change its
acceptance criteria, disable the three-review checkpoint, or permit advancement without
independent passing evidence.

Saved runs keep their existing review routing until explicitly upgraded. At an idle,
reconciled boundary, enable checkpoints without launching a provider:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run \
  --milestone-checkpoints --show-goal
```

For an active run, queue a safe boundary pause and the upgrade:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run \
  --request-milestone-checkpoints
```

The queued request does not edit `state.json`, signal workers, or acquire the writer
lock. The old runner observes the pause marker at its next boundary. Its next launch
reconciles the saved stage and applies the request under the writer lock. An already
running loop continues without an extra resume gate; saved pauses still require
their usual explicit resume. Use `--show-goal` to apply and inspect without launching
a provider. Preexisting operator pause
markers are preserved. Migration keeps models, sessions, the approved contract and
artifacts; final-only or combined-review overrides are archived in the user event,
and existing bounded work goes to the Tester first. Legacy briefs retain their current
task's criterion scope without rewriting or implicitly approving a new contract.
If an old stage first needs report-only repair, the upgrade remains queued. An
explicit `--resume-paused` can finish that bounded read-only repair under the old
schema, then apply the upgrade and continue to the Tester. It never repeats implementation to migrate.

## Handoffs and context

The Plan Reviewer finalizes the plan, the Builder implements one bounded task, and the Tester independently validates
actual source with evidence per criterion. Each task records its milestone, requirements,
applicable criteria and validation plan. Every role receives the complete approved
contract and current task and echoes the contract revision/hash and task ID. The Tester gets
the Builder's full implementation report, workspace/revision and actual changes. The Completion Reviewer gets
both reports, milestone status and references to prior validation evidence.
Bulky evidence indexes, archived-validation indexes, legacy checkpoints and source
file maps move into hashed run-local `context/*.json` artifacts. The handoff keeps
recent index entries and exact retrieval paths; requirements, saved answers,
feedback, permissions, current reports and unresolved findings remain inline.
Compact JSON reduces repeated formatting overhead. Per-stage context metrics record
externalized fields and bytes saved. This does not remove provider session history
or alter independent-review requirements.

Tester and Completion Reviewer handoffs also replace exact repeated text with
inline JSON pointers when that makes the packet smaller. `acceptance_criteria_ref`
points to the complete definitions in `goal_contract.body.acceptance_criteria`;
the approved contract stays intact. A replay check's `command_ref` can point to
the identical command in `validation.checks`, while its independent exit code,
error, source revision and evidence remain present. These references affect only
the prompt copy. Saved state, reports and the public status view retain their
original shape. Different criteria or commands are never combined.
Scripted providers that consume handoff JSON must follow these references;
output report schemas still require the full criterion wording and commands.

## Baseline comparison

For an explicitly authorized existing-failure exception, use the maintained
comparator instead of generating a new parser inside each task:

```sh
autocode compare-baseline baseline.log candidate.log --output comparison.json
# Optional: normalize only the two explicitly equivalent checkout prefixes.
autocode compare-baseline baseline.log candidate.log --baseline-root /baseline/repo --candidate-root /current/repo --output comparison.json
```

The comparator supports completed Vitest default-reporter text logs. It checks
failure identities and full diagnostic signatures, reconciles failed tests and
unique failed files against summaries, and rejects duplicates, incomplete logs,
unknown layouts, unhandled errors, reduced totals or newly disabled tests. Numbers,
assertion operands and source locations remain significant. When dependencies are
known to be equivalent but installed at different relative depths, the explicit
`--normalize-dependency-prefixes` option removes the relative prefix before
`node_modules/` in Vitest stack frames only; package paths, versions and locations
remain significant, and the report records this option. Exit codes are 0 for
matching failure evidence, 1 for new/changed failures, and 2 for invalid evidence.
The report preserves diagnostics and raw input hashes. A match does **not** grant a
baseline exception or prove task completion: the Tester must still verify the
approved exception, source provenance and equivalent test selection. Baseline-only
failures are listed for investigation, not automatically claimed as fixed.

## Completion Reviewer decisions

The Completion Reviewer chooses `CONTINUE`, `REWORK`, `BLOCKED` or `COMPLETE`. The first two require a
concrete next task; rework describes the smallest correction for a verified defect.
A `CONTINUE` task with `kind=validate` sends existing work directly to the Tester when it only
needs revalidation. Required behaviors and success cannot be changed by a plan.

A material ambiguity, contradiction, infeasible constraint, scope change or extra
permission need is reported to the Plan Reviewer at a safe stage boundary. The Plan Reviewer presents a
`BLOCKED` decision in `WAITING_FOR_USER` when user input is required, with
the discovery, impact, smallest decision, options and proposed delta. Answers return
to discovery; a revised goal needs new approval. Useful partial work is retained.

## Completion gate

Completion requires the current approved revision, a Tester PASS on the current artifact,
passing evidence for every required criterion, no blocking findings, intact evidence
hashes, actual human approvals where required, and the Completion Reviewer's completion request against
that same revision. New build briefs also require the Tester's explicit end-to-end flow result
and its pinned evidence on the current source revision. Completion prints each criterion
with its evidence and any agreed limitations. Unknown, skipped and untested results cannot pass. Green tests
cannot substitute for missing criterion results.

### The runner re-runs the Tester's checks

A Tester's checks are first matched against its own session: the provider's
event log or a capture receipt must show each command ran with the reported exit
code. That shows the command ran, not that it passes on the code as it is. So
before a PASS is accepted, the runner requires its own current execution of each check
(`tools/autocode_check_replay.py`):

- **Where.** From the repository root, in a scratch copy of the current source:
  the committed code plus every uncommitted change, without ignored files or
  `.autocode/`. A command that names the workspace's absolute path runs against
  the copy. A replay never writes to the workspace.
- **How.** With the credential-free environment agents get, and a 900-second limit
  per check. A command cited more than once runs once.
- **What must hold.** Every check exits 0. There are no exceptions a model can
  claim: a check that needs a server or other setup starts and stops it itself,
  for example with a script in the repository.
- **When one does not reproduce.** The Tester's report is rejected with the
  command, the runner's exit code and the end of its output. That is the ordinary
  rejected-report path: a bounded report repair may drop the check or cite
  another command that ran (each is replayed again), then the run pauses and
  `--resume-paused` asks for a fresh validation.
- **Record.** The result is saved with the validation, bound to its source
  revision, under `<run>/check-replay/`, and shown in the status view as
  `evidence.check_replay`. Each invocation gets a fresh directory, including
  retries of the same report. Later replays preserve the earlier receipt and
  logs at their original paths; a failed replay remains available after a pass.

If the controller stops after saving a successful supplementary check but before
committing validation, it may reuse that exact completed runner receipt when the
same Tester obligation resumes. Reuse requires unchanged source, command,
task and contract, purpose, execution environment, interpreter, runtime and
dependencies, plus intact original output and complete nonzero collected test
results. It does not turn a Builder receipt into independent proof. Approved
canonical commands, protected checks and a fresh Tester's obligations still
execute; unsupported test inventories are not guessed. Public evidence records
the scheduling decision and preserves the original receipt, output hashes and
execution interval. Those artifacts remain pinned by the Completion gate.

An uncertain earlier launch blocks subsequent owned verification even if its
source, task or command identity changed. Starting a new identity does not prove
that the previous process stopped. Missing, partial, stale or altered receipts
cannot authorize reuse.

This replaces trust in the Tester's own session with a run the runner owns.
It does not judge whether the checks test the right thing: that is still the
Tester's and the Completion Reviewer's job.

For a human-review criterion, inspect the displayed validation and the actual artifact,
then record your decision in chat or using the displayed artifact-specific token:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run \
  --approve-review C1 --review-token 'r3:<goal hash>@<source hash>:<validation hash>'
autocode --workspace /path/to/project --run-dir /path/to/run
```

Changing the source or validation invalidates reuse of human approval. Goal approval
does not count as approval of a subsequently produced artifact. Resuming a completed
run rechecks its source and evidence before reporting success. Stale completion pauses
for fresh Tester validation; `--status` reports `completion_current` without changing state.
Source snapshots include executable modes and changes inside initialized submodules.

Logical phases are `DISCOVERING`, `AWAITING_GOAL_APPROVAL`, `READY_TO_EXECUTE`,
`EXECUTING`, `WAITING_FOR_USER`, `COMPLETE`, and `PAUSED_OR_BLOCKED`. While an initial
interview awaits answers its phase is DISCOVERING and its status is WAITING_FOR_USER.
`--status` and `--dry-run` are read-only. Exit 0 means an action was saved or the task
completed; exit 2 means user input/pause/error (inspect status, not exit code alone).

## Timeouts and watchdog

Iteration, active-time, per-stage, reported-token and no-progress limits still pause
with a checkpoint. They never cause completion. New runs distinguish inactivity,
tool execution and total stage runtime:

| Setting | New-run default | Meaning |
| --- | --- | --- |
| `--max-idle-seconds` | `300` | Stop a provider with no new recognized activity while no tool is running. |
| `--max-tool-seconds` | `1800` | Stop a tool that exceeds its fixed deadline, including a quiet test command. Output and repeated starts do not extend this deadline. |
| `--max-stage-seconds` | `3600` | Hard cap for the entire stage, enforced even while activity continues. |
| `--max-seconds` | `43200` | Total active provider time for the run, checked at stage boundaries. |

Each flag accepts `0` to disable that limit. The iteration ceiling has no new-run
default; the two time limits above bound a run's spend instead, and Resolver may
double each once (see [Budget ownership](#budget-ownership-and-human-escalation)).
Runs created before these defaults keep the limits they saved. Saved stage limits are preserved,
including an existing five-minute cap or an explicit zero; use
`--max-stage-seconds 0` to deliberately remove a saved hard cap. Saved runs gain
the inactivity and tool defaults at their next configured launch. Running worker
processes keep the code/settings they started with until the runner is relaunched.

The watchdog reads raw Codex/OpenCode events incrementally. Completed tools and
new provider text count as activity; repeated event IDs, repeated text, tool-output
updates, malformed lines and arbitrary log chatter do not buy more time. A tracked
tool uses its own clock, so a healthy quiet command can exceed the provider's idle
limit. OpenCode versions that report tools only after completion use a bounded
descendant-process interval instead. Process identity alone cannot distinguish a
tool from a persistent provider wrapper: status labels this fallback as inferred.
Only a newly completed tool can renew that interval; output, duplicate completions
and descendant churn cannot. Without start events, concurrent unreported tools
cannot each have a precise deadline; an explicit stage cap remains available.
Explicit tool starts supersede the fallback. These observations measure liveness, not acceptance progress;
independent milestone evidence remains mandatory.

OpenCode's JSON CLI waits for a text block to finish before publishing it. AutoCode
loads a local hook for each OpenCode invocation to forward native text and reasoning
progress as bounded hashes while the block is still streaming. Existing plugins and
agent permissions are preserved. The hook does not emit periodic heartbeats or
completion evidence: a real idle gap still expires, and the stage hard cap remains
in force even during continuous streaming.

CLI updates and `--status`'s `active_stage.activity` show provider/tool activity,
elapsed and idle time, active tool time, applicable limits and an observation
timestamp. A saved observation does not prove a recorded worker is still alive.
`longest_idle_seconds` is the longest quiet period that ended with new activity or
a tool start (the open one is `idle_seconds`), so earlier silences can be compared
with the limit before changing it. An inactivity stop names its limit, whether that
is the runner default or was set explicitly, and how to change it (`autocode resume
--max-idle-seconds N`). Once the automatic recovery allowance is spent, the new
limit is saved but no provider launches until `--grant-recovery N` is also given.
Routes of a model family that reasons in long silent blocks run under a higher default:
MiMo routes get 900 seconds when the limit is the runner default (`autocode_idle_policy`).
Native deltas count when the provider exposes them; wholly silent reasoning intervals
still use the configured inactivity limit. An explicit `--max-idle-seconds` is always
used as given, and the saved setting is not rewritten.
A workflow job's stop (review, design, design check, bug investigation, question,
stuck-stage investigation) says instead that its exact retry runs under the same
limit: that retry is bound to the limits the job ran under, so a changed limit
would make it stale. Resolver never changes this limit, even when limits were
delegated to it.
The task conversation also receives durable role-based progress messages: stage
transitions, blockers with next steps, completion, and a heartbeat every 60 seconds
while the code runner observes an active stage or Builder batch. Parallel-worker
status changes publish immediately at checkpoints. Autocode UI shares the same
stage-transition notifications. Updates appear in the dashboard conversation and
terminal stderr; existing JSON stdout interfaces remain unchanged. Repeated
heartbeats replace the previous heartbeat, and the latest 100 messages are retained
across restarts. These are local task updates, not push messages into external chat
applications. Existing raw event logs remain available for complete history.
Timeout records and recovery context distinguish `idle`, `tool` and `stage` causes,
and preserve the failed task ID and its effective timeout limits. Recovery instructions
require a changed execution plan: inspect partial work, reuse valid completed checks,
split long calls, or address the diagnosed stall. The Completion Reviewer cannot assign
the identical writer task again under unchanged limits. This guard does not change
timeouts or grant permission to extend a budget.
Deadline enforcement runs independently of process-table sampling, state writes
and event-file reads. A blocked observer cannot leave a worker unsupervised.

## Open findings

The runner keeps one list of reviewer findings in `state.json` under
`findings_ledger`. Every Tester finding and every structured Plan Reviewer finding (the
optional `findings` array of a decision, with severity, finding, evidence and
blocking) gets a stable ID derived from the reviewer and the finding text, the
report that raised it, the task assigned to fix it, and the report that resolved
it. Only the reviewer who raised a finding can close it, by submitting a newer
report that no longer lists it; a finding reported again after a fix keeps its ID
and counts the repeat. With milestone checkpoints, each finding also records the
milestone criteria it was raised under, and a report closes it only if that report
reviewed all of those criteria. A validation of different work, or a report-only
repair that reformats an earlier report, leaves the finding open and marks it as
not rechecked. Findings written only as prose in `next_task.requirements`
are not tracked. Role handoffs include `open_findings` for both reviewers, and the
dashboard's task view shows the list with each finding's source, fix task and
repeat count. `unresolved_findings` still holds the Tester's latest findings unchanged.

The Plan Reviewer can keep a correction batch small by naming the ledger IDs a REWORK task
addresses in `next_task.findings`; an empty or missing list takes every open finding.
`--max-findings-per-task N` (saved as `limits.max_findings_per_task`) rejects a
REWORK task that bundles more than `N` open findings, so the correction has to be
split. The default is unlimited, and `0` disables the check on a saved run.

## Activity log

Every run keeps an append-only `activity.jsonl` next to `state.json`. It is always on
and records what AutoCode itself did, one JSON object per event:

| Event | Recorded |
| --- | --- |
| `invocation` | Program, flag names (never values), and caller: `unattended` through `autocode-unattended`, otherwise `direct`. Written by any command that saves the run. |
| `transition` | Status, phase, next stage, iteration, task ID, which of those changed, and the runner's stop reason (up to 500 characters) when the status changed. |
| `stage_started` / `stage_finished` | Stage, role, route role, iteration, engine, model, task ID, times, duration, exit code, timeout, rejection, number of changed files, token counts. |
| `findings` | Counts by status and severity. |
| `plan_revision` / `plan_approval` / `human_review` | Brief revision numbers, approval status, and criterion IDs reviewed by a person. |
| `builders` / `stages_rewound` | Parallel Builder milestone statuses; a shortened stage history. |

It holds no content: no task text, prompts, plans, answers, feedback, findings
text, file names, code, diffs or model output. Those stay in `state.json` and the
per-stage artifacts. Nothing is trimmed, unlike `progress_messages` in
`state.json`. Only saves that change the run add lines, so read-only commands such
as `--status` add none. A failure to append never fails the checkpoint.

`autocode-unattended --analyze` summarizes the log (calls by caller, stages, stops,
operator decisions) and `--out` copies it. Runs saved before this log existed have
no `activity.jsonl`; their log starts with the next save.

## Pause, recovery, and abandonment

### Usage accounting

Token usage and provider-reported cost are recorded for accounting. Cumulative
token counts do not stop execution. Missing counts remain unknown and are never
treated as zero; interrupted attempts retain their logs and partial work.

Existing runs discard retired token-budget settings at their next launch. An
operational question issued by the old token guard is withdrawn; resume the saved
run with `--resume-paused`. Plan, permission and artifact-review decisions retain
their existing approval gates.

### Stage recovery

`--pause-after-stage` and a run-local `pause-requested` file stop at a saved boundary.
For a timed-out provider stage with no terminal response, Autocode confirms its
tracked workers are gone, archives the incomplete request and preserves its partial
edits/logs, clears the uncertain role session, and continues from a fresh recovery
checkpoint. It never replays that timed-out request. Each automatic recovery consumes
the existing no-progress budget. Consecutive timeouts without an accepted stage also
pause at that configured limit for every role, including the Plan Reviewer and Tester.
A successful stage resets the consecutive-timeout counter. A separate ceiling of
three automatic recoveries covers timeouts and provider-capacity failures. External-directory
denials have their own accounting instead: a denial retry never consumes the
timeout-recovery budget, repeats of the same denied operation hold after one
workspace-only retry, and distinct denials hold at their own ceiling of three
recoveries without an accepted stage. Accepted
intermediate reports and milestone-budget extensions do not reset these ceilings.
Inspect the saved cause and adjust limits as needed; explicit `--resume-paused`
acknowledges `PAUSED_TIMEOUT_RECOVERY` and resets recovery counters while retaining
history. Setting `--no-progress-limit 0` disables the unchanged-batch limit, but
never disables the three-recovery safety ceiling.
A run held at `PAUSED_NO_PROGRESS` keeps its count. `--resume-paused
--no-progress-limit N` acknowledges that pause when N is above the count, or `0`;
an N at or below the count holds without launching the Builder. Information alone
(`--resolver-response provide_information`) never acknowledges it. Reasserting an
already saved N on resume also acknowledges it, for example after a response
consumed the request. The flag never acknowledges another cause's pause, such as
the active-time limit.
A terminal response, live worker or requested pause remains paused for inspection.
Other uncertain provider requests still require explicit reconciliation.
`--resume-paused` acknowledges operational pauses only. Saved limits persist unless you
explicitly override them. For example, resume a run paused at its iteration ceiling with
`--resume-paused --max-iterations 25` to set the total ceiling to 25. Changing a limit
does not approve a draft brief.

Provider stages track their subprocesses, including detached tool processes. On normal
exit, timeout or interruption, Autocode stops tracked workers before taking the final
snapshot and releasing the workspace lock. Saved process identities also block a new
runner while known workers remain alive. This is process supervision for trusted local
tools; it does not replace an OS sandbox or contain deliberately hidden daemons.
Rejected and recovered attempts count toward the active-time budget.

A completed response with invalid JSON or an invalid report is archived and pauses;
`--resume-paused` starts a new explicit attempt. Timeouts with no terminal response
receive the bounded automatic recovery above; other uncertain responses need inspection
first.

An execution report whose two read-only repairs are exhausted can be retried with
`--resume-paused`. If the same Validator report fails repeatedly, the failure
guard may stop recovery earlier: the cheap serialization correction contributes
to that guard but does not spend a full repair attempt. After correcting the
cause and answering any published operational request, use the exact action
shown by status: `--resume-paused --retry-report ATTEMPT_ID`. This requests fresh
Validator evidence, preserving the real repair count, failure history and limits. Autocode archives the rejected reports and starts a fresh role
session; it does not replay implementation or planning. Each retry runs one fresh
Validator attempt. If that attempt fails the same way, the guard stops it before
any repair, and status names that new attempt for the next explicit retry, never an
earlier archived one. After a source edit `--retry-report` is refused and names
`--resume-paused`, which validates the current source instead. When the source changes
while a run is paused (an operator edit), a queued report repair can no longer
run: `--resume-paused` archives it, evidence intact, and starts a fresh attempt
of the same stage, in a new provider session, on the current source. It does
not do this while a Resolver operational request is published or queued:
that request is answered or withdrawn only through its own actions. After it is
answered, an explicit resume recognizes the changed source and archives its
stale repair instead of repeating the guidance hold. A finished read-only response that was
never applied stays paused instead, and its message names the `--abandon-stage`
step. `--accept-completion` refuses a validation of another source, goal
revision or task and says so; resume to re-validate first. A transport-change pause
can be resumed with `--resume-paused --accept-transport-change` after Autocode checks
that the current OpenCode models and subscription routes are available.

To retain partial edits and set aside a stopped attempt manually:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run --status
# Copy the exact attempt_id from that status, after inspecting its events and edits:
autocode --workspace /path/to/project --run-dir /path/to/run --abandon-stage '001/terra-01'
autocode --workspace /path/to/project --run-dir /path/to/run --resume-paused
```

Abandoning a stage preserves its logs, source snapshots and partial edits, clears that
role's uncertain session and invalidates previous validation. It launches no agent.
On explicit resume, if the saved guards permit another stage, an abandoned Builder
or Tester returns to its owning stage with the retained-work recovery context.
Abandoned planning and report-repair attempts return to their owning workflow
stage, not an internal repair stage. An abandoned Completion Reviewer first routes
through the workflow's review stage to obtain fresh evidence (the Tester in
the standard workflow). Alternate workflow approvals and routing still apply.
Neither the retained partial work nor the abandoned report counts as accepted
implementation or validation; subsequent completion must pass the normal gates.
It does not approve an unapproved brief or stop an already-running worker.

### Resolver-owned operational recovery

Runner-observed stalls are operational work for Resolver, not requests for a
user to buy another retry or approve a larger planning allowance. Timeout,
capacity, and permitted workspace-path recovery retain diagnostic receipts and
the original evidence. Planning retries receive that diagnosis and a bounded
source inventory excluding `.autocode` and `.git`, rather than repeating broad
repository discovery without the failure context.

For joint planning, a Plan Reviewer call that times out or whose provider fails
returned no review, so its call is given back before the next attempt (see
[workflow](workflow.md)). A given-back call never also funds recovery: one failed
review earns a refund or a recovery call, never both. A review call admitted under
this refund policy (since 2026-09-29) is always given back when it fails, so Resolver
reserves no planning recovery calls for such calls. Repeated silence stops instead
at the ceiling of three automatic recoveries above (`PAUSED_TIMEOUT_RECOVERY`), where
`--grant-recovery N` authorizes more. The one-use reviewer route fallback needs a
recovery-funded second silent attempt, so it does not arise from such calls either.

Review calls admitted before that policy stay charged. For their proven
nonterminal timeouts, Resolver can fund at most **two separately accounted
recovery calls per planning cycle**; these grants never erase the ordinary
allowance or its usage. Explicit or unmarked saved review caps are protected.
Grants are single-use, consumed durably at admission, pinned to the
cycle/source/contract/settings/inputs/evidence, and cannot be replenished by
restarting. Failed recovery calls do not create more grants. Global time,
iteration, no-progress and recovery ceilings still apply.

This narrow read-only recovery policy works before plan approval; it cannot
approve a draft, start implementation, change requirements, grant permissions,
switch billing routes, or change models/deadlines. Requirements and Planner
timeouts remain in their own planning stage, never jump to execution review.

### Evidence-bound repair

Repeated repair incidents also retain their diagnostic history across task IDs,
provider sessions and restarts. Original reports, errors, check output and prior
attempts remain available through pinned packet artifacts. Changing incidental
temporary paths or rephrasing a hypothesis is not new evidence.

Before another paid diagnosis or repair of the same incident, the final dispatch
boundary checks whether an unresolved causal question remains or a concrete,
supported change has a discriminating check. A structured `recovery_change` may
declare that change and cite its pinned evidence; it does not grant permission,
approve scope, create another retry allowance or establish that the repair worked.
The absence of a proposal is `null`, not a request to buy report repair. A
proposal that is not a bounded change with the original discriminating check
and pinned evidence is unproven and treated like `null`: it buys no novelty and
is not routed as a known correction, so a repeated incident still holds, and a
hold that weighed the proposal names the check it failed. A Resolver's repair
plan passes an unproven proposal to the Builder only as advice, with that
reason, as an operational diagnosis does. An unproven proposal never pauses the
run as a stale handoff; changed retained evidence still pauses dispatch
admission as one (#418).

An unchanged incident can pause as `PAUSED_NO_PROGRESS` before another provider
launch. A source hash, session rotation or comment-only edit alone cannot clear
that hold. Existing healthy or uncertain workers are not restarted to create a
new attempt. A supported known correction still receives the ordinary retry or
escalation decision, assignment checks, fresh independent validation and the
Completion gate. Unsupported changes remain unknown rather than being treated as
proven progress.

An attempt that automatic recovery archived because it ended without a completed
turn (a provider timeout, capacity or startup failure, or a denied path) returned
no report, so relaunching it repeats no experiment; that recovery route's own
budget bounds the relaunch. An uncertain attempt an operator abandoned, a
truncated report or a rejected report still counts. An accepted operational
diagnosis that recommends a retry admits the Builder without a proposed source
change until one Builder attempt returns a result; an attempt that automatic
recovery archives does not use it up (#422). The Builder receives the diagnosis
and its recommendation. A change the diagnosis proposed that the incident packet
cannot attest reaches the Builder only as advice, with the reason, and is never
treated as a new experiment.

The explicit `--resume-paused --retry-failed-stage` control can authorize one
scoped retry of a recorded hold under the existing limits. It retains previous
attempts and evidence; ordinary resume is not that authorization. Unlike a
diagnosis's retry, it is spent by the attempt it admits even if that attempt
times out, and so is a `--retry-builder` grant. The grant does not carry over to
the automatic recovery that follows. A relaunch of the same stage is admitted or
held by novelty as it would be without the grant; a Builder timeout outside
final-audit-only routing goes to the Completion Reviewer instead.
Permission, product and scope decisions still require their existing bound human
controls.

### Budget ownership and human escalation

Budget limits are not all provider quota. New runs record whether each configured
bound is a runner default or an explicit CLI constraint, including abbreviated
CLI options. Limits saved without provenance remain protected; they are not
silently reinterpreted as permission to spend more.

Resolver can extend an internal default once when accepted, current progress
and known reported usage justify it. Extensions are separate durable ledger
entries; elapsed time, failed attempts and token records never reset. Each eligible
limit can at most double, with these policy ceilings:

| Default limit | Maximum after one extension |
| --- | --- |
| Iteration ceiling | 30 |
| Active-time allowance | 86,400 seconds |
| Stage timeout | 7,200 seconds |
| Milestone active time | 10,800 seconds |
| Ordinary planning review allowance | 2 to 4 calls |

These are delegated recovery-policy ceilings, not user account balances or
mandatory spending targets. An explicit cap, missing progress, repeated unchanged
failures, unknown usage, pending input or an exhausted financial guard prevents
automatic growth. Disabled limits remain disabled, and unknown usage never becomes
zero. Billing routes and provider quota cannot be changed by this mechanism.
The planning extension requires a changed accepted plan and matching review/revision
evidence; it is not granted for narrative churn or recycled failed calls.

**Resolver is the only human-request publisher.** Other roles stage internal
proposals. The serialized runner boundary first evaluates permitted recovery or
the human-only decision, then issues a bound request before status, chat or the
dashboard may present actionable controls. Draft questions remain in the sealed
draft but are not actionable merely because a model wrote them. Read-only views
verify receipts; they do not create authority. Project-free intake also uses the
Resolver request-only adapter, without fabricating a run or approved contract.

If recovery cannot proceed safely, Resolver asks for human help, retaining the
original pause cause, attempts and evidence. This is not a fake requirements
question or a completion claim. Material replies use the current `--resolver-token`;
goal and artifact approval retain their existing exact approval/review tokens.
Operational responses use `--resolver-request ID --resolver-token TOKEN
--resolver-response provide_information --resolver-message TEXT` or
`--resolver-response leave_paused`. They are information, not implicit permission
to retry, increase limits, change scope or approve work. The response returns to
Resolver, and an unchanged stopped condition is not repeatedly reissued as a
new question. Explicit administrative actions remain separately validated.

Source/contract/evidence changes, stale tokens and queued interventions invalidate
old requests. Real requirements questions, permission changes, plan approval and
declared artifact acceptance remain human decisions. External service failures
cannot be guaranteed resolvable; automatic recovery is bounded rather than infinite.

The Plan Reviewer and Tester use the read-only sandbox with the Codex engine; the Builder uses workspace-write.
Built-in OpenCode execution launches in this checkout additionally require the
qualified macOS Seatbelt shell boundary on OpenCode 1.18.33. Each launch uses a
fresh provider session, preserves effective Bash restrictions, and disables other
model tools. Nonwriter stages may write only to their fresh stage scratch area;
the Builder may also write application files, but not runner state, evidence,
configuration or runtime authority. Failed conformance or changed boundary files
pause as `PAUSED_TOOL_CONTAINMENT` before the provider is launched.

Tool networking remains denied, including ephemeral loopback HTTP tests. The
tested Seatbelt `localhost` rule also permits non-loopback addresses belonging to
the host; it is not an exact loopback-IP boundary. Loopback capability requests
are therefore rejected, not silently enabled. A test requiring such networking
needs a separately qualified execution path; neither an approved test command nor
a successful runner preflight establishes that the model's shell can execute it.

This boundary covers shell-tool subprocesses, not the authenticated OpenCode
client or its plugin hooks. Planning, report-only repair and configured custom
providers are outside this kernel-containment claim; they retain the native
permissions and snapshot checks described in [Providers](providers.md).
The runner does not pass blanket auto-approval. Contract permission text is a role instruction,
not a general-purpose OS policy compiler. The Codex sandbox/approval system remains
responsible for individual tool permissions; custom MCP/connector write permissions
should be configured accordingly. This is intended for trusted local repositories.

See also: [Workflow](workflow.md) · [Models](models.md) · [Testing](testing.md)
