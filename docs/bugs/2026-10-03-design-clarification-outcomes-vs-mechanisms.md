# Design clarification should separate outcomes from technical choices

Status: open. Observed in one live design-only trial on 2026-10-03. The
requirements/planning usability improvement has not been implemented. A separate
runtime recovery fix worked during the trial but is absent from the checkout
rechecked on 2026-10-04; see the current-status section below. Neither issue is a
request to bypass clarification or plan approval.

## Evidence

Task: "Design an AWS solution: we want a DLQ to be monitored and also certain
rows on DynamoDB to be monitored and alerted on Slack."

The trial explicitly prohibited implementation, deployment, AWS/Slack API calls,
credential access and approving decisions on the user's behalf. It used an empty
workspace and `--workflow design --joint-planning --no-chat`, without adaptive
planning. Workflow recognition was therefore skipped.

Run: `20261003-015741-design-an-aws-solution-we-want-a-dlq-to-be-monit-78a7323a`.
Relevant artifacts: `iterations/001/requirements-gather-01.json` and
`iterations/001/discovery-01.json`. Local artifacts are retained in the temporary
trial workspace, not committed as evidence bundles.

Observed stages: `review_design` (GPT-6 Sol), `requirements_gather` (GLM-5.3),
then `astra_discovery` (GLM-5.3). The run correctly stopped at
`WAITING_FOR_USER`, with no accepted assumptions, technical approach or milestones.
The interrupted launcher was recovered from the saved stage; no second trial
was started.

Three questions blocked planning:

- Q1: Which DLQ/service, and alert on any message, queue depth or message age?
- Q2: Which DynamoDB table/rows, and alert on state, staleness, count or changes?
- Q3: Which Slack destination and integration: webhook, Slack app or AWS Chatbot?

Q1 and Q2 contain necessary outcome questions. Q3 mixes a user-owned destination
and organizational constraints with technical choices the Planner could recommend.
Its options do include "No preference; design should present the options with
trade-offs", but the user still has to navigate integration terminology.

The Planner listed possible AWS mechanisms without selecting an architecture.
It deferred account/region, evaluation windows, routing, message contents and
deduplication/cadence. The trial stopped before later clarification, so it does
not establish that these details would be forgotten or that a completed design
would be unsafe. Scale, cost, security and delivery reliability remain unverified.

## Desired Behavior

- Ask users for alert conditions, timing, destinations and organizational constraints.
- Discover existing conventions from accessible repository evidence when available.
- Recommend engineering mechanisms, explaining alternatives and tradeoffs; do not
  require a user to select an AWS service or Slack API unless it is a binding constraint.
- Allow a provisional comparison before exact resource names are supplied when the
  user requests design only; distinguish parameterizable facts from true blockers.
- Keep unknowns explicit. Surface scale/cost, data sensitivity, repeated alerts,
  notification failure handling and recovery before a design is ready for approval.
- Preserve user ownership of consequential decisions and exact plan approval.
  A technical recommendation is not authorization for access, spend or deployment.

## Follow-Up Verification

Add a scenario with an empty workspace and this vague monitoring requirement.
Its oracle should require meaningful outcome/constraint questions and distinguish
those from technical recommendations, without asserting a particular AWS architecture.
Include a continuation where the user supplies alert semantics but has no integration
preference: the Planner should recommend a mechanism, retain unresolved consequential
constraints, and leave execution blocked until exact approval. Routine regression
coverage must use a fake provider; another live trial requires explicit authorization.

## Authorized Continuation

The user subsequently authorized continuing the same live trial with illustrative
requirements: an existing SQS DLQ alerting when nonempty; DynamoDB items PENDING
for over 30 minutes; alerts within five minutes; no repeated item alerts during
the same condition; Slack #ops-alerts with no mandated integration; no customer
data in messages. Resource identities and the real schema/index/scale remained
unknown. These are test inputs, not discoveries about a real AWS environment.

`discovery-02.json` produced a concrete, single-milestone plan to author
`docs/aws-monitoring-design.md`, with seven acceptance criteria. It proposed
CloudWatch DLQ monitoring, conditional DynamoDB mechanisms, durable alert state
and a likely webhook recommendation. It did not require exact resource names to
produce the draft. `plan-challenge-01.json` blocked it on four substantive issues:

- The plan weakened five-minute alert delivery into detection and omitted latency
  assumptions and delivery failure handling.
- PENDING-entry timestamps, evaluation without subsequent writes and per-episode
  de-duplication/reset were underspecified.
- Item identifiers may contain customer data and cannot be assumed safe for Slack.
- Document/change-set inspection cannot prove no external calls occurred, and
  the agent-added "no commands were executed" criterion contradicted read-only checks.

This is evidence that independent review catches important design gaps, not that
the initial draft is a completed or approved design. Revision attempts then hit
stage/inactivity timeouts without a final report; retained provider sessions show
unfinished reasoning. Automatic recovery exhausted its allowance. The CLI's
suggested `--resume-paused --grant-recovery 1` was rejected because the saved
frontier had become a human escalation rather than `PAUSED_TIMEOUT_RECOVERY`;
changing the bounded limits also invalidated the published request, leaving
`RESOLVER_PENDING`. No run-state edits, approval bypasses or infrastructure calls
were used. At this observation point no design document exists. Recovery through
the public interface and the completed design remain unverified.

### Planning Completed; Document Execution Blocked

