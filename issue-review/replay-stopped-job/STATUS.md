# Recorded stopped-job route (#4005)

A saved replay could ask for a new model after the original workflow job had
already recorded a route answer and retry, or stop because those actions were
not exported. The correction exports the recorded semantic actions and applies
only the matching fresh public action after the current stop, source, route and
next dispatch pass the existing retry gates. It preserves fresh tokens and does
not query a live provider for model availability.

This is a conservative slice. An incomplete action sequence, a retry without a
preceding recorded route, an ambiguous historical effort needed to repair drift,
a changed source, or a dispatch with source writes remains held. Multi-route
and other workflow recovery forms have not been qualified. Existing recordings
are not rewritten. The fake-provider observation is not live-provider evidence.

The real offline source-bound route and retry, altered-source and mismatched-stop
controls passed; the incomplete-action tests exercise a synthetic projection, and
the historical-effort test isolates the decision with a mocked projection and
source-check. No synthetic projection is represented as an authenticated tape.
The 33 existing stopped-job tests and 105 replay/architecture tests passed. An
initial synthetic legacy control failed at tape integrity, before the decision;
the corrected decision-only control passed. A fresh real public replay reached
the original design-conflict pause without a model call. In the actual macOS
VS Code host, the editor applied the exact recorded answer and retry, consumed
both dispatches, displayed the saved stopped result, and cleaned up the owned
processes. Earlier host attempts exposed a temporary-path spelling mismatch and
an overly specific UI assertion; both attempts and evidence were retained.

This result does not establish Linux editor behavior or exact-head CI. Current
master integration, independent diff review and required CI remain outstanding.
