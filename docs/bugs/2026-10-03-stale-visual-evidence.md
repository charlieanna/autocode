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

No live model or Figma calls were used. This resolves evidence freshness, not
the broader inventory and visual fidelity work in #250/#251. The protocol trusts
the project's declared fixture, build command and local tools; it is not a
cryptographic attestation against fabricated acquisition records.
