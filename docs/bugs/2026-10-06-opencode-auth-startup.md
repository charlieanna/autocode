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
Fresh live-model qualification is still pending; no PR has been opened.

An earlier affected gate on ba3fd4bb had six CLI timeouts at unchanged limits.
A clean old-master control reproduced the first timeout. Those failures remain
retained; the subsequent current-master pass does not establish that every
earlier timeout had the same cause.
