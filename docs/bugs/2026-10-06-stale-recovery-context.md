# An abandoned attempt's recovery note outlived later work (#563)

`--abandon-stage` writes `recovery_context` telling the next stage to inspect
that partial work. The note stayed in state after a later stage saved and after
`--edit-goal` plus `--approve-goal`. Every later prompt still carried it, and a
recovery-budget pause quoted the abandon instruction as its last cause even
when a different recovery had spent the budget.

A saved attempt now retires a `recovery_context` that names some other attempt.
Approving a contract revision retires whatever note is current. The retired
note is appended to `recovery_context_archive`; nothing reads that list. The
budget pause names the latest counted recovery (its timeout reason, or a
provider-startup failure) and otherwise says the cause is unknown.

A live model cannot be asked to exhaust a recovery budget on top of an older
abandon. `tests.test_stale_recovery_context` drives abandon, a later save,
`--edit-goal` / `--approve-goal`, and `stop_reason` without a provider.
