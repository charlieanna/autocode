# A format failure in repair hid the original validation error

A completed Tester report was rejected because one changed-code line had no independent
exception decision. The first report-only repair returned commentary instead of JSON.
The next repair received the original report, but only the newer format error. It restored
the original JSON without addressing the missing decision, causing unnecessary Builder
rework while the evidence gate correctly continued to refuse acceptance.

The queue now records the original rejection alongside the pinned original attempt. A
later repair receives that diagnostic as `original_error` and the latest rejection as
`error`. The prompt distinguishes their drafts: an earlier rejection does not establish
that a later draft is still defective, and it must not undo valid corrections or restore
superseded planning decisions. Legacy pending records without the original diagnostic do
not invent one. Existing source pins, evidence admission and the two-repair bound remain.

The focused handoff regression fails on the base revision because the original diagnostic
is absent. A public-CLI regression injects an invalid evidence citation, then a non-JSON
first repair. The second repair must recover the original diagnostic, fix the citation
against the enclosing proof, and advance only the checked milestone without another Builder.
The same CLI regression fails on base with `PAUSED_INVALID_OUTPUT` after both repairs.

Validation: the affected Linux selection passed 647 tests in 37 modules, including the
public-CLI regression, repair handoff, stage repair, report delivery, replay, architecture
and run-state checks. Ruff lint and formatting passed. This change makes no claim that an
entire live heavy workload has completed.
