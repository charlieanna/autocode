# A budget flag for another bound, or a pause intervention, released an operational pause

Found while reviewing the #486 fix and reproduced on master `e042daa` with the fake
provider. **Fixed** 2026-10-06; regression in `tests/test_operational_pause_authority.py`.
The rule (#379, #486): a protected operational pause is released, and a provider
admitted, only by the operator's authority for that pause. A settings write is not
that authority, and neither is a pause intervention.

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

## Not changed

- **Feedback after an answered request.** A feedback intervention after an answered
  request still restarts Requirements on resume. Feedback is the operator's direction
  to replan; whether it may run before the operational pause is acknowledged is a
  policy decision.
- **A pause queued while an unanswered request is published.** The pause is never
  applied, because the invocation stops at that request first. The pending inbox also
  makes the request's token stale, so an answer is refused as out of date until the
  input is applied. This fails closed, but the run needs a recovery action to move.
- **`autopilot.dispatch_unit`.** It has the same save-then-launch shape as the build
  loop's dispatch. It is used only by the `autopilot` entry, and `autopilot.py` is
  not grown here.
