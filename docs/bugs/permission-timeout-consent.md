# Applying an approved verification timeout (#987)

A permission answer previously recorded consent without changing the runner's
timeout. Verification retried with the old allowance, timed out again, and
required another decision and manual settings edits.

Timeout requests now carry explicit choices in the existing `proposed_delta`
string, encoded as JSON:

```json
{"operational_options":[{"option":"Set suite timeout to 5400 seconds","changes":{"settings.regression.test_timeout":5400}},{"option":"Decline","changes":{}}]}
```

Each `options` entry must appear once, in the same order. The supported paths
are `settings.regression.test_timeout`, `settings.limits.tool_timeout_seconds`
and `settings.limits.stage_timeout_seconds`. Values must be positive integer
seconds. A request should name each change and any interacting deadline;
changing a command timeout does not also increase a stage or total-run budget.

The question displays the exact effects and pins all offered settings, including
whether each key existed. The existing resolver token and approved contract
bind the decision. Answer with the exact option text through the usual CLI or
TaskRun answer path. A successful answer applies only that option's changes
and retains a before/after receipt in its existing user event. Changed settings,
unsupported controls and conditional/free-text answers refuse the whole change.
Existing question consumption prevents applying an answer twice.
Changed tool/stage limits become explicitly user-owned, so default-budget
recovery cannot increase the approved allowance again. TaskRun keeps any
stored tool/stage timeout flags aligned after the answer, including `--flag=value`
forms; a stale supplied token is rejected before applying such a decision.

Prose-only legacy permission requests retain their existing consent behavior;
they never infer a timeout change. This payload does not refill elapsed run
budgets, reset counters, change models or waive verification/approval gates.
External command/supervisor deadlines also remain in effect.
