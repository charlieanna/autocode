# Task-run interface

[← Back to README](../README.md)

How a program drives one AutoCode task run: start it, read where it stands,
answer what it asks, and let it continue. This is the only supported way for
code outside the runner (the scenario harness, and the planned architecture and
multi-component layer) to control a run. Such code must not import runner
internals such as `autocode.py` or read `state.json` directly; that file has
about 140 keys and changes without notice.

Every step is one CLI invocation, and the run's state lives on disk. A caller
that crashes can reattach to the same run directory with
`TaskRun(workspace, run_dir)`, or with `TaskRun.attach(workspace)` if it never
learned the run directory (it returns the workspace's only run, or `None`).

## Python client

`tools/autocode_taskrun.py` wraps the commands below.

This example accepts the displayed plan and chooses question defaults or the first
listed option. An interactive client should collect the person’s answer instead,
using the token from the view they saw. Operational recovery needs a separate decision.

```python
from autocode_cli.autocode_taskrun import TaskRun, TaskRunError

run = TaskRun.start(workspace, brief, options=("--engine", "codex"))
view = run.advance_until_input()
while not view["done"]:
    need = view["needs"]
    if need["kind"] == "answer" and need.get("resolver_scope") in ("blocker", "operational_exhaustion"):
        break  # Inspect recovery guidance; respond_operational() handles these requests.
    if need["kind"] == "approve_plan":
        view = run.approve_plan(need["token"])
    elif need["kind"] == "answer":
        # One question per pass: an answer consumes the request, and the questions
        # left come back in the returned view under a new resolver_token.
        question = need["questions"][0]
        # Decision questions may have no default; choose among their options.
        view = run.answer(question["id"], question["proposed_default"] or question["options"][0],
                          resolver_token=need.get("resolver_token"))
    elif need["kind"] == "continue":
        view = run.advance_until_input()
    else:
        break   # review, planning_budget or resume: a decision for the caller or a person
```

`TaskRun.start` works directly in the given workspace (`--in-place`): the caller
owns the workspace, typically a worktree it created, so start one run per
workspace at a time. `options` (engine and model flags) are passed whenever the
run starts or advances. Any rejected command raises `TaskRunError` with
AutoCode's message.

Inputs fixed when a run starts, such as `--ui-run`, belong in `start_options`
instead of `options`: `TaskRun.start(workspace, brief, options=("--engine", "codex"),
start_options=("--ui-run", str(design_run)))`. They are passed once; later advances
and reattachment use the saved design settings.

Operator-declared prerequisites can be supplied once with `--task-preflight`
in `start_options`. A failed prerequisite pauses before paid dispatch and is
visible in the additive `task_preflight` status field. See
[task-preflight.md](task-preflight.md) for phase selection, copied input checks,
receipt reuse and supported correction; readiness does not replace proof.

## Commands

All commands take `--workspace WORKSPACE`; commands on an existing run add
`--run-dir RUN`.

| Step | Command | Exit status |
| --- | --- | --- |
| Start | `autocode "BRIEF" --in-place --no-chat [options]` | 0 complete, 2 stopped for input |
| Status | `autocode --status` | 0; prints JSON, the view is under `"view"` |
| Display brief | `autocode --show-goal` | 0; prints the current brief for human review |
| Continue | `autocode --no-chat [options]` | 0 complete, 2 stopped for input |
| Resume a pause | `autocode --resume-paused --no-chat [options]` | 0 complete, 2 stopped for input |
| Grant N recoveries after resolving the cause | `autocode --resume-paused --grant-recovery N --no-chat [options]` | 0 complete, 2 stopped for input |
| Accept a changed OpenCode transport | `autocode --resume-paused --accept-transport-change --no-chat [options]` | 0 complete, 2 stopped for input |
| Answer | `autocode --answer QUESTION_ID=TEXT --resolver-token TOKEN` (`--answer` repeatable) | 0 saved, 2 rejected |
| Respond to an operational Resolver request | `autocode --resolver-request ID --resolver-token TOKEN --resolver-response provide_information --resolver-message TEXT` | 0 saved, 2 rejected |
| Approve the plan | `autocode --approve-goal TOKEN` | 0 saved, 2 rejected |
| Approve a review | `autocode --approve-review CRITERION --review-token TOKEN` | 0 saved, 2 rejected |
| Plan feedback | `autocode --feedback TEXT` | 0 saved, 2 rejected |
| Follow up a finished run | `autocode --follow-up TEXT` | 0 saved, 2 rejected |

Answers and approvals never launch a model; continue afterwards. A follow-up
is the next request in the same conversation: on a finished run it records a
new turn (`turn` in the status view) and reopens the run, which recognizes the
kind of job again from the new message and continues in the same run
directory. After a review, a follow-up that asks to act on the findings is
planned from them: the review's blocking findings are the requirements, so no
requirements questions are asked, and the plan still goes to the user for
approval. A usage error
also exits 2, with a message starting `usage:` on stderr; the client checks for
it so a mistyped flag is not mistaken for a pause. A rejection also exits 2,
starting `Input rejected:`, and startup can exit 2 before any run exists;
`TaskRun.start` raises with the tail of the CLI's output so a startup failure
is never mistaken for a pause.
`TaskRun.respond_operational()` uses the separate Resolver response command;
an operational request cannot be answered with `TaskRun.answer()`.
Each answer consumes the Resolver request whose token it carries, and the
questions still open come back under a new `resolver_token`, so a token read
before an earlier answer is rejected. Without `resolver_token`,
`TaskRun.answer()` reads the status view and uses the current request's token
after checking that the request lists the question. When a person chose the
answer, pass the token of the view they saw, so it cannot apply to a request
they never saw.
`TaskRun.accept_transport_change()` uses the explicit transport-change command
after a person inspects the new route and the saved run reports
`PAUSED_TRANSPORT_CHANGED`.

## Exact stopped-run recovery

The additive `view.recovery` projection describes a saved pause: what happened,
what is retained, recorded failure groups and specific next actions. Its `token`
binds the run, task, scope, settings, attempts and failure history. It does not
grant approval, increase limits or confirm that a human request is authorized.
Current AutoResolver request and approval gates remain authoritative. Running
and complete tasks have no recovery card. Unknown pause types retain inspection
and corrective feedback rather than offering a guessed execution command.

A caller displaying this card can append `--expected-recovery-token TOKEN` to
an existing `--resume-paused` or `--abandon-stage` command. The runner rechecks
the token under its run lock before applying settings or writing the checkpoint.
If the pause changed, it refuses: refresh and inspect the new card. Existing
CLI callers can omit the flag; all ordinary liveness, scope and approval gates
still apply. Abandoning an interrupted attempt preserves partial work and does
not resume automatically. Stop remains terminal for that conversation.

## Inspecting current verification

`autocode --run-dir RUN --status --inspect-evidence` performs a read-only source
and evidence inspection. The additive `view.verification` field lists every
planned criterion and method, its recorded result and current checked / failed /
unchecked state. A normal status projection labels saved results `not_inspected`;
a saved PASS alone never establishes current proof.

The inspection checks the report's source, task, plan and criterion identity,
then hashes its pinned project-local evidence. It rereads the source and checkpoint
to detect work changing during inspection. Missing, stale or unavailable evidence
keeps current requirements unchecked while retaining the old report. Human
acceptance remains a separately authenticated requirement. The inspection never
runs tests, changes the checkpoint, approves work or substitutes for the full
completion gate. The dashboard uses this supported inspection for selected task
detail; list polling does not hash every project checkout.

## Status view

Produced by `tools/autocode_run_view.py` from the saved state. Fields may be
added; existing fields keep their names and meanings, and `schema` changes if a
meaning must change.

```json
{
  "schema": 2,
  "status": "AWAITING_GOAL_APPROVAL",
  "done": false,
  "needs": {"kind": "approve_plan", "token": "..."},
  "phase": "AWAITING_GOAL_APPROVAL",
  "next_stage": "orchestrator",
  "iteration": 1,
  "stop_reason": null,
  "current_task": {"id": "task-1", "objective": "...", "milestone_id": "M1"},
  "workflow": "build",
  "turn": 1,
  "evidence": {
    "outcome": "...",
    "base_commit": "...",
    "acceptance": [{"id": "AC1", "criterion": "...", "status": "passed", "evidence": "...",
                    "validator_status": "PASS", "human_reviewed": false}],
    "validator_source_revision": "...",
    "findings": [{"id": "F1", "status": "resolved", "severity": "minor", "finding": "..."}],
    "regression_proof": null
  }
}
```

`evidence` is what the run agreed to deliver and what supports it, for reports
made outside the runner (such as a pull request body, docs/issues.md):
the approved contract's intended outcome, the base commit, one row per
acceptance criterion with its latest recorded outcome and evidence, the
findings ledger, and, for bug fixes, the runner's own fail-before/pass-after
regression proof (`verdict`, `fail_to_pass`, `failures`, `unverified`,
`commands`, `source_revision`, and `case_tests`: each English test case's
proving tests; `null` otherwise). Each acceptance row also carries
`validator_status`: the latest saved validation's result for that criterion
(`FAIL`, `PASS` or `NOT_VERIFIED`), or `null` when that validation has no row
for it. A failed criterion must be distinguishable from an unchecked one; the
decision report's own `status` is a separate field and a separate vocabulary
(`verified` / not). `validator_source_revision` is the source revision that
validation checked, or `null` before one. The view does not read the
workspace: after rework, `validator_status` still reports that validation
until a newer one replaces it, so compare `validator_source_revision` to the
workspace before treating the status as current. `test_cases` lists a
reproduced bug's regression tests in plain English (`id`, `given`, `when`,
`then`; empty otherwise; see [Bug fixes](workflow.md#bug-fixes)). `check_replay`
is the current validation's checks as the runner itself re-ran them in a clean
copy: `verdict`, `source_revision` and one row per command (`command`,
`exit_code`, `timed_out`, `output`); `null` before a PASS validation and for
validations that predate it (see [Execution](execution.md#the-runner-re-runs-the-validators-checks)).

`usage` is the run's tokens and cost so far: `stages` (finished), `active_stage` (the stage
running now, or null), `tokens`, `cost_usd` (`reported`, `estimated`, `complete`), `unknown_stages`
and `by_role` (see [Cost reporting](cost-reporting.md#every-task-continuously)). Unknown cost is
not zero: `complete` is false while a stage has none or is running.

`progress` is how far the run has got, counted only from saved records
(`tools/autocode_progress_view.py`; added by `autocode --status`, which supplies
the accepted milestones, the valid review receipts and the state of the recorded
workers). `line` is the one line a person reads, for example
`Builder working · 1 of 3 tasks done · 1 of 3 requirements checked, 1 failed · 1 open problem · nothing needed from you`;
its wording may change, so programs read the fields beside it:

- `headline`: `Complete`, `Waiting for you`, `Paused`, `Waiting for another run`,
  `<job> working`, `<job> finished` (its report is saved but not applied yet),
  `Next: <job>`, `<job> stopped without saving a report` (the recorded workers are
  gone; see the top-level `stale` and `next_action`; a stopped runner check is named
  `Runner check (...)`), or the status in words when no stage is known. Job names
  come from `autocode_roles`; `stage` is that name.
- `needs_you`: what a person has to do, in words: what `needs` asks for, the
  top-level `next_action` when the workers are gone, or `plan approval needed`
  while a saved plan waits to be shown for approval.
- `tasks`: one item per milestone of the current plan, proposed or approved (`number`,
  `id`, `label`, `state`, `current`), with `done` and `total`. `state` is `done`
  (accepted under the current contract, or the run is complete), `working` (the run is
  on it now), `waiting`, or `unknown` when milestone checkpoints are off (`known` is
  then false until the run completes).
- `requirements`: one item per acceptance criterion. Its state comes from the newest
  `PASS` or `FAIL` for it across the Tester results under the current contract (the
  current validation and the ones before it); `NOT_VERIFIED` and a missing row leave
  an earlier verdict standing. `checked` is a pass in the current validation with no
  different source recorded since; `checked_earlier` a pass from an earlier
  validation, or from one the source has changed since (`rebuilt_since_check`);
  `failed`; `awaiting_review` (a human-review criterion the Tester passed but no
  person has accepted yet); `reviewed` (a person's review receipt is valid for the
  current validation); `unchecked` otherwise, never `checked`. Each state has a
  count. Results under another contract, or with no contract hash under a contract,
  do not count. The view does not read the workspace.
- `problems`: `open` counts the open findings (`items`: `id`, `finding`, `source`,
  `severity`, `blocking`) plus, for a code or design review and a design-conflict
  stop, the findings that job saved instead (`reports`: `kind`, `blocking`,
  `advisory`, `report_path`).
- `for_earlier_request`: true after a `--follow-up` until the follow-up's own plan is
  drafted; the previous request's tasks, requirements and review findings are then
  left out.

No field is a percentage or an estimate from elapsed time.

`displayed_plan` is an optional structured approval projection: `revision`, `hash`,
`token`, `acceptance_criteria`, `constraints` and `permission_boundaries`. It appears
only when the last displayed token matches the current sealed contract. Missing,
modified or stale contracts do not expose it. Criterion `verification_method` and
`human_review` values are copied without interpreting or coercing model-authored
text. Automation must check these structured fields and bind the token to
`needs.token`; do not infer approval authority by parsing headings or review labels
embedded in `--show-goal` prose. This projection is not approval, execution permission
or completion proof; the existing CLI approval checks remain authoritative.

`direct_rework_assignments` records a repair assigned directly from a Completion
Owner's accepted REWORK report. Each entry binds the original and assigned tasks,
contract, source, report and evidence hashes, and the ordinary retry charged by
the runner. It is assignment provenance, not a Resolver diagnosis or completion
proof. The list is empty for runs that have never used this path.

Direct assignment is limited to the first ordinary repair of a single serial
milestone, with an independent Tester's executed failure and a complete task
within the same approved scope. Ambiguous or incomplete tasks, repeated failures,
parallel/integrated work and recovery cases retain the Resolver path. Pending
human decisions remain intact and hold the handoff before either route. Modified
sealed evidence pauses before dispatch. Every assigned repair still requires
fresh verification and independent completion acceptance.

`workflow` is the kind of job AutoCode recognized from the request, decided by
the first stage of every new run (`recognize_workflow`): one of `build`,
`bugfix`, `review`, `design` or `discuss` (see `scenarios/README.md`,
"Workflows"). It is `null` until that stage has run, and for runs that predate
it. `workflow_source` is `"model"` when the recognizer decided it and `"user"`
when `--workflow` named it; `workflow_reason` is the recognizer's one-sentence
reason. Both are `null` whenever `workflow` is.

`needs` is `null` when the run is complete. Otherwise its `kind` says what the
run is waiting for:

| `kind` | Waiting for | Extra fields | Answer with |
| --- | --- | --- | --- |
| `approve_plan` | approval of the plan AutoCode displayed | `token` | Approve the plan |
| `answer` | answers to clarifying questions or a decision | `questions` (id, question, why, options, proposed_default), `request_kind`; for a question Resolver published, also `resolver_request_id`, `resolver_token` and `resolver_scope` | Answer, with the current `resolver_token`; the questions left return under a new one |
| `review` | a person to accept specific acceptance criteria | `criteria`, `token`, `question` | Approve a review, per criterion |
| `planning_budget` | more plan-review calls | `reason` | Plan feedback, or `--planning-review-call-limit N` |
| `recover_source` | an attempt without a saved original source identity | retained retry metadata, `recovery_hint`; `action` is null | Inspect the archive and current changes before a new run |
| `retry_job` | inspection of a stopped workflow job | `job_retry_token`, `archive`, `write_diagnosis`, `recovery_hint` | Exact retry after restoring original source |
| `resume` | a person to inspect a pause and resolve its cause | `reason` | Resume a pause, once resolved |
| `continue` | nothing; the run can simply proceed | | Continue |

A `resolver_scope` of `operational_exhaustion` or `blocker` means Resolver
stopped the run because it could not continue safely (for example, the
run time limit was reached). That question is for a person who has looked at
the run; a caller must not answer it with a proposed default.

Approving a plan or a review is a real user decision. Automated callers should
do it only when a person has delegated that decision to them, as the scenario
harness does for test runs.

## Runner checks in status

`view.runner_check` describes a local check in progress before the Tester:
its `stage`, plain-language `summary`, `started_at`, `updated_at`, `command` and
`output` path. It is `null` when no such check is active. These checks do not
consume a model turn. A resumed run saves its running state before the first
test starts, so it does not continue to look paused throughout a long suite.

The CLI also returns `runner_check_workers` with `checked`, `alive` and
`live_pids`, using the controller's recorded process birth identity. An exited
controller makes the checkpoint `stale`; an inaccessible process is unknown,
not assumed dead. Inspect retained test output before restarting a stale check.
The check activity never substitutes for a passing proof or independent review.

## Dependencies between existing runs

An already authorized delivery should not become a request for a person to assemble
hashes and review logs. At an exact stopped permission request, an operator can bind
the consumer to a producer using `--bind-dependency SPEC.json` (also
`TaskRun.bind_dependency`). The specification names `producer_workspace`,
`producer_run`, `question_id`, the current `request_token`, a human-readable `label`,
an explicit `files` list, and a `destination` beneath the consumer run's `evidence/`.
This action delegates only that delivery, records the authorization, and presents
`WAITING_FOR_DEPENDENCY` / `needs.kind=dependency`, with no question to answer.
It cannot consume a plan approval or artifact acceptance request.

Run `python tools/autocode_dependencies.py --workspace CONSUMER --run-dir RUN --watch`
to supervise this binding. The worker uses `TaskRun.status`, never another run's
private state. The additive `view.delivery` exists only for runner-verified current
completion, with the source snapshot, approved contract and independently recorded
Tester/completion review pins. The worker waits without model calls, copies only
the declared regular files into an atomic evidence bundle, checks the producer again,
then calls `--receive-dependency MANIFEST` and continues the consumer. It never imports
source into the consumer itself or accepts the consumer's integration result.

Both the binding and delivered receipt survive restarts. A single worker lock prevents
duplicate transports; a crash after publishing the bundle is safely replayable.
Changed producer source, missing review pins, modified deliveries and changed consumer
source/contracts block transport or resume. Operator feedback or an edited plan cancels
the wait so the new decision can be reviewed. Keep the worker running while waiting;
if it exits with an error, reconcile the reported cause and restart it against the same
run. A failed/incomplete producer cannot release the consumer.

`TaskRun.grant_recovery(N)` explicitly grants a positive number of additional recoveries after the operator inspects saved work and fixes the cause. It preserves recovery history and uses the CLI checkpoint guards. `resume_paused()` and operational guidance do not grant an allowance.

### Failed workflow jobs

A stopped Reviewer, Architect, Analyst or Investigator publishes
`needs.kind = "retry_job"`, retaining its owning stage and archived transcript.
Inspect `needs.archive`, `needs.reason` and `needs.write_diagnosis`; then call
`run.retry_job(view["needs"]["job_retry_token"])` for one fresh attempt under the
saved source, route and limits. The CLI equivalent is
`--resume-paused --retry-failed-stage --job-retry-token TOKEN`. A plain resume
keeps the pause. Stale source/configuration, a token for a different attempt,
or unresolved restoration is rejected before any model request.

A missing or corrupt capture file does not prevent retry if the current source
exactly matches the identity saved before the attempt, including file modes and
Git HEAD. After manual restoration, the same token can be used; the old
`unrestored`/`write_diagnosis` fields remain historical evidence and the CLI
rechecks the full identity. It does not reconstruct an original identity from
the current checkout.

In status schema 2, an older attempt that never recorded its original identity
exposes `needs.kind = "recover_source"` and `action = null`, with
`recovery_hint`. Its token, archive and diagnosis remain available for inspection,
but cannot authorize an exact retry. Inspect the retained work and current changes
before starting a new run. No automatic restart or budget reset occurs.

### Saved code checkpoints

`status()["code_checkpoints"]` adds immutable source receipts for recorded,
code-changing Builder steps. Historical step metadata without source objects
is inspectable but cannot be restored. `recorded_check` is historical, never
current proof. This interface does not silently materialize snapshots for old runs.

Use `compare_checkpoint(id)` to inspect all changed paths and a bounded textual
diff (including uncommitted additions). `restore_checkpoint(id, expected_token,
request_id)` requires that exact comparison and returns a new `TaskRun` on a new
managed branch. The original branch, staged changes, run and later work remain
intact. Restoration is supported at an explicit reconciled pause of an ordinary
build run with the same approved contract. Active workers/checks, pending
interventions, human decisions, terminal Stop, progressive contracts and other
workflow kinds refuse with an explanation; no worker is interrupted.

The new continuation is paused, with the exact plan approval, saved settings,
model routes, limits and consumed allowances preserved. All milestones require
fresh proof. Implementation/validation/completion and human artifact acceptance
are not inherited as current. Earlier finding dispositions are restored; later
open findings are marked rolled back, with complete original history retained
in `restoration-history.json` and on the original run. Explicit `resume_paused()`
uses the normal Builder/Validator/completion gates. No approval or provider call
happens during restoration. Replaying the same request reconciles its owned
candidate; changed or uncertain partial candidates are preserved and refused.

The CLI equivalent is `autocode checkpoint --workspace WORKTREE --run-dir RUN
--compare CHECKPOINT`, followed by the mutually exclusive `--restore CHECKPOINT
--expected-token TOKEN --request-id ID`. The dashboard uses this supported
interface, places confirmation in chat and comparison in the Changes pane.

### Conversation task provenance

New dashboard attachments, including saved legacy conversations, carry a canonical
`autocode.conversation-task` envelope. The full validated handoff remains in the
task presented to workers: human and assistant messages, titles, structured drafts,
routes and receipts are retained. Only its human messages are requirement sources;
assistant suggestions and unapproved drafts do not become mandatory requirements.
Later human feedback and answers retain their existing source rules. A human can
adopt a suggested literal by requesting it in a message, feedback or answer.

Source projection requires the complete canonical envelope and valid handoff digest.
Extra instructions, altered envelopes and ordinary CLI tasks retain whole-task
source semantics. Existing saved flattened tasks are not rewritten or reinterpreted.
This format changes neither model routes nor the legacy/continuous handoff controls,
and conveys no approval to implement.
