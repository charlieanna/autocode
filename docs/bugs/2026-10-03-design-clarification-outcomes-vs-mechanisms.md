# Design clarification asks the user to pick mechanisms

Status 2026-10-05: the clarification finding is open as
[#450](https://github.com/charlieanna/autocode/issues/450). Most recovery problems
the same trial hit are fixed; what remains is tracked in
[#448](https://github.com/charlieanna/autocode/issues/448) (last section). Found
in one live design-only trial on 2026-10-03.

## Clarification asked for mechanisms, not only outcomes

Request: "Design an AWS solution: we want a DLQ to be monitored and also certain
rows on DynamoDB to be monitored and alerted on Slack." Empty workspace,
`--workflow design --joint-planning --no-chat`, no adaptive planning. The trial
forbade implementation, deployment, AWS/Slack calls and credential access.
Requirements correctly stopped at `WAITING_FOR_USER` with three questions:

- Q1: which DLQ, and alert on any message, queue depth or message age?
- Q2: which table and rows, and alert on state, staleness, count or changes?
- Q3: which Slack destination, and webhook, Slack app or AWS Chatbot?

Q1 and Q2 ask for outcomes the user owns. Q3 mixes a user-owned destination with
an integration choice the Planner could recommend; its "no preference" option
still made the user read integration terms. `QUESTION_POLICY` in
`tools/units/autoplanner.py` classifies questions (discoverable or decision, a
`technical` category, delegable defaults; #62), but has no rule that a technical
mechanism is recommended with its trade-offs rather than asked, unless the user
has a binding constraint.

Wanted: ask for alert conditions, timing, destinations and organizational
constraints; recommend mechanisms and name the alternatives; for a design-only
job, allow a provisional plan before exact resource names, separating parameters
from true blockers; keep scale and cost, data sensitivity, repeated alerts and
delivery failure explicit before approval. Approval still covers only the exact
plan; a recommendation authorizes no access, spend or deployment.

To verify: a fake-provider scenario with an empty workspace and this request,
whose oracle requires outcome and constraint questions and rejects a question
that only asks the user to pick a mechanism, without asserting an architecture;
then a continuation where the user gives alert semantics and no integration
preference, so the Planner recommends one and the plan still waits for approval.

## Independent review worked

With illustrative answers the user authorized (SQS DLQ nonempty, items PENDING
over 30 minutes, alerts within five minutes, no repeat per episode, `#ops-alerts`,
no customer data), the Plan Reviewer blocked the first plan for real gaps:
delivery latency weakened to detection, PENDING timestamps and episode reset
unspecified, item identifiers that may be customer data, and an unprovable "no
commands were executed" criterion. The Validator then rejected the written design
because timely delivery and no repeated alert cannot both hold after an ambiguous
webhook outcome. The run stopped at `WAITING_FOR_USER` for that user decision,
which is correct.

## Recovery problems in the same trial

- Three Requirements timeout recoveries counted as unchanged implementation
  batches and stopped the run before its first Builder. Fixed by #306
  (`autocode_recovery_progress`; see
  `2026-10-03-recovery-grants-and-planning-progress.md`).
- The CLI suggested `--grant-recovery` for a pause that rejects it. Since #288 the
  published advice names the grant only where the CLI accepts it
  (`autocode_recovery_limits.advice`). `autocode_run_actions.next_command`, added
  for #301, is not called.
- On that timeout-recovery path, changing the bounded limits also invalidated
  the published request, leaving `RESOLVER_PENDING`. Not reproduced on
  2026-10-05 (#448): the change retires the request, and a plain resume asks again
  with `--grant-recovery` advice that continues
  (`2026-10-05-no-progress-bound-reassertion.md`).
- Raising `--no-progress-limit` did not retire the published no-progress request.
  Fixed: `autocode --resume-paused --no-progress-limit N` resumes in one command
  (#334), also after a `provide_information` answer (#378), and a limit raised in
  an earlier invocation is honored by a later `--resume-paused` while the request
  is still published.
- After a `provide_information` answer and a raise saved in a separate
  invocation, a bare `--resume-paused` republishes the request. That is by
  design (`17ea12b3`, `active-time-pause-acknowledgment.md`): a saved bound is
  acknowledged by reasserting it on resume. Reasserting it then crashed with
  "Role result belongs to another implementation task"; fixed by
  [#482](https://github.com/charlieanna/autocode/pull/482).
- The pause's advice (`recovery_limits.INFORM_ADVICE`) said to answer and then
  `--resume-paused`, which holds, so following it led into the sequence above.
  Fixed (#448): when the limit caused the pause, its advice names `autocode resume
  --no-progress-limit N` and the retained count, also for the trial's republished
  limit 4/count 3 request. After a consumed response, the status view's
  `needs.action` names the same command. Unchanged batches in a run that never
  recovered also no longer spend the recovery ceiling
  (`2026-10-05-no-progress-bound-reassertion.md`).
