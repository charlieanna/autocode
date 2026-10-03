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
