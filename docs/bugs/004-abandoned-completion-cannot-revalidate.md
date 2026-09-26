# Bug 004: Abandoning an uncertain completion attempt leaves no route back to validation

**Severity:** High
**Status:** Fixed in working tree; offline regression verified (2026-09-26). Live rerun pending.
**Found:** 2026-09-26 live trial LIVE-01 (GLM-5.3 / MiMo 2.6 Pro), correctness campaign
**Run:** `autocode-live-greeting-3` → `20260926-002059-build-a-deterministic-greeting-cli-named-greet-p-a3632142`

## Summary

The delivered artifact is correct and the Validator had already passed it, but
the run cannot reach `COMPLETE` after an interrupted Completion Owner attempt is
abandoned. `--abandon-stage` invalidates the previous validation (documented),
yet every resume path keeps routing to the Completion Owner, whose completion
gate correctly refuses for missing evidence. There is no route back to the
Validator to re-validate unchanged work, so the run settles at
`PAUSED_REPEATED_FAILURE` with correct code it cannot certify.

This is a pipeline routing gap, not a model-quality failure and not a false
completion claim.

## Reproduction

1. Run `LIVE-01` (greeting CLI) to the completion stage with real models.
2. Interrupt the process while `astra_review` (Completion Owner) is mid-flight
   (here: outer tool timeout killed the runner at ~30 minutes).
3. `--status` correctly reports `PAUSED_INTERRUPTED`.
4. `--resume-paused` pauses with `PAUSED_PROVIDER_UNCERTAIN` and instructs:
   `use --abandon-stage 001/astra_review-01`.
5. Run `--abandon-stage 001/astra_review-01`. The runner records
   `validation_archive: ["Uncertain stage abandoned"]` and clears `validation`.
6. `--resume-paused` dispatches `astra_review` (Completion Owner) again. Its
   report is rejected: `Completion rejected: missing, stale, failed or
   unverified independent evidence`.
7. Repeat `--resume-paused`, or use `--unit autoreview`: both dispatch
   `astra_review` again, never `sol`. The run ends at `PAUSED_REPEATED_FAILURE`.

## Evidence

Delivered project (`greet.py`, `test_greet.py`, `README.md`) passes the
independent FX01 oracle **12/12**:

```
[PASS] no-arg.exit=2 / no-arg.output='usage'
[PASS] ada.exit=0 / ada.output='Hello, Ada'
[PASS] multiword.output='Hello, Ada Lovelace'
[PASS] unicode.output='Hello, Zoë'
[PASS] two-arg.exit=2 / two-arg.output='usage'
[PASS] deliverables=['greet.py','test_greet.py','README.md']
[PASS] stdlib_only=[]
```

The Validator (`sol-01.json`) had already produced a valid **PASS** report with
`AC1..ACn` criterion results and `event:` evidence refs before the interruption.

State after abandonment:

```
status: PAUSED_REPEATED_FAILURE
next_stage: astra_review
validation in state: False
validation_archive: ['Uncertain stage abandoned']
astra_review attempts: 5
failure_history: 2
```

## Root cause

Two invariants conflict at the recovery boundary:

1. `--abandon-stage` invalidates prior validation, because the abandoned
   response may have described a different artifact.
2. The stage router treats a pending `astra_review` as the next action even when
   `state["validation"]` is empty, so the completion gate is invoked without the
   evidence it requires.

The Completion Owner can emit `CONTINUE` with `next_task.kind=validate`, which
would request fresh validation. In this trial it repeatedly requested completion
instead. The defect is that the default abandonment routing depends on a model
choosing that recovery step, even though the runner knows validation was removed.
The completion gate correctly refused those COMPLETE decisions.

## Expected behavior

After an uncertain completion attempt is abandoned on unchanged source, the next
dispatch must re-validate the current artifact before any completion decision:

- route to the Validator (`sol`) when `state["validation"]` is missing or was
  invalidated by the abandonment, then to the Completion Owner; or
- let the pause name the exact recovery command (for example
  `--retry-report` / a revalidation unit) instead of re-dispatching a stage that
  cannot pass.

Abandonment should not silently remove the only path to the evidence the
completion gate demands.

## Implemented fix

`tools/autocode.py` now routes abandonment of `astra_review`, including its
report-only repair attempt, through `workflow.review_stage(state)`. Under the
standard workflow this means `sol` before another completion decision. Alternate
workflow approval guards and routing remain authoritative. Abandonment still
archives validation, clears human-review acceptance, retains work and evidence,
and launches no agent.

An explicit `--resume-paused` also recognizes old checkpoints already stuck in
this exact loop. It requires a matching archived completion abandonment, unchanged
source/task/contract identity, the corresponding user event and validation archive,
and only the recognized failed completion retries after that boundary. It does
not override an active or uncertain attempt, pending repair, unrelated completion
failure, subsequent accepted work, or an existing repeated validation failure.
The correction is saved durably without clearing failure counts or restoring
invalidated evidence. Ordinary retry limits and completion gates are unchanged.

## Regression verification

The new CLI regression failed before the fix because abandonment selected
`astra_review` instead of `sol`. It now reaches `TASK_COMPLETE` through fresh
`sol` and `astra_review` stages, with one Builder execution, unchanged source and
approved goal, and current independent evidence. A second CLI case recreates the
old persisted repeated-failure loop and verifies explicit-resume recovery while
retaining failure history. Unit cases cover abandoned completion report repair
and fail-closed recovery conditions.

248 offline tests passed across `test_autocode`, `test_subprocess`,
`test_report_repair`, `test_workflow`, `test_final_workflow`, `test_goals`,
`test_resolver_runtime`, `test_milestone_checkpoints`, and `test_activity_runtime`.
No live provider was launched and the original failed live run was not modified
during this fix. This result does not qualify the remaining live-testing ladder.

## Related

- `recheck_completion()` already routes stale completion to
  `workflow.review_stage(state)` for a similar reason — this gap is the same
  invariant missing at the abandonment boundary.
- `test-scenarios/NOTES.md` scenario 06 records a similar prescribed-recovery
  dead end after quota pause; both should share one recovery contract.
- The stale-checkpoint labeling fix (commit `0012d88`) makes this class of state
  visible; this bug is the follow-on routing defect it exposed.
