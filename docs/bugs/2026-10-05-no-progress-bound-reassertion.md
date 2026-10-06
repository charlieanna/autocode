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
with N above the retained count, which the advice states, or `0`. It names the saved
limit only where reasserting it is accepted. After a consumed response, the view's
resume need has `action` `--resume-paused --no-progress-limit N` and the retained
count in `no_progress_batches`, which the view did not otherwise show. The request's
options add the acknowledgment. The regressions are in
`tests/test_no_progress_recovery.py` and `tests/test_recovery_grant_advice.py`.

Both apply only when the limit caused the pause
(`recovery_limits.no_progress_bound_holds`). Other holds publish the same
`PAUSED_NO_PROGRESS`, such as a recovery novelty hold (#422), which names
`--retry-failed-stage` in its own reason, or owned workers waiting to be
reconciled. Raising the bound does not address those holds, so their advice is
unchanged. The first version compared the count with the saved limit, which missed
the trial's own checkpoint: guidance consumed, limit 4 saved in its own invocation,
then a plain resume republished the request at limit 4/count 3. It also took a
novelty hold at or above the limit for the limit's. The cause is now the build
loop's reason (`recovery_limits.NO_PROGRESS_CAUSE`): the published error, else the
stop reason, else the pause's earlier requests in issue order, skipping the notes
AutoResolver composes. After a response, the republished request carries only that
note, so only the first request still names the cause.

## Unchanged batches spent the recovery ceiling

Fixed 2026-10-05 (#448). `recovery_accounting.spent` read a run without
`automatic_recoveries_since_resume` as a run saved before that key, and counted
the larger of `consecutive_timeout_recoveries` and `no_progress_batches`. Every
counted recovery writes the key, so on current code that shape is a run that has
never recovered, and its `no_progress_batches` are unchanged implementation
batches. At three of them the recovery guard, which runs before the no-progress
check, paused the run as `PAUSED_TIMEOUT_RECOVERY` and advised `--grant-recovery`,
which recorded a real grant before the no-progress pause could even appear. Under
an explicit `--no-progress-limit 5` the same three batches stopped the run. The
fallback now counts `no_progress_batches` only for a run whose history has timeout
recoveries, the legacy shape; otherwise it counts consecutive timeout recoveries.
This is the reverse of the attribution #306 fixed, where planning recoveries counted
as unchanged batches. That fix is on master: only Builder timeout and permission
recoveries increment the count.

`run_actions.next_command` (#301) also names `--no-progress-limit`, but nothing
calls it. It is left as it was. The advice the CLI publishes comes from
`recovery_limits.advice`.

## Not changed

- Any other explicit-recovery resume that leaves a live operational request in place
  may reach the same legacy-blocker reconciliation once resume bookkeeping changes
  the binding. Only the no-progress case was reproduced.
- With `--unit autoplanner`, a resume stops at the Builder handoff before the
  no-progress check, so a bound that does not admit the count also exits 0 at
  `RUNNING`; the next launch holds again before any Builder. The tests therefore
  run the next launch to completion.
- The 2026-10-03 note's timeout-recovery report did not reproduce on 2026-10-05. At
  a published `PAUSED_TIMEOUT_RECOVERY` request, changing a stage timeout retires the
  request without reaching `RESOLVER_PENDING`, and a plain resume asks again with
  `--grant-recovery` advice. The grant, alone or with the changed timeout, continues
  to completion. When consecutive timeouts at the no-progress limit caused the stop,
  `--resume-paused --no-progress-limit N` above them continues too.
- The work in progress also expected a plain resume to acknowledge a saved raise,
  resolver per-incident attempts to survive an explicit resume, and
  `--no-progress-limit 0` not to admit. Master chose otherwise (reassert on resume,
  the audited `reset_for_resume` epoch, 0 removes a bound); the tests follow master.
  On 2026-10-05 the user confirmed that `0` means no cap and admits a
  `PAUSED_NO_PROGRESS` request, live or reasserted (#448's criterion follows).
