# T09 — Process ownership and idempotent actions (OWN-01..OWN-12)

Executed 2026-09-24. 12/12 executed, 12 PASS, 0 environment blocks.
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t09`
Bundles: `.tmp-autopilot-testkit/artifacts/OWN-*/NN/`.

| ID | Status | Before | Notes |
|---|---|---|---|
| OWN-01 | PASS | full | second controller gets a Paused contention receipt (existing lock tests) |
| OWN-02 | PASS | full | live worker blocks replacement; dead worker frees admission |
| OWN-03 | PASS | full | surviving child writer holds ownership until confirmed termination |
| OWN-04 | PASS | partial | only liveness probes (signal 0) ever sent; no PID-only kill (kill-scope case new) |
| OWN-05 | PASS | full | stopped is advertised only after the operator acknowledgement |
| OWN-06 | PASS | partial | durable pause linearizes before any launch (pause-first ordering new) |
| OWN-07 | PASS | partial | replayed Resume changes nothing and launches nothing (new) |
| OWN-08 | PASS | gap→covered | FX05 mapped to the supported idempotent surface: intervention request ids return the original receipt exactly once |
| OWN-09 | PASS | gap→covered | same id with a changed payload → explicit conflict, payload unchanged |
| OWN-10 | PASS | partial | uncertain stage never auto-repeated across two retry requests (scoped mapping: the runner has no external HTTP side-effect adapter; stage uncertainty is the supported surface) |
| OWN-11 | PASS | full | failed process inspection fails closed (existing guard test) |
| OWN-12 | PASS | partial | stop receipt, preserved work; after an acknowledged pause the next plain start is the one new safe attempt (scoped note recorded) |

No product defects.
