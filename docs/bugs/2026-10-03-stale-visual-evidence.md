# Stale implementation captures (#227)

The existing completion gate checked a validation report's source revision,
candidate PNG dimensions and evidence hashes. A report could name the current
revision while citing a screenshot taken before the implementation changed.
The screenshot had no independently checked source/acquisition binding.

The original live pilot withheld independent acceptance; it did not falsely
complete. A disposable gate reproduction on master `a53d775e` accepted the
unbound image after the source changed. The new public TaskRun regression
captures synthetic A, changes source to B, captures B and presents B explicitly
to the scripted Validator. That provider deliberately cites A instead. The
fixed runner refuses acceptance with `Stale implementation capture`.

For a negative control, a separate engine copy bypassed only capture verification
while retaining the remaining contract, report, replay and completion gates.
The same regression failed at its `done == false` assertion: the broken engine
returned `TASK_COMPLETE`. This demonstrates that the oracle detects the target
failure rather than passing on an unrelated early pause.

The fix adds [browser capture bundles](../visual-captures.md), selects only
current bundles for stage contexts, and rechecks them at report consumption
and completion. Each bundle ties the candidate to source, reference, case,
state, viewport, configuration, fixture, collector, actual browser response
bytes and any fresh build outputs. Missing or changed evidence refuses
acceptance. Historical attempts and failures remain available.

Five real Chromium CLI checks passed against disposable loopback projects:

- A-to-B capture selection retains A and selects only B.
- A server returning older bytes than the current local source is refused.
- A source change during teardown prevents publication and preserves artifacts.
- Generated assets need a fresh build; later output changes invalidate the bundle.
- Setup failure retains diagnostics without publishing an accepted capture.

Offline tests also cover missing manifests, tampered images/fixtures/receipts,
source changes after selection, wrong reference/case/viewport, extra historical
image citations and native Figma completion. The scripted passing path exercises
report plumbing, not image judgment; a fresh receipt never supplies visual PASS.
The real browser checks require the explicit local Playwright opt-in documented
in the guide and are skipped in an ordinary environment without it.

Those initial checks used no live models. A subsequent live OpenCode run exercised
Builder capture and independent visual inspection for both supplied viewports.
The current captures were accepted as fresh and the historical captures were
retained and rejected. Local fault injection into copies of the real review
report also rejected stale and missing capture evidence.

**The recovered live workflow reached `TASK_COMPLETE` on 2026-10-03**, using
runtime commit `ac909357`. Its independent Validator passed all ten criteria and
both visual cases after reading both current PNGs and both references. The runner
proved seven named tests fail on the original source and pass on the candidate;
independent command replay and protected-test checks also passed. The Completion
Owner accepted the result with no unresolved findings.

Getting there required the [Node named-proof adapter](2026-10-03-node-named-proof.md)
for [#295](https://github.com/charlieanna/autocode/issues/295) and a
[retained-work handoff fix](2026-10-03-retained-repair-assignment.md). The campaign
included model escalation, a timeout and explicit finite retries; this is a
recovered-run result, not evidence of first-attempt reliability. The criteria,
protected tests and proof gates were preserved.

Local fault injection into copies of the final live PASS report rejected each
historical viewport capture, a missing receipt and an extra historical image
citation alongside fresh captures. An independent browser comparison found zero
differing pixels at both supplied viewports and passed interaction, keyboard and
breakpoint checks. The live fixture used synthetic exported references, not a
remote Figma file. Raw reports and evidence remain outside the repository.

This change addresses evidence freshness; the broader inventory and fidelity
work in #250/#251 remains separate. The protocol trusts the project's declared
fixture, build command and local tools; it is not a cryptographic attestation
against fabricated acquisition records.
