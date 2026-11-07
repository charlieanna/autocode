# A stopped Completion Review archived the evidence its own retry needed

**Severity:** High
**Status:** Fixed (2026-10-30) and verified offline against the live checkpoint it stranded.
**Found:** 2026-10-30, live GLM-5.3 / MiMo 2.6 Pro end-to-end run of the `autocode chat` TUI.
**Run:** `/tmp/autocode-e2e-node-20261030` → `20261030-202609-chat-976429`

## Summary

A Builder delivered correct code, the Tester passed it, and the runner's regression proof passed for
that exact source. Two Completion Review attempts then stopped on the 300-second idle bound. The
automatic recovery that archived each stopped attempt also removed the Tester's validation, and the
third attempt's valid `COMPLETE` was refused forever with a message blaming the proof:

```
Completion rejected: this change has no passing regression proof for the current source
(no proof exists for the current source)
```

The run's own `--resume-paused` re-ran the same Completion Review, which returned `COMPLETE` again and
paused identically. The delivered product was correct (independently re-checked: 8/8 tests plus the
four acceptance criteria by hand); AutoCode simply could no longer certify it.

## Root cause

Two invariants met at the recovery boundary:

1. `automatically_recover_timed_out_stage` retired `state["validation"]` for **any** non-report-only
   timed-out stage, under the label "Timed-out implementation automatically archived" — including a
   read-only Completion Review that had changed nothing (`changed_files` was empty).
2. `timeout_recovery_route` sent the retry back to that same Completion Review, whose completion gate
   requires the evidence the first step had just removed.

The runner already owned the right policy: `autocode_validation_recovery.current()` /
`permission_retry()` keep exactly-current pinned evidence across an interrupted completion retry and
route stale or missing evidence to `workflow.review_stage(state)`. The timeout path did not consult it.

The same class at the *manual* abandonment boundary was fixed earlier
([004-abandoned-completion-cannot-revalidate.md](004-abandoned-completion-cannot-revalidate.md)); its
resume repair matches `stage_abandoned` and "Uncertain stage abandoned", so it refused this shape.

## Secondary defect: the message pointed at the wrong thing

When the proof is `PASS`, intact and current, and every due case is covered, `regression.rejection()`
fell through to "no proof exists for the current source". The missing element was the independent
changed-line acceptance (the live run had 19 reviewer-reason obligations), so the sentence turned a
recovery gap into an apparent regression failure.

## Implemented fix

- `automatically_recover_timed_out_stage` now asks `autocode_validation_recovery.current()` before
  retiring anything. A stopped completion review that changed nothing keeps the current pinned
  validation and the reviews recorded against it, and retries the Completion Review. A writer, or
  evidence that no longer binds, is retired as before — and when retiring it would strand the very
  retry the router chose, the next dispatch becomes the workflow's verification stage (`sol`, or
  `astra_checkpoint` under reviewer routing; a final-audit-only run re-produces its own evidence).
- `prepare_timeout_archived_completion` re-opens a checkpoint already stuck in this shape at the
  plain `--resume-paused` the view advertises. It restores only the one report that recovery itself
  archived, and only when it still binds this source, contract, criteria set and task with every
  evidence pin intact, the retired attempt recorded no changes, nothing was accepted after it, and the
  runner's own proof already passes for this source. It adds no attempt, no retry allowance, no new
  flag and no question for the person; failure history, routes, limits and receipts are untouched.
- `regression.rejection()` now separates "the proof reports no failed case; the independent
  changed-line or receipt-custody evidence is missing or no longer binds" from a genuinely absent or
  failing proof. No gate condition changed, and the immutable `verification.json` is never rewritten.

## Verification

- `tests/test_activity_runtime.py`: a completion-review timeout with current evidence keeps it and
  retries completion; each broken binding (source, contract, criteria, task, evidence pin) retires it
  and revalidates first; a writer timeout still retires even current evidence and keeps the recovery
  review; reviewer-mode routing sends the retry to `astra_checkpoint`.
- `tests/test_completion_timeout_recovery.py` builds the stranded checkpoint around a real
  authenticated proof: `regression.complete(..., all_due=True)` is false before the resume and true
  after, the repair is idempotent, and 25 near-miss checkpoints are each refused without mutating or
  saving anything.
- `tests/test_test_cases.py`: the corrected rejection, with the absent- and failing-proof wording
  pinned unchanged.
- Offline replay of the real checkpoint (a copy; the live run directory was never written): the repair
  returned true, and both `regression.complete(..., all_due=True)` and
  `completion.completion_ready(...)` went from false to true with failure history, stages, routes and
  limits identical.
- Negative control: the three new completion-review timeout cases and the two new rejection cases fail
  on `origin/master` `1f261fee2` and pass on this change; 211 existing tests across architecture,
  run-state, validation-recovery, report-repair, recovery-view, command-input and runtime-export
  modules pass on both.
- Not covered locally: the macOS host cannot run the mutation-capture proofs (10 cases in
  `tests.test_test_cases` and 2 in `tests.test_proof_seam.PythonSeamProofTests` error the same way on
  unmodified `1f261fee2`, unchanged in count by this work), and no fake-provider CLI case was added,
  so the end-to-end resume-to-`TASK_COMPLETE` path is Ubuntu CI and live-run evidence, not a claim
  from this checkout.

## Related

- `recheck_completion()` and `autocode_validation_recovery` route stale completion back through the
  review stage for the same reason; this gap was that invariant missing at the timeout boundary.
- Bug 004 above: the manual-abandonment sibling of this routing defect.
