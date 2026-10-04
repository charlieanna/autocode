# Validation-only rounds for a finding the Validator never closes

Issue #300. A live etcd run kept a Validator-owned blocking finding open after
every criterion and the regression proof passed. The Completion Owner closed its
own finding and kept sending unchanged work back to the Validator. The Validator's
closing reports were rejected for real reasons (wrong receipt paths; no executed
event for a receipt it was asked to reconfirm without re-running), so the finding
stayed open and the run spent model calls until its budgets ran out.

An offline fixture (`completion_rework_bookkeeping` in
`scenarios/harness/completion_rework_provider.py`) reproduces the controller side:
the Validator passes but never disposes of its finding, and the Completion Owner
answers each validation with another `validate` task. Before the fix the run
launched three validation-only Validators, then three Completion Owner calls the
milestone gate rejected and an Investigator, and paused with milestone-replan
advice ("REWORK with a changed approach") that did not describe the problem. With
milestone stall reviews off, it ran until the iteration ceiling.

`autocode_validation_rounds` now counts validation-only rounds: Validator dispatches
at the source the latest accepted validation already reviewed, while blocking
findings are open. After two rounds in a row that changed nothing (same contract,
source, blocking findings, passing criteria and accepted milestones), the runner
launches no further Validator. It publishes one AutoResolver operational request
(`PAUSED_RESOLVER`) naming each finding and why it is open (resolution not
accepted, not rechecked, still reported) and the rejected Validator reports from
those rounds. The answer closes nothing and grants no retry.

Not changed, deliberately: findings are not resolved by matching text or evidence,
and a user's answer never closes a reviewer's finding. Only the raising reviewer
closes it through a fresh, independently evidenced report (`autocode_findings`).
