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

```python
from autocode_cli.autocode_taskrun import TaskRun, TaskRunError

run = TaskRun.start(workspace, brief, options=("--engine", "codex"))
view = run.advance_until_input()
while not view["done"]:
    need = view["needs"]
    if need["kind"] == "approve_plan":
        view = run.approve_plan(need["token"])
    elif need["kind"] == "answer":
        for question in need["questions"]:
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
| Answer | `autocode --answer QUESTION_ID=TEXT [--resolver-token TOKEN]` | 0 saved, 2 rejected |
| Respond to an operational AutoResolver request | `autocode --resolver-request ID --resolver-token TOKEN --resolver-response provide_information --resolver-message TEXT` | 0 saved, 2 rejected |
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
`TaskRun.respond_operational()` uses the separate AutoResolver response command;
an operational request cannot be answered with `TaskRun.answer()`.
`TaskRun.accept_transport_change()` uses the explicit transport-change command
after a person inspects the new route and the saved run reports
`PAUSED_TRANSPORT_CHANGED`.

## Status view

Produced by `tools/autocode_run_view.py` from the saved state. Fields may be
added; existing fields keep their names and meanings, and `schema` changes if a
meaning must change.

```json
{
  "schema": 1,
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
    "acceptance": [{"id": "AC1", "criterion": "...", "status": "passed", "evidence": "...", "human_reviewed": false}],
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
proving tests; `null` otherwise). `test_cases` lists a reproduced bug's
regression tests in plain English (`id`, `given`, `when`, `then`; empty
otherwise; see [Bug fixes](workflow.md#bug-fixes)). `check_replay` is the
current validation's checks as the runner itself re-ran them in a clean copy:
`verdict`, `source_revision` and one row per command (`command`, `exit_code`,
`timed_out`, `output`); `null` before a PASS validation and for validations
that predate it (see [Execution](execution.md#the-runner-re-runs-the-validators-checks)).

`usage` is the run's tokens and cost so far: `stages` (finished), `active_stage` (the stage
running now, or null), `tokens`, `cost_usd` (`reported`, `estimated`, `complete`), `unknown_stages`
and `by_role` (see [Cost reporting](cost-reporting.md#every-task-continuously)). Unknown cost is
not zero: `complete` is false while a stage has none or is running.

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
| `answer` | answers to clarifying questions or a decision | `questions` (id, question, why, options, proposed_default), `request_kind`; for a question AutoResolver published, also `resolver_request_id`, `resolver_token` and `resolver_scope` | Answer, once per question (with `--resolver-token` when given) |
| `review` | a person to accept specific acceptance criteria | `criteria`, `token`, `question` | Approve a review, per criterion |
| `planning_budget` | more plan-review calls | `reason` | Plan feedback, or `--planning-review-call-limit N` |
| `resume` | a person to inspect a pause and resolve its cause | `reason` | Resume a pause, once resolved |
| `continue` | nothing; the run can simply proceed | | Continue |

A `resolver_scope` of `operational_exhaustion` or `blocker` means AutoResolver
stopped the run because it could not continue safely (for example, the
run time limit was reached). That question is for a person who has looked at
the run; a caller must not answer it with a proposed default.

Approving a plan or a review is a real user decision. Automated callers should
do it only when a person has delegated that decision to them, as the scenario
harness does for test runs.

## Runner checks in status

`view.runner_check` describes a local check in progress before the Validator:
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
Validator/completion review pins. The worker waits without model calls, copies only
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
