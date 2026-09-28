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
            view = run.answer(question["id"], question["proposed_default"] or question["options"][0])
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

## Commands

All commands take `--workspace WORKSPACE`; commands on an existing run add
`--run-dir RUN`.

| Step | Command | Exit status |
| --- | --- | --- |
| Start | `autocode "BRIEF" --in-place --no-chat [options]` | 0 complete, 2 stopped for input |
| Status | `autocode --status` | 0; prints JSON, the view is under `"view"` |
| Continue | `autocode --no-chat [options]` | 0 complete, 2 stopped for input |
| Resume a pause | `autocode --resume-paused --no-chat [options]` | 0 complete, 2 stopped for input |
| Answer | `autocode --answer QUESTION_ID=TEXT` | 0 saved, 2 rejected |
| Approve the plan | `autocode --approve-goal TOKEN` | 0 saved, 2 rejected |
| Approve a review | `autocode --approve-review CRITERION --review-token TOKEN` | 0 saved, 2 rejected |
| Plan feedback | `autocode --feedback TEXT` | 0 saved, 2 rejected |

Answers and approvals never launch a model; continue afterwards. A usage error
also exits 2, with a message starting `usage:` on stderr; the client checks for
it so a mistyped flag is not mistaken for a pause.

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
otherwise; see [Bug fixes](workflow.md#bug-fixes)).

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
| `answer` | answers to clarifying questions or a decision | `questions` (id, question, why, options, proposed_default), `request_kind` | Answer, once per question |
| `review` | a person to accept specific acceptance criteria | `criteria`, `token`, `question` | Approve a review, per criterion |
| `planning_budget` | more plan-review calls | `reason` | Plan feedback, or `--planning-review-call-limit N` |
| `resume` | a person to inspect a pause and resolve its cause | `reason` | Resume a pause, once resolved |
| `continue` | nothing; the run can simply proceed | | Continue |

Approving a plan or a review is a real user decision. Automated callers should
do it only when a person has delegated that decision to them, as the scenario
harness does for test runs.
