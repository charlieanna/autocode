# Source inventory refusal after a completed stage (#1507)

The real VS Code / Kilo greeting qualification reached its revised Plan Reviewer
result, then `git ls-files` refused the macOS-mounted Linux fixture with
`fatal: detected dubious ownership`. The subprocess exception escaped as a Python
traceback and left the public view showing a finished stage as running. This was
an environment refusal; the application still needed to represent it reliably.

Git inventory and HEAD failures now raise `PAUSED_SOURCE_UNAVAILABLE`, classified
as an environment problem. The diagnostic includes the failed command and Git's
reason, and asks the operator to fix access before resuming. No empty inventory
is accepted and no Git trust setting is changed. The public view offers
`--resume-paused`, retaining the completed report for the existing reconciliation
checks instead of asking the operator to abandon it. Those checks still decide
whether the report can be reused.

## Focused verification

- Snapshot, content-hash, pause-category, explanation, public-view and architecture
  checks: 101 passed, 23.960 s. After changing the resume projection, its 25 view
  tests passed again in 0.470 s.
- Supported Linux public CLI fault injection: one test passed in 48.415 s. Git
  refuses immediately after a completed Plan Reviewer report. A resume while the
  refusal remains pauses again without launching any provider; after access is
  restored, recovery preserves every existing report and does not replay the
  reviewer. The subsequent finalize stage runs and exposes plan approval, with
  no implementation started. This uses the offline fixture provider, no models.
- Ruff lint and format checks and `git diff --check` passed.

The Linux command was `python3 -m unittest
tests.test_chat_cli.ChatCliTests.test_source_inventory_refusal_retains_the_report_and_resumes_without_another_provider_call`
in image `autocode-pr1425-qualification:20261016`, with `--pid=host
--security-opt seccomp=unconfined --cap-drop ALL --network none`, user
`autocode-test`, read-only source and Git metadata, and a container-native fixture.

Retained failed attempts: a missing evidence directory failed setup in 0.005 s;
a macOS-mounted fixture encountered Git ownership refusal before the injected
stage in 12.935 s; the initial public view incorrectly offered abandonment
(16.065 s); an overly broad test assertion rejected the legitimate following
finalize stage (25.056 s). The final regression explicitly distinguishes a
replayed reviewer from that new stage. Evidence is ignored under
`.scenario-runs/app-release-qualification-20261016/evidence/issue-1507-native/`.

The original live run used runtime `401c62e6` and was recovered by trusting only
the exact task-owned fixture directory inside its disposable container, then
pressing native Continue. That allowed its retained report to reconcile without
another model request. It does not qualify this newer runtime or establish a
complete live feature pass. No additional paid run was needed to reproduce and
verify this error handling.
