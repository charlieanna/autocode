# An in-process exception stranded the verification pending launch (#414)

`autocode_verification_schedule.run` wrote `pending.json`, then called
`execute(out)` and `current_identity()` with no handler. Any exception raised
there — a `git`/`scratch_run` error inside `execute`, a `ValueError` from
check replay's identity closure when the source changed under the obligation,
or Ctrl-C during a long check — left the pending launch with no completed
receipt. `_reconcile_pending` then raised `PAUSED_VERIFICATION_UNCERTAIN` on
every later resume, and nothing in the code could clear it; the only way out
was deleting files under the run directory by hand.

## Fix

An exception that unwinds through `run` is known-failed state, not
uncertainty: the process was alive and able to record an outcome. `run` now
catches it, records a failed receipt for the attempt (preserving any partial
result from a completed `execute`, with the error written on top so it is
never reusable), publishes the completed pointer and clears the pending
launch, then re-raises the original error so the caller's ordinary rejection
path is unchanged. The next attempt runs a fresh check.

A hard crash (nothing runs to record anything) still leaves `pending.json`
behind and still pauses for a person to reconcile its owned processes; that
fail-closed guarantee is untouched, because no Python code executes in that
case. The crash-after-durable-completion window (receipt written, pending not
yet cleared) also behaves as before: the next run reconciles and reuses.

The receipt-and-pointer sequence is shared by the success and failure paths
(`_finish`), so completion stays durable before the pending launch clears.

## What changed behaviorally

- Ctrl-C or a scratch error during a scheduled check no longer wedges the run:
  the resume re-runs the check fresh.
- A source change detected after `execute` returns now leaves the same trail
  it always did at the caller (report rejected, fresh validation required)
  without the extra permanent pause.
- `tests/test_verification_schedule.py` previously codified the wedge as
  desired (`test_interrupted_launch_pauses_instead_of_double_launch`,
  `test_changed_identity_cannot_evade_an_uncertain_launch`); both are
  rewritten for the new property, and the uncertain-launch replay test now
  simulates a hard crash by planting a pending launch with no receipt.

## Regression coverage

`tests.test_verification_schedule` covers the three exception paths (scratch
error, KeyboardInterrupt, stale obligation after a successful execute), the
failed receipt's non-reuse under any identity change, and the hard-crash pause
at both the schedule and replay levels.
