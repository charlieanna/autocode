# Reasserting a raised no-progress bound after a repeated request

Found while splitting the desktop work in progress handed off in
[#412](https://github.com/charlieanna/autocode/issues/412) (commit `77933a8`,
`tests/test_no_progress_recovery.py`). **Fixed** 2026-10-05; regression in
`tests/test_no_progress_recovery.py`.

## Reproduced behavior

A run held at `PAUSED_NO_PROGRESS` publishes an AutoResolver operational request.
Its advice says to send corrective information and then `--resume-paused`. In the
2026-10-03 AWS design trial the operator did that, saved a larger
`--no-progress-limit`, and resumed:

- A plain resume after the saved raise asked again. As for the active-time pause
  (`active-time-pause-acknowledgment.md`), a saved bound is acknowledged only by
  reasserting it on resume. That is unchanged.
- Reasserting it with `--resume-paused --no-progress-limit 4` then exited 2 with
  `autocode: Role result belongs to another implementation task`, saved nothing,
  and left the run on the repeated request. The resume's resolver epoch reset
  (`resolver_runtime.reset_for_resume`) appends a user event, which is part of the
  request's binding. The still-pending request went stale, and
  `run_records.normalize_human_boundary` reconciled its leftover `user_request` as a
  legacy blocker sourced from the plan output. `autopilot.queue_resolution` refused
  that output in `goals.execution_guard` (`PAUSED_STALE_TASK`), first in
  `run_actions.handle` and again in the error handler's own save, so the run's
  pause handling never recorded it.

Reasserting directly after the response, before a plain resume asked again,
already worked.

## Correction

The active-time reassertion in `autocode_run_setup.load_locked` is now a table,
`REASSERTABLE_BOUNDS`, that also covers `PAUSED_NO_PROGRESS`. `--resume-paused
--no-progress-limit N`, with N above the retained count or 0, supersedes a live
no-progress request, or acknowledges one a response consumed, before resume
bookkeeping runs. The retained count, plan approval and timeout-recovery history
are unchanged, and a bound that does not admit the count never launches the Builder.

## Not changed

- The no-progress advice still names `--resolver-response`
  (`recovery_limits.INFORM_ADVICE`, pinned by `tests/test_recovery_grant_advice.py`),
  while `run_actions.next_command` names `--no-progress-limit`. Information alone
  never admits this pause, so the advice leads operators into the sequence above.
- Any other explicit-recovery resume that leaves a live operational request in place
  may reach the same legacy-blocker reconciliation once resume bookkeeping changes
  the binding. Only the no-progress case was reproduced.
- The work in progress also expected a plain resume to acknowledge a saved raise,
  resolver per-incident attempts to survive an explicit resume, and
  `--no-progress-limit 0` not to admit. Master chose otherwise (reassert on resume,
  the audited `reset_for_resume` epoch, 0 removes a bound); the tests follow master.
