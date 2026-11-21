# Kilo and OpenCode stopped-route effort follow-up

Base: master `5335ff6e6895b1203c42bc650a37119d380ac744`. Draft work for
#4005; the issue remains open until its remaining behavior and required checks
are verified.

When a saved model stop has a changed reasoning level, the existing free-form
route question can be offered after a bounded structural check. The current
code needlessly holds Kilo/OpenCode here, even when the exact saved model and
level are a structurally valid answer. The status observation uses the saved
provider's configuration and does not list models or claim availability. On
submission the existing locked route admission still validates the selected
model with that provider and checks the source, token, configuration and
independent-review constraints. The provider check uses the existing owner-approved
one-hour successful-preflight reuse policy, so it need not perform a new listing.
It does not silently change the saved route.

An authenticated read-only Kilo roster returned both configured model families;
the existing provider check took 2.607 seconds. No model was called. This is why
the status poll must not add a provider roster query. The public CLI regression
uses a synthetic Kilo-shaped saved stop and local scripted binary, not a live
Kilo inference or proof of actual editor behavior. It checks that an unavailable
answer is refused without changing run state, and accepts the valid answer under
a different explicit provider environment and therefore a separate preflight
key. A prior two-stage fake roster control remained unavailable on its second
invocation because the existing cache retained its first successful roster.
That failed attempt is preserved; this follow-up does not alter the cache policy.

The CI selector also retains the public route, needs and status tests for future
changes to the shared retry-admission module. Its regression fails on the base.
The bounded status worker resolves the saved run-wide facade even when the
stopped role uses Codex, and holds replay routes until their facade can be
reconstructed safely. Mixed-role saved transport changes also hold.

At f9b2df001, 228 focused retry/status/architecture tests passed. After the
transport-parity changes, 374 focused retry/status/architecture/CI-selection
tests passed on c86720319; subsequent e24c3863f changes only fake audit logging,
whose public CLI test also passed. Repository Ruff, format and mypy passed.
At e24c3863f, the Ubuntu suite, lint and advisory diff coverage passed, but `ci-required` failed with
the actual Linux editor check:
the synthetic Kilo question appeared and status made no model listing, but the
token did not rotate after its answer. The existing Codex host test failed at
the same step. Full log: `/private/tmp/autocode-4022-extension-e24.log`. The
GitHub job is <https://github.com/charlieanna/autocode/actions/runs/44889392491/job/136801825878>.
The test uses a local fake Kilo provider, never a live model call.
An automatic approval review rejected an earlier smoke that changed HOME, and
that action was not run. Status-only controls with inherited HOME passed.
The separately diagnosed #4023 editor defect showed an extra confirmation
dialog after a typed stopped-job answer; #4023 corrected the shared path.

After integrating #4023, the Linux Extension Development Host at 6645afdfa
passed the synthetic Kilo answer end to end: the route was visible, the named
answer rotated the retry token, and the fake provider logged no model run. The
same host job failed the Codex test before submission because it checked its
visible text during the transient "Opening this chat" state. The failed job is
<https://github.com/charlieanna/autocode/actions/runs/44890540651/job/136805417902>;
`/private/tmp/autocode-4022-extension-6645.log` preserves the full output.
This is scripted Kilo-shaped coverage, not a live Kilo model task. The required
gate and final integrated-head host outcome remain open.

At 3720b97a1, both editor answers timed out. The Codex action log confirmed
the CLI rejected the answer because a run-referencing process was active; the
Kilo test had no action log and its cause was not established. The full job is
<https://github.com/charlieanna/autocode/actions/runs/44891347985/job/136807776432>.
The separate Linux containment job failed both Rustup test variants because
the contained `cargo --version` could not create its read-only update-hash
directory; 88 of 90 tests passed. Its log is
`/private/tmp/autocode-4022-3720-linux-containment.log`. The Rustup fix is a
separate workstream; do not infer that its prior missing-executable error and
this read-only-directory error are identical without verification.
The Kilo fixture now uses the same forwarding diagnostic as the Codex fixture.
On a busy refusal the wrapper takes a best-effort process sample after the
CLI returns: PID, creation time, command hash and safe status-option category.
The sample can be absent or describe a reused PID; it grants no authority and
does not establish which process the earlier guard observed.

The local integration now includes #4035's verified editor refusal visibility
and its shared host-fixture CI registry. The Kilo fixture and its shared CLI and
cleanup helpers are registered as consumers of the Kilo editor test. On this
integration 228 focused job-route, status-observation, diagnostics and
CI-selection tests passed with process enumeration available; the log is
`/private/tmp/autocode-kilo-integration-focused.log`. A sandbox-only run was
unable to enumerate processes and refused test setup; its single-test control
is `/private/tmp/autocode-kilo-integration-sandbox-failure.log`. Ruff and host
JavaScript syntax checks passed. These local results are not final-head CI or
actual editor verification; #4024 and #4031 are still being qualified.

The Kilo fixture also uses the shared, merged #4036 private-host setup and
all-steps cleanup. It selects its private AutoCode registry before its first
subprocess, validates the returned owned directory and retains failed test
fixtures. A stubbed callback control for early Kilo fixture failure fails on
the prior Kilo host because it targets an inherited host, and passes here; the
baseline log is `/private/tmp/autocode-kilo-host-early-baseline.vFHNIp/result.log`.
The offline fixture succeeds with its private registry preselected. This does
not establish the final actual-editor result.

Still required: focused checks and master control, actual VS Code behavior on
the final head, current-head `ci-required`, and review of unsupported or absent
structural candidates. Do not close #4005 on this partial increment alone.
