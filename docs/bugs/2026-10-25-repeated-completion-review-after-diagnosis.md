# Completion Review repeated without limit after an answered diagnosis

**Seen:** 2026-10-25, live run stopped by the user (`PAUSED_INTERRUPTED`) after the
Completion Reviewer ran 11 times in a row on an unchanged source.

## What happened

1. The runner's regression replay failed once on an unrelated timing flake, so
   `regression_proof` was `FAIL` for the current source.
2. The Completion Reviewer returned `REWORK`; the Resolver diagnosed it once for this task
   and source, and the user answered its question.
3. On the next `REWORK` for the same source, `autopilot.queue_resolution` found the diagnosis
   current and called `autoresolver.wait_on_existing_diagnosis`, which staged a `blocker`
   proposal with `next_stage="astra_review"`.
4. `autocode_run_records.normalize_human_boundary` evaluated it as `defer` ("needs internal
   AutoResolver investigation"), called `queue_resolution` again, which staged the same
   blocker (one diagnosis per task and source), and then set the run `RUNNING`.
5. The run went back to `astra_review`, the reviewer saw the same `FAIL`, and the cycle
   repeated. Before the Completion Owner, a failed proof of an unchanged source was never
   replayed, so nothing could change.

## Fix

- The writer boundary sets the run `RUNNING` after requeueing a deferred blocker only when
  the requeue actually scheduled the Resolver (`next_stage == "astra_resolve"`). Otherwise
  it holds in `RESOLVER_PENDING` with a stop reason that names the existing diagnosis.
  A resume persists through the same boundary and holds again, so the cycle cannot recur
  without a person, a changed setting or new source.
- Before the Completion Owner reviews, a `FAIL` proof of the current source that was
  proved only once is replayed once more (`autocode_regression._review_needs_proof`).
  A second failure stands, so a real failure still demands rework.

Regressions: `tests/test_resolver_producers.py`
(`test_a_repeated_rework_after_an_answered_diagnosis_holds_instead_of_looping`) and
`tests/test_regression_review_recheck.py`.
