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
