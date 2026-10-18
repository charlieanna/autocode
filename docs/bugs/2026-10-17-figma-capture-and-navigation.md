# Figma qualification: capture, navigation and repeated inventory work

Playwright's default screenshot caret hiding leaves empty `style` attributes on
inputs and textareas. The capture driver's before/after DOM guard then refuses
an otherwise stable form. Keeping the caret's initial behavior avoids that
capture-induced mutation. The guard still rejects an application changing its
DOM during the screenshot. Real Chromium regressions in
`tests/test_visual_capture.py` cover both cases, alongside stale assets, changed
source and failed setup.

A used Figma `NODE` action with a missing, null or empty `destinationId`
previously left dependency closure with no destination to check. The recursive
closure check now refuses it, including actions inside conditional blocks.
[Figma's action schema](https://developers.figma.com/docs/plugins/api/Action/)
retains null targets for deleted nodes and supports nested conditional actions.
Excluded original actions remain preserved with their scope reason; an unused
broken prototype does not erase unrelated approved scope.

The public manifest intake regression in `tests/test_design_scope_intake.py`
covers `NAVIGATE`, `CHANGE_TO`, nested conditionals, excluded original actions,
valid navigation, and legitimately target-less back, URL and media controls.

Planning inspection also rebuilt the design catalog after authenticating it.
`context` and `planning_input_pins` now reuse the catalog returned by that same
verification call. Each later invocation still reauthenticates mutable inputs.
The [measured correction](https://github.com/charlieanna/autocode/issues/1488)
removed one of two builds and preserved result digests on the approved two-file
bundle; its focused tests also reject reference changes after an earlier success.

These corrections do not establish visual acceptance. Fresh captures and independent
inspection of the approved reference and current implementation remain required.

## Native pre-launch validation

The retained native run stopped twice: Planner02 exhausted its900-second cap
while printing a71KB draft in tool chunks instead of submitting a final report;
Requirements03 later exhausted its300-second idle cap with an empty provider
stream. The redacted failure bundle manifest is
`b3f2e952f56301b40f5bb8d787d0daaaa5877373a675e61a62f70d4e72045257`.
No partial draft or empty stream is successful evidence.

Process samples showed JSON parsing during launch preparation. The native owner
reparsed and canonicalized every historical row on each journal read. It now
reuses that validation only for identical bytes freshly reread by the same
invocation. Its64MiB raw-byte cache never supplies ownership, path or chain
authority: lock and namespace checks, safe file reads, sequence/filename/previous
checks and external evidence pins still run. Values returned from the cache are
independent copies; changed bytes require the original strict parser, digest
and canonical-byte validation. The bound does not evict a repeatedly read prefix
just to retain a new suffix.

A controlled corpus made only from the completed public plan (24 rows,11.9MB,
12 passes) used1.298 CPU seconds without reuse and0.421 with it, with identical
row identities. This67.6% reduction measures repeated row validation, not whole
native launch time. Warm-cache tampering, renamed sequence files and lost locks
remain refused. Neither this benchmark nor the guard tests establish live image
acceptance; a repaired immutable runtime still needs actual qualification.

## Canonical regression follow-up

The full canonical gate exposed two additional refusal-path bugs after the
master update. Requirements chat now treats malformed active-stage and report
repair records as unavailable provisional text, preserving the usable public
status view. An unreadable nested Git source now makes an editable controller
ineligible for verification reuse instead of escaping identity collection as a
snapshot exception. Fresh execution remains required for that incomplete identity.

The existing liveness, verification schedule and architecture regressions plus
malformed repair projection coverage passed87 focused tests. Assignment scenarios
also reproduced spurious2-second startup timeouts on different cases; only their
successful fake-process fixture budgets changed. The actual hung-worker case
keeps its original3-second stage and2-second idle/tool bounds. All24 assignment
scenarios then passed. These results do not turn the retained failed full gate
into a passing full-suite receipt, or establish native image acceptance.
