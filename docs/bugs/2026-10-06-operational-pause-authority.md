# A budget flag for another bound, or a pause intervention, released an operational pause

Found while reviewing the #486 fix and reproduced on master `e042daa` with the fake
provider. **Fixed** 2026-10-06; regression in `tests/test_operational_pause_authority.py`.
The rule (#379, #486): a protected operational pause is released, and a provider
admitted, only by the operator's authority for that pause. A settings write is not
that authority, and neither is a pause intervention, queued feedback, a requested
pause, enabling joint planning or an edited goal. A review of the first fix found more
paths; they are in "Review follow-ups" below. "Composition with #509 and #581" covers
the merge with those changes.

## Reproduced behavior

**(a) A budget flag for another bound.** The run was held at
`PAUSED_RESOLVER_OPERATIONAL` with AutoResolver's `operational_exhaustion` request
published, answered or not. `autocode --resume-paused --max-stage-seconds 1200`
launched the Tester, and the run completed. `--max-seconds`, `--max-idle-seconds` and
`--max-iterations` did the same. So did restating the default `--max-stage-seconds
3600`, because an explicit flag turns a default into a user-explicit limit, which is a
settings change. Every status that publishes this request was released, except an
unanswered `PAUSED_TIME_LIMIT`: #394 had fixed that one case by asking again in
`autocode_run_setup.load_locked`.

- **Unanswered.** The settings write withdrew the request (`load_locked`), and only
  `PAUSED_TIME_LIMIT` was asked again.
- **Answered.** The settings digest is part of the request binding, so the answered
  frontier no longer held the run.
- **In both cases.** `run_actions.explicit_recovery_requested` counted *any* budget
  flag as a recovery action, so neither the republish nor the reconsideration ran.
  The generic resume then set `RUNNING`. No stage guard re-checks permission-recovery
  exhaustion.

**(b) A pause intervention.** After the request was answered, the operator ran
`autocode intervention submit --kind pause`, then a plain invocation, then
`--resume-paused`, and the Tester launched.

1. The plain invocation asked again while the pause was still queued.
   `record_operational_exhaustion` checked only the `pause-requested` file, but the
   request binding also includes the inbox. So the saved request deferred
   (`RESOLVER_PENDING`), and its receipt named an inbox that applying the pause then
   emptied. It could never be published.
2. The pause itself replaced the status with `PAUSED_INTERVENTION`.
3. `--resume-paused` treated that like any pause and set `RUNNING`. The Builder
   loop's save before launch moved the run back to `RESOLVER_PENDING`, but the stage
   launched anyway.

Where the cause was set realistically at the stage where it pauses, a stage guard
caught some statuses again (time, iteration, timeout recovery, and before the Builder
no-progress and the milestone budgets). Most statuses have no such guard.

## Fix

- `autocode_pause_authority` (new, domain layer) names the pause holding a run. It
  reads that from the run's live or queued operational request, the request its
  answer consumed, or the saved status. It also says whether the explicit budget
  flags change that pause's bound (`held_origin`, `changes_held_bound`; the tables
  moved from `run_actions`, where `_explicit_budget_change` was never called).
- `run_actions.explicit_recovery_requested(args, state)` counts a budget flag only
  when it changes the held pause's bound. Any other budget flag is a settings write.
  The republish, the reconsideration and the answered-frontier hold therefore apply.
- `load_locked` asks again for every pause, not only the active-time limit, after a
  settings write withdraws the request without changing its bound. It uses the
  request's own cause.
- `record_operational_exhaustion` refuses while any interruption is pending
  (`resolver_human.pending_interruptions`, the same set the binding uses). The input
  is applied first, and the request is asked afterwards.
- A pause intervention that lands on a run already held at a pause records that
  pause in `pause_intent.held_pause` (`autocode_stop.boundary_effects`). On
  `--resume-paused`, `autocode_stop.resume_interrupted` acknowledges the
  intervention and returns the run to that pause. The pause's own resume rules then
  apply in the same invocation.
- `resolver_human.release_stranded_operational` handles a request an earlier version
  stranded this way. The request goes back to its pause and is asked again with a
  fresh receipt.
- Defense in depth: the build loop's dispatch launches nothing when its own save took
  the run off `RUNNING` (`autocode_build_loop.dispatch_code_stage`).

