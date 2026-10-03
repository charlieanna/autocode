# Draft GitHub scope updates for #22 and #23 (local only)

**Do not publish these edits while the progressive-planning work lives on a
local branch.** The approved plan authorizes preparing this text locally;
publishing to GitHub happens only when a pull request for this work is
explicitly authorized. Do not close #22 or #23, delete their prerequisites,
or claim the original Project → Workstream → Milestone → Task hierarchy is
delivered. Keep #16 open: offline rule coverage is not live effectiveness.

## Recorded scope exception

The user authorized a limited exception to these issues' start conditions:
the smaller **single-run progressive-planning** version may proceed before a
real project needs it and before #16's "done" tests pass. This exception does
not establish that #16 passed, does not authorize live trials or spending, and
does not enable the original multi-run hierarchy. The original proposals below
stay valid as **deferred work**.

## Proposed edit for #22 (workstreams / project agreement)

Add to the issue body or as a pinned comment:

> **Scope update (progressive-planning version, in progress).** Part of what
> this issue asks for is being delivered differently than the original
> parent/child-run sketch, in one continuing run:
>
> - **Implemented and offline-verified:** a fixed product contract with one
>   whole-product milestone; an optional Planner-proposed slice map (complete
>   capability/requirement coverage, first slice, tentative future slices) with
>   replayable machine checks; an explicit continuation delegation sealed into
>   ordinary plan approval (hash plan content first, then seal its identity
>   into the approved contract); inherited requirement coverage that a slice
>   revision can never drop; per-slice allowances that retries and splits
>   share. The existing controller activates reviewed artifacts, executes
>   serial bounded tasks, verifies cumulative checkpoints, accounts actual
>   stage usage, classifies progress and enforces full-product completion.
>   Three real-CLI fake-provider scenarios have independent oracles, reference
>   solutions and broken controls. Sixteen real CLI tests cover restart,
>   regression repair, split/reorder, shared quotas, stale/fabricated authority,
>   approved product/permission changes and selective authenticated retirement.
>   Ordinary reapproval preserves spent usage and historical proof; removal is
>   signed as an exact named check definition visibly shown in the revised
>   plan, never inferred from a changed plan/criterion ID.
> - **Validation limits:** the coverage is offline with fake providers, not
>   live-model effectiveness. The original real-project and #16 conditions
>   remain unsatisfied; the unchanged browser accessibility baseline failure
>   is recorded below rather than hidden or treated as a feature success.
> - **Deferred (original proposal):** Project → Workstream → Milestone → Task
>   as separate child runs, a shared parent agreement file across lanes,
>   cross-run delegation and multi-run approval invalidation. Prerequisites
>   unchanged: a real project that needs this, and #16's done tests. The
>   hierarchy prototype is checkpointed on `feat/hierarchical-planning` for
>   reference.
> - **Start-condition exception (recorded):** the single-run version above may
>   proceed before those prerequisites. It does not satisfy or remove them.

## Proposed edit for #23 (interface contracts / walking skeleton)

Add to the issue body or as a pinned comment:

> **Scope update (progressive-planning version, in progress).** Version one
> adopts the issue's two instincts inside a single run, and defers the rest:
>
> - **Walking skeleton as the first slice:** the proposal's first slice must
>   have an observable useful result across the essential layers (the main
>   user journey), bounded writable paths, product-criterion references and
>   nonempty machine checks whose commands the runner can actually replay.
>   Investigation/setup may be tasks but cannot be reported as delivery.
> - **Integrated verification:** every slice checkpoint re-runs the entire
>   cumulative required-check set (earlier slices' retained demonstrations,
>   `fully_verify` targets and always-applicable constraints). A slice can
>   pass while a broader product criterion stays open; partial progress is
>   never product acceptance. Simulated-scale results must say what they do
>   not prove.
> - **Controlled change:** interface/technical changes proceed only after
>   independent review and fresh cumulative proof. Product changes, new
>   permissions and unresolved product decisions return to the user through
>   the ordinary goal-change/answer/approval path; spent usage, findings and
>   check obligations survive the new contract hash, and only checks whose
>   behavior an approved change explicitly removes may retire (shown in the
>   visible revised plan).
> - **Deferred (original proposal):** versioned producer/consumer interface
>   contracts as first-class shared artifacts across independent workstreams,
>   and change-request invalidation of sibling workstream approvals. Those
>   need the multi-run hierarchy deferred in #22.
>
> The final product check follows the goal's named user journeys and the full
> product-checklist proof, not the last slice's tests alone.

## Version-one gates this work must pass before either issue is touched again

1. Real CLI with fake providers and default limits: initial planning consumes
   its normal review calls, S1 verifies while a broad criterion stays OPEN,
   restart recovers, S2 completes, full proof, completion in one run; fake
   clock proves per-slice and aggregate budget accounting.
2. Negative authority: ordinary/legacy approval, stale or tampered plan/review/
   source, unknown delegation and undetailed future slices cannot authorize
   dispatch.
3. Partial-proof safety and cumulative regression/rework: a local slice PASS
   never marks the product; S2 breaking S1's required behavior runs A+B, fails,
   repairs and re-proves; a fabricated PASS is rejected, never accepted as
   FAIL.
4. Revision/recovery: split/reorder without losing coverage, findings, checks
   or budgets; exactly-once recovery around activation and checkpoints.
5. Findings/stagnation, final-proof safety, approval/app compatibility,
   state/architecture compatibility (`progressive` is the only new top-level
   state record; `slice_id` the only new mutable task field), allowance
   accounting, and the product-change mid-run path.

## Local Verification Notes

The installed worktree package, not the main checkout's installed package, is
used for final verification. The final complete suite run executed 2,374 tests
in 161 modules. Its two fixture errors used stale settings references after
transactional approval; they have been corrected and all 63 module tests pass.
The final changed-file gate passes 1,442 tests in 90 modules, including that
module, the corrected mutation-control anchor and architecture limits. The
other failures, UI-12 and UI-13,
share the unchanged dashboard accessibility assertion: the 759 px mobile top
bar measures 57 px rather than 56 px. The same failure was reproduced using an
isolated snapshot of base commit `ae7ad162`. It is baseline evidence, not a
reason to weaken the test or modify production dashboard assets in this work.

Actual shipped progressive approval rendering, ordinary token/envelope
submission, stale rejection, separate approve/build actions and visible
retirement disclosure have passed their Node tests. These tests do not replace
the separate browser accessibility gate or establish live-model effectiveness.

All sixteen progressive real-CLI tests pass. The final permission audit found
no confirmed runtime-safety defect. Two additional CLI tests cover four denial
and conditional-consent cases, including answering the original frozen valid
pending request after recreating the client. They preserve the unchanged goal
token, checklist, budget, history and delegation, use the original Resolver
envelope, and do not edit or reapprove the goal. No arbitrary legacy serialized
request shape was fabricated to manufacture this coverage.

The final fake catalog reports
49 PASS, one existing refund fixture NOT_EXERCISED and one live-Investigator
scenario SKIPPED; no live spend was authorized. The scenario harness passes
66 tests. Controller ratchets are measured at 1,532 lines for `autocode.py`,
1,311 for `autocode_goals.py`, 679 for `autocode_support.py` and 985 for
`autopilot.py`; no new import cycle is permitted. All implementation changes
remain local and uncommitted, and these issue updates have not been published.
