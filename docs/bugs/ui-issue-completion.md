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
| #29 | Work checklist reads saved criteria; state and next step are visible | Complete milestone/task progress and linked problem/requirement details |
| #30 | Current-token question targeting, per-question default delegation, partial-answer tests | Audit grouped answer history, re-asked questions and bulk suggestions |
| #31 | Exact-token approval, stale refusal, separate approval/build action | Real-browser approval/start regression passed; issue closure still requires acceptance review |
| #33 | Recovery/AutoResolver cards and guarded actions | Exhaustive status/action mapping, repeated-failure summaries and plain wording |
| #34 | Loopback preview and truthful empty state | Per-project persistence, requirement-linked screenshot cards, change refresh |
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
