# Preserve capture receipts in native tool output

A live Go Arena attempt redirected the capture helper's stdout to `/dev/null`,
then printed only selected receipt fields. The receipt file and full test log
remained present, but the native capture event no longer contained the receipt.
The Tester and two report repairs were rejected with
`No independently executed Validator tool event matches receipt`.

OpenCode event providers now receive explicit guidance to retain capture's
complete JSON stdout. Capture already saves the underlying command's full
output in its separate log. Filtering the receipt removes the evidence needed
to authenticate it. Reading an earlier receipt is not another test execution.

The receipt verifier remains strict. Regression coverage accepts a complete
native receipt and rejects suppressed or filtered output while preserving the
saved receipt and log. This guidance does not repair historical native events,
relax clean-checkout replay, or establish that the Go implementation passes.
