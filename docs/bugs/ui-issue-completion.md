# UI issue completion ledger

The September Figma build (PR #292) is merged. Its final master CI run
37143840279 passed. That establishes its test gates, not completion of every
open UI issue or pixel identity with Figma.

This follow-up keeps the approved project/conversation layout and human actions
in chat. Existing user data and unrelated running tasks are preserved.
No installed app change or merge is part of the current review branch.

## Current repair batch

- **#289, browser catalogue cleanup:** replace immediate-child-only timeout
  supervision with the existing owned process-tree supervisor. Keep command,
  stdout/stderr, timeout/interruption reason and cleanup receipts. No case or
  deadline is removed. Controlled regressions exercise a normal child, detached
  child, unrelated process, setup failure, timeout and interruption.
- **#32, message intent:** deterministic context-first routing; task status
  questions and typed controls do not become feedback. Proposed corrections
  require chat confirmation bound to the saved text and plan token. Preserve
  idempotent retries, uncertain-delivery reconciliation and answer provenance.
- **Approval browser observation:** wait for both the saved approval and the
  completed action before asserting that Start building is available. The
  separate approval/start assertions remain mandatory.

## Remaining acceptance audit

These are open work items, not completion claims. Older issue descriptions must
be reconciled with the approved Figma brief and subsequent user instructions.

| Issue | Observed implementation | Work still requiring proof or implementation |
| --- | --- | --- |
| #27 / #28 | Persistent chat, project/conversation sidebar, Work/artifact pane, narrow-screen drawers | Audit every shell acceptance case and final design fidelity |
| #29 | Saved progress strip, milestone states, requirement/problem detail links implemented | Local gates passed; review pending. Old or absent proof stays unknown |
| #30 | Inline answer fields, saved drafts, grouped answer history, provenance and re-asked-token rejection implemented | Local gates passed; review pending. Suggestions remain explicit per question |
| #31 | Exact-token approval, stale refusal, separate approval/build action | Real-browser approval/start regression passed; issue closure still requires acceptance review |
| #33 | Recovery/AutoResolver cards and guarded actions | Exhaustive status/action mapping, repeated-failure summaries and plain wording |
| #34 | Per-project preview persistence, saved-code refresh and requirement-linked screenshot cards implemented | Local gates passed; review pending. App startup remains manual |
| #35 | Checkpoint inspection, comparison and a drafted restore request | New-branch rollback semantics and evidence invalidation are not delivered by a draft request |
| #36 | Existing project creation and model settings | First-run readiness checklist and provider setup guidance |
| #60 | Scoped permanent-delete implementation and 12 safety tests exist | Audit current browser coverage and all preservation/discovery criteria; never delete real user data during verification |
| #17 / #18 / #19 / #21 | Several backend projection/proof/naming/draft facilities exist | Trace each remaining issue criterion to current public data and evidence |
| #250 / #251 / #262 / #297 | Reference/capture work exists on current master | Track complete reference inventory, deterministic comparison and independent visual adjudication separately; avoid duplicating concurrent work |

## Verification rules

- Use the repository venv and actual public CLI/HTTP interfaces.
- Run affected canonical tests, dashboard Python and Node tests, fake scenarios,
  and real-browser checks. Broaden to full canonical tests when shared changes
  require it, and before merging the complete UI delivery.
- Browser checks cover desktop/tablet/mobile, failure and reload states,
  keyboard/focus and usable targets. Inspect captured screenshots.
- Keep failed receipts. A passing retry does not erase a failure or establish
  its cause. Timeout cleanup is separate from diagnosing browser reliability.
- Close an issue only when its own acceptance criteria are supported. A merged
  layout PR, selected passing test, or collection manifest is insufficient.

## Verified first repair batch (2026-10-03)

- 45 chat tests passed, including real CLI delivery, HTTP submission,
  concurrent confirmation, restart/uncertain-receipt reconciliation, and
  byte-identical state for unconfirmed changes and status messages.
- Real Chromium checks passed at 1440×1024, 1024×768 and 390×844. They cover
  typed approval/control safety, confirm/decline, reload, pending questions,
  no horizontal overflow, and minimum 44-pixel confirmation targets. This
  regression also runs as a dedicated CI step under process supervision.
- All 301 dashboard Python tests and standalone Node tests passed before the
  final pending-question and stale-retry guards; all 45 affected chat tests and the three-size
  browser check passed again after those guards. The changed gate passed 23 tests across
  3 modules, including all 14 browser catalogue cases.
- 54 fake scenarios passed. The one live-model Investigator scenario was
  explicitly skipped; no live-model proof is claimed.
- The first full browser gate failed on the mobile approval observation race.
  The corrected observation waits for the actual action to settle and for an
  enabled Start building button. Its subsequent full catalogue gate passed;
  no assertions were removed and no time limits were raised.

This proves the bounded repairs above. It does not prove that every remaining
UI issue is complete or that the rendered app is pixel-identical to Figma.

The final two regressions also establish that a failed correction cannot be
retried against a newer plan and a newly opened question cannot consume a
pending confirmation before delivery is allowed. Both failed before repair.


## Next repair batch: saved progress and question cards

- **#29:** project task counts only from the supported CLI status and matching
  contract acceptance. Preserve unknown completion for legacy plans or absent
  receipts. Requirement results retain failed and unchecked counts separately.
  Linked requirement/problem detail rows preserve focus during polling. The
  expanded list scrolls into view on phones; saved status stays above long lists.
- **#30:** add explicit per-question text submission without classifying literal
  answers as controls. Retain main-composer drafts independently. Group accepted
  answer history with typed/delegated provenance; keep failed sends visible and
  re-asked questions distinct. Existing current-request token checks remain the
  authority boundary. No answer approves a plan.

Focused evidence: all 315 dashboard Python tests and standalone Node tests
passed. The Work matrix passed nine state/viewport combinations. The question
matrix passed partial answers, accepting every suggestion, reload, independent
draft preservation and a stale/re-asked request at three viewport sizes.
Both browser matrices use the real HTTP adapter and page with disposable tasks;
the fixture supplies the external runner responses and never invokes a model.

The broader run exposed real layout and refresh-focus regressions, which were
fixed. The Plan-pane checks caught an unintended
expansion of that pane; new task/problem links are now scoped to Work, preserving
the original Plan-pane assertions and layout. The question test initially clicked
composer text covering an off-screen button; it now scrolls the real target into
view and verifies the hit target before a pointer click. Failed receipts are
retained locally.

The full canonical selection ran 2,998 tests in 221 modules. Its only failed
module was the dashboard catalogue, which had cached the old JavaScript
function-signature assertion before that test was corrected. A fresh complete
14-case catalogue then passed in 333 seconds with its original deadlines. The
corrected test exercises the Work link and compact read-only Plan behavior.
All standalone Node tests passed. The final CI-equivalent browser gates for
chat intent, Work and question cards passed with successful owned-worker cleanup.
All 54 fake scenarios passed; one live-model-only Investigator case was skipped.
These results verify this batch, not the remaining backlog or pixel identity.


## Preview and evidence batch

Project preview preferences are shared across conversations in that project,
with legacy task preferences preserved. A saved code change refreshes the frame;
polling keeps it intact. Screenshot cards cite the recorded requirement and
source. The bounded image endpoint validates recorded fingerprints when available
and refuses changed bytes, stale report selections, traversal, sibling task
paths, symlinks, nonregular files and oversized/non-image payloads.

Eight image projection/HTTP tests and the three-size real-browser preview matrix
passed. The browser matrix verifies persisted addresses, sibling conversations,
project isolation, recorded screenshot rendering, requirement navigation, and
saved-code refresh without a provider or changes to real tasks. The full dashboard
run executed 323 tests and exposed one empty-state regression: the conversation
name was missing. It was restored, and all 15 existing pane acceptance checks
passed unchanged. All standalone Node tests passed before that text repair; the
focused preview check passed again afterward. The first mobile browser attempt
tried to open an already-open drawer; the corrected test observes its open state
and uses native pointer clicks. Original deadlines and failed receipts remain.
The fresh full canonical gate passed 2,999 tests across 221 modules in 549 seconds,
including all 14 browser catalogue cases with their original caps. All 54 fake
scenarios passed; the live-model-only Investigator case was explicitly skipped.
The preview browser matrix passed again after the empty-state repair, including
owned-worker cleanup with no live descendants; its phone capture was inspected.

An earlier full-suite run failed two progressive goal-change cases because the
legacy-worker guard reported an active process. The unchanged seven-test module
passed alone. A deterministic regression reproduced a concrete guard defect:
the same relative run suffix in another workspace, or a longer sibling run name,
was treated as this run. The guard now matches path boundaries. Same-run
controllers, provider output paths and ambiguous bare relative legacy paths
remain blocking. The legacy check was extracted into a lower-level module and
the support-module size limit was reduced. All 100 guard, dispatch, progressive
and architecture tests passed, followed by the full canonical and fake gates
above. Historical failed receipts remain available; no unrelated process was
stopped during diagnosis.

The previously published progress/question-card commit `74a08e6a` also passed
GitHub CI run 37152526823, including the scenario harness, dashboard Python,
standalone Node and all three dedicated browser matrices.

The complete dashboard Python gate was rerun after the empty-state fix: all 323
passed. Final JavaScript review then added two focused regressions and repairs:
failed steps cannot masquerade as a landed candidate for auto-refresh, and a
browser that denies persistent storage still opens the explicitly entered URL
for this session. Standalone Node checks and the real three-size browser matrix
passed after those final changes, including reload after denied storage. The
full canonical/fake results above preceded this final JavaScript-only repair;
CI repeats the broader gates on the pushed revision.


## Server transcript batch

The task page now consumes one server projection over the existing journal,
answers, progress and delivery receipts. Stable source IDs and causal reply
links preserve durable order when clocks disagree. Receipt merging retains the
original request ID for confirmation and retry; failed sends and re-asked
questions remain distinct. Legacy answers without recorded provenance are
labelled explicitly and are not folded into newly delivered answer groups.
Status questions cite unchecked requirements and open findings from saved
records. The projection is display-only and has no runner imports or writes.
Current approval/question controls and checkpoint inspection remain separate
from the transcript; no new execution authority is introduced.

All 335 dashboard Python tests passed, including ten pure projection cases and
an actual HTTP repeat/no-state-write check for completed, waiting and rework
states. The canonical changed selection passed its four architecture tests;
that selection does not cover dashboard modules, so it is not presented as a
substitute for the explicit dashboard gate. All 54 fake scenarios passed; the
live-only Investigator case was skipped.

Real-browser testing caught an incorrect grouping of an old legacy answer with
two new replies. A focused JavaScript regression failed before its repair.
The unchanged question-card browser assertions then passed at desktop, tablet
and phone sizes, including partial/suggested answers, reload and re-asked tokens.
The phone capture was inspected. The chat-intent browser test also passed at
all three sizes after its pointer helper was corrected to scroll the target
into view and verify it is hit-testable. Deadlines and assertions were retained;
failed receipts remain local. Both supervised browser runs reaped their roots
and reported no live owned descendants. The broad Python/fake gates preceded
the final JavaScript-only grouping repair; the focused JavaScript and question
browser gates ran after it. This does not establish the full #16 guided trial
or final Figma visual acceptance.

## Shared role names

The terminal, dashboard stage/model labels and packaged assets use one display
catalogue. Internal stage IDs, saved state, model routes and pins are unchanged.
All dispatched stage IDs and report-repair variants are covered. The two modes
that combine validation and completion review say “Validator / Completion
Owner” regardless of the model selected for that job. README and model docs
use the same job names; historical stored messages are not rewritten.

The first full gate ran 3,003 tests in 222 modules and found one obsolete
“Requirements Gatherer” expectation in the model catalogue. The other modules,
including the 14-case browser catalogue, passed. After correcting that display
expectation, all 43 role/status/catalogue/architecture checks passed. The required
changed-file gate selected the entire suite because package metadata changed:
all 3,003 tests in 222 modules then passed, including the browser catalogue.
All 54 fake scenarios passed; the live-only Investigator case was skipped.

The dashboard Python gate ran 335 tests and found one old model-selector label
expectation. Its corrected 16-test module passed, as did all 31 chat/HTTP tests
including a new check that the actual served script and page use the shared
catalogue. All 24 standalone Node checks passed. A built wheel was extracted
and imported in isolated Python; it includes the catalogue and serves resolved
page labels without installing anything. Final copy-only cleanup removed the
remaining alternate role labels, followed by another passing 24-script Node
gate. The full suite result preceded that final copy-only cleanup. These are
functional naming checks, not Figma visual acceptance or overall backlog closure.

## Recovery cards (#33), verification still in progress

Stopped tasks now expose a three-part chat card from the additive public
`recovery` view. Specific existing CLI actions are constructed on the server,
bound to the inspected stopped checkpoint, and checked again under the run lock.
Retry preserves the contract, saved routes, limits and failure history. Abandon
and Resume remain separate. Repeated failures retain their source identities.
An internal unpublished question does not become human authority. Unknown stops
still offer inspection and feedback; a deliberate Stop offers a new conversation.
Supported status that finds an absent worker behind a saved RUNNING state offers
inspection without inventing a paused state, execution token or another worker.

Focused projection, adapter and real-CLI recovery tests passed. All 345 dashboard
Python tests, 25 standalone Node checks, 54 fake scenarios (one live-only skip)
and 173 scenario-harness cases passed. Recovery and question-card browser
matrices passed at desktop, tablet and phone sizes. The final absent-worker
follow-up passed 19 Python tests and the recovery browser matrix; its phone
capture was inspected and the supervisor confirmed no live owned descendants.
A subsequent wording-only change calls an absent-worker checkpoint a checkpoint,
not a saved pause; its renderer regression passed.

The first required full changed-file gate ran 3,013 tests in 223 modules and
failed two modules: an exact status-field expectation omitted the additive
`recovery` field, and a scenario cleanup raised EPERM. The field expectation was
corrected. Its 29-test module and the unchanged 41-test scenario-oracle module
passed separately and in the second full gate. The cleanup cause is unresolved;
its historical failure has not been hidden or turned into cleanup success.

The second full gate ran the same 3,013 tests/223 modules in 2,236 seconds and
failed 18 modules. Diagnostics show existing subprocess/browser deadlines and
one missed intermediate worker-activity assertion. Host load exceeded 200 on
10 logical CPUs during that run. This is a failed gate, not proof of a clean
revision. Lower-concurrency failure rechecks and final integrated regression
remain required. No deadlines or assertions were relaxed and no unrelated
workers were interrupted. These receipts do not close #33, the remaining UI
backlog, or final Figma visual acceptance.


## Batch 7 — planned checks and current proof (#17, #29)

Added read-only `--status --inspect-evidence` and additive
`view.verification`. Requirements and their planned methods remain visible
before testing. Current checked/failed/unchecked results require the report to
match the source, task, contract and checklist, with intact pinned project-local
evidence. A changing checkpoint/source or unavailable inspection fails closed.
Authenticated human acceptance remains separate. Existing independent review,
regression proof and completion gates are unchanged.

Selected task detail and status questions in chat use that supported inspection.
The Work and Checks panes keep old reports available but stop displaying their
PASS labels as current checkmarks. Stale recorded completion is clearly labeled
and does not reopen completed-task mutation controls. Binding also rejects a
receipt for another displayed assignment/checklist or during a recorded attempt.

Verification: the changed selection passed 266 tests in 15 modules at two-module
concurrency, including the real CLI test that edits source and corrupts evidence
while requiring the saved checkpoint to remain byte-identical. A further 29
proof-boundary/architecture tests passed, including the existing missing-criterion,
fabricated-event, blocking-finding and changed-human-review-evidence refusals.
All 352 dashboard tests and 26 standalone Node scripts passed. All 54 fake
scenarios passed; the one live-model-only scenario was skipped as designed.

The real HTTP/page browser matrix passed five lifecycle/freshness states at
1440x1024, 1024x768 and 390x844. Desktop and phone captures were inspected. The
supervisor confirmed root reaping and no live owned descendants. These are
fixture-based functional captures, not independent Figma acceptance. A final
caption-only edit clarifies that current evidence supplies the checkmarks.

Failed receipts are retained: a fixture initially changed a report after sealing
its human-request token; the production authority check correctly refused it.
The fixture now seals last. Two older display tests incorrectly counted raw saved
results; they now exercise both inspected and uninspected records. The additional
stale-result assertion exposed a real render-cache bug: the Work pane omitted
verification changes from its fingerprint. That bug was fixed and the full
dashboard/browser checks passed afterward.

The earlier full-branch gate remains failed and requires integrated rechecking.
Its exact unchanged worker-activity assertion passed on isolated recheck; this
does not establish the other failed modules or make that old receipt green.
No merge, install, issue closure or user artifact acceptance is claimed.
