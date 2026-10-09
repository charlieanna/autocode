# Completion permission recovery discards validation and retries completion

## Fix (2026-09-30)

`autocode_validation_recovery.permission_retry` retains validation and human
reviews only for an unchanged completion attempt with current source, criteria,
contract, task and evidence hashes. Otherwise it archives validation, clears
human reviews and sends completion through the workflow's verification stage
before retrying (`sol` for builds). Builder
interruptions continue to invalidate prior validation. The public-CLI denial
regression and focused stale-binding tests pass.

Historical reproduction on the unfixed revision follows.

Verified on `628b257df5ed6c07d00d3d59a94ee44fc8a078db`. An OpenCode
external-directory denial during completion can discard an accepted Validator
PASS, then retry completion without scheduling fresh validation. The next
completion claim fails the independent-evidence gate. This describes the original unfixed revision.

In `tools/autocode.py:1587–1589`,
`automatically_recover_external_directory_denial()` archives and removes current
validation regardless of the interrupted stage or its changed files. At lines
1592 and 1606–1607 it resumes that same stage. For `astra_review`, this leaves
completion running without current validation. The explicit abandonment path
already routes an interrupted completion to `workflow.review_stage(state)` at
lines 1189–1197 because abandonment invalidates validation.

## Observed full-live transition

Ignored evidence is under
`.scenario-runs/20260929-codex-campaign/live/20260929T233328Z-feature-refund-window-codex-only-l8mthkta/`.
Within its `project/.autocode/runs/20260929-163329-add-refunds-to-the-shop-package-write-shop-refun-806a77c7/`:

- `iterations/001/sol-01.json` reports PASS. Runner replay also passed all
  recorded checks, including the independent calendar-boundary probe.
- `iterations/001/archived-astra_review-01-21cd65/astra_review-01.jsonl`
  records a denied, malformed external evidence path and a nonterminal turn.
- The corresponding `astra_review-01.before.json` and `.after.json` snapshots
  are byte-identical: SHA256
  `38ee0a8f68c215ce75fdf9e4e62ca62953bedf3ba735121b7db93ceb8fcec236`.
  Recovery records no changed files. Both recovery and archived validation bind
  to source revision
  `28b4e33d52a47feb66888533c18402ee34bf901cf6edb1e00aac0b74b46842a9`.
- `state.json` retains the archived PASS with reason
  `External-directory denial before terminal implementation report`, current
  validation absent, and recovery's next stage `astra_review`.
- `iterations/001/archived-astra_review-02-2a39df/astra_review-02.json` declares
  COMPLETE. The runner rejects it for missing independent evidence.

The failure is safe: completion was refused. The model's malformed path and
subsequent COMPLETE claim contributed; the runner's recovery transition created
the missing-validation condition. Suggested correction: after invalidating
validation at completion, schedule fresh independent validation before permitting
another completion claim. Retaining existing validation would instead require
explicit proof that all of its bindings remain valid.

The Investigator subsequently diagnosed this transition, but its cited
`run/state.json` was omitted by the separate
[evidence-staging defect](2026-09-29-investigator-evidence-staging.md).
Temporary replay of the exact probe exits 1 with current staging and 0 when the
cited run-root state is included. The active-time cap then ended recovery at
1239/1200 seconds. The original result remains NOT_EXERCISED because the benchmark
requires `astra_resolve`, which was never reached; all five application checks
pass. Neither passing application checks nor this diagnosis changes that result.

## Independent tenant-authorization corroboration

The full-live `ladder-23-tenant-authorization` case at
`.scenario-runs/20260929-codex-campaign/live/20260930T005130Z-ladder-23-tenant-authorization-codex-only-yj4ovdht/`
repeats this transition. Its accepted `iterations/001/sol-01.json` PASS is
archived after completion permission recovery. Both interrupted completion
attempts record no changed files and the archived validation's source revision
`783102ba5aa006bd23389d05fdecc5df7cfc23eb19ebf810e0796b178f10e197`.
All six before/after snapshots across the three completion attempts are identical,
SHA256 `306e9f5bed4e50ca46898de3dbf1a7994b0efa904b832914e16b4d5958333d81`.
The third COMPLETE report is rejected because current validation is absent.

The Investigator's initial citations exist. Its exact probe fails in temporary
iteration-root staging because `run/state.json` is missing, and passes when the
authoritative run root stages both cited files. A later report repair introduces
a nonexistent truncated citation; the investigation ends without recovery.
The final status is **HONEST_BLOCKER / PAUSED_INVALID_OUTPUT**, with all six
application checks passing, at 1078/1200 active seconds and 1,825,320/2,000,000
reported tokens. This stop is below the global budgets. The original result
SHA256 is `3d6eb629713723a06fe05285097779b1054d8c475b54d0fcce411736bbfd8f89`;
the ignored `triage/ladder-23-tenant-authorization.json` records 23 unchanged
evidence hashes and the independent scratch replay. Completion remains refused.

## Black-box regression test

`scenarios.test_adversarial_recovery.RecoveryAttacks.test_completion_permission_recovery_keeps_or_rebuilds_validation`
now reproduces the transition through the public CLI with a scripted provider.
It builds and validates a real greeting project, verifies that the completion
handoff contains an accepted PASS bound to the current source, then emits one
nonterminal `external_directory` denial without changing any project file.
The next completion handoff has the identical source revision but no validation,
with no intervening Validator call. The test fails on that missing binding.
It imports no runtime modules and never writes runner state. A future fix must
retain evidence whose bindings remain valid or schedule fresh validation before
completion resumes; the test accepts either behavior.
