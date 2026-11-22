# Stopped job with an unset current effort (#4005)

Base: `bf75a2c9b25c5b2f216bda9b8bbfb2c9e2b96c7e`. This is an
incremental fix; #4005 remains open for its other unresolved cases.

An offline scripted-provider public run stopped on quota with an exact saved
model and effort. In a test checkpoint, removing the current role's effort and
the advisory candidate list caused master to show `recover_source`, although
its existing token-bound answer for the saved model and effort succeeded without
another model call. A missing or null effort represents no requested level in
`autocode_route_fit.fit`. The status preflight now recognizes that state along
with the already-supported named levels; an unrecognized current value remains
held. The submitted answer still passes the existing concrete token, source,
configuration, provider, and effort checks under lock.

The supervised real-CLI regression fails on the base at the public status
assertion after the same answer succeeds. It passes on the candidate for both a
missing key and an explicit null, including stale-token refusal and unchanged
model-call count. Five focused stopped-job tests passed, including existing
source/configuration, unsupported-effort, refusal and queued-intervention
controls; the CI scope suite passed 34 tests. The synthetic Kilo-shaped editor
fixture was preflighted through the public CLI with the current effort missing:
its route appeared, the answer rotated the token and restored the effort, and
no model was launched. The fixture uses a local scripted provider; it is not a
live Kilo run. The actual VS Code host and exact-head CI remain pending. Replay
provider routes are unchanged and are not claimed fixed.

Independent review requested retaining the explicit string type check for named
levels; the final correction does so. The final focused rerun passed three
tests, including malformed current values and both unset representations.
The adjacent status/admission matrix passed 45 tests. Repository Ruff and format
checks and mypy (996 source files) passed. The saved red control is
`/private/tmp/autocode-4005-master-red.log`; local preflight and test logs are
unpublished diagnostic artifacts, not repository proof receipts.

Local macOS host attempts have not qualified the editor result. The first
stopped before editor launch because Python resolved `/var` to `/private/var`
while the host compared the uncanonicalized temporary root; the fixture now
canonicalizes its owned root before comparison and cleanup. The next attempt
stopped before launch because the local dependency directory lacked
`playwright-core`. With an existing matching dependency supplied read-only,
VS Code launched but its Electron target closed before `firstWindow`, with no
answer submitted. The three failed attempts and their fixture roots were
retained; Linux extension-host CI remains the required editor observation.
