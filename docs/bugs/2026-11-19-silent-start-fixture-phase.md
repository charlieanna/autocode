# The timeout observer owns the retry fixture's phase

The affected UX selection hit an ordinary idle timeout in the Builder
silent-start recovery test. The same case passes individually on master and
the candidate, so the failure alone did not establish a product regression.

Both the Planner and Builder fixtures decided whether to answer by having the
first provider process write a `launched` marker. The startup guard may stop
that process before its Python code executes. On retry, the missing marker
then selects the first attempt's 30-second silence again. The Builder exceeds
its ordinary 10-second idle limit. The Planner's existing longer floor lets
that unintended silence finish, so it can pass while exercising the wrong
fixture phase.

A controlled three-second delay after stdin acceptance reproduced the Builder
failure on unchanged master. At each first timeout the child-written marker
was absent. The two cases took 47 seconds and one errored. The corrected
fixtures select their reply phase from the test, after observing and recovering
the first timeout. The same controlled delay now passes both cases in 12 seconds.

Every original assertion and runtime limit remains: the initial no-event stop,
the unchanged source, the admitted recovery, a slow successful retry and its
report. Only test data changes. The production timeout policy, retry allowance,
models and completion gates are unchanged; no paid model run is needed.

All 67 activity/recovery and architecture checks pass in 81 seconds, along
with Ruff lint and formatting.