Acknowledgment paths are unchanged: raising the exhausted bound (including a
consumed request's iteration and milestone bounds), `REASSERTABLE_BOUNDS`,
`--grant-recovery`, `--retry-failed-stage`, `--abandon-stage` and route answers. The
tests cover each budget pause unanswered, answered and after a pause intervention.

## Review follow-ups

Each of these released the pause, on master and after the first fix, and is now held
(same test module, every pause status unless noted). Each also reproduces on master
`5071412`, after #581. That change re-evaluates corrective information once, at the next
explicit resume after a `provide_information` answer, and a stop caused outside the run
(`operational_information.INFORMATION_CAUSES`) then continues. Since that re-evaluation
is the pause's own rule, the reproductions use an unanswered request or a pause it holds,
such as an answered `PAUSED_TIMEOUT_RECOVERY`. The tests compare each input with it: an
input admits what the answered request alone admits (a pause intervention leaves the run
as the information found it), or nothing (the input changed what the information was
bound to, so the request is asked again).

- **A second pause intervention.** Applied while the run was already at
  `PAUSED_INTERVENTION` from the first, it replaced `pause_intent` and lost
  `held_pause`, so `--resume-paused` took the generic resume. A later intervention now
  keeps the pause the earlier one interrupted (`autocode_stop.interrupted_pause`).
- **Queued feedback.** It replaced the status with `PAUSED_INTERVENTION`, and the next
  resume restarted Requirements discovery. With an unanswered request it was held
  until a settings write withdrew the request. Feedback that lands on an operational
  pause now records `held_pause` like a pause does. The exception is a pause that
  offers feedback (`pause_authority.feedback_acknowledges`): an exhausted plan-review
  budget, and the validation-only stop, whose request names `--feedback`
  (`tests.test_rework_cli`). This replaces the "Feedback after an answered request"
  policy note the first fix left open.
- **Pending input with no request asked.** `record_operational_exhaustion` refuses
  while input is pending, and both callers then fell through to the generic resume.
  The input was a queued intervention, the `pause-requested` file, or
  `--request-milestone-checkpoints`, which writes that file. Now
  `run_actions.hold_for_input` applies the input under the pause and launches
  nothing: interventions as above, and milestone checkpoints are enabled. It then
  asks the request again. An operator's own `pause-requested` file holds the run at
  its pause until removed. A request that input queued after its publication left
  stale is withdrawn first. Before, it could not be answered, and the input was never
  applied. At an unanswered `PAUSED_TIMEOUT_RECOVERY` with a pause queued, a plain
  invocation left no request to answer, and `--grant-recovery` was refused ("requires
  a run paused for exhausted timeout recovery"). Only an unrelated settings write
  moved the run. Now the plain invocation applies the pause, `--resume-paused` asks
  the request again, and `--grant-recovery` releases it.
- **`--joint-planning`.** `load_locked` set `RUNNING` and `requirements_gather`
  directly on an answered pause, so a plain invocation launched Requirements. That
  worked even through `autocode-unattended`, which refuses `--resume-paused` as an
  operator decision. The setting is still saved, and planning restarts once the pause
  is released. With an unanswered request, `configure` already refused it.
- **`--feedback TEXT` on an unanswered request.** `goals.feedback` accepted
  `WAITING_FOR_USER` as a conversation checkpoint and set `RUNNING`. It is now refused
  at an operational pause that does not offer feedback
  (`pause_authority.feedback_refusal`), as it already was once the request had been
  answered.

One fail-closed regression of the first fix is corrected. An acknowledgement, such as
the exhausted bound's flag or `--grant-recovery`, could arrive in the same command
that applied a queued pause intervention. The intervention then recorded the
acknowledged pause as held, and the acknowledgement was lost. Restating the saved
iteration or milestone bound did not acknowledge it again. Now
`consume_interventions(..., released=True)` records nothing held when this
invocation already gave that pause's authority, so the next `--resume-paused`
continues, as on master.

A pause asked again after `resume_interrupted` no longer repeats the earlier
request's advice in its cause (`pause_authority.held_cause`).

## Composition with #509 and #581

Master `87d8db3` has #586 (#509): `--answer` or `--approve-goal` given with
`--resume-paused` dispatches the next stage in the same invocation once the action
clears a human gate (`run_actions.resume_dispatch_requested`). It also has #581: the
first explicit resume after a `provide_information` answer re-evaluates that
information once (`resolver.information_reviews`, the `information_review` view field).

- **An answer or approval at an operational pause.** Neither is accepted there. The
  operational request takes only `--resolver-response`, so `--answer` is refused, and
  `--approve-goal` finds no plan request to approve. The tests send both with
  `--resume-paused` at every pause after an answered request, and at a sample of
  unanswered ones. Nothing launches. The next `--resume-paused` then admits exactly
  what the answered request alone admits, and #581 records the same decision (held or
  admitted), so neither input uses up or stands in for that one re-evaluation.
- **An edited goal put a human gate in place of the pause.** `--edit-goal` was
  accepted at any operational pause, answered or not, on master too. It installed a
  draft and asked for its approval (`AWAITING_GOAL_APPROVAL`), and
  `--approve-goal TOKEN --resume-paused` then launched the Planner, Builder and Tester
  in that one invocation. Before #586 the approval saved, and the next invocation
  launched them. Now `pause_authority.correction_refusal` refuses an edited goal
  wherever brief feedback is refused, including at a pause that a pause intervention
  interrupted (`pause_authority.interrupted`, which `autocode_stop.interrupted_pause`
  now reads too). At an ordinary pause and at an exhausted plan-review budget an edited
  goal is still accepted, and approving it with `--resume-paused` still dispatches the
  Planner. A new run's answer and approval gates still dispatch the same way, tested
  as real processes.

## Not changed

- **`autopilot.dispatch_unit`.** It has the same save-then-launch shape as the build
  loop's dispatch. It is used only by the `autopilot` entry, and `autopilot.py` is
  not grown here.
- **`--joint-planning` on an unanswered request** stops in `configure` with an
  uncaught `ValueError`. That fails closed, but it is not a clean refusal.
- **`--revise-figma-manifest`** (a design reference revision) is accepted at any
  `PAUSED_*` status. It saves brief feedback and replaces the status with
  `PAUSED_DESIGN_INPUT_CHANGED`, which a plain `--resume-paused` continues into
  Requirements. Found by reading the code, not reproduced: no CLI fixture builds a
  verified design manifest. It is the same kind of correction that is refused above.
