# A replayed Validator rejection cycled on every resume (#254)

The runner re-runs every check a Validator cites, in a clean copy of the source
(`autocode_check_replay`). When a cited check fails there, the report is rejected with an
error that quotes the replay's receipt, `<run>/check-replay/<attempt stem>-<uuid hex>/replay.json`.
Each attempt has its own receipt directory, so the original report and each report repair
quoted a different path.

The failure ledger's fingerprint (`autocode_failures.signature`) normalized only the attempt's
own artifact path and long hex digests. `validator-01-<hex>` and
`validator-report-repair-01-<hex>` stayed different, so three identical rejections never
counted as a repeat: the run paused `PAUSED_INVALID_OUTPUT` at a streak of 1. That pause
publishes no AutoResolver request, and a plain `--resume-paused` archives the spent repair and
runs a fresh Validator with two new repairs, with no bound.

## Measured offline

The fake provider with `AUTOCODE_FIXTURE_UNREPRODUCIBLE_CHECK` cites a check that passes only in
the Validator's own session; its report repairs return the same report.

| | First run | Each plain `--resume-paused` | After two resumes |
| --- | --- | --- | --- |
| Before | Validator, 2 repairs, Investigator; `PAUSED_INVALID_OUTPUT` | 3 Validator calls (1 fresh, 2 repairs) | 9 identical rejections, streak 1 |
| After | Validator, 2 repairs, Investigator; `PAUSED_REPEATED_FAILURE` | 0 calls | 3 rejections, streak 3 |

After the change each `--resume-paused --retry-failed-stage` costs exactly one Validator call. An
identical failure holds again at once, with no repair and no second Investigator. Before the change
the flag was refused at an exhausted rejection ("Reconcile the active attempt or pending report
repair"), and after a source edit no command moved a published hold (#302).

## What changed

- The fingerprint ignores per-attempt noise (`autocode_failures.normalize`): run-owned attempt
  artifacts, check-replay receipt and check directories, archived-attempt directories, mkdtemp and
  pytest temp names, macOS temp roots, UUIDs, ISO timestamps and elapsed durations. Repository
  paths (also under a replay's `scratch/tree/`), distinct `/tmp` files, commands, exit codes, test
  ids, line numbers and concern ids still tell failures apart.
- A stalled failure is never repaired again. An open repair round stops at the stall but keeps its
  latest rejected report and error paired. A repair left pending with its attempts given back (a
  chat resume at a published hold resets them) is held at dispatch.
- `--resume-paused --retry-failed-stage` accepts a stalled failure whose spent repair is still
  pending, with or without a source edit (`autocode_failure_retry.stalled_target`). The same check
  decides whether the hold advertises the flag.
- `recovery.failure_groups[]` gains `streak` and `authorized_retries`.

Tests: `tests/test_failure_retry.py` (`FailureRetryCLITests`: the negative control, which failed
before the change, and the sound control, where corrective information reaches the one
authorized attempt and it completes), `tests/test_failures.py`, `tests/test_report_repair.py`,
`tests/test_paused_source_edit.py`, `tests/test_recovery_advice_conformance.py`.

## Still open

These are left to later #254 slices:

- Alternating failures A, B, A reset each other's streak.
- A rejection that has not stalled still runs fresh on every resume.
- The paid Investigator still runs once even when the runner's own replay receipt already shows
  the cause.
- An edit unrelated to the failure still buys a fresh attempt.
- An accepted operational diagnosis still erases the failure history.
