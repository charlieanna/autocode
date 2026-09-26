# T11 — Isolation and trust boundaries (SEC-01..SEC-10)

Executed 2026-09-24. 10/10 executed, 10 PASS. Threat-model scope: the runner's
documented trusted-workspace boundaries; unsupported prevention is stated, not
claimed.
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t11`

| ID | Status | Notes |
|---|---|---|
| SEC-01 | PASS | evidence paths cannot escape; outside sentinel untouched |
| SEC-02 | PASS | task data stays one argv element; real execution leaves canaries intact |
| SEC-03 | PASS | repository instruction text never becomes authorization |
| SEC-04 | PASS | reviewer stages stay read-only; degenerate review rejected transactionally |
| SEC-05 | PASS | local_settings surfaces only whitelisted nonsecret keys; canaries absent |
| SEC-06 | PASS | worker-authored approval events invalid; genuine ones still valid |
| SEC-07 | PASS | archive machinery moves run folders only; source hashes survive (full lifecycle in T12) |
| SEC-08 | PASS | external-directory access auto-denied; recovery stays workspace-contained |
| SEC-09 | PASS | cross-project report refused; project B state unchanged |
| SEC-10 | PASS | scope deltas surface as explicit user decisions |

No product defects. Kernel-level sandboxing/multi-tenant hardening remain
outside the supported threat model (documented, not claimed).
