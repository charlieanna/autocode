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
are retained rather than rerun. Other settings can accompany an accepted retry and apply to
the continuing run; a refused retry saves none of that invocation's settings. A parallel member
reruns on the Builder route saved in its own run, so a Builder model, provider or reasoning
effort change with `--retry-builder` is refused. A member stopped on quota, content-filter refusal or an authenticated output limit
asks `route-terra`; its answer changes that route.
The retry also refuses a difference saved earlier in the run's Builder settings, naming
both routes. Answer that member's open model question to choose its model, or restore the
run's Builder settings to the member's route separately before retrying.
A Builder that needs the stronger model its batch's
checkers run does not pause the run and cannot be retried this way: its milestone is
built serially later (see [Builder retry policy](models.md#builder-retry-policy)).
A Builder stopped by quota, a content-filter refusal or an authenticated output limit asks you to name
another model instead, and the status view does not offer it a retry (see
[A parallel Builder stopped on its model](models.md#a-parallel-builder-stopped-on-its-model)).
`--retry-builder` refuses a refused or output-limited Builder while its question is open; for a quota stop it
reruns the same model. Once that question was answered with corrective information or left
paused, `--retry-builder` is the member's one way on: it asks a refused or output-limited member's question
again, launching nothing, or reruns a quota-stopped member on its saved model.

An explicit member retry adopts the run's saved operator overrides for
`--max-tool-seconds`, `--max-stage-seconds` and `--max-milestone-seconds`, including
`0` to disable the corresponding bound. Save these flags before the retry or
pass them with it. Only selected members receive these bounds and their budget
ownership; previous attempts keep their recorded deadlines, and spent milestone
time stays charged. A settings save alone or automatic recovery does not change
child limits. Every existing pause and reconciliation gate still applies.

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

A repair of the Plan Reviewer's final plan report also receives this planning cycle's
earlier reports (the first draft, the review's concerns and the revision's responses).
Only the revision's report keeps its contract, the plan draft under review, so that
draft's full contract appears once, as in the final review's own prompt. The superseded
first draft keeps every other field but not its contract, whose full text stays at that
report's saved path. The repair's `protected_contract` is an excerpt of the current goal
contract, which at this point is the same revised draft, so the draft's required
behaviors, scope exclusions, constraints, failure cases, acceptance criteria and
permission boundaries still appear a second time there.

Report sources are limited to 128 KiB each and the runner-supplied repair prompt to
256 KiB, including provider-added schema/instructions. Missing, empty, or oversized
inputs pause as `PAUSED_REPORT_REPAIR_INPUT` before another provider call, without
charging a repair attempt. Full originals remain available for inspection; report
content is never silently truncated to satisfy the limit. Old pending OpenCode
checkpoints with missing report files can materialize their terminal response locally
from intact, pinned events instead of making the model search the log.

Saved repair inputs do not shrink, so a repair paused this way can never launch.
Resume explicitly (`autocode resume`, or `--resume-paused`) to archive it in
`report_repair_archive`, pinned evidence intact, and start a fresh attempt of the
rejected stage, as after exhausted repairs:

- A planning stage edits nothing, so it simply runs again. A fresh Plan Reviewer
  attempt is one more plan-review call: if the allowance is spent, the run stops
  there as at any review, and `--planning-review-call-limit N` followed by a resume
  continues it.
- An execution stage starts in a new provider session on the current source. If
  that stage already failed the same way three times at an unchanged source, the
  resume pauses as `PAUSED_REPEATED_FAILURE` until the cause changes.

Repairs remain read-only, use the existing attempt limit and role/model routing, and
cannot approve plans, rerun tests, replace original execution evidence, or convert
unsupported observations into passing validation.

## Milestone checkpoints and acceptance

New runs enforce a milestone checkpoint in the runner. Each task names an outcome,
affected paths, requirements, acceptance criteria and a validation plan. A validate task,
which writes nothing, may name no paths: it checks its milestone's (in a progressive run,
the active slice's), and none when its milestone owns none. A plan's first task is held to this rule while the plan is a draft,
so a plan the runner could not start is repaired before anyone is asked to approve it. The Builder can
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
every prerequisite. An otherwise reusable milestone with an open blocking finding
recorded against criteria it owns is revalidated instead: only its own review can
close that finding, and the run then reviews it before anything depending on it
starts. The milestones depending on it are still carried and count once it is
accepted again. Added files under an owned directory count as changes. Missing
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
source/evidence drift revokes scheduling acceptance. A carried milestone reviewed
again and accepted on fresh validation under the approved revision counts like any
other accepted milestone. Budgets and retry counters are preserved. Final
completion still requires fresh independent validation of every criterion and the
full integration flow on the current code and approved revision.
Deploying this code never retroactively manufactures manifests for existing runs.

Three independent reviews without any new passing criteria require an evidence-backed
`REWORK` with a changed approach or a smaller batch inside the same milestone. One
automatic replan is allowed; another three reviews without progress pause the run.
Changing files, renaming task IDs, or oscillating between previously passing checks
does not reset progress. The runner compares task fields; the Plan Reviewer remains responsible
for judging whether the changed approach is substantively useful.

The Plan Reviewer's prompt states this gate from the same check the runner applies
(`tools/autocode_milestone_replan.py`), with the milestone's current counts. While a
replan is required it reads `MILESTONE REPLAN REQUIRED`: only that `REWORK` is accepted,
a `CONTINUE` (including a `kind=validate` revalidation) is refused, and revalidation-only
work is a `REWORK` with `next_task.kind=validate`. Once the replans are spent and the
milestone stalls again it reads `MILESTONE REPLANS SPENT`: any further task on that
milestone pauses the run `PAUSED_MILESTONE_STALLED`. It also says which decisions call the
Resolver before that pause: a `REWORK`, and a `BLOCKED` whose `user_request.kind` is not
`permission` or `goal_change`. A `CONTINUE` pauses without it. In both states the general
rule to answer `CONTINUE` with a validate task is replaced for that milestone (#459). For an
integrated batch, both statements name the member milestones. A task is on the batch when its
`next_task.milestone_id` is a member, and the batch's own `batch:<digest>` id is never one.

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

A milestone budget you set with `--max-milestone-seconds N` is also checked before a climbed
Builder attempt launches: the milestone's Builder escalating to its stronger model, or the Builder
climbing a rung of its effort ladder. The attempt is projected as for the
[active-time budget](#timeouts-and-watchdog), from the last Builder attempt's recorded duration,
never more than the stage time limit. If the milestone's counted time so far plus that projection
would pass `N`, the run stops with `PAUSED_MILESTONE_BUDGET` before the attempt starts, instead of
launching it and stopping at the next writer launch with that time spent. The stop names the climb,
the projection, the milestone's time so far, the total and the budget to resume with, for example
`The Builder's stronger attempt (gpt-6-sol at xhigh effort) did not launch: projected at 600 s of
active time, the last Builder attempt's duration, it would take this milestone's active time from
650 s to 1,250 s, past the 1,000 s milestone budget you set ... resume with --max-milestone-seconds
1250 or more`. The climb stays saved, so `--resume-paused --max-milestone-seconds M` with `M` at
least that total launches that same attempt, including one a repair's recovery packet holds (see
[Evidence-bound repair](#evidence-bound-repair)); that resume also restarts the milestone's
count, as it always does. Reasserting the saved budget alone does not acknowledge this pause; any
other `M`, or `0` for no limit, does, and the attempt is then checked against `M` from the
restarted count. Only the Builder is checked against this budget, since it gates writer launches;
a Tester or Completion Reviewer climb is not. The new-run default and a budget delegated with
`--autoresolver-managed-limits` are not projected and keep only the check at the writer launch, as
do a progressive run's slice budget and a last attempt without a recorded duration. So does a
budget given while turning checkpoints on for a saved run, in the same command
(`--milestone-checkpoints --max-milestone-seconds N` on a resume) or queued with
`--request-milestone-checkpoints`: it is saved without being recorded as yours, until a later resume
sets `--max-milestone-seconds` again. When a climb would also cross the cost cap or the run's
active-time budget, that stop comes first. Once the climbed attempt has launched, the writer-launch
check governs it, and a parallel Builder leaves this check to the serial run.

A progressive run's slice budget pauses with the same `PAUSED_MILESTONE_BUDGET` status, naming the
ceiling it spent: `local_seconds`, the slice's, which `--max-milestone-seconds` sets, or
`run_seconds`, the run's, which `--max-seconds` sets. A resume does not restart the slice's count,
so that stop's advice asks for `--resume-paused --max-milestone-seconds N` with `N` above the
slice's counted time (or `0`), adding `--max-seconds M` above the run's counted time when it names
`run_seconds`.

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

<a id="the-runner-re-runs-the-validators-checks"></a>

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
- **What is refused.** A check that runs `git status` is refused before anything
  runs: it reads the working tree's state, not the product, and once the work is
  committed and merged it lists nothing
  ([bug note](bugs/2026-10-06-replay-uncommitted-git-state.md)). So a new plan
  that names `git status` is refused when its author hands it in: a criterion's
  verification method, a task's validation plan or requirements, a progressive
  slice's check, a user's `--edit-goal` or `--goal-file`. The Planner, Plan Reviewer, Completion
  Reviewer or Resolver gets its report back for repair, with every such row named.
  A contract approved before this rule is not checked again ([bug note](bugs/2026-10-07-plan-names-git-status.md)).
  A new plan with a command that `/bin/sh` cannot parse is refused at the same
  points, in the same repair. The replay runs each planned command with
  `/bin/sh -c`, so the runner asks that shell to read the command with `sh -n`,
  which runs nothing. A live step with backticks inside double quotes could
  never run ([bug note](bugs/2026-10-07-plan-check-shell-syntax.md)).
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

### Stability of named proof, and diff coverage

Named proof runs each `test:` and `guard:` test three times on the candidate in
the same scratch tree, and each `test:` test three times on the original code
(`tools/autocode_proof_stability.py`). One pass is not evidence. A test whose
runs disagree (it passed and failed) is `unstable`: it does not count as proof,
the proof fails, and the status view shows the test, its runs and the next step:
fix the test, do not retry the proof. Only the proving tests repeat, selected by
name; the suite still runs once, and each run keeps the 900-second limit. When a
runner cannot select the tests by name, or a repeat does not report a test, the
single run stands as before and the record says so (`unrepeated`).

When the project already has a coverage tool (`coverage.py`, `go test
-coverprofile`, `c8`, or Vitest's coverage package), the proving-test command
runs once more in that scratch tree to record line coverage
(`tools/autocode_diff_coverage.py`). That run cannot change the verdict.
`--status` shows it as `evidence.diff_coverage`: the changed product hunks no
proving test executed (`uncovered`), and the changed files the report never
measured (`unmeasured`, never counted as covered). Nothing is installed into the
project; a project without a tool gets a note and no extra test run.

### Mutation check of named proof

Once named proof passes, the runner mutates the change's product lines in the
same candidate scratch tree and runs the proving tests against each mutant
(`tools/autocode_mutation_check.py`; see [Named test proof](named-test-proof.md#mutation-check)).
It runs only once the proof's verdict is final and a pass: after every judged
run, the case check and the execution-context check, in the candidate tree, which
is kept for it and removed afterwards. A proof that fails a case or is
UNVERIFIED runs no mutant. It covers a Python `unittest` or `pytest` proof whose
due cases name tests. Each file gets its exact bytes and modification time back
after each mutant, and the project's workspace is never touched. The runs are
kept beside the proof (`regression/proof-NN-*/mutation-check/`) but are not
among its receipts (`checks`), so a mutant that times out or breaks an import can
never make the proof UNVERIFIED, stop a passing proof from being reused, or block
completion. One 900-second budget covers the unmutated run of the proving tests
and every mutant: the check replay's limit, not the suite timeout. A mutant may
take ten times the unmutated run's duration, at least 60 seconds, and never more
than the budget left; no mutant starts with less than 60 seconds left, and one
the budget cut short is `not_run`, not `timeout`. When the proving tests are
slow, one mutant that never returns can use the rest of the budget.
`--status` shows the result as `evidence.mutation_check`; it is report only.
The review workflow runs no named proof, so it has no mutation check yet.

### Process-recovery proof for declared lifecycle APIs

Exception tests are not process-death tests: a queue whose token counter lives in
one process, or an outbox that restores a pending event only when a callback
raises, passes its own tests and still loses work when a worker is killed. Tests
in one interpreter are not contention tests either: a claim that chooses a job in
one transaction and leases it in another passes them and still leases one job to
two workers. When a person's request declares one of two Python APIs, the runner
runs its own fixed lifecycle observation of it (#451, `tools/autocode_risk_*.py`):

- **What triggers it.** Only a person's words (the task, answers, feedback): a
  constructor with one storage argument (`LeaseQueue(path)`, `Store(db_path)`)
  followed by a lease-queue API (`enqueue`, `claim`, `ack`, `nack`, `pending`)
  with durability or old-token wording, or a transactional-outbox API
  (`create_order`, `orders`, `pending`, `publish`) with outbox or reopening
  wording. An argument named anything but `path` counts only when the API names
  the family's core methods (`enqueue`, `claim` and `ack`; `create_order` and
  `publish`) and the constructor's own sentence says what it is (queue, lease or
  job; outbox, order or event; the class name counts), so `TodoList(filename)`
  with `claim(item)` stays an ordinary task, and `Logger(log_file)` mentioned
  between `LeaseQueue(path)` and its methods neither takes them nor splits the
  declaration. A `Name(path)` call still starts a new declaration wherever it
  appears. Generic durability, concurrency, security or performance wording adds
  no observation. The runner also needs every fact the protocol checks to be
  stated (signatures, return values, the token and deadline rules, at-least-once
  delivery). If one is missing the plan cannot be approved: the refusal names each
  missing fact and asks for a restatement in feedback that says it changes that
  API, because the runner never guesses an expected value. A declaration also
  promises atomicity under contention only when its own text says so in one
  clause: the queue's claims are atomic under contention or concurrency, or
  concurrent `create_order` calls for one key commit once. Durability wording
  alone adds no race, and neither does a clause with any negation in it ("need
  not", "out of scope", even "so a job is never leased twice"): the grammar
  cannot tell which part a negation excludes, so that sentence is listed as
  unverified instead of raced.
- **Binding.** The independent Plan Reviewer selects only the declaration, its
  criteria and the original public module; the runner records it in
  `goal_contract.body.risk_acceptance` with the public modules captured before the
  Builder runs.
- **What it does.** Three fresh interpreters on one database. The queue holder is
  killed with SIGKILL after it claims; a new interpreter at the exact deadline must
  get a new token, and the dead holder's token must not ack or nack; a third
  finishes both jobs. The outbox publisher is killed inside a sink callback after
  the sink durably accepted the event; a new interpreter must still see that event
  pending under the same `event_id` and redeliver it (at least once, never claimed
  exactly once). When contention is promised, the same supervisor then starts
  three more interpreters on a second database and releases them together at two
  barriers. Each enqueues the same twelve jobs (every job must be accepted exactly
  once) and then claims four times (twelve different jobs, twelve different
  tokens), or each creates the same six orders (every order must return `True`
  exactly once and never raise) and then sees the same six orders and events. At
  most six owned workers, 30 seconds within the replay allowance, bounded output;
  every worker is reaped. The databases live in a temporary directory inside the
  replay copy (in the system temporary directory when that copy's path is too
  deep for SQLite) and are removed afterwards.
- **What a contention PASS means.** A race can be missed: a narrow window between
  a read and a write is often not hit by three interpreters. A PASS says no lost
  or duplicated write showed up, not that the code is atomic. The catalog's
  planted contention mutants widen their window on purpose, so their refusal is
  deterministic.
- **When.** With the Tester's PASS, in the same clean replay, limited to the
  current task's criteria; a whole-product claim runs every observation. Completion
  needs a current, pinned PASS (`evidence.check_replay.risk_acceptance`).
- **When it fails.** That is a product defect, not a bad report, so the Tester's
  report stands and the replay's verdict is `FAIL`. The runner opens a blocking
  finding with source `runner` (`tools/autocode_risk_findings.py`) naming the
  violated promise, what the protocol did and the pinned transcript. The
  completion gate refuses while it is open; the Completion Reviewer and Resolver
  see it in `open_findings`; a correction task assigns it to the Builder, whose
  handoff lists it in `actionable_findings`. No report closes it: the runner does,
  when its own observation passes on a source it has not failed on. Because a race
  can be missed, a PASS on a source it already failed on (the same candidate
  validated again, after `--resume-paused`, or reverted to) closes nothing. An
  approved amendment that removes the observation retracts its finding; completion
  still needs every current observation to pass. A repair that changes nothing
  stops at the ordinary no-progress limit with the finding still open.

Every other durability or concurrency sentence in the request is listed under
`evidence.unverified_risk_claims` (`tools/autocode_risk_disclosure.py`): no runner
protocol exercises it, so a PASS is never read as proof of it. The list adds no
check, so an ordinary task never acquires a crash-recovery or contention battery.

## Human review in the terminal

For a human-review criterion, inspect the displayed validation and the actual artifact.
`autocode review` shows each pending criterion, recorded checks and named tests,
available exact-output observations, changed-line coverage, and open findings:

```sh
autocode review --workspace /path/to/project --run-dir /path/to/run
autocode review --workspace /path/to/project --run-dir /path/to/run --show
autocode review --workspace /path/to/project --run-dir /path/to/run --json
```

Enter `approve`, `reject`, or `skip` (`a`, `r`, `s` also work). Rejection asks for a
nonempty note and saves it as feedback for revision. The next continuation gathers
a revised plan, which needs fresh approval before building. Skipping or reaching
the end of input leaves unsubmitted decisions unchanged. Decisions can be piped
from a script; the output is plain text, including under `NO_COLOR`.

`--show` and `--json` only display evidence. Missing evidence stays unverified;
run-level diff coverage and exact-output observations are labeled as such rather
than attributed to a criterion without a recorded association. The command submits
the displayed artifact token automatically and never starts a model. Source or
validation changes during inspection cause the decision to be refused.

Each criterion also lists the saved Builder steps of the plan milestones that
cover it (`criterion_diffs` in `--json`). Each step's diff runs from the saved
step before it (or the run's base) to its own saved checkpoint, limited to the
files it changed and read from Git objects, not the working tree. It is labeled
as attribution, not proof: it shows where to look. A file another milestone's
step also changed is flagged, since its diff may hold that work. A step after an
unavailable checkpoint is shown as unavailable rather than mixed with it. Parallel
Builders' integrated batch is one saved step that names its milestones; each
criterion shows only the files its own milestone changed in it and names the
files left out for the other milestones.

The direct approval interface remains available:

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
A snapshot rereads only the files whose device, inode, size or modification and
status-change times changed since the previous snapshot of that workspace in the same
AutoCode process; a hash is reused only for a file whose times were already 10
seconds old when it was read, and a wall-clock jump forgets every reused hash. Keep a
workspace on a file system whose clock agrees with this computer's: a network share
whose server clock runs more than 10 seconds behind could let two writes in one tick
look like one. On Windows, where a file's times can be set back and `st_ctime` is the
creation time, every snapshot rereads every file. The hashes and source revisions are
the same as before.

Those hashes live only in the memory of one AutoCode process (never in a file a stage
could rewrite), so every new process reads the whole workspace once: 2.5–3 s on a
13.6k-file project, and a chat starts a new process for each turn, each saved message
and each status refresh. With `AUTOCODE_SOURCE_HOST=1` in the environment of the
AutoCode process, `--status` and every other command ask a long-lived per-user helper
(`autocode_source_host`) for the snapshot instead. The helper runs the same snapshot
function with its own in-memory hashes and answers over a private Unix socket in a
`0700` folder under the temporary directory; the answer is identical, and nothing is
written to disk. One helper serves one code version and one Git environment, and it
exits after 30 idle minutes. A process that finds no helper starts one and takes its
own snapshot that time; a helper that fails or answers slowly is not asked again by
that process. On the 13.6k-file project a status refresh drops from 1.5–3 s to about
0.45 s. The setting is off by default while it is proven in live chats (#1707).

Logical phases are `DISCOVERING`, `AWAITING_GOAL_APPROVAL`, `READY_TO_EXECUTE`,
`EXECUTING`, `WAITING_FOR_USER`, `COMPLETE`, and `PAUSED_OR_BLOCKED`. While an initial
interview awaits answers its phase is DISCOVERING and its status is WAITING_FOR_USER.
`--status` and `--dry-run` are read-only. Exit 0 means an action was saved or the task
completed; exit 2 means user input/pause/error (inspect status, not exit code alone).

### Who approved

An approval token says what was approved; the run also records who approved it (#722).
Each of these saves the Git `user.name` and `user.email` the workspace resolves, the host
name, and the command it came through: `autocode`, the `resume` command word when it was
typed, and the option names of that invocation, never their values, so tokens stay out of it.

- A plan approval (`--approve-goal`, or `yes` in the terminal conversation) and an artifact
  acceptance (`--approve-review`, `autocode review`, or `y` at the conversation's review
  prompt), on the approval event.
- A completion acceptance (`--accept-completion`), on the event it saves when it closes a run
  whose gates the runner verified but whose completion report could not be produced.
- A goal edit (`--edit-goal body.json` or `--goal-file PATH`), on the draft revision the edit
  installed. The draft still needs its own review and approval.
- An explicit resume (`--resume-paused`, `autocode resume`, `TaskRun.resume_paused()`) that
  takes the run out of the pause holding it: the pause, the pause behind AutoResolver's
  operational request, or the pause a pause intervention interrupted. It is saved when the
  run goes on to build: once every check that could keep the run at that pause has passed,
  just before its next stage launches. A resume the pause's own rules refuse records nothing,
  such as `--resume-paused` without a higher `--max-iterations` at an iteration limit you
  set, or a resume while the run's `pause-requested` file is still in place. Nor does one
  whose next attempt is projected past a budget: a role's climb on its effort ladder, like the
  Builder's stronger attempt, is checked against the cost cap, the time limit and the
  milestone budget before the resume is saved, so a resume that pauses there again is not
  listed (#611).

On a shared machine add `--as NAME` to `--approve-goal`, `--approve-review`,
`--accept-completion`, `--edit-goal`, `--goal-file` or the resume; the name is saved beside the
Git identity as the person's own claim. With a resume it is saved only when the resume releases
a pause, as above.

None of this is verified, and none of it grants anything: Git identity is whatever the
checkout configures, and `--as` is taken at the person's word. The token checks and each
pause's own rules remain the authority; the one refusal is a project's required approver,
below. `--status` lists every approval, completion acceptance, goal edit and
resume with its provenance under `approvals` ([Status view](task-run.md#status-view)), and
the completion summary lists the same records just before its cost line. A record saved before
AutoCode kept it shows no provenance. A build, bug-fix or design run that recorded none, such as
one saved before AutoCode kept approval events, a design review or a bug that did not reproduce,
says `Approvals: none recorded` there instead of leaving the section out. A review or discuss run
never asks for an approval: its one stage completes the run without a plan. Its summary has no
such line, but still lists any record the run saved, such as a resume from a pause.

The evidence report of a completed build, bug-fix or design run lists the same records under
`approvals` in `evidence.json` and as an Approvals table in `evidence.md`, just after the
intended outcome ([Canonical completion evidence](task-run.md#canonical-completion-evidence)).
A review or discuss report has neither, not even a resume, because those workflows have no
approval step and `autocode review-pr --post` publishes a review's report on someone else's pull
request ([Issues](issues.md#reviewing-a-pull-request)). A report written before AutoCode listed
approvals has no such section either, until AutoCode saves the completed run again; reading its
status does not. Each row gives the time, what was decided (the plan token approved, the criterion
accepted, completion accepted, the draft an edit saved, or the pause a resume released), the Git
identity, host, command and any `--as` name, marked as a claim. A record saved without provenance
says it was not recorded, a workflow-policy approval says no person made it, and a run with none says
`None recorded`. When the table names an approver, the report's "Not verified" list adds that
approver identity is recorded as given; it also counts any records saved without a recorded
approver. Because `autocode issue pr` wraps that `evidence.md` exactly
([Issues](issues.md#what-it-does)), the pull request body lists the approvals too. A completed
components run's report lists its child runs' approvals, each with the child it came from, in
the same way ([Canonical completion evidence](task-run.md#canonical-completion-evidence)).

The scenario harness checks this record from outside on each task run a scenario completes,
including a components run's own task runs. It reads `approvals` from that run's final `--status --json`, and each row a
person made must name the scenario's person, the host and a command of option names only. Each
plan and review approval the harness gave a run must also be listed among that run's own rows,
and so must each budget pause the scenario's person acknowledged, as the resume that names it.
In a components scenario each such gate names the task run it was given to, so an
approval listed only in another component's run fails
([scenarios/README.md](../scenarios/README.md)).

#### Required approver

A team that wants only named people to approve can list their Git email addresses as
`require-approver` in its [project settings](cli.md#project-settings):

```toml
require-approver = ["lead@example.com", "deputy@example.com"]
```

Each entry is one plain address: a single `@` between a non-empty local part and domain, with
no spaces, angle brackets, quotes, parentheses, square brackets, commas, semicolons, colons or
backslashes. An entry such as `Lee`, `Lee <lee@example.com>`, `<lee@example.com>` or
`mailto:lee@example.com` stops a new run before it starts, naming the file and the key.

A run started with that setting refuses a plan approval (`--approve-goal`, or `yes` in the
terminal conversation), an artifact acceptance (`--approve-review`, `autocode review`, a
`TaskRun` approval, or `y` at the conversation's review prompt) or a completion acceptance
(`--accept-completion`) whose recorded Git `user.email` is not on the list, compared without
regard to case. The refusal comes before anything is approved: the command exits 2 (the
conversation stops at the same question, without asking about any further criterion) with a
message naming the required addresses and the actual Git identity, nothing it carried is saved,
and the run stays at the same stop for a listed approver. An `--as` name is a claim and never
counts. A goal edit installs a draft that still needs a listed approver's approval, a resume
releases a pause rather than approving anything, and a small bug fix approved under the
workflow policy has no person to check, so none of those is refused.

The identity checked is the output of `git -C WORKSPACE config --get user.email`, where
`WORKSPACE` is the run's saved workspace (its task worktree, or the checkout an `--in-place`
run works in), run in AutoCode's own environment: that of the `autocode` command recording
the approval (a `TaskRun` runs that command with its caller's environment, or the `env` it
was given). Anything that changes what Git resolves there changes the identity: Git's
environment variables (`GIT_CONFIG_COUNT` with `GIT_CONFIG_KEY_n` and `GIT_CONFIG_VALUE_n`,
`GIT_CONFIG_GLOBAL`, `HOME`) or a repository-local `user.email` in the workspace. The setting
guards against an approval made under the wrong configured identity, such as someone not on
the list approving from a machine set up with their own address; it does not stop someone who
deliberately sets a listed address. It is not authentication.

The run saves the list when it starts (`settings.require_approver` in `state.json`), as it
saves every project setting: a changed or deleted file never alters a run in progress, a run
started before the setting existed lets anyone approve, and no flag sets or overrides it. A
new run started from a design handoff (`autocode --ui-run HANDOFF`, with or without a typed
task) takes it like any other. Without the setting anyone may approve, as before.

Not covered:

- A replay (`autocode replay`, [Replay](replay.md)) never takes the setting, and a new run
  started with `--replay-recording` does not list it among the project settings it took. A
  replay re-applies the recorded run's own plan and review decisions through `TaskRun`, with
  no person approving, and keeps the recording's settings; checking those decisions against
  whoever runs the replay would refuse every replay a listed approver did not run.
- `autocode components`. Each child run reads `require-approver` from its own Git worktree,
  checked out at a commit, so only a committed `.autocode/config.toml` (or `pyproject.toml`)
  reaches it: an uncommitted change to the file in the project checkout does not.

## Timeouts and watchdog

Iteration, active-time, per-stage, cost ([Money cap](#money-cap)) and no-progress limits still pause
with a checkpoint. They never cause completion. New runs distinguish inactivity,
tool execution and total stage runtime:

| Setting | New-run default | Meaning |
| --- | --- | --- |
| `--max-idle-seconds` | `300` | Stop a provider with no new recognized activity while no tool is running. |
| `--max-tool-seconds` | `1800` | Stop a tool that exceeds its fixed deadline, including a quiet test command. Output and repeated starts do not extend this deadline. |
| `--max-stage-seconds` | `3600` | Hard cap for the entire stage, enforced even while activity continues. |
| `--max-seconds` | `43200` | Total active provider time for the run (its model attempts' durations added together), checked at stage boundaries and, when you set it, before an attempt on a stronger route (below). |

The bug Investigator has one more stop, a reproduction-first budget (#1902). The runner reads
the stage's own event stream, with no model, and counts completed source-reading and searching
calls (file reads, `grep`, `find`, `cat`, `git log` and similar) until the first reproduction
attempt: a command that runs code or tests, or a write in the scratch copy. When
`limits.investigation_exploration_budget` such calls (default `30`; `0` disables; saved settings
only, there is no flag yet) pass with no attempt, the stage stops early with the reason
`Stopped after N source-exploration calls without attempting a reproduction`. It is a stage
timeout (`PAUSED_PROVIDER_TIMEOUT`, then the usual `PAUSED_JOB_FAILURE` retry question), and the
exact retry's handoff lists what the stopped attempt read (`explored_paths`,
`reproduction_attempts`, `stop_kind`) as unverified leads. A command the classifier does not
recognize counts as neither kind, so a miss delays this stop and never causes one.

Each flag accepts `0` to disable that limit. The iteration ceiling has no new-run
default; the two time limits above bound a run's spend instead, and Resolver may
double each once (see [Budget ownership](#budget-ownership-and-human-escalation)).
An active-time budget you set with `--max-seconds N` is also checked before an attempt on a stronger
route, the same climbs the [money cap](#money-cap) prices: a milestone's Builder escalating to its
stronger model, and a role climbing a rung of its effort ladder. The first attempt on the climbed
route is timed from the role's last attempt's recorded duration, never more than the stage time
limit that would stop it. If the active time so far plus that projection would pass the budget, the
run stops with `PAUSED_TIME_LIMIT` before the attempt starts, and the stop names the climb, the
projection, the active time so far, the total and the budget to resume with, for example `The
Tester's climb to GPT-6 Sol XHigh (gpt-6-sol at xhigh effort) did not launch: projected at 600 s of
active time, the last Tester attempt's duration, it would take the run's active time from 650 s to
1,250 s, past the 1,000 s active-time budget you set ... resume with --max-seconds 1250 or more`.
Resuming with `--max-seconds M` of at least that total launches that same attempt, since the climb
stays saved. A smaller `M` above the active time so far is accepted and saved as your answer to the pause,
but the run pauses again before the same attempt, and the new stop names `M` as the budget. Like
the cost projection it is an estimate, not a bound: a higher effort can run longer than the attempt
it follows. The new-run default and a budget delegated with `--autoresolver-managed-limits` are not
projected, because Resolver may extend them once at the stage boundary; for them, and when the last
attempt has no recorded duration, only the stage-boundary check applies, as before. When a climb would cross both
the cost cap and this budget, the cost stop comes first. Once the climbed attempt has launched, the
stage-boundary check governs it, and a parallel Builder leaves this check to the serial run. A Builder's
climb is then checked against a milestone budget you set
([Milestone checkpoints](#milestone-checkpoints-and-acceptance)).
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
MiMo routes get 900 seconds when the limit is the runner default (`autocode_idle_policy`), and so
do the Requirements, Planner and Plan Reviewer launches at 600 seconds: those stages return one
report and emit nothing before it, and live Planner calls for a large plan took 300 to 340 seconds.
A MiMo Planner gets 900. A limit you set is used as given, the saved setting is never rewritten,
and the stop text names the floor that applied.
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

## Money cap

`--max-cost-usd N` (or `max-cost-usd = N` in the [project settings](cli.md#project-settings))
caps what a run may spend, in USD. A run has no cap unless you set one, and `0` removes it.

- **What counts.** Each finished stage counts once: the cost its provider reported (the built-in
  claude provider reports one), else its recorded tokens at the dated price table
  `tools/autocode_prices.json` (`autocode models --prices` prints it), else nothing. Stages the runner
  executes itself cost nothing. A stage on a model without a rate, or one whose provider recorded no
  token counts, is *unpriced*: it is not counted, so the spend is a floor, and the status view and
  cost line say how many stages are unpriced. Check `autocode models --prices` before relying on a cap
  with an unusual model, and use a provider that reports token counts or cost. While parallel
  Builders run, their spend is not yet in the total, which reads as a floor until their batch is
  accounted.
- **When it pauses.** The cap is checked at every stage boundary, right after the active-time limit,
  before anything launches. Once the spend reaches the cap the run stops with `PAUSED_COST_LIMIT`, in
  the *your decision* category, and AutoResolver publishes the usual operational request naming the
  spend and the cap. Stages already running finish first, so a run can end above its cap by a stage it
  could not price (below), or by one parallel batch: workers are charged when the parent accounts their batch.
- **Before an attempt the run can price.** Every attempt of a job that already ran in this run is priced
  before it launches: the job's last attempt's recorded tokens at the saved route's rate. If the spend so far
  plus that projection would pass the cap, the run stops with `PAUSED_COST_LIMIT` before the attempt starts,
  and the stop names the job, its model and effort, the tokens behind the projection and the attempt they came
  from, the spend, the total and the cap to resume with, for example `The next Builder attempt (gpt-6-sol at
  high effort) did not launch: projected at $0.42, the last Builder attempt's 61,200 input and 9,800 output
  tokens at gpt-6-sol's price, it would take the spend from $4.80 to $5.22, past the $5.00 cost cap ... resume
  with --max-cost-usd 5.22 or more`. So a run under a cap never launches a stage it can price past the cap: before
  it, the ledger could go from $1.81 to $2.96 inside one stage under a $2.00 cap, and only then pause. A job's
  first attempt has no projection, nor does an attempt whose job's last attempt recorded no token counts or whose
  model has no rate: those launch, and the stage-boundary check governs them. The projection is an estimate,
  not a bound: the next attempt can use more or fewer tokens than the one it follows.
- **Before an attempt on a stronger route.** Two kinds of climb move a struggling role to a stronger route: a
  milestone's Builder escalating to its stronger model (see [Builder retry policy](models.md#builder-retry-policy)),
  and a role climbing one rung of its effort ladder after it struggled, such as the Tester going from GPT-6 Sol
  High to XHigh (see [Models](models.md#default-models)). The first attempt on the stronger route is priced the
  same way, at the stronger route's rate, and its stop names the rung: `The Tester's climb to GPT-6 Sol Max
  (gpt-6-sol at max effort) did not launch: projected at $0.42, ... it would take the spend from $4.80 to $5.22,
  past the $5.00 cost cap ... resume with --max-cost-usd 5.22 or more`. A higher effort can use more tokens
  than the attempt it follows, and an effort climb on the same model is priced like one more attempt on that
  model. Once the stronger attempt has launched, the stage-boundary check governs it. A parallel Builder sees
  only its own spend, so both checks run in the serial run only; a parallel batch whose Builders would start on
  a climbed Builder route is not priced before it launches, and the stage-boundary check governs it. A climbed
  attempt is also checked against an active-time budget you set, after the cost check ([Timeouts and
  watchdog](#timeouts-and-watchdog)); an ordinary next attempt is not, and the stage-boundary check governs
  its time.
- **How it continues.** Only you decide: `autocode resume --max-cost-usd M` (or `--resume-paused
  --max-cost-usd M`) with `M` above the spend, or `0` for no cap. After a stop before a priced attempt, `M`
  must be at least the cap that stop names, or the run pauses again before the attempt. The route, and a
  climb, stay saved, so raising the cap lets that same attempt launch.
  Restating a saved cap that is still above the spend acknowledges it too; restating an exhausted
  cap, a plain resume, information alone or any other setting does not, and the run stays paused.
  AutoCode never raises this cap itself: it is not a runner default, `--autoresolver-managed-limits`
  does not delegate it, and AutoResolver's one-time doubling of default budgets never applies to it.
- **What it is not.** The price table holds list prices for metered API use. A subscription or
  coding-plan route is billed by its plan, so its spend here is the API-equivalent figure, not your
  bill. Per-request premiums (cache writes, long-context surcharges) are not visible in a stage's
  totals, so the spend is an estimate that can run below the provider's invoice.

A run restored from a [saved code checkpoint](task-run.md#saved-code-checkpoints) keeps counting:
like active time, the spend of the run it was restored from carries over (`cost.inherited_usd`), so
restoring never renews the cap.

`--status` prints the spend on stderr in one line, for example
`Cost: $0.42 so far ($0.40 estimated, $0.02 reported by the provider); cap $5.00.`, and the
completion summary ends with the same line. The status view's `cost` field holds the numbers
([Task run](task-run.md#status-view)); [Cost reporting](cost-reporting.md) explains the bases.

## Open findings

The runner keeps one list of reviewer findings in `state.json` under
`findings_ledger`. Every Tester finding and every structured Plan Reviewer finding (the
optional `findings` array of a decision, with severity, finding, evidence and
blocking) gets a stable ID derived from the reviewer and the finding text, the
report that raised it, the task assigned to fix it, and the report that resolved
it. Only the reviewer who raised a finding can close it, by submitting a newer
report that no longer lists it; a finding reported again after a fix keeps its ID
and counts the repeat. The runner raises findings of its own (source `runner`)
when its lifecycle observation fails, and only the runner closes them, when that
observation passes (see [Process-recovery proof](#process-recovery-proof-for-declared-lifecycle-apis)).
In a `--package` run it also raises one when the candidate changes a file outside
the package, checked before each Tester and Completion Reviewer dispatch (not
before the Code Reviewer, Designer or Design Reviewer) and by
`--accept-completion`, and closes it when a later check finds no such change
([Monorepo packages](workflow.md#monorepo-packages)).
When the regression proof owed for completion does not pass on the source about to
be judged, it raises one naming the proof's reasons, checked before each Tester and
Completion Reviewer dispatch (`tools/autocode_proof_findings.py`, #2475). It closes
the finding when the proof passes on a source the finding has not failed on. A
Completion Reviewer that asks to complete while it is open is sent back once for
that source to review again; a second request pauses `PAUSED_COMPLETION_REVIEW`,
which the Investigator takes. The completion gate no longer stops for the person
to resume an unproven change. Runs with a workflow reviewer routing keep the
completion gate as before.
With milestone checkpoints, each finding also records the
milestone criteria it was raised under, and a report closes it only if that report
reviewed all of those criteria. A validation of different work, or a report-only
repair that reformats an earlier report, leaves the finding open and marks it as
not rechecked. Findings written only as prose in `next_task.requirements`
are not tracked. Role handoffs include `open_findings` for both reviewers, and the
dashboard's task view shows the list with each finding's source, fix task and
repeat count. `unresolved_findings` still holds the Tester's latest findings unchanged.

When you approve a revision that moves an acceptance criterion to another
milestone, open findings recorded against that criterion move with it, in the
same approval. Each finding is attributed to the milestone that now owns each
criterion it cites; one whose criteria now belong to several milestones is split
into one finding per milestone (the copies carry `split_from`, the original
finding's ID). Like any finding, each part blocks its milestone, the milestones
depending on it and any other milestone listing all of the part's criteria, and
closes only by a review that covered all of those criteria, normally its
milestone's. When several milestones list a moved criterion, the part goes to one
the revision moved it to, else to one the run reviews anyway, else to the accepted
one with the fewest milestones depending on it; an accepted milestone that
receives a part is revalidated rather than carried forward, so its reviewer can
close it. A criterion the revision reworded moves the same way, and its new
owner's reviewer judges the finding against the new wording. Nothing is closed or
downgraded by the move, and resolving one part does not close another. A finding
that cites a criterion the approved contract no longer assigns to any milestone is
left exactly as it was, even when its other criteria still exist; if it is
blocking, it still blocks every milestone until a person settles it with
`--close-finding`, which closes the whole finding. Each move is recorded once in
the finding's `scope_history` and listed in the status view's
`evidence.finding_scope_moves` (`tools/autocode_finding_rescope.py`).

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
| `invocation` | Program, flag names (never values), and caller: `unattended` through `autocode unattended`, otherwise `direct`. Written by any command that saves the run. |
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

`autocode unattended --analyze` summarizes the log (calls by caller, stages, stops,
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
checkpoint. It never replays that timed-out request. An automatic recovery of a
Builder stage also counts as an unchanged implementation batch; recoveries of
planning and review stages do not, and unchanged batches never spend the recovery
ceiling below. Consecutive timeouts without an accepted stage also
pause at that configured limit for every role, including the Plan Reviewer and Tester.
A valid saved provider stage resets both the consecutive-timeout counter and the
three-recovery failure streak (#1820). Failed, rejected, interrupted and runner-only
reports do not renew the streak. Timeouts, startup failures and recoverable network
failures still have their bounded recovery paths. External-directory
denials have their own accounting instead: a denial retry never consumes the
timeout-recovery budget, repeats of the same denied operation hold after one
workspace-only retry, and distinct denials hold at their own ceiling of three
recoveries without an accepted stage. Milestone-budget extensions do not renew
recovery. A valid saved stage preserves every failed attempt, token, cost and time
record; it does not extend any run limit, change a route or approve a plan.
Three recovery failures without a valid saved stage still stop. A grant remains
available after inspecting an actually exhausted streak; ordinary resume never
grants more attempts to an unchanged failure.

Older runs may already be held by the former run-wide counter even after successful
stages. When the retained recovery/grant ledger balances and proves intervening
success, status offers ordinary Resume. The core rechecks that request under its
lock, corrects only the streak and retains a correction receipt, without a slot
grant. Inconsistent or incomplete history stays held. Every other admission gate,
including explicit time/cost limits, unchanged-failure holds and plan approval,
still applies. Setting `--no-progress-limit 0` never disables the streak bound.
A run held at `PAUSED_NO_PROGRESS` keeps its count. `--resume-paused
--no-progress-limit N` acknowledges that pause when N is above the count, or `0`;
an N at or below the count holds without launching the Builder. Information alone
(`--resolver-response provide_information`) never acknowledges it. Reasserting an
already saved N on resume also acknowledges it, for example after a response
consumed the request. The flag never acknowledges another cause's pause, such as
the active-time limit. When the limit caused the pause, its published request and
`stop_reason` name this command and the retained count. They also name the saved
limit when reasserting it is accepted, for example a limit raised in its own invocation
before a plain resume asked again. After a response consumes the request, the status
view's resume need has `action` `--resume-paused --no-progress-limit N` and the
retained count in `no_progress_batches`. Other holds that pause as
`PAUSED_NO_PROGRESS`, such as a recovery novelty hold or owned workers to reconcile,
name their own action instead.
A terminal response, live worker or requested pause remains paused for inspection.
Other uncertain provider requests still require explicit reconciliation.
If `PAUSED_VERIFICATION_UNCERTAIN` names an unfinished clean verification,
first abandon the exact model attempt displayed by status, when present, then
use `--resume-paused`. Explicit resume authenticates that verification's original
command admission and terminal cleanup and checks every recorded native process
identity. A missing, corrupt, unbound or unfinished receipt, a symlinked or
nonregular artifact, a live process or denied inspection keeps the pause.
Authenticated stopped ownership can retire the pending obligation only after
publishing a durable interrupted receipt. That receipt preserves the admitted
context, reports an unknown exit and duration, and supplies no passing evidence
or reusable result; verification must execute again. If its original completed
receipt was saved before interruption, it is preserved. When only its completed
pointer is missing, explicit recovery additionally authenticates the original
collected command result, exact admission and unchanged output before publishing
that pointer. Recovery never supplies a missing result or collected exit. Status and
ordinary dispatch cannot perform this recovery.
`--resume-paused` acknowledges operational pauses only. Saved limits persist unless you
explicitly override them. For example, resume a run paused at its iteration ceiling with
`--resume-paused --max-iterations 25` to set the total ceiling to 25. Changing a limit
does not approve a draft brief. Only a flag for the bound the pause exhausted
acknowledges it. A flag for any other limit is still saved, even when it restates the
default, but it is only a settings change. The pause stays in force, and AutoResolver
asks its operational request again under the new settings, answered or not. For
example, `--max-stage-seconds` on a run paused for exhausted recoveries changes the
stage limit and launches nothing. Enabling `--joint-planning` once the request is
answered is a settings change too: planning restarts once the pause is released. While
an unanswered operational request is shown it is refused, and the setting is not saved.

Input that arrives while an operational pause holds the run is applied by the next
invocation, without starting a provider, and the pause stays in force. A pause or
feedback intervention is applied as usual, also by a `--resume-paused` that finds it
still queued, and the next `--resume-paused` then acknowledges it and returns the run to
the earlier pause, whose own rules apply. Those include the one
re-evaluation of corrective information already sent for its request (see below), which
applies only while the run is as that information found it; applied feedback changes the
run, so the request is asked again instead. Feedback acknowledges only a pause that
offers it: an exhausted plan-review budget, or a validation-only stop whose request
names `--feedback`. `--feedback`, `--edit-goal`, `--goal-file` and `--revise-figma-manifest`
are refused at any other operational pause, including one a pause intervention interrupted:
each would put a plan to approve in place of the pause. Queued milestone checkpoints are enabled. A
run-local `pause-requested` file keeps the run at its pause until you remove it. Once the
input is applied, an unanswered operational request is asked again. If the same command
also acknowledges the pause (the exhausted bound's flag, `--grant-recovery`), the pause is
released first, and a queued pause intervention then pauses the released run.
`--retry-builder` is checked only after queued input is applied, so it does not release
the pause in that command: the input is applied under the pause, the retry is not
applied, and you send it again with `--resume-paused`.

An `--answer` or `--approve-goal` given with `--resume-paused` dispatches the next stage
in the same command once it clears a human gate (#509). At an operational pause there is
no such gate to clear: the operational request takes only `--resolver-response`, and
neither input releases the pause or uses up the re-evaluation of corrective information
described below, which then holds or continues the run exactly as it would without them.
See `docs/bugs/2026-10-06-operational-pause-authority.md`.

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

An execution report whose two read-only repairs are exhausted, or whose repair
cannot launch (`PAUSED_REPORT_REPAIR_INPUT`), can be retried with
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
new attempt. The hold's stop names the incident's check and its packet. Its
reason is followed by what is left of each budget the one scoped retry runs under:
with a cost cap, the [`Cost:` line `--status` prints](#money-cap); with a time
limit, the run's active time against it; and, with milestone checkpoints on, the
current milestone's time against its budget:
"... authorizes one attempt under existing limits. Cost: $0.53 so far; cap $5.00.
Active time: 1,250 s of the 43,200 s limit. Milestone M1: 300 s of its 5,400 s budget."
The time limit is the one the stage boundary enforces, whether you, the runner's
default or AutoResolver's extension set it. The milestone line is left out while a
progressive run's slice budget governs, or before the milestone has counted time.
The figures are read when the stop is written, never saved with the hold's
reason, so a cap, time limit or milestone budget you change at the hold, with or
without `--resume-paused`, is the one the stop then states. A budget the run does
not have is not mentioned (#611).
A supported known correction still receives the ordinary retry or escalation
decision, assignment checks, fresh independent validation and the Completion
gate. Unsupported changes remain unknown rather than being treated as proven
progress.

A recovery packet is bound to the run's settings, but not to its launch ceilings: the iteration
limit, the active-time budget, the cost cap and the milestone budget. They decide whether an
attempt may launch, not what it acts on, so resuming with a new one, as a budget stop advises,
or AutoResolver's one-time extension of a default one, keeps the packet and the repair it holds,
and buys no novelty. Other settings changes, such as another model or stage time limit, still
make the repair a stale handoff (`PAUSED_STALE_HANDOFF`). Bindings recorded before #611, on a
packet, a repair admission, a novelty hold, a retry grant or a retained diagnosis, hashed the
milestone budget too. Each still holds while the run's settings are unchanged, and stops holding
when the milestone budget changes. A binding recorded after the upgrade leaves the budget out, even
for a packet saved before it: once the Resolver's plan for that packet finishes, the repair
admission written then lets its repair launch after a milestone budget change.

The `rework-same-failure` scenario (scenarios/README.md) shows the hold on a correct repair: the
required suite keeps failing on a file the task may not change, so after the second identical
failure the run stops at `RESOLVER_PENDING` naming that check, with no paid diagnosis or third
Builder attempt. The status view's `cost` shows the spend under the cost cap, and the stop's own
text states that spend and the cap, and the run's active time and its time limit (#611).

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

A completed review of a task and source already diagnosed by the Resolver is
accepted without another diagnosis. An unanswered request keeps its question,
origin and evidence through the existing publication boundary; it does not
become a rejected report that an ordinary resume retries. Consumed corrective
information stays recorded and grants no new attempt. Replaying its retained
private proposal after elapsed-time accounting changes does not reissue the
answered question. Change the source or the
relevant verification setting when the retained diagnosis requires it; merely
raising a budget or repeating a review does not reopen diagnosis (#1108).

The packet binds the source its incident was captured on. A Builder attempt it
admitted may stop without being accepted and leave its work: a rejected attempt
keeps its in-scope edits for the retry (the runner removes or restores only what
it wrote outside its assignment), and a timed-out one keeps partial edits. That
work is not a stale handoff. The next Builder attempt is bound as at the packet's
source when each file, checked one by one, holds either that source's content or
what the packet's latest Builder attempt left, and Git HEAD has not moved. Any
other content, such as a person's new edit while the run is paused, still pauses as
`PAUSED_STALE_HANDOFF`. `--resume-paused --accept-source-edit` hands that edit to the
pending repair: its source revision becomes the current snapshot, and the packet bound
to the replaced source stays on disk but is no longer what admission compares. The
approved contract, task, budget, proof and evidence pins stay, and the command refuses
when those changed or when the Resolver wrote the source. Because the check is per file, a file restored to its
bound content, an out-of-scope edit re-applied exactly as the attempt left it, or
a mix of the two states across files is admitted. That is safe: each file holds
the packet's own source or AutoCode's own output, the assignment scope check still
covers the whole assignment after the retry, and the retry is validated afresh.
When the runner rejected such an attempt's output and the stuck-stage
Investigator recommends a retry, that retry admits the Builder until one attempt
returns a result, as an accepted operational diagnosis's does; a spent diagnosis
retry does not hide it. An investigation of a novelty hold grants nothing.

When the same incident repeats with nothing new (no bounded correction, no
changed input, no unspent grant), the hold is still raised, but the stage loop's
recovery chain settles it (`autocode_resolver_recovery.recover_no_progress`,
#2485): AutoCode records the one scoped retry grant a person used to give with
`--retry-failed-stage` itself (an `actor: autocode` entry in
`failure_retry_authorizations`, bound to the packet and the hold's binding), and
the next dispatch spends it under the existing limits; the dispatch receipt names
the grant kind `automatic`. A person is never asked to authorize it. If the same
hold comes back after that retry, nothing automatic is left: the run ends there
with a finished, failed outcome recorded as a stop receipt AutoCode applied
itself (`autocode_stop.end`), and the status view shows "AutoCode could not
finish this task" with the reason and the incident's evidence packet
(`recovery.outcome`), as it shows a person's own stop. The run launches no
further stages and offers no retry flag, packet path or token. A hold for another
reason (a diagnosis without a causal question, a known cause that needs its
deterministic correction, an unreconciled attempt), or one whose source, settings
or scope no longer match, still waits as before, and the explicit
`--resume-paused --retry-failed-stage` control can authorize one scoped retry of
such a recorded hold under the existing limits. It retains previous attempts and
evidence; ordinary resume is not that authorization. Unlike a diagnosis's retry,
it is spent by the attempt it admits even if that attempt times out, and so is a
`--retry-builder` grant. The grant does not carry over to
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
zero. A milestone budget given while turning checkpoints on for a saved run
(`--milestone-checkpoints --max-milestone-seconds N` on a resume, or queued with
`--request-milestone-checkpoints --max-milestone-seconds N`) is saved without being recorded
as yours, so Resolver may extend it once as it would the default; a later resume with
`--max-milestone-seconds` records the budget as yours. Billing routes and provider quota
cannot be changed by this mechanism.
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
new question. Explicit administrative actions remain separately validated. The
status view names the response as the request's `action`, and `--answer` to such
a request is refused with the same form. When a blocker's answer is a decision
about the approved contract (a criterion Resolver proved contradictory), it may
include a structured `recommended_revision`. The request displays the current
and proposed wording of each affected criterion. Reply with
`--resolver-response provide_information --resolver-message 'Accept recommended revision'`
and the displayed request ID and token to install that exact correction as a
draft. The draft follows the configured planning flow: joint planning sends it
to the Plan Reviewer, then waits for a fresh `--approve-goal` token. Acceptance
saves the correction and launches no provider; explicitly continue to review it.
It preserves criterion IDs, verification methods, human-review requirements,
source citations and the rest of the contract. The original request, contract,
source and diagnosis must still match; a stale acceptance must be inspected
again. The correction is retained as authenticated feedback for subsequent review.

Other replies, including a generic `yes`, remain information and keep the run
held. Without a structured recommendation, use `--edit-goal body.json`, then
review and approve the revised plan. `leave_paused` always keeps the run paused.

Corrective information is evaluated once. Accepting `provide_information` schedules
one Resolver re-evaluation, bound to the request ID and token, the response, the
pause, the source revision, the saved settings (including the saved transport
identity), the task, the contract, the recovery accounting and the request's
evidence pins. The next `autocode resume` (or `--resume-paused` without another
recovery flag) consumes it, without a provider call:

- If anything it is bound to changed, it is retired as stale, the run stays
  paused and Resolver asks a fresh request for the current run. Information
  an AutoCode release from before this re-evaluation accepted has no scheduled
  evaluation; Resolver asks a fresh request for it the same way.
- If the stop needs an operator control that information cannot supply (a spent
  automatic-recovery allowance, a reached time, iteration, milestone or
  no-progress bound, a repeated failure, a Builder retry limit, a stopped
  parallel member, a parallel member stopped on its quota or by its
  provider's content filter, a stalled milestone, spent report-only repairs, an
  unreconciled attempt), the run stays paused. A member stopped on its model
  continues only with `--resume-paused --retry-builder M`, which asks a refused
  member's model question again or reruns a quota-stopped one unchanged
  ([A parallel Builder stopped on its model](models.md#a-parallel-builder-stopped-on-its-model)).
  Spent report-only repairs and
  validation-only rounds that stalled on open blocking findings are held under
  whichever pause carries them, `PAUSED_RESOLVER` included. Where the CLI
  accepts a control at that stop, the stop reason and status name that exact
  command; after a content-filter refusal the resume it names changes the
  role's model (`--sol-model MODEL`, for example), since the same model would
  likely refuse again. Later resumes repeat the decision without evaluating
  again.
- If the cause lies outside the run (`PAUSED_PROVIDER_CAPACITY`,
  `PAUSED_RATE_LIMIT`, `PAUSED_BUDGET`, `PAUSED_CONTENT_FILTER`,
  `PAUSED_PROVIDER_UNCERTAIN`, `PAUSED_UNCERTAIN_STAGE`, `PAUSED_WORKSPACE_BUSY`,
  a Resolver stop (`PAUSED_RESOLVER_OPERATIONAL`, `PAUSED_RESOLVER`), or
  `PAUSED_PLANNING_BUDGET` with reserved recovery left) and none of the bounds
  above holds it, the request's own advice applies: the run continues through
  the normal resume path, whose admission checks (limits, permissions,
  transport, source, approval, interventions) still run before any provider
  launches. The continuation does not renew the per-incident Resolver attempts
  or the pending report-repair attempts that an explicit resume at other
  pauses renews, so a stop found there, such as a live transport change,
  Resolver's own per-incident limit or another parallel Builder member that
  needs a model only you can name, is a new stop or a new request. Any other
  stop is held.

A repeated resume never evaluates the same response twice, and the same response
sent again changes nothing (the CLI says so, and names a newer request if one is
waiting). A resume killed after writing its decision record but
before saving the run state evaluates the response again, still without a
provider call; the record the saved state names is the decision that took
effect. `leave_paused` is final.
Requirements answers, plan approval, `--retry-failed-stage` and `--grant-recovery`
keep their own paths.

Source/contract/evidence changes, stale tokens and queued interventions invalidate
old requests. Real requirements questions, permission changes, plan approval and
declared artifact acceptance remain human decisions. External service failures
cannot be guaranteed resolvable; automatic recovery is bounded rather than infinite.

## Native tool containment

The Plan Reviewer and Tester use the read-only sandbox with the Codex engine; the Builder uses workspace-write.
Every Planner has a shell (owner decision 2026-10-21) so it can compute the values its examples state:
Codex's read-only sandbox, Claude Code's `Bash`, and on OpenCode and Kilo the same contained shell the
read-only checkers get, which also cannot read the run's private state. A planning report recovery has no tools.
Built-in OpenCode launches in this checkout, planning included, additionally require a
qualified kernel shell boundary on OpenCode 1.18.33: macOS Seatbelt
(`/usr/bin/sandbox-exec`) or, on Linux, bubblewrap (`/usr/bin/bwrap`, see
[below](#linux-bubblewrap-boundary)). Each launch uses a
fresh provider session, preserves effective Bash restrictions, and disables other
model tools. Nonwriter stages may write only to their fresh stage scratch area;
the Builder may also write application files, but not runner state, evidence,
configuration or runtime authority. Failed conformance or changed boundary files
pause as `PAUSED_TOOL_CONTAINMENT` before the provider is launched. Each launch's
`tool_containment` record (on its stage record and in its conformance proof) names
the `backend` (`seatbelt` or `bubblewrap`) and `backend_version` (the macOS release,
or `bwrap --version`). The status view's `tool_containment_backend` repeats both for
the latest contained launch, with its stage and iteration (see
[Task-run interface](task-run.md#status-view)).

A new run, and every resume, first checks that this boundary can exist here: macOS
with `/usr/bin/sandbox-exec`, or Linux with `/usr/bin/bwrap` able to create its
namespaces (one `bwrap` probe; a kernel or AppArmor policy that denies unprivileged
user namespaces fails it), and `opencode --version` exactly 1.18.33. This check runs no
conformance; each launch still does. Elsewhere (another platform, Linux without a
working bubblewrap, or another OpenCode version) the run is refused before any stage,
planning included, spends anything, and the message names the reason. `autocode doctor`
makes the same check before a first task, running `opencode --version` in the workspace
as run setup does. When a new run would launch built-in OpenCode stages (no `--engine`,
or `--engine opencode`, with the built-in `opencode` provider), its `containment` line
names the backend and version that run would get (for example
`Linux bubblewrap (/usr/bin/bwrap), bubblewrap 0.9.0`), or warns with the reason the run
would be refused. With no `opencode` on `PATH` it names the sandbox and leaves the fix
to the `engine:opencode` line. `autocode doctor --json` gives the same as `containment`
(`backend`, `backend_version`, `problem`); it is `null`, and there is no line, for a
Codex, Claude Code or configured-provider run. The warning does not fail doctor. Two
ways on:

- Use the qualified setup.
- Add `--allow-uncontained-tools` to the new run or to the resume. The Builder,
  Validator, Completion Reviewer, Resolver, Investigator, workflow-job and planning stages
  (the Planner's shell keeps edits denied) then launch with OpenCode's own permission checks and the workspace snapshot
  checks only, with no kernel containment and no boundary prompt. The choice is
  saved with the run (it is not repeated on later resumes) and recorded as an
  `uncontained_tools_accepted` user event with the time and reason. Each such stage record says
  `uncontained_tools: true`, and the status view says `tool_containment:
  "uncontained_user_accepted"`. Only that flag sets it: no environment variable,
  model output or dashboard default.

If the boundary was available at setup but fails at a launch (for example OpenCode
was upgraded mid-run), the run still pauses as `PAUSED_TOOL_CONTAINMENT`; that
message names `--allow-uncontained-tools` as the explicit way to continue. Native
Codex runs, configured providers and `--dry-run` previews are not checked, except a
Codex run whose stuck-stage Investigator is pinned to an OpenCode model
(`--investigator-model provider/model`): that stage runs on built-in OpenCode, so
the run is checked and accepts the flag like an OpenCode run.

Tool networking remains denied, including ephemeral loopback HTTP tests on macOS
(on Linux a stage's own namespaced loopback works; see below). The
tested Seatbelt `localhost` rule also permits non-loopback addresses belonging to
the host; it is not an exact loopback-IP boundary. Loopback capability requests
are therefore rejected, not silently enabled. A test requiring such networking
needs a separately qualified execution path; neither an approved test command nor
a successful runner preflight establishes that the model's shell can execute it.

This boundary covers shell-tool subprocesses, not the authenticated OpenCode
client or its plugin hooks. Planning, report-only repair and configured custom
providers are outside this kernel-containment claim; they retain the native
permissions and snapshot checks described in [Providers](providers.md).
The exception is the planning stages of a native visual run (native role
isolation). They run in this boundary read-only, and their shell also cannot
read the run store, `.autocode/runs`: not this run's `state.json`, events or
receipts, and no other run's files. Only this run's `planning/` artifacts (the
predecessor plan and its delta) stay readable there; the stage gets the rest of
its run context from its handoff (#1250). Nor can it read these `.autocode`
stores, which hold other runs, conversation records or what they keep: `archive` (runs archived from
task worktrees), `arena` (`autocode arena` attempts and their runs), `builders`
(parallel Builder workspaces and their runs), `bundles` (`autocode bundle`
archives), `conversation-handoffs` (requirements conversation handoffs),
`conversation-starts` (conversation start markers), `investigation` (bug
investigation workspaces), `issues` (`autocode issue` worktrees and their runs),
`programs` (state an `autocode program` left; that command is archived on the
`archive/program` branch), `pull-reviews` (`autocode review-pr` checkouts and their runs),
`registry` (the run registry), `replay` (replay recordings), `task-flows`,
`verification` (`autocode verify` checkouts and reports) and `worktrees` (task
worktrees, and any an archived program made). The list is not every place a run can be kept: a run kept
elsewhere in the workspace (an `--out` or `--arena` directory outside these
stores, or another `.autocode` directory) is not denied. Each store that exists
when the launch is prepared is denied. The launch creates none of them: the only
thing it adds to the workspace is its own `.autocode/tool-containment-<hex>/`
control directory, as every contained launch does. New contents of a denied store
stay unreadable, but a store first made after the launch's profile was written is
not covered. Every check before the provider starts computes the denial again, up
to the held launch's seal just before the provider is released, so a store made
before that last check pauses the launch as `PAUSED_TOOL_CONTAINMENT`. Such a
store usually comes from ordinary activity, for example another AutoCode command
in the same checkout making `.autocode/issues` for the first time, so this pause
is not a sandbox or setup problem: the stop reason names the new store, and a
plain `--resume-paused` prepares the boundary again with it denied. One made
after that last check, such as once the provider has started, causes no pause and
can be read by the stage until it ends; a later planning launch denies it. A
store path that is a symlink, even a dangling one, or that is not a directory
cannot be denied, so every native planning launch in that workspace pauses as
`PAUSED_TOOL_CONTAINMENT` before its provider starts, naming the path: replace it
with a real directory, or remove it, then `--resume-paused`.
Everything else in `.autocode` stays as readable as the rest of the workspace:
the launch's own control directory and scratch, project settings,
retained design inputs, capture bundles, the shared `evidence` and `output`
areas, other launches' `tool-containment-*` control directories, and AutoCode's
other internal files.
Before the provider starts, each such launch shows through the actual native
shell that a file in its run directory and in each denied store cannot be
read, and records the denial as `private_reads` in its `tool_containment` record
and conformance proof. A boundary that no longer enforces any one of these
denials pauses as `PAUSED_TOOL_CONTAINMENT`.
On macOS such a launch also
pauses that way, naming the path, when a path in its Seatbelt profile holds a
control character such as a tab or newline: the re-check before launch accepts
no such path.
The runner does not pass blanket auto-approval. Contract permission text is a role instruction,
not a general-purpose OS policy compiler. The Codex sandbox/approval system remains
responsible for individual tool permissions; custom MCP/connector write permissions
should be configured accordingly. This is intended for trusted local repositories.

### Linux bubblewrap boundary

On Linux the same authority is built from mounts (#709). The contained shell runs in
new user, PID, IPC, UTS, cgroup and network namespaces with every capability dropped,
inside an empty read-only root that holds only:

- the system runtime (`/usr`, `/bin`, `/sbin`, `/lib*`) and the loader, alternatives,
  time zone and account files under `/etc` that ordinary tools read, read-only;
- the selected toolchains outside the system runtime (a hosted Python, Node or Go:
  the executable, the shared libraries its RPATH or RUNPATH names, read from the ELF
  file without running it, and the install directories macOS also grants), read-only,
  plus the symlinks the tools were found through;
- the workspace, read-only, or writable for the Builder; the runner's state,
  evidence, `.git`, OpenCode configuration and runtime are mounted read-only over it.
  A writable directory that holds one of these further down is mounted onto itself,
  so the Builder can write in it but cannot rename it to recreate the protected name;
- the stage's own scratch, writable. A reader's verification copy is one read-only
  mount inside it;
- for a native visual run's planning stage, an empty read-only `tmpfs` over
  `.autocode/runs`, with only that run's `planning/` mounted read-only inside it,
  and one over each existing private store listed above (Seatbelt denies reading
  the same paths).

There is no home directory, `/tmp`, `/run` or `/var`, so a write anywhere else fails
(`Read-only file system`, or `No such file or directory` where nothing is mounted)
and nothing outside these mounts can be read. A file named `.env`, `.env.*`,
`auth.json`, `id_rsa` or `id_ed25519` in the workspace when the stage starts is
covered by a device node the shell cannot open (`Permission denied`), as Seatbelt
refuses reading those names.

Two differences from Seatbelt follow from mounts:

- A protected name that does not exist when a Builder starts (usually `.opencode`,
  `opencode.json` or `opencode.jsonc`) cannot be a mount point without creating it in
  the real workspace. The launch records these as `watched_paths`; if one appears, the
  command that ran fails with exit 126 naming the path and the shell runs no further
  command for that launch. The file stays; OpenCode configuration it adds also changes
  the run's configuration identity, which pauses the next stage as
  `PAUSED_TRANSPORT_CHANGED`.
- The network namespace has its own loopback, so a test may listen on and connect to
  `127.0.0.1` inside the stage. The host's loopback services and every other address
  stay unreachable.

### Conformance in CI

CI runs the direct shell/Seatbelt controls in `UnicodeKernelTests` and
`KernelTests` from `tests/test_tool_containment.py` in a separate
`macos-tool-containment` job on macOS 15 with Python 3.11. It requires executable
`/usr/bin/sandbox-exec`, a nonempty selection from each class, and every selected
test to execute and pass without skips or expected failures. The
`linux-tool-containment` job does the same on Ubuntu with bubblewrap installed, adding
`BubblewrapKernelTests` and the selected-toolchain controls in `tests/test_toolchain.py`.
Only the macOS job also runs the native readonly check tests (four classes in
`tests/test_native_check_execution.py` and `OriginalCheckFailureTests` in
`tests/test_native_initial_callers.py`): native checks need macOS Seatbelt and the
Command Line Tools Git, so those tests skip on other hosts. These model-free checks exercise allowed source access, protected-path denials and
boundary-file integrity. They do not run actual OpenCode tool dispatch: the opt-in
`NativeToolTests` (`AUTOCODE_NATIVE_CONTAINMENT_TEST=1`, OpenCode 1.18.33 on `PATH`)
do, on either backend. The full scenario suite continues to run on Ubuntu.

The `linux-tool-containment` job also runs `ContainedFakeOpenCodeFlow` from
`tests/test_contained_fake_cli.py` without skips. It drives a fake OpenCode run through
the CLI whose Builder, Tester and Completion Reviewer launch inside the real bubblewrap
boundary. It checks that the run finishes, that `--status --json` names the backend, and
that a Builder shell write planted outside the workspace is blocked; an uncontained
control shows the same write lands. The fake client's own edits and checks run outside
the boundary, so this does not replace `NativeToolTests` either.

A pull request runs this job when it changes any Python file that flow loads, or a stage
report schema in `tools/autocode-schemas/`. `tools/ci_scope.py` works the Python files out
from the test module each time: it follows their import statements, including those inside
functions, and every script the code starts or copies by file name, such as the CLI, the
fake clients and the runtime's own workers. That is most of `tools/`, so a change to the
status view or the containment setup check runs the job without anyone adding it to a list.
It does not follow a module loaded by a name built at run time through `importlib`, such as
a subcommand from the subcommand table. Prompt changes do not select the job.

The scenario harness runs the same contained flow as a scenario, in the suite job rather
than in `linux-tool-containment`: `scenarios/test_contained_opencode.py` runs
`greenfield-greeting-cli` with the scripted model behind the fake OpenCode client, with no
`--allow-uncontained-tools`, so its Builder, Tester and Completion Reviewer launch inside
bubblewrap. It checks that the scenario's oracle passes and the run completes; that every
record of those three stages names `bubblewrap` and the version `autocode doctor` reports;
that a Builder shell write planted outside the project is blocked; and that `--status --json`
says `tool_containment: "contained"` and names the same backend. It asks
`autocode doctor --json` whether this host has the boundary. Without a working bubblewrap,
the test skips locally and fails in CI on Linux. Any other problem doctor reports, such as
the fake client no longer reporting the qualified OpenCode version, fails it everywhere. The
suite job installs bubblewrap before its scenario harness step. On a pull request that step
runs the case when the case itself, the shared harness (`scenarios/harness/`,
`scenarios/run.py`) or the fake OpenCode client changes (`SCENARIO_FIXTURES` in
`tools/run_suite.py`); master runs it every time. The container backend (`docker` or
`podman`) and a fake scenario under it are still open.

## Secret redaction

Evidence is meant to be shared: pull request bodies, exported reports, bug
bundles. A user's project can still print a token from a config file, a `.env`
the Builder reads, or a test fixture, and the check replay captures the end of
output. Redaction is applied **where text leaves the run directory** — the raw
capture in `.autocode/` is never rewritten, and evidence hashes keep covering
the raw bytes (#712).

What is replaced with `[REDACTED]`:

- GitHub, OpenAI, Anthropic and AWS token shapes (`ghp_…`, `github_pat_…`,
  `sk-…`, `sk-ant-…`, `AKIA…`)
- `Authorization: Bearer …` runs
- Generic `api_key=` / `secret:` / `token=` / `password=` assignments
- Any value the provider launch marked secret

What is **not** redacted: ordinary command output, test names, file paths and
diagnostics that do not match those shapes. A canary scenario plants a secret
in the seed and asserts it appears in no exported artifact
(`tests/test_secret_redaction.py`).

See also: [Workflow](workflow.md) · [Models](models.md) · [Testing](testing.md)

## Learned project defaults

At successful completion AutoCode can save a shareable test-command default and
project conventions in `.autocode/project.json`. Future tasks use that command
only when neither the command line nor explicit project configuration supplies
one, and never in a `--package` run. Existing saved tasks retain their settings; learned route history never
changes provider selection or budgets. See [Project memory](project-memory.md).
