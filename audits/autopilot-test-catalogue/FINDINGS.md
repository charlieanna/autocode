# T05 — Finding identity and lifecycle (FND-01..FND-14)

Executed on 2026-09-24 against checkout `153d89ceeaa50d6350f9230775036d8da8e89681`
(`master`; the two commits after the catalogue's grounding `6e38c37` touch only
dashboard files). Runner: `.venv/bin/python` 3.14.6, psutil 7.2.2, macOS
(darwin 25.6.0, arm64). All cases ran offline with sockets blocked in the
harness smoke and with fake/patched providers everywhere else; no real model,
network or user project was touched.

Commands:

```
env -u AUTOCODE_TEST_CLI ../.venv/bin/python -m unittest tools.test_catalogue_t00   # harness smoke
env -u AUTOCODE_TEST_CLI ../.venv/bin/python -m unittest tools.test_catalogue_t05   # FND-01..FND-14
env -u AUTOCODE_TEST_CLI ../.venv/bin/python -m unittest tools.test_findings tools.test_findings_controller
```

Results: 14/14 executed, 14 PASS, 0 FAIL, 0 environment blocks. Evidence
bundles: `.tmp-autopilot-testkit/artifacts/FND-*/NN/` (state snapshots,
trace.jsonl, assertions.json, operations.json, environment.json, result.json).
Reset and replay with `rm -rf .tmp-autopilot-testkit && .venv/bin/python -m
unittest tools.test_catalogue_t05`.

## Scenario map

| ID | Status | Coverage before | Executed by | Existing regression rerun |
|---|---|---|---|---|
| FND-01 | PASS | full (unit) | `test_catalogue_t05.UnitFindingCases.test_fnd01_identical_wording_keeps_two_defects` | `test_findings.LedgerTests.test_same_wording_with_different_evidence_stays_two_findings` |
| FND-02 | PASS | full (unit) | `...test_fnd02_omission_is_not_resolution` | `test_findings.test_sol_findings_open_repeat_and_close_only_by_explicit_disposition` |
| FND-03 | PASS | full (unit + controller) | `...ControllerFindingCases.test_fnd03_blocked_review_still_records_findings` | `test_findings.test_blocked_review_records_new_findings_and_closes_nothing`, `test_findings_controller.test_blocked_user_request_records_the_finding_before_pausing` |
| FND-04 | PASS | partial (terra reconcile only) | `...test_fnd04_replayed_report_applies_exactly_once` (new controller case: sol report + findings) | `test_autocode.test_crash_after_completed_terra_reconciles_without_reexecution` |
| FND-05 | PASS | full (unit) | `...test_fnd05_update_by_cited_identity_preserves_history` | `test_findings.test_sol_findings_open_repeat...` (cited-id refresh) |
| FND-06 | PASS | gap (only unknown-id no-op tested) | `...test_fnd06_other_reviewer_cannot_close` (new) | — |
| FND-07 | PASS | full (unit) | `...test_fnd07_disposition_outside_reviewed_scope` | `test_findings.test_a_disposition_must_cover_the_scope_the_finding_was_raised_under` |
| FND-08 | PASS | full (unit) | `...test_fnd08_unknown_disposition_ids_are_harmless` | `test_findings.test_dispositions_are_checked_and_repairs_cannot_close` |
| FND-09 | PASS | full (unit) | `...test_fnd09_report_only_repair_cannot_close` | same as FND-08 |
| FND-10 | PASS | full (unit) | `...test_fnd10_retraction_recorded_separately_from_fix` | `test_findings.test_sol_findings_open_repeat...` (retraction branch) |
| FND-11 | PASS | partial (assignment linkage only) | `...test_fnd11_builder_claim_cannot_close_its_fix` (new controller case) | `test_findings.test_assignment_links_open_findings...` |
| FND-12 | PASS | partial (subset selection only) | `...test_fnd12_partial_fix_keeps_unselected_findings` (new) | — |
| FND-13 | PASS | gap | `...test_fnd13_recurrence_after_closure_tracks_new_occurrence` (new) | — |
| FND-14 | PASS | gap | `...test_fnd14_cross_project_disposition_is_isolated` (new controller case) | — |

## Findings

No product defects found in T05. The controller-level wiring behaves as the
catalogue expects: BLOCKED reviews record findings before pausing, reconciled
review reports apply exactly once, builder fix-claims never reach the ledger,
and a cross-run report is rejected by the execution guard (`PAUSED_STALE_GOAL`)
before it can touch another project's ledger.

Two scoped observations (not failures, product promises tracking rather than
more):

- **FND-13**: a recurring defect opens a *new* ledger row; there is no explicit
  pointer from the new occurrence to the historical resolution beyond the
  retained closed row. Recorded as a `scoped_note` in the FND-13 bundle.
- **FND-14**: finding-id *strings* can coincide across two runs (both derive
from a per-state sequence). Isolation is enforced by run/contract binding at
the controller, not by globally unique ids — which is what the scenario asks
the runner to guarantee.

## Harness notes

- Expected ledger states come from `autopilot_testkit.FindingsOracle`, a
  reference model that never imports `autocode_findings`; production ids are
  positionally translated for the oracle at `FindingCase.to_oracle`.
- Deliberately wrong oracle expectations are proven detectable by
  `test_catalogue_t00.HarnessSmokeTests` (T00).
- The `not_rechecked` annotation a later report legitimately adds is excluded
  from "unchanged finding" comparisons (documented semantics, verified by
  FND-02/FND-08/FND-12).

Next work: T06 (`tools/test_catalogue_t06.py`) — command evidence and candidate
identity, including one reproduced defect in wrapper unwrapping (EVD-03).
