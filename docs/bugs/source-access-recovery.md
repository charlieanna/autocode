# A source read failure interrupted automatic recovery

A live Plan Reviewer reached its startup deadline. While archiving the stopped
attempt, Git transiently refused the source inventory. AutoCode retained the
attempt at `PAUSED_SOURCE_UNAVAILABLE`. Subsequent source reads succeeded, but
the public view still asked a person to inspect the pause and resume it.

The same gap affects any stage or retained result whose reconciliation needs a
fresh inventory, including a source failure before the first provider starts.
Source availability and supervision are facts AutoCode can recheck; neither is
a requirements or plan decision.

The status view now offers the existing resume action only after a fresh source
inventory succeeds and any retained supervisor is positively stopped with a
clean receipt. The action is bound to the exact saved recovery token. VS Code
takes that named continuation once for the current offer. Unknown or live
supervision, unconfirmed cleanup, an outstanding stop, unavailable source and
native or durable authority do not grant automatic continuation.

The writer rechecks source and the stopped attempt under its existing run lock.
It records source recovery as a runner action, preserving the original failure,
usage, model choices, limits and retry history. It does not renew Resolver or
report-repair allowances. Existing artifact reconciliation, source freshness,
plan approval, test evidence and launch gates still decide what can proceed.
An unavailable source remains visibly blocked without asking a person to run a
Git command or choose a recovery. AutoCode changes no Git trust configuration.
The public recovery history also shows the successful source recheck and its
original error, without presenting it as test evidence or plan approval.

Regressions vary planning, Builder, Tester and completion stages, prelaunch
failures, unreadable source, uncertain supervision, stops and foreign or unbound
client actions. A public TaskRun check retains the failed source read and model
output, refuses another provider while source is unreadable, then reconciles
the retained report when access returns.
