# What the headless conformance client found (#1012)

`scenarios/catalog/conformance-lifecycle` drives one run through the whole #1012 lifecycle
with a client that takes only the action each status view names
(`scenarios/harness/conformance.py`). Two places where the view did not name the action:

## A held repeated incident named no retry — fixed

A person pressed Ctrl-C while the rework Builder ran (the view showed `Builder working` and a
failed requirement). The view then named `--abandon-stage ATTEMPT` and `--resume-paused`,
and the client took both. The resume paused at `PAUSED_NO_PROGRESS`: "No causal progress for
the same incident". This is documented behavior: an uncertain attempt an operator abandoned
still counts as an attempt (docs/execution.md, #422), so the incident's one admitted repair
was spent.

The hold's own reason said `--resume-paused --retry-failed-stage authorizes one attempt under
existing limits`, and the CLI accepts it (`TaskRun.retry_failed_stage()`). But it appeared
only in that prose. The published request's `needs` named only the corrective-information
response (`--resolver-message TEXT`), and the recovery card offered only inspect and feedback.
A client that reads only the view could not take the retry.

Now the `answer` need of such a request also carries `retry_action`
(`--resume-paused --retry-failed-stage`). It is named only when a novelty hold waits on the
next stage and nothing is left to reconcile, the same checks `authorize_retry` makes
(`autocode_recovery_limits.scoped_retry`). The client takes it only after its own interrupt
or feedback. A hold it did not cause stays a stop for a person.

## `continue` has no `next_command` — by design

While a run is between stages (`RUNNING`, `needs.kind` `continue`), `next_command` is null.
The docs say so (`next_command` is null for active runs), and the needs table names the
action, `autocode --no-chat`, which takes no token. The client uses it for `continue` only;
any other need without a `next_command` is a conformance failure.
