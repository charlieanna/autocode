# Execution and completion

[← Back to README](../README.md)

This page covers independent milestone Builders, milestone checkpoints and acceptance,
open findings, timeouts and recovery, and the completion gate.

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
the captured baseline. The Validator checks the combined result before the Completion Owner
and required human acceptance gates can authorize completion. Builder success or
patch integration alone does not satisfy those gates. There is no automatic merge
into `master`.

Failed workers, stale baselines, or overlapping worker changes pause the run and
retain worktrees and logs for inspection. After inspecting a failed Builder, explicitly
retry it once all workers have stopped:

```sh
autocode --run-dir RUN --resume-paused --retry-builder M2
```

Repeat `--retry-builder` to select additional failed milestones. Successful siblings
are retained rather than rerun. An explicit retry archives an uncertain stage while
preserving its edits and logs. Report-only repairs and completed-response recovery
run automatically through the existing bounded recovery mechanisms. After a revised
plan is reapproved, child workers and batches from the previous plan are safely
archived once stopped, preserving their work and logs.
Interrupted worktree setup resumes only when its source matches the saved baseline;
an incomplete or manually modified checkout pauses for inspection without overwriting files.

The active batch is saved in
`state.orchestration_batch`, with `id`, `status`, and `workers`; each worker records
`milestone_id`, `status`, `workspace`, and `run_dir`. Integrated batches move to
`state.orchestration_history`. The dashboard labels `orchestrator` as runner-owned
and displays saved batch/worker statuses and worktree/log locations. Those statuses
are checkpoint reports, not proof that worker processes are currently alive.

## Milestone checkpoints and acceptance

New runs enforce a milestone checkpoint in the runner. Each task names an outcome,
affected paths, requirements, acceptance criteria and a validation plan. The Builder can
implement, test and repair within the task; every completed handoff goes to the Validator and
then the Plan Reviewer. A switch to a different milestone requires the Validator's passing evidence for
**all criteria in the current milestone**, current source/contract/task identities,
intact evidence, no blocking findings, and its required human reviews. A writer's
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
prevents further writing; the Validator and Plan Reviewer can still validate finished work. It does not
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
and existing bounded work goes to the Validator first. Legacy briefs retain their current
task's criterion scope without rewriting or implicitly approving a new contract.
If an old stage first needs report-only repair, the upgrade remains queued. An
explicit `--resume-paused` can finish that bounded read-only repair under the old
schema, then apply the upgrade and continue to the Validator. It never repeats implementation to migrate.

## Handoffs and context

The Plan Reviewer finalizes the plan, the Builder implements one bounded task, and the Validator independently validates
actual source with evidence per criterion. Each task records its milestone, requirements,
applicable criteria and validation plan. Every role receives the complete approved
contract and current task and echoes the contract revision/hash and task ID. The Validator gets
the Builder's full implementation report, workspace/revision and actual changes. The Completion Owner gets
both reports, milestone status and references to prior validation evidence.
Bulky evidence indexes, archived-validation indexes, legacy checkpoints and source
file maps move into hashed run-local `context/*.json` artifacts. The handoff keeps
recent index entries and exact retrieval paths; requirements, saved answers,
feedback, permissions, current reports and unresolved findings remain inline.
Compact JSON reduces repeated formatting overhead. Per-stage context metrics record
externalized fields and bytes saved. This does not remove provider session history
or alter independent-review requirements.

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
baseline exception or prove task completion: the Validator must still verify the
approved exception, source provenance and equivalent test selection. Baseline-only
failures are listed for investigation, not automatically claimed as fixed.

## Completion Owner decisions

The Completion Owner chooses `CONTINUE`, `REWORK`, `BLOCKED` or `COMPLETE`. The first two require a
concrete next task; rework describes the smallest correction for a verified defect.
A `CONTINUE` task with `kind=validate` sends existing work directly to the Validator when it only
needs revalidation. Required behaviors and success cannot be changed by a plan.

