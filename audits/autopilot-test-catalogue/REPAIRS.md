# T07 — Corrections and resolver boundaries (FIX-01..FIX-10)

Executed 2026-09-24. 10/10 executed: 9 PASS, 1 PASS-with-scoped-gap (FIX-04).
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t07`
Bundles: `.tmp-autopilot-testkit/artifacts/FIX-*/NN/`.

| ID | Status | Before | Notes |
|---|---|---|---|
| FIX-01 | PASS | full | resolver keeps the reviewer's acceptance inventory; verify-claims rejected; existing `test_findings_controller` resolver tests |
| FIX-02 | PASS | full | source edit, COMPLETE verdict and self-disposition all refused at the resolver boundary |
| FIX-03 | PASS | full | oversized batch rejected with split guidance; F3/F4 stay open; existing limit tests + FND-12 |
| FIX-04 | PASS (scoped gap) | proposed capability | intra-repair checkpoint queues are not a product promise (catalogue-tagged `proposed-capability-test`); stage-level recovery verified instead. Needs an explicit product decision if wanted |
| FIX-05 | PASS | full | saved attempts drive the fixed ladder one rung at a time; contract never restarted; existing `test_escalation` + ladder tests |
| FIX-06 | PASS | full | failure identity keyed by stage/artifact/error-class survives renames and restarts; budget exhaustion explains itself; existing failure-identity tests |
| FIX-07 | PASS | full | permission gaps route to the user; no ladder movement; source boundary unchanged |
| FIX-08 | PASS | full | missing browser capability stays an unverified-criterion blocker with no correction batch (EVD-15 companion) |
| FIX-09 | PASS | gap→covered | regression during repair opens a new blocking finding on the new candidate; C1 acceptance cannot mask it |
| FIX-10 | PASS | partial | no wall-clock kill when `max_seconds` is unconfigured and progress continues; configured `no_progress_batches` stall still triggers recovery; existing `test_limits_pause...` |

No product defects. One scoped gap (FIX-04) awaiting an explicit decision.
