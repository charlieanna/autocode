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

Still required: focused checks and master control, actual VS Code behavior on
the final head, current-head `ci-required`, and review of unsupported or absent
structural candidates. Do not close #4005 on this partial increment alone.
