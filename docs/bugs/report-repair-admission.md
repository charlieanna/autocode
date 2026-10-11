# Report repair admission (#846)

The bounded allowance is checked before a repair provider is dispatched.
`reject_completed_stage` stops queuing repairs when the allowance is spent;
`execute_report_repair` checks again before even the same-session correction.
A default run admits at most two full report-only repairs per rejected stage.

The public CLI regression in `tests/test_stage_repair_cli.py` uses the offline
provider to return three distinct schema errors: one original report and two
repairs. Distinct errors prevent the consecutive-failure guard from stopping the
case early. The run pauses without approval or building, and its provider trace
contains exactly two repairs. Invoking the paused run again buys no provider call.
A third repair would succeed deliberately, so an off-by-one admission changes the
observable outcome and cannot pass this regression.

This answers the exhausted-allowance question. It does not predict an unseen
model response: an admitted repair may still return a changed finding disposition
that the ledger refuses. Its refusal depends on the returned report, not merely
the available allowance. The existing provenance rules and row-copy instruction
remain unchanged; no new permission or unbounded repair is introduced.
