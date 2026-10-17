# Form capture and incomplete prototype navigation

Playwright's default screenshot caret hiding leaves empty `style` attributes on
inputs and textareas. The capture driver's before/after DOM guard then refuses
an otherwise stable form. Keeping the caret's initial behavior avoids that
capture-induced mutation. The guard still rejects an application changing its
DOM during the screenshot. Real Chromium regressions in
`tests/test_visual_capture.py` cover both cases, alongside stale assets, changed
source and failed setup.

A Figma `NODE` action with a missing, null or empty `destinationId` previously
passed source validation when its derived target was also empty. No destination
was left for dependency closure to check. Source validation now refuses that
incomplete navigation before intake can claim coverage. The public manifest
intake regression in `tests/test_design_scope_intake.py` covers both `NAVIGATE`
and `CHANGE_TO`, plus valid navigation and legitimately target-less `BACK` and
`URL` controls.

Neither correction establishes visual acceptance. Fresh captures and independent
inspection of the approved reference and current implementation remain required.
