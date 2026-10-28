# Empty planning repair input (#2426)

Live TUI run `20261028-165106-chat-887793` on
`bc1dec6a07c0c4f5081d3924d814ac111b484b65` returned malformed revision JSON,
then a terminal report-only response without final text. The original revision
took 60.809 seconds and the empty repair 28.842 seconds. The run stopped at
`PAUSED_REPORT_REPAIR_INPUT` after 390.516 active seconds, before implementation.
Retained bundle manifest:
`b0a0fdf71fbcd6895d5d447fa15c34b4d525934f1787c5e9d98222ae89490d9b`.
This did not exercise #2422: malformed JSON is ineligible for bounded field edits.

The next repair unconditionally loaded the empty latest response and stopped,
even though the nonempty original and one of two repair attempts remained.
The empty attempt now remains an explicit input descriptor; the next planning
repair gets the original text and its parser diagnostic, current review and
protected contract. No JSON is guessed or accepted locally. The ordinary report
validator still rejects empty output. Original-empty, missing, oversized or
changed inputs still stop. Existing attempt accounting and limits are unchanged.

On explicit Resume of an older hold, a completed empty repair with a nonempty
original and a remaining repair allowance keeps its pending repair. It does not
restart the original planning stage. Normal dispatch rechecks source pins,
workspace, contract and bounds. Timeout/interruption recovery is unchanged.

The public regression makes a malformed original, an empty first repair and a
corrected second repair reach exact-plan approval without Builder or repeated
revision. Negative controls retain the stop for an empty original and exhausted
empty repairs. A saved-checkpoint qualification creates the hold with unmodified
master and resumes it with the candidate through public CLI commands: one
revision, two total repairs, original attempt identities retained, no Builder.
These are deterministic recovery checks, not proof that a live model will return
a valid next report or that overall task latency is solved.
