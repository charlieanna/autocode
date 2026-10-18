# Applied Pause survives deferred plan approval

Issue: #1561; editor acceptance: #1528.

In live run `20261018-052917-vscode-cdc786` on `55f4bedd`, an applied Pause
coexisted with a deferred goal-approval proposal. Normalization adjudicated the
proposal and returned the same controller to Running without a resume. Redacted
bundle manifest SHA-256:
`9d708d11273d594003364127930b111b4857feb5c2b5bb131466cecc17d5b9b4`.

Normalization now holds an unacknowledged parent Pause before adjudication,
retaining the proposal and saved evidence. Worker request cleanup and terminal
Stop stay unchanged. Explicit resume acknowledges the Pause and resumes review;
it cannot approve the plan. No state keys, tokens or stage IDs change.

Validation: 11 pure approval/Stop persistence tests passed. On the supported
Linux host, `python3 -m unittest tests.test_paused_approval_cli` passed in
12.967 seconds: paused advance launches no provider, one explicit resume reaches
fresh approval, and no implementation is created. Removing only the new guard
in a separate Linux copy makes the same public test fail in 9.755 seconds.
The initial fixture incorrectly retained a completed review cycle after deleting
its token; the final fixture starts a real unfinished planning cycle.

The regression is mapped into focused CI and classified slow by its measured
Linux duration. Architecture, run-state and CI policy checks passed in the
combined qualification candidate. No additional live models were called.
