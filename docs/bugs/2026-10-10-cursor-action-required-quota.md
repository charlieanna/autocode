# Cursor quota errors with ActionRequiredError

Fresh original Go and TypeScript Arena attempts ended without reports after
Cursor printed `ActionRequiredError: You've hit your usage limit`. The error
classifier accepted trailing `Error:` lines but skipped this named error, so
the runs surfaced an uncertain provider exit instead of the existing quota
stop and model-routing advice.

Recognize this specific prefix in the same trailing-error reader. Only the
provider's final consecutive error lines count: matching text inside JSON tool
output or before a subsequent event does not establish a quota stop. The
existing message classifier still determines quota, rate limit or uncertainty.
This does not change retries, model selection, acceptance gates or Arena checks.

Quota exhaustion cannot be requested deterministically. Fault-injected log
tests cover the observed prefix, ANSI styling, tool-output isolation,
continuation and a non-quota action-required error. The failed live attempts
remain unqualified and are not resumed or regraded.