A material ambiguity, contradiction, infeasible constraint, scope change or extra
permission need is reported to the Plan Reviewer at a safe stage boundary. The Plan Reviewer presents a
`BLOCKED` decision in `WAITING_FOR_USER` when user input is required, with
the discovery, impact, smallest decision, options and proposed delta. Answers return
to discovery; a revised goal needs new approval. Useful partial work is retained.

## Completion gate

Completion requires the current approved revision, a Validator PASS on the current artifact,
passing evidence for every required criterion, no blocking findings, intact evidence
hashes, actual human approvals where required, and the Completion Owner's completion request against
that same revision. New build briefs also require the Validator's explicit end-to-end flow result
and its pinned evidence on the current source revision. Completion prints each criterion
with its evidence and any agreed limitations. Unknown, skipped and untested results cannot pass. Green tests
cannot substitute for missing criterion results.

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
for fresh Validator validation; `--status` reports `completion_current` without changing state.
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
| `--max-stage-seconds` | `0` (off) | Optional hard cap for the entire stage, enforced even while activity continues. |

Each flag accepts `0` to disable that limit. Saved stage limits are preserved,
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

CLI updates and `--status`'s `active_stage.activity` show provider/tool activity,
elapsed and idle time, active tool time, applicable limits and an observation
timestamp. A saved observation does not prove a recorded worker is still alive.
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
split long calls, or address the diagnosed stall. The Completion Owner cannot assign
the identical writer task again under unchanged limits. This guard does not change
timeouts or grant permission to extend a budget.
Deadline enforcement runs independently of process-table sampling, state writes
and event-file reads. A blocked observer cannot leave a worker unsupervised.

## Open findings

The runner keeps one list of reviewer findings in `state.json` under
`findings_ledger`. Every Validator finding and every structured Plan Reviewer finding (the
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
repeat count. `unresolved_findings` still holds the Validator's latest findings unchanged.

The Plan Reviewer can keep a correction batch small by naming the ledger IDs a REWORK task
addresses in `next_task.findings`; an empty or missing list takes every open finding.
`--max-findings-per-task N` (saved as `limits.max_findings_per_task`) rejects a
REWORK task that bundles more than `N` open findings, so the correction has to be
split. The default is unlimited, and `0` disables the check on a saved run.

## Pause, recovery, and abandonment

`--pause-after-stage` and a run-local `pause-requested` file stop at a saved boundary.
For a timed-out provider stage with no terminal response, Autocode confirms its
tracked workers are gone, archives the incomplete request and preserves its partial
edits/logs, clears the uncertain role session, and continues from a fresh recovery
checkpoint. It never replays that timed-out request. Each automatic recovery consumes
the existing no-progress budget. Consecutive timeouts without an accepted stage also
pause at that configured limit for every role, including the Plan Reviewer and Validator.
A successful stage resets the consecutive-timeout counter. A separate ceiling of
three automatic recoveries covers timeouts and external-directory denials. Accepted
intermediate reports and milestone-budget extensions do not reset this ceiling.
Inspect the saved cause and adjust limits as needed; explicit `--resume-paused`
acknowledges `PAUSED_TIMEOUT_RECOVERY` and resets recovery counters while retaining
history. Setting `--no-progress-limit 0` disables the unchanged-batch limit, but
never disables the three-recovery safety ceiling.
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
`--resume-paused`. Autocode archives the rejected reports and starts a fresh role
session; it does not replay implementation or planning. A transport-change pause
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
On explicit resume, the Plan Reviewer inspects the retained work and chooses the next step, except
in final-audit-only routing where the Planner/Builder receives the recovery context directly.
It does not approve an unapproved brief or stop an already-running worker.

The Plan Reviewer and Validator use the read-only sandbox with the Codex engine; the Builder uses workspace-write.
OpenCode uses the native permissions and snapshot checks described in [Providers](providers.md).
The runner does not pass blanket auto-approval. Contract permission text is a role instruction,
not a general-purpose OS policy compiler. The Codex sandbox/approval system remains
responsible for individual tool permissions; custom MCP/connector write permissions
should be configured accordingly. This is intended for trusted local repositories.

See also: [Workflow](workflow.md) · [Models](models.md) · [Testing](testing.md)