The timeout boundary was subsequently recovered through two public invocations:
`--unit autoplanner --resume-paused --no-chat` re-established the correctly bound
operational request, then `--unit autoplanner --resume-paused --grant-recovery 1
--no-chat` granted one recorded recovery. With the already-saved finite limits,
`requirements-revise-05.json` completed and `plan-finalize-01.json` resolved all
four concerns. The final plan has one document milestone and ten acceptance
criteria, including timing assumptions, PENDING-episode transitions and a payload
allowlist. The user explicitly approved exact revision 5 for document generation
and local verification only.

Execution then stopped before the first Builder: three planning timeout recoveries
had left `no_progress_batches=3`, hitting the default implementation admission
limit. Raising that limit explicitly to 4 did not reset any counter, but the
operational-exhaustion request remained active even though the numerical guard
would now admit a batch. Supplying corrective information through
`--resolver-response provide_information` and resuming republished the same cause
instead of checking the corrected bound. One attempted reconciliation also
reported "Role result belongs to another goal revision".

The current public interface cannot retire this no-progress pause after the
finite threshold change: explicit bound-change handling omits
`PAUSED_NO_PROGRESS`, and operational-request reconsideration only handles the
planning review budget. The user authorized a narrow runtime fix with regression
coverage so the same approved document-only trial can continue. A completed
design and its validation are still pending at this observation point.

### Actual Document and Final Decision Checkpoint

The narrow runtime fix reconciles a retained, authenticated no-progress request
on explicit resume only when the current user-explicit finite bound admits the
retained count. It preserves the exact approval, counters, history and recovery
allowances. Seven CLI regressions cover corrected and still-exhausted bounds,
republished and consumed requests, unrelated changes and a complete fake build.
Architecture and adjacent resolver tests pass. The same live trial resumed after
a provider-free unit handoff; no additional no-progress increase was needed.

AutoCode authored the actual 293-line `docs/aws-monitoring-design.md` in the
isolated trial workspace, then ran `sol`, `astra_review` and `astra_resolve`.
The first document failed validation: its pre-post suppression could lose an
alert, its query could not observe an exited item, its evaluator-to-notifier
handoff was underspecified, and an emitted-metric alternative could not detect
an untouched item becoming overdue. A second Builder pass repaired those paths.
The explicit active-time cap then stopped the run before re-validation; the
user authorized raising the total from 3,600 to 4,500 seconds, without resetting
usage. The second Validator, Completion Owner and AutoResolver completed their
assessments and stopped at a genuine user decision, not a runtime admission error.

Current public outcome: `WAITING_FOR_USER`, `done=false`. Seven criteria are
verified as document requirements; AC3 and AC9 are blocked, and AC7 is unverified.
Local term checks passed 64/64, but semantic validation still rejected the design.
The only non-ignored trial deliverable is the Markdown document; no infrastructure
was delivered or live AWS/Slack behavior tested.

The draft recommends CloudWatch -> SNS -> Lambda -> Slack for the non-consuming
DLQ leg; an EventBridge-scheduled, conditional DynamoDB query/read evaluator with
episode state and an SQS notifier handoff; Streams-based tracking as the fallback
when schema/index prerequisites fail; and an incoming webhook for #ops-alerts.
It labels timing assumptions and thirteen open inputs, rather than discovering
actual account, table or Slack facts.

The remaining blocker is the strict combination of timely delivery and no repeated
item alert after an ambiguous webhook outcome. Slack may accept the post while
the response or the subsequent DELIVERED-state write is lost. Retrying can repeat
the Slack message; suppressing retries can miss delivery. The draft admits that
tradeoff but cannot waive the saved requirement. The review also retains conflicting
UNCERTAIN queue-acknowledgment rules and unspecified confirming-read consistency.
AutoResolver asks the user to keep both guarantees pending authoritative idempotency
evidence, explicitly permit repeats on uncertain outcomes, or accept possibly missed
alerts to prioritize no repeat. No option has been chosen on the user's behalf.
The document is therefore a produced, reviewed draft, not an accepted final design.

Fix validation: focused recovery plus architecture (11 tests) and adjacent resolver
coverage (101 tests) pass; the scenario harness passes 74 tests. The fake catalog
has no oracle failure, with one required path not exercised and one live-only case
skipped. The changed-file gate ran 1,118 tests across 68 modules and initially failed
two modules: missing editable package import and concurrent finding-scope coverage.
An offline editable reinstall resolved the package check; the remaining unrelated
`tests.test_autocode_finding_scope_repair` failure expects the missing
`finding_scope_reconciliations` status field. That concurrent work was not changed.

## Current Status: 2026-10-04

The public task-run status still reports `WAITING_FOR_USER`, `done=false`, no
active stage, and an answer required. Closing the assistant session does not
complete the run or choose a delivery tradeoff. The draft and run are saved in the
temporary trial workspace, not archived as permanent repository artifacts.

A fresh offline check ran:

```sh
.venv/bin/python -m unittest tests.test_autocode_finding_scope_repair tests.test_no_progress_recovery
```

It failed: the current runtime has no `reconcile_no_progress_request` helper,
and CLI cases for finite-bound increases fail again. The prior successful
recovery fix is therefore historical trial evidence, not a verified fix in the
current checkout. The finding-scope test still fails on the missing
`finding_scope_reconciliations` view field. These retained tests are currently
untracked. No intervening source changes were reverted or overwritten.

Open follow-ups are recovery-fix integration with passing regressions, the
finding-scope/status-view mismatch, and the original outcome-versus-mechanism
clarification improvement. The Slack delivery/no-repeat decision is separately
an unresolved requirement of the example design, not an AutoCode runtime failure.
