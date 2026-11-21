# Save-action refusal visibility

Base: master `f5752a7e94d51bdc5ca7b5ee3982b646f1d2a103`. Draft; actual
extension host and exact-head CI are still required.

The Linux host run for #4031 showed an actual named saved-model answer exit 2
with `autocode: Existing run process 4448 is active; leave it untouched`, but
the sidebar showed no error. `RunController.take` accepted every unclassified
exit 2 as a pause. The same result handling was shared by bound raises and
failed-job time controls. The process identity in that old log was not retained;
do not infer what process it was or clear an owner.

The public TaskRun contract says save-only answers, approvals, feedback and
limit changes return 0 on save and 2 on refusal. Only those documented named
decisions are classified as save-only; advancing and `--reconcile-only` keep
their existing pause/reconciliation exit-2 behavior. The CLI adapter also uses
TaskRun's established line-start refusal envelopes on either output stream and
ignores only the exact capability-drop notice. It does not grant new authority.
The controller is shared by sidebar, Run Details and extension commands.

Six targeted tests failed on base (sidebar, Run Details, answer, approval,
job-time and save-only bound raise), while advancing and reconciliation
controls passed. The initial broad implementation incorrectly treated all
non-advancing actions as save-only and failed two existing automatic supervision
tests; `/private/tmp/autocode-save-refusal-npm.log` preserves that failure.
The narrowed version passes the full extension suite (793 passed, 22 skipped),
typecheck, full Ruff/format and mypy. The headless refusal-envelope regression
also failed on base and passes after the alignment. The tests mock failures;
they do not establish the actual editor outcome.
