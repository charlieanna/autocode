# T08 — Crash and persistence recovery (CRH-01..CRH-14)

Executed 2026-09-24. 14/14 executed, 14 PASS, 0 environment blocks.
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t08`
Bundles: `.tmp-autopilot-testkit/artifacts/CRH-*/NN/`.

| ID | Status | Before | Notes |
|---|---|---|---|
| CRH-01 | PASS | partial | full staged run: one launch per stage; restart adds zero (new) |
| CRH-02 | PASS | partial | durable intent without a result never relaunches; late result reconciles once (new) |
| CRH-03 | PASS | partial | live worker and dead-but-unproven worker both block replacement (explicit case new) |
| CRH-04 | PASS | full | retained partial edits, honest disposition, no overlap (existing abandon test) |
| CRH-05 | PASS | full | durable terra result applied without re-execution (existing reconcile test) |
| CRH-06 | PASS | partial | REWORK review recovered exactly once, F1 kept, one repair request (new; PASS variant = FND-4) |
| CRH-07 | PASS | full | pre-replace failure keeps old state; success publishes new; no temp litter |
| CRH-08 | PASS | gap | ENOSPC/EACCES raise and never publish success (new) |
| CRH-09 | PASS | gap | truncated state refuses; never auto-initialized fresh (new) |
| CRH-10 | PASS | gap | compound failure keeps the sole result recoverable; disk-reload reapplies once (new) |
| CRH-11 | PASS | gap | already-applied integration patch recognized by identity; reapply refused (new) |
| CRH-12 | PASS | partial | authority/finding canonical state stable across three restarts, zero launches |
| CRH-13 | PASS | full | terminal state preserved, zero workers; installed entry point deferred to T13 |
| CRH-14 | PASS | gap | post-completion user edit preserved, stale pause reported, no rewriting (new) |

No product defects. CRH-07/08 are filesystem-injection tests (not power-loss
proofs) per the harness protocol's labeling requirement.
