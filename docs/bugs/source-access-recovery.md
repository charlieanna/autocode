# A source read failure interrupted automatic recovery

A live Plan Reviewer reached its startup deadline. While archiving the stopped
attempt, Git transiently refused the source inventory. AutoCode retained the
attempt at `PAUSED_SOURCE_UNAVAILABLE`. Subsequent source reads succeeded, but
the public view still asked a person to inspect the pause and resume it.

The same gap affects any stage or retained result whose reconciliation needs a
fresh inventory, including a source failure before the first provider starts.
Source availability and supervision are facts AutoCode can recheck; neither is
a requirements or plan decision.

The same live run then hit a Git read failure while preparing the regression
proof, before the Tester had started. That query raised a generic runtime error,
so the run blamed an invalid Tester answer and offered a manual retry. Required
proof inventory and pinned-policy reads now use the same source-availability
classification as snapshots. They retain the Git failure and take the ordinary
source recovery path. Git writes and optional missing-object reads retain their
existing behavior; no report is accepted by a successful source read.

The required review-diff reads at the end of a stage use that same source
classification, both for streamed ordinary diffs and selected-source diffs built
with a temporary index. Previously their Git exception could trigger a report
repair or escape as a traceback. Scratch-index mutations keep their existing
failure handling, and no diff or model report is accepted after a failed read.

The live Tester and Completion Reviewer exposed the same lost classification
at two more boundaries: a scratch worker returned only a traceback, and native
copy preparation labelled its failed source query as broken Kilo containment.
Required reads now keep their domain pause through report application and
provider admission. A worker publishes a negative result bound to its exact
request and native output. The parent authenticates its admission, runtime and
every inner command's collected custody before restoring the source pause.
Unknown cleanup remains held; this transport grants no test PASS, accepted
model report or fresh retry allowance. A source failure cannot trigger a
report-only model repair.

The status view now offers the existing resume action only after a fresh source
inventory succeeds and any retained supervisor is positively stopped with a
clean receipt. The action is bound to the exact saved recovery token. VS Code
takes that named continuation for the current offer. A non-rejected exit 2
that leaves the exact automatic offer unchanged is retried only after a fresh
status read, at most three invocations. A successful no-op or explicit rejection
is acknowledged once; a question or approval never becomes an automatic action.
This also covers decision publication and stopped-supervision metadata actions.
The editor shows recovery progress instead of asking the person to continue.
Unknown or live
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
While a checked live controller reconciles that saved failure, the view suppresses
its obsolete blocked card using the same observation that suppresses further
actions. The failed attempt stays in recovery history.

Regressions vary planning, Builder, Tester and completion stages, prelaunch
failures, unreadable source, uncertain supervision, stops and foreign or unbound
client actions. A public TaskRun check retains the failed source read and model
output, refuses another provider while source is unreadable, then reconciles
the retained report when access returns.

The live resume exposed another boundary race: a pause accepted while saved
evidence was being checked was acknowledged by the resume already in progress.
Locked setup now records the pause that invocation may release. A newer applied
pause is reasserted before final admission, including when reconciliation has
proposed completion. Its receipt remains unacknowledged until a later explicit
resume. Source and timeout CLI regressions reproduce this without a model.

The first live GLM/Sol editor flow completed with all six requirements checked,
three passing tests and only the two requested files changed. Its redacted bundle
manifest is `7740c4bb9285e03251101abc26d869b183a2b91ff6bc2db4c86059ecf78fb9ad`.

The bounded editor follow-up hit the review-diff boundary after a valid
Requirements report repair. Installing the correction while its controller was
stopped and taking the existing named continuation recovered that retained
report. The editor displayed the reviewed Requirements with the same 33 attempts
and recorded spend; source recovery launched no additional model call. The
original report's out-of-range source citation remains correctly rejected in
history, independently of the later Git failure.
