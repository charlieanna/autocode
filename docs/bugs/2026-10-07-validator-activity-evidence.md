# Tester activity evidence changed during normal review

[Issue #638](https://github.com/charlieanna/autocode/issues/638) was reproduced
by the `pytest-stop-fixture-teardown` live Arena qualification on commit
`34a04ec`. Attempt `6e02f5a40f194297a96a3f00a535cf03` stopped after 2,806 seconds
with `PAUSED_INVALID_OUTPUT`: “Validator evidence changed before repair.” Its
original result and stage reports remain preserved in the ignored qualification
evidence; the attempt has not been resumed or rewritten.

The Tester cited this run's live `activity.jsonl` for AC-09. Acceptance pinned
its current hash, then the runner appended normal stage events. The Completion
Reviewer requested REWORK, but repair admission correctly refused the changed
pin. The Investigator paused because repeating that review would reuse the
same stale evidence. The twelve independent Arena oracle checks passed, but
the task did not complete.

Evidence acceptance now saves a content-addressed copy of the exact own
activity log, using the same atomic snapshot mechanism as run-state evidence.
It pins that accepted prefix. Later activity appends leave the prefix unchanged;
later citations get a new snapshot. Resolved aliases of the own log behave the
same way. Ordinary project files named `activity.jsonl` still use their original
paths and hashes. Corrupting a saved copy remains an error, and repair admission
still rejects genuinely changed pinned evidence.

The stopped candidate also had a separate Completion Review finding about
`BaseException` finalizer aggregation, and its broad repository test command
had twenty failures whose cause was not classified. Those results require further validation; the original STOPPED attempt remains
preserved. A
public CLI regression checks that activity citations survive Completion REWORK,
then the normal automatic repair and independent checks complete.
