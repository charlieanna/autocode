# Repairing command citations from authenticated receipts

Issue #1050: a Tester report can cite the wrong command or exit code even
though its retained capture receipt records the actual execution. Returning
only the mismatch message leaves the report repair without explicit receipt
authority.

The repair handoff now includes a bounded projection of the receipt after the
existing attempt or executed-event binding and output hash checks succeed.
It carries the exact argv, integer exit code and evidence reference, plus
pins for the receipt and full output. The existing read-only repair and
acceptance checks still apply; this context does not grant another execution,
turn a failed command into a pass, or accept the repaired report automatically.

Missing, oversized, changed or foreign-attempt evidence keeps the existing
handoff. Public CLI regressions cover wrong-command and wrong-exit repairs,
and reject foreign-attempt and changed-output evidence. This diagnosis alone
does not establish the cause of any historical stopped Arena attempt.
