# Live queue qualification: Pause, idle limits and recovery

Issues: [#1561](https://github.com/charlieanna/autocode/issues/1561),
[#1562](https://github.com/charlieanna/autocode/issues/1562),
[#1563](https://github.com/charlieanna/autocode/issues/1563).
Related editor acceptance: [#1528](https://github.com/charlieanna/autocode/issues/1528).

## Recorded failure

The native VS Code development-host queue exercise used core `55f4bedd`, Kilo
CLI 7.8.3, Z.ai GLM-5.3 and Xiaomi Singapore MiMo-v2.6-pro. A 79-minute attempt
and a fresh 50-minute medium-reasoning attempt both stopped before Builder.
The first used 3600 active / 900 stage / 300 idle / 900 tool seconds and a $5
cap; the second used 2700 / 600 / 300 / 600 seconds and a $2 cap. Requirements
and Builder were medium in the first attempt; planning/review roles were high.
All reasoning settings were medium in the second. Accounting was incomplete;
historical estimates are not token-plan account bills.

Redacted bundle manifest hashes (archives/logs are not committed):

- `20261018-052917-vscode-cdc786`:
  `9d708d11273d594003364127930b111b4857feb5c2b5bb131466cecc17d5b9b4`.
- `20261018-065129-vscode-0b0548`:
  `d923fce5df0ecb8177bf16f1e1e23bb7d99d91cc7275e0a43d5b95b02aeedc08`.

## Corrections

An applied Pause coexisted with a deferred goal-approval proposal. Persistence
adjudicated that proposal and overwrote the pause with Running. Normalization
now retains the proposal and reasserts the unacknowledged parent Pause before
adjudication. Worker request cleanup is unchanged. Explicit resume acknowledges
the pause, allowing review to continue without approving the plan.

Two timed-out reviewers emitted only a Kilo `step_start`. The watchdog treated
that lifecycle event plus a persistent provider child as a running tool and
masked the explicit 300-second idle limit. Lifecycle events now renew idle
once but cannot establish process-fallback tool evidence. Substantive progress
and real tool events retain their existing deadlines. This fixes that bounded
inactivity policy; it does not establish why MiMo was silent or guarantee that
planning will complete.

Structured HTTP 401 failures now produce a fixed authentication diagnostic,
without echoing response bodies or credentials. Standalone access probes stop
with `PAUSED_AUTH` before probing another model. A launched stage retains its
uncertain-result reconciliation authority and cannot replay automatically.

Chat emits one operational request with its recorded cause and the effect of
replying. Routed or incompletely bound questions retain their existing message.
The editor renders the core's intentional Stop explanation and offered new
conversation, distinguishes exact reconciliation/resume actions, and removes
the operational reply's misleading continuation promise. Run details opens the
conversation for an offered reply. Polling changes take effect without reload;
timer/listener disposal remains explicit.

## Verification

Tests use saved error/event shapes, fake clocks/process inventories and offline
providers. No new live model run or credential transfer was needed.

- 57 pure activity, idle-policy and mocked supervisor tests passed.
- 44 authentication and model-preflight tests passed.
- 11 Pause/Stop persistence tests and 11 final chat projection tests passed.
- 47 architecture, run-state and CI-selection checks plus 35 suite/CI policy
  checks passed. Pinned Mypy passed on all seven changed runtime/selection files.
- 145 extension tests passed; after the final Run details navigation change,
  nine focused recovery/timer tests and typecheck passed. Production packaging
  passed. Pinned Ruff lint/format checks passed on changed Python files.
- Supported Linux fake-provider checks passed: both authentication CLI flows,
  five process/watchdog/cleanup cases, and all three existing chat CLI cases.
- The new public Pause CLI regression passed in 12.967 seconds: no provider
  while paused, one explicit resume to fresh plan approval, no implementation.

The initial eight-case Linux command took 76.452 seconds: seven passed; the
new Pause fixture failed because it cleared a token while retaining a completed
cycle. A second diagnostic run confirmed that inconsistency. The final fixture
uses the existing planning restart operation to create an unfinished cycle;
the runtime guard did not change. The three chat CLI cases took 75.488 seconds.
Negative controls exposed the original Pause, lifecycle-only fallback and 401
diagnostic failures before their fixes. The same public Pause regression failed
in 9.755 seconds in a separate Linux copy with only the new guard removed.

The production VSIX SHA-256 is
`ced84c349aaff181046bedbc6371dd15c26eeb5e17e397d4449377f11d20e535`.
Native editor re-verification and a completed live queue flow remain unverified
after the earlier computer-control outage. These repairs do not close #1528's
full request-to-completion and follow-up acceptance.
