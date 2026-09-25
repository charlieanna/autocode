# T12 — Dashboard truth and action lifecycles (UI-01..UI-14)

Executed 2026-09-24. 14/14 executed: 11 PASS, 3 PASS with an honestly
recorded limitation (UI-02, UI-03, UI-13). Browser-level evidence comes from
the repository's own local browser bridge (agent-browser) against disposable
fixtures — no network, no real workspaces.
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t12`
(≈5 minutes: eight browser suites, each executed once and cached.)
Bundles: `.tmp-autopilot-testkit/artifacts/UI-*/NN/`.

| ID | Status | Executed evidence |
|---|---|---|
| UI-01 | PASS | monitor snapshot parity + `test_status_ui.js` |
| UI-02 | PASS (scoped gap) | `test_task_clarity.js` verifies required text is visible; no synthetic clipped-layout failure injector exists in the product test surface |
| UI-03 | PASS (scoped gap) | `test_m3_lifecycle_browser_ui.js` captures lifecycle states/viewports; the complete 7×3 matrix needs the frozen FX04 reference fixture |
| UI-04 | PASS | lifecycle suite (recovered-not-running then resumed) |
| UI-05 | PASS | `test_model_replacement_ui.js` (unchanged then confirmed) |
| UI-06 | PASS | `test_task_archive_ui.js` (restored with history) |
| UI-07 | PASS | task archive suite (repeated requests stay single-shot) |
| UI-08 | PASS | `test_chat_composer_ui.js` (draft retention) |
| UI-09 | PASS | lifecycle suite (reconciled actions in UI status) |
| UI-10 | PASS | `test_refresh_ui.js` (stale updates ignored) |
| UI-11 | PASS | monitor worker claims backed by real process-table inspection + status suite |
| UI-12 | PASS | `test_shell_a11y_ui.js` (keyboard focus, dialog return) |
| UI-13 | PASS (environment limitation) | focus visibility verified; forced-colors rendering not captured in this environment — explicitly unverified, not claimed |
| UI-14 | PASS | terminal snapshot (no next stage, no active worker) + lifecycle suite |

No product defects. Three scoped gaps recorded in bundles (failure-injector,
21-screen matrix, forced-colors capture) awaiting product/fixture decisions.
