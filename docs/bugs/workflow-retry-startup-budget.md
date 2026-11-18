# Retries restarted the short startup guard

A live VS Code verification run's acceptance-test writer reached its 300-second
hard deadline after executing tools. AutoCode authorized a retry on the same
model and effort with a 600-second deadline. That retry emitted no initial event
within the runner's 90-second startup guard, so it paused and offered a model or
effort change before using its longer allowance.

The guard was selected independently of the launch's retry allowance. A later
Builder in the same run demonstrated the wider cause: a silent-start retry ran
without the guard, did useful work until its hard deadline, then its next retry
restarted the 90-second guard. Looking only at the last attempt's stall phase let
the guard alternate between attempts. Repeated report repairs had the same
gap after an attempt that emitted events.

One startup policy now reads the existing retry contracts: the job's exact
unconsumed authorization, a stage's current recovery-ledger binding, or the
report repair's pinned queue and attempt count. Every such retry uses its
ordinary inactivity limit. Source and configuration admission, explicit idle
limits, hard deadlines, total time, spending caps and retry allowances still
apply. First launches, other stages and mismatched recovery contexts keep their
startup guard. No route, effort, saved limit or retry allowance is changed.

Regressions exercise acceptance-test, design-review, stuck-investigation and
repair jobs across three model families, plus Builder, Tester, planning and
completion stage recoveries and repeated report repairs. Public TaskRun
regressions inject hard deadlines followed by 120-second silent startups for a
workflow job and a Builder. They require acceptance of the retry's report;
independent validation remains necessary. Original timeouts and retries stay
accounted for, and stale-handoff admission remains strict.
