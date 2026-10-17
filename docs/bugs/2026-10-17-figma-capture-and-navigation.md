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
