# Stopped-job effort recovery editor regression

## Verified outcome, 2026-11-21

PR #4023 merged as `4dcd9087679c46aca89b9c79d7be2d08c176a370` after review
of exact head `d8a0bd4d212e80c6d3ab77875c98d18201cb3d2f` and green
[`ci-required`, run 44890470195](https://github.com/charlieanna/autocode/actions/runs/44890470195).
The actual Linux VS Code Extension Host passed all six checks, including the
model-and-effort answer with the default confirmation policy enabled, token
rotation, saved medium effort and no additional provider call. The Ubuntu suite,
lint and advisory diff coverage also passed. There were no unresolved review
threads. The retained exact-head host log is
`/private/tmp/autocode-4023-d8-editor-host.log`.

The shared confirmation fix is complete. Kilo route admission and its actual
editor acceptance remain separate work in #4022. Earlier failed and pending
observations below are preserved as history, not the current outcome.

## Implementation and verification history

The status fix is already on master through #4004 (merge 1644ee1ef948bb6b65161e166c652ad0604d5b97). This follow-up adds a real offline Extension Development Host regression for its Codex quota case. The fixture creates a genuine stopped job with the scripted provider, changes only the saved stopped role's effort, clears saved candidate suggestions, and recomputes source identity after moving its owned checkout. The real editor must expose the core's named model decision, send the bound model and effort, observe a rotated retry token, and start no additional provider call. Its processes and warm host use a private namespace.

The actual Mac VS Code host passed the candidate fbe8662e68c9561080cae00d784cbe4e71ccc7c9 with the original version of this regression (`/private/tmp/autocode-quota-route-host-final-mac.log`); that candidate and the #4004 source head c56a6e26c860f75c3900da385cd4b33d3baa6535 were byte-identical across `tools/`, extension source and package files. An actual editor control on pre-fix 067fdca13668d83f5a6be31392edcd6ce1e91a7f opened the chat and failed because the decision field was absent (`/private/tmp/autocode-quota-route-host-baseline-mac.log`). A later Mac editor control on then-master a263eba8933b06960d1bad7c768301a309240163 did not reach the chat because the activity-bar click timed out; its independent public CLI status reproduced the false blocked state. The exact public effort-drift and explanation assertions were added after the successful host run and passed against a fresh offline CLI fixture. This test-only follow-up cannot fail on current master because the product change has already landed; its pre-fix behavior comparison is recorded above.

Kilo uses the OpenCode provider path, which this Codex-only test does not verify. Kilo route admission and its editor check remain separate work. Exact-head focused tests, Ruff, mypy and Linux host CI must pass before this follow-up is merged.

On the follow-up based on master 5335ff6e6895b1203c42bc650a37119d380ac744, the complete real Mac host test passed, including the added exact public wording assertions (`/private/tmp/autocode-quota-route-followup-mac.log`). Graceful editor close exceeded the fixture's short deadline; its identity-checked cleanup stopped the owned editor and no matching owned process remained. The prior Mac setup failures remain separate. Full repository Ruff check and format check passed (3,732 files), as did `mypy tools/` (993 source files) and focused `mypy --follow-imports=silent tests/job_route_vscode_fixture.py`. Running mypy directly over that test with default import traversal also surfaced six pre-existing type errors in `tests/test_needs_contract.py`; the required `mypy tools/` succeeds. Linux exact-head host and required CI remain pending.

Linux Extension Host on a63990bb62e5d711789a7ec3d326753540e7b9a3 showed the correct model-and-reasoning question and exact wording, but timed out waiting for token rotation after submission (`/private/tmp/autocode-4021-extension-failed.log`); `ci-required` was red. Its screenshot upload found no matching file, so it cannot diagnose the result. The follow-up test revision records the actual public answer action's exit and token-redacted error, post-submit view, and screenshot failure without changing product behavior. The cause remains under investigation until the new Linux host run completes.

The diagnostic Linux host confirmed the submit button opened an extra `Continue AutoCode?` modal and launched no answer command (`/private/tmp/autocode-4023-diagnostic-linux.log`). The shared client now treats only the exact current-token, save-only `--answer route-ROLE=VALUE --job-retry-token -` as the decision already submitted. Actual retries, commands combined with advancement or time changes, stale tokens, and extra arguments or environment values keep their existing confirmation policy. Chat and Run Details use the same classifier; core token and route validation remain unchanged. Two editor-surface regressions failed against the merged master and pass with the fix; focused shared-action tests and the extension suite (783 passed, 22 skipped) pass locally, as does TypeScript typecheck. The host test verifies that the default person-only confirmation setting is enabled and no additional provider call runs. Exact-head Linux extension and required CI are pending; Kilo-shaped integration is tracked separately in #4022.

The host fixture preserves the runner's inherited HOME and CODEX_HOME and asserts both values are unchanged. It keeps a private project, app-specific config, user-data profile and temporary directory, and removes live provider credentials from its child environment. The inherited-home version is for the disposable Linux host runner; local HOME-changing actions were not repeated after automatic review rejected one.
