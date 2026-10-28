# Repeated Kilo launch-policy resolution (#2439)

The completed paging TUI run `20261028-211621-chat-154a01` on master
`b1e1fb3abf863c3fcf699be5fdfdf33c4ef4a65a` used Codex Medium and GLM-5.3 Low
through Kilo 7.8.3. Its retained bundle manifest is
`c27a115df40e368c516d47088af44895bd2667b1fc603a02097544272998c079`.

Postapproval setup included a selected-agent lookup, an intermediate lookup of
the generated restricted agent, then a final native observation after the shell
boundary was installed. The intermediate observation only reconstructed the
bash policy already computed from the fresh selected agent. Before Tester and
Completion Reviewer, those two wrapper lifetimes totalled 6.65 seconds; their
start-to-next-start spacing totalled 5.22 seconds. The observer sampled at 0.2s,
and these are wrapper intervals, not pure inference or guaranteed time saved.
The complete 61.8s metadata interval also includes necessary conformance work.

The fix passes that ordered, launch-local policy directly into containment and
checks it against the generated agent definition. It retains the fresh operator
lookup, version qualification, final effective-policy and exclusive-bash checks,
private-store denial, kernel receipts and toolchain probes. Any final mismatch
refuses before dispatch. No cache or saved-state field is added; later launches
resolve their operator configuration anew. OpenCode and paths that do not supply
this narrow Kilo handoff retain their baseline observation. Permission comparisons
also preserve native last-match rule order.

The regression calls the real restriction/containment seam with deterministic
native observations. Original code fails because it performs the extra generated
agent lookup. Positive and negative cases cover policy order, malformed/stale
handoffs, changed next-launch settings, exposed tools and failed conformance
probes. Original Kilo qualification runs in an offline Linux container with fresh
fixture homes and no account credentials. No paid model call is required to
establish the permission and subprocess behavior; total task speed remains #2113.
