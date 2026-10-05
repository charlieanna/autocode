# Citation-only repair rejected an original verified finding resolution

A fresh Validator can execute passing checks and resolve its earlier finding,
then have its report rejected because an unrelated artifact citation is missing.
The report-only repair previously rejected even an unchanged resolution from that
fresh review. Two otherwise valid repairs then exhausted the allowance and left
the finding open.

An offline public-CLI reproduction on merged master `ceb3314f` demonstrated the
gap: the control completed; the fault case executed the same passing check, changed
only the bad citation during repair, and stopped on “A report-only repair cannot
close findings.” No run-state injection was used. Original evidence remains under
the ignored `.scenario-runs/20261001-post215-repair-repro/` directory.

The runner now derives `preserved_finding_dispositions` on its temporary original
stage record from the hash-pinned, completed original review. The findings ledger
reads this source-specific allowlist and permits only exact unchanged disposition
rows. It still enforces reviewer ownership, reviewed scope and passing verification.
Incomplete, blocked or previously repaired reviews cannot supply this allowlist. Repairs
cannot introduce a closure or change its identity, action or verification text.

This gap was investigated after four fresh Codex-only OpenCode trials on the same
master: the feature and ISO-week bug-fix cases completed (6/6 and 5/5 oracle checks),
while two TODO runs stopped with correct application output (10/10 each). Their
observed stops were invalid artifact paths/permission recovery and weak generated
tests exhausting the pinned milestone retry policy. Those runs did not reach the
disposition guard, so this fix is not claimed to cure their recorded stops.

No trial emitted an output-token-limit `length` signal. Requirements-stage
truncation remains unreproduced in this campaign and was not changed.

## Follow-up: an edited closure row (#459, 2026-10-05)

In live run `8soi9a5s` the repair prompt said "Correct format and evidence
citations", so a Validator report repair also rewrote the evidence inside a
closure row (`check:1` became a receipt path). The guard refused it with a
message that named no row, the next repair dropped the closures, the finding
stayed open, and the validation and repair cycle repeated. The repair prompt now
says to copy each kept row byte-for-byte, evidence included, to omit a row
rather than edit it, and to omit one whose cited `check:N` it corrected or
renumbered. The refusal names every refused row of open findings, with the
fields that differ, or says that the original review cannot authorize any row
(then no copy, however exact, would be kept). The guard itself is unchanged:
only exact rows from a completed, nonblocked original review survive a repair.

This reduces churn only when the repairer follows the prompt and the refusal.
A repairer that keeps editing or dropping the closure still cycles as before
(in the CLI fixture: two Validator rounds, four repairs, then completion is
refused and the run pauses). Bounding repeated closure refusals belongs with
the report-repair limits in #446.
