# OpenCode auth-summary startup deadline (#539)

A native OpenCode 1.18.33 run using OpenAI Sol and Astra paused before its
Completion Reviewer at `PAUSED_BILLING_ROUTE`. Six earlier model streams had
completed. The production auth-summary check retried three cold starts with a
15-second limit each; all timed out (47.93 seconds including cleanup). A separate
read-only diagnostic of the same CLI completed in 31.84 seconds and reported
OpenAI OAuth. No credential file was opened, model route changed, or paused run
resumed.

The fix shares the existing 45-second subprocess allowance across at most three
attempts. One cold start can now finish within that allowance; an early transport
failure can retry with only the remaining time. A completed negative or ambiguous
summary is still final, and exhausting the deadline still prevents a model
request. Process creation and cleanup can add overhead beyond the subprocess
waiting allowance.

Deterministic tests cover a 32-second simulated cold start, decreasing retry
budgets, deadline exhaustion, and the existing rejection and explicit-route
rules. The real native command through the candidate completed in 36.20 seconds
with exit zero and OAuth. This checks the provider startup path; it is not a
completed live-model campaign. On master e042daaf, the affected gate passed
254 tests (10 opt-in skips); the fake catalog exited zero with 60 PASS, the
existing refund-window NOT_EXERCISED and the live-only Investigator SKIPPED.
Architecture checks also passed (4 tests).

An earlier affected gate on ba3fd4bb had six CLI timeouts at unchanged limits.
A clean old-master control reproduced the first timeout. Those failures remain
retained; the subsequent current-master pass does not establish that every
earlier timeout had the same cause.

Fresh live qualification on implementation d82b9a47 used native OpenCode 1.18.33
with Sol for requirements, planning and building, and Astra for plan review,
testing and completion. Run
`20261006-101946-fix-greet-py-so-greet-name-world-returns-exactly-36873f6e`
reached TASK_COMPLETE in one iteration and 628.77 active seconds. All six native
streams reached EOF without dropped events. The original 1,800-second active
limit and 360/120/120-second stage/idle/tool limits remained unchanged.

The public evidence inspection found all eight criteria checked on the current
source. The runner proved four failing-before/passing-after cases. The independent
eight-input oracle and protected-file checks passed; all 26 terminal audit checks
passed, including unchanged runtime/native binary, current proof, original
limits and no remaining owned workers. Each native stage used the pinned
provider's auth preflight. The live greeting fixture isolates authentication
startup from the separate optional-fixture-skip defect in #416; its exact
class-skip fixture still needs its own after-fix qualification.

Evidence retained outside Git: `verification-539/openai-after-r1`,
`terminal-audit-r1.json`, and `autocode-539-live-after-oracle-r1`. The native
slow-start diagnostics and deterministic deadline tests establish the timeout
behavior; the fresh full TaskRun establishes integration with real models.
