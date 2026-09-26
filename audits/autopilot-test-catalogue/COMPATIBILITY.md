# T13 — Versions, providers, limits and installed CLI (CFG-01..CFG-12)

Executed 2026-09-24. 12/12 executed: 11 PASS, 1 PASS with a scoped note
(CFG-03). The installed-CLI case runs the real `~/.local/bin/autocode`
(pipx, autocode-supervisor 0.7.1) outside the repository, offline.
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t13`

| ID | Status | Notes |
|---|---|---|
| CFG-01 | PASS | legacy/modern migration regressions rerun green; no invented approval |
| CFG-02 | PASS | version-99 state never runs; nonzero exit; fixture unchanged |
| CFG-03 | PASS (scoped note) | no CLI `--version` flag exists; versions recorded from package metadata; flag is a scoped gap |
| CFG-04 | PASS | route persistence regression rerun green |
| CFG-05 | PASS | rate-limit/budget/timeout statuses; credit exhaustion pauses as budget |
| CFG-06 | PASS | event refs refused in receipt-only routes |
| CFG-07 | PASS | limits survive state round-trips; limit-pause regression green |
| CFG-08 | PASS | 5000-entry ledger snapshot measured under the declared 10s threshold |
| CFG-09 | PASS | installed CLI `--help` and offline dry-run outside the repo; no run-state writes |
| CFG-10 | PASS | unit aliases agree with source units; separate console scripts noted |
| CFG-11 | PASS | platform matrix recorded with TESTED/UNTESTED/UNSUPPORTED cells |
| CFG-12 | PASS | representative controller flow under blocked sockets: zero network attempts |

No product defects.
