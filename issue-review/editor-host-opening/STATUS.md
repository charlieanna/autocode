# VS Code host History setup

Draft. The Linux extension-host job for #4004 failed when its chat webview
was detached during the History setup action. The suite now reacquires the chat
frame when that specific failure occurs and accepts success only after the
actual target Runs row is visible. It retries at most three times, preserves
unrelated errors and opens the row once. Four deterministic helper tests pass;
no timeout or sleep is used by those tests.

The unmodified host suite also failed on this Mac while Playwright waited to
scroll the AutoCode activity item into view. An isolated diagnostic waiting for
the item and dispatching its event instead timed out in the renderer; other DOM
inspection calls also stalled. Logs are retained at
`/private/tmp/autocode-status-retry-host-harness-control-proper.log` and
`/private/tmp/autocode-host-opening-candidate.log`. Four earlier #4004 editor
setup attempts likewise did not reach the chat, so neither job nor repeated
failure timeout/recovery has an actual-editor result. The temporary editor hosts
were stopped; the previously approved workspace and wrapper were not modified.

The helper's fake controls do not prove the actual Linux editor or the Mac
renderer recovered. Run the real Linux host CI and keep #4004 draft until its
own timeout and recovery are visibly observed in the actual extension.
