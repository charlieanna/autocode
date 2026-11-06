# Bounded review report repair

A live Tester report with many changed-line decisions needed a citation correction.
Re-emitting the full report changed escape sequences in unrelated captured commands;
the receipt gate correctly rejected those changes. Later repair prompts duplicated
almost the entire original and latest reports and exceeded the existing input bound.

Valid JSON reports from the Tester, Completion Reviewer and checkpoint review now
offer the existing exact-source-bound JSON edit protocol. Unedited values remain
identical after JSON decoding, and the reconstructed report still goes through all
ordinary schema, source, receipt and evidence admission. Malformed review text
retains the existing full-report protocol. No edit executes a check or grants approval.

Historical review JSON is carried as a lossless inline overlay against the complete
latest rejected report when that is smaller. Every old value and original artifact
identity stays available without tools. The instruction distinguishes this immutable
execution baseline from a superseded planning draft and preserves literal escapes.
Input/output caps and the bounded repair policy remain unchanged.

Regressions reconstruct a large baseline exactly, exercise the actual repair handoff,
verify that invented commands still fail receipt authentication, and complete an
offline CLI workflow with a small Tester edit while retaining the raw edit response.
