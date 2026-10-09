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
findings are open. When a blocking finding stays open through two rounds in a row
that made no progress on it, the runner launches no further Validator. It
publishes one AutoResolver operational request (`PAUSED_RESOLVER`) naming those
findings and why each is open (resolution not accepted, not rechecked, still
reported) and the rejected Validator reports from those rounds. The answer closes
nothing and grants no retry. The question says that answering keeps the run paused
and that goal feedback (`--feedback`) is the way on; feedback is accepted only
while the question is asked, since an answer returns the run to `PAUSED_RESOLVER`.
A source edit alone does not resume the run while the request is published (the
operational-request hold in #288/#301).

Rounds are counted per finding, after review found that the first version keyed
them on the whole frontier: any change of the passing criteria or of the open set,
including a criterion losing PASS or another finding opening, restarted the count,
so a Validator whose results alternated could keep the same finding going
indefinitely. Progress now has to be new at this contract and source: a criterion
passing or a milestone accepted for the first time, or the finding reaching a state
it had not had (reported, not rechecked, resolution pending) or fewer unverified
criteria than before. Each can happen only finitely often.

The stop comes at the next Validator dispatch, so the Completion Owner call that
asked for it still runs; stopping before that call would also stop an Owner that
was about to choose rework instead.

Not changed, deliberately: findings are not resolved by matching text or evidence,
and a user's answer never closes a reviewer's finding. Only the raising reviewer
closes it through a fresh, independently evidenced report (`autocode_findings`).

A duplicate that no reviewer report can close (the etcd case: the user settled the
problem, and only one of its two findings closed) is now closed by the user, by
name: `--close-finding ID --close-reason TEXT` (`autocode_finding_close`). The row
records that the user closed it and why, and a `findings_closed` user event records
the decision. Nothing closes a finding because its text matches another; the
validation-only stop only points out a stalled finding with the same text or
evidence as a resolved one and names the command. Closing every finding the stop
asked about answers it, and the next resume continues.
