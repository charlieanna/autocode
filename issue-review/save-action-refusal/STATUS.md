# Save-action refusal visibility

Base: master `f5752a7e94d51bdc5ca7b5ee3982b646f1d2a103`. Draft; actual
extension host and exact-head CI are still required.

The Linux host run for #4031 showed an actual named saved-model answer exit 2
with `autocode: Existing run process 4448 is active; leave it untouched`, but
the sidebar showed no error. `RunController.take` accepted every unclassified
exit 2 as a pause. The same result handling was shared by bound raises and
failed-job time controls. In that test, PID 4448 was the deliberately held
synthetic writer; the action log matched the refusal to its child PID. The
earlier #4022 PID 4491 was not identified. Do not infer its identity or clear
an owner.

The public TaskRun contract says save-only answers, approvals, feedback and
limit changes return 0 on save and 2 on refusal. Only those documented named
decisions are classified as save-only; advancing and `--reconcile-only` keep
their existing pause/reconciliation exit-2 behavior. The CLI adapter also uses
TaskRun's established line-start refusal envelopes on either output stream and
ignores only the exact capability-drop notice. Its bounded error detail keeps
the refusal reason first even when the other stream has a warning or later
output is long. It does not grant new authority.
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

The offline host regression now holds a process with writer-shaped real CLI
argv, submits the exact saved-model answer and requires a visible refusal and
unchanged token, then releases the owned process and requires a successful
answer/token rotation. The held process stops at an injected test barrier and
does not execute the synthetic writer action; no model is called. The test
waits for the second action to finish before its status poll, so it does not
depend on the separate, unmerged path-first observer guard fix (#4031).
HOME and CODEX_HOME remain inherited in this new host scenario. The actual Linux
VS Code host passed all six cases on head 55aeff699, including the visible
held-writer refusal and the later accepted answer; log
`/private/tmp/autocode-4035-55ae-editor-success.log`. That head's Ubuntu suite
and required CI failed: both Rustup containment controls hit a read-only
`~/.rustup/update-hashes`, tracked separately in #4024. The exact log is
`/private/tmp/autocode-4035-55ae-suite.log`; this head is not green.

The guard fixture is dynamically loaded by the extension host. The shared CI
resource registry now names that consumer and the separate Rustup host fixture,
so editing either fixture alone selects the extension job. Python selection
continues to include actual imports and path references while not conflating
unrelated nested `sitecustomize.py` files. Both new controls fail on 55ae and
pass here; 240 CI selector tests, Ruff and mypy pass. Fresh exact-head CI and
the actual host are required after the qualified Rustup correction lands.
