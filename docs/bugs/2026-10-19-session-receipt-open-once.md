# Session ownership receipt read, 2026-10-19

The native VS Code qualification run `20261019-110001-vscode-abfc35`,
on core `12ef80f17ad557e8335c6c8b5418765e6fd9aa74`, stopped its
`001/acceptance-tests-02` Tester attempt after 8.315 seconds with
`receipt_phase=read; receipt_reason=not_found`. Its event file was empty;
no test or Builder result was delivered. The redacted bundle manifest is
`77b0e011aa6f03c13f26be9b8a33b0e39e0320dddefbec9e7b52dc307980072e`.

The diagnostic means the pathname size check succeeded and the later text
read raised `FileNotFoundError`. A subsequently observed terminal receipt
was present and matched all launch bindings. The keeper publishes through a
same-directory temporary file and atomic replacement; source inspection found
no target-unlink gap in that writer. The responsible filesystem operation and
the cause of the transient absence remain unproven.

The reader nevertheless had two independent pathname lookups and an unbounded
read after checking size. #1662 opens once, checks that descriptor with
`fstat`, and reads at most the existing size limit plus one byte from it.
Default text decoding, schema and nonce/owner/keeper/provider comparisons stay
the same. Missing-at-open, unreadable, malformed, oversized or unbound receipts
still fail closed. There is no retry, extra grace, fallback authority or cleanup
change. This removes the demonstrated check/open race; it does not establish
that the live filesystem incident is fixed.

Deterministic controls remove, replace or grow the receipt once immediately
after the size check in both implementations. All three fail against the old
reader and pass with the descriptor reader. A Linux public-launch control uses
a real child session handoff and removes the opened receipt at the same point:
the old reader terminates the provider, while the new reader completes and
cleans up normally. Negative controls retain foreign-binding, absent, malformed,
size-limit and one-read failure checks. No live model traffic was needed for
this focused fix.
