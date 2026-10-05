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

## The advice named a command that holds

Fixed 2026-10-05 (#448). The no-progress pause had no entry in
`recovery_limits.BOUND_ADVICE`. Its request, the status view's question and
`stop_reason` therefore all ended in the generic `INFORM_ADVICE`: send corrective
information, then `autocode resume`. Information alone never admits this pause, so
that resume exits 2 with "AutoResolver retained the human guidance". Once the
response had consumed the request, the view's `resume` need named no command at
all. The pause now has its own advice: `autocode resume --no-progress-limit N`,
with N above the retained count, or `0`. After a consumed response, the view's
`needs.action` is `--resume-paused --no-progress-limit N`. The regressions are in
`tests/test_no_progress_recovery.py`.

Both apply only while the count has reached its saved limit
(`recovery_limits.no_progress_bound_holds`). Other holds publish the same
`PAUSED_NO_PROGRESS`, such as a recovery novelty hold (#422), which names
`--retry-failed-stage` in its own reason, or owned workers waiting to be
reconciled. Raising the bound does not address those holds, so their advice is
unchanged. One counter-caused shape still gets the generic advice: a limit raised
in a separate invocation, followed by a plain resume that publishes the request
again. Its count is then below the saved limit, the same as the other holds.
Reasserting that saved limit on resume admits it, as described above.

`run_actions.next_command` (#301) also names `--no-progress-limit`, but nothing
calls it. It is left as it was. The advice the CLI publishes comes from
`recovery_limits.advice`.

## Not changed

- Any other explicit-recovery resume that leaves a live operational request in place
  may reach the same legacy-blocker reconciliation once resume bookkeeping changes
  the binding. Only the no-progress case was reproduced.
- The work in progress also expected a plain resume to acknowledge a saved raise,
  resolver per-incident attempts to survive an explicit resume, and
  `--no-progress-limit 0` not to admit. Master chose otherwise (reassert on resume,
  the audited `reset_for_resume` epoch, 0 removes a bound); the tests follow master.
  On 2026-10-05 the user confirmed that `0` means no cap and admits a
  `PAUSED_NO_PROGRESS` request, live or reasserted (#448's criterion follows).
