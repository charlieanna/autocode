# Scope record for #22 and #23

Issues #22 (big projects as workstreams under one approved agreement) and #23
(versioned interfaces, a walking skeleton first, a final check by user journey) are
one feature: #23 says to design it alongside #22. Two versions exist:

- **Single-run progressive planning**, merged in #204: one continuing run delivers the
  product in slices under one approved contract.
- **The multi-run program agreement**: `autocode program` ([Programs](../program.md)),
  where each workstream is its own run, on branch `claude/elegant-planck-ynb2dq` (no
  pull request yet).

Neither issue is done. Still owed: #16's done tests, a real project that needs this,
and a live run that reaches the program code (AGENTS.md: until then a pull request is a
draft that says so). Keep #16 open: offline rule coverage is not live effectiveness. Do
not close #22 or #23, and do not delete their prerequisites.

## Start-condition exceptions

Both issues say not to start until a real project needs this and #16's done tests pass.

- **2026-10-01, single run.** The user authorized the smaller single-run
  progressive-planning version to proceed before those prerequisites. It does not
  establish that #16 passed, does not authorize live trials or spending, and does not
  enable the multi-run hierarchy.
- **2026-10-05, multi run.** The owner asked to continue the multi-run work from branch
  `master-947o2v` (commit 32df278, "Program agreement: approval, pins, re-checks,
  interfaces, skeleton, journeys"), before #16's done tests and before a real project.
  The work continues on `claude/elegant-planck-ynb2dq`. Like the first, this exception
  does not satisfy or remove those prerequisites.

## #22: what the original proposal deferred, and where it stands

The #204 scope comments (2026-10-01 and 2026-10-02) deferred four parts of the original
proposal. The program agreement delivers three of them:

| Deferred in #204 | Now |
| --- | --- |
| Project → Workstream → Milestone → Task as separate child runs | **Delivered.** Each workstream is an ordinary `autocode --workflow build` run in its own worktree, merged onto one integration branch; milestones and tasks stay inside each run |
| A shared parent agreement file | **Delivered** as the program manifest, approved by exact token (`a<revision>:<digest>`), every revision approved too. It is not shared "across lanes": `autocode tasks` lanes are unchanged, and a program replaces them for work that must be combined |
| Multi-run approval invalidation | **Delivered.** Each workstream records the fingerprint of its part of the agreement (`pin`); an approved revision that changes it makes the workstream `STALE`, retires its run and starts a fresh one that asks for approval again. Unaffected workstreams keep their approval |
| Cross-run delegation | **Not delivered, by design.** Approving the agreement approves no workstream's plan; every workstream run asks for its own exact approval. The single-run continuation delegation from #204 is unchanged |

The reviewer's questions, as built: results are combined by `--no-ff` merges onto the
integration branch, each followed by the cumulative checks; all workstreams live in
one repository; the first real project is still open.

## #23: what the original proposal deferred, and where it stands

| Deferred in #204 | Now |
| --- | --- |
| Versioned producer/consumer interface contracts shared across independent workstreams | **Delivered** as manifest entries (`shared.interfaces[]` with `version`, `producer`, `consumers`, `schema`, `behavior`), not as separate artifact files. The person approves every interface and every change to it, by approving the agreement; no model does |
| Change-request invalidation of sibling workstream approvals | **Delivered.** `program request-change` holds every workstream bound to the interface (its producer and consumers, every workstream when it has no producer, and the final check) and keeps the program from completing; accepting it is an approved revision that publishes the next version, after which those that had started are re-checked; `program resolve-change --reject` rejects it; a revision that removes the interface withdraws it |

At program level the program agreement also requires exactly one walking-skeleton
workstream, merged and verified before any other starts (a `skeleton_exempt` reason
lets a part that connects to nothing at run time go ahead, the issue's second
question); re-runs the cumulative checks after every merge and undoes its own failing
merge; and completes only when the final check verifies every named journey, listing
what a simulated journey does not prove.

### Corrections to the single-run claims

The #204 texts below overstate two points for the single-run version:

- "The final product check follows the goal's named user journeys": a single-run
  contract has no named journeys. Its final check is the full product checklist (every
  acceptance criterion), not the last slice's tests. Named journeys exist only in a
  program agreement.
- "Simulated-scale results must say what they do not prove": only a program journey
  enforces this (`simulated: true` needs `does_not_prove`). The single-run version has
  no such rule.

Also, the single-run first slice is a walking skeleton by instruction: the Planner is
told that the first slice walks the main user journey, and the runner checks only that
it has bounded paths, product criterion ids and nonempty replayable checks.

### Open decision

Should single-run progressive planning also require a walking-skeleton first slice
(checked, not only asked for) and named journeys, as programs do? Undecided, and no work
has started. Until it is decided, single-run progressive planning is unchanged
(`tools/units/autoplanner.py`, `tools/autocode_progressive_plan.py`).

## Done when → proof

The proofs below are offline: scripted children (`ProgramHarness` in
`tests/test_program.py`), real child runs under the scripted model (the
`program-notes-cli` scenario, `scenarios/run.py run program-notes-cli --fake`, and its
`FakeRunTests` in `scenarios/test_harness.py`), and the fake Codex provider. None is a
live run.

### #22

| Done when | Proof |
| --- | --- |
| A sample project with two workstreams runs, each workstream a normal job linked to the parent agreement | `tests/test_program_agreement_runs.py` `test_each_workstream_is_a_normal_run_pinned_to_the_agreement_it_was_built_from`; `program-notes-cli` (`FakeRunTests.test_a_program_verifies_its_skeleton_first_and_rechecks_an_interface_change`: three workstreams and the final check, each a build run, merged under the approved agreement; oracle `every_workstream_a_merged_reviewed_run`); `tests/test_program.py` `CliFixtureTest` (the first wave as real CLI runs) |
| A workstream plan that drops an inherited requirement is rejected | `tests/test_program_agreement_runs.py` `test_a_draft_that_drops_an_inherited_requirement_is_sent_back_before_anyone_approves_it`, `test_an_approved_plan_that_drops_an_inherited_requirement_never_merges`, `test_a_child_that_completes_without_showing_a_conforming_plan_never_merges`, `test_a_child_that_refuses_the_programs_feedback_pauses_for_a_person`, `test_repeated_dropping_plans_pause_for_a_person`, `test_after_a_person_acts_on_a_paused_plan_the_child_re_plans_and_merges_only_a_conforming_plan`; `tests/test_program_agreement.py` `InheritanceTest`. Scripted children only: the scenario's planner keeps every inherited id |
| Changing the parent agreement means the affected workstreams need approval again | `tests/test_program_agreement_runs.py` `test_a_revision_takes_approval_only_from_the_workstreams_it_affects`, `test_a_workstream_waiting_for_approval_loses_it_when_its_part_of_the_agreement_changes`; `tests/test_program.py` `test_nothing_starts_until_the_agreement_is_approved`; `tests/test_program_agreement.py` `ScopeTest`, `RevisionTest`; `program-notes-cli` (the accepted change request's revision re-checks exactly the producer and both consumers under the new revision, retiring those that had started; oracle `change_rechecked_producer_and_consumers[store]`) |
| A normal single job creates no project-level files | `tests/test_taskrun.py` `TaskRunTests.test_start_approve_and_complete` (`assert_no_project_level_files`: a completed job through the task-run interface leaves only its run and the runner's housekeeping, no `.autocode/programs`, `.autocode/task-flows`, `.autocode-components` or `<run>/progressive`, no extra worktree and no program branch) |

### #23

| Done when | Proof |
| --- | --- |
| When one part requests a change to an agreement, the parts that use it lose their approval and can't be done until re-checked | `tests/test_program_agreement_runs.py` `test_an_interface_change_request_takes_approval_from_its_producer_and_users`, `test_an_open_change_request_holds_the_final_check_and_outranks_completion`, `test_a_request_on_an_interface_without_a_producer_holds_every_workstream_until_withdrawn`, `test_a_delivered_interface_is_never_changed_in_place`; `tests/test_program_agreement.py` `RevisionTest.test_interface_definitions_change_only_with_a_new_version`, `QuietInterfaceChangeTest`; `program-notes-cli` (oracle `change_request_accepted[store]`, `change_rechecked_producer_and_consumers[store]`) |
| No feature part can be done before the thin working version is verified | `tests/test_program_agreement_runs.py` `test_the_walking_skeleton_is_verified_before_any_other_workstream_starts`, `test_every_merge_reruns_the_cumulative_checks_and_a_failing_merge_is_undone`; `tests/test_program_agreement.py` `ValidateTest.test_exactly_one_code_skeleton_without_dependencies`, `test_code_workstreams_extend_the_skeleton_unless_exempt`; `program-notes-cli` (oracle `skeleton_verified_first`, `nothing_started_before_the_skeleton`, `cumulative_checks_rerun`; `FakeRunTests.test_a_workstream_that_breaks_the_skeleton_journey_is_undone_by_the_cumulative_checks`) |
| The final product check refers to the user journeys in the project agreement by name | `tests/test_program_agreement_runs.py` `test_the_final_check_follows_named_journeys_and_says_what_simulations_do_not_prove`, `test_a_journey_the_final_check_did_not_verify_keeps_the_program_open`; `tests/test_program_agreement.py` `ValidateTest.test_journeys_are_required_and_well_formed`; `program-notes-cli` (oracle `journey_verified_by_name[capture-and-find]`). `FakeRunTests.test_a_defect_only_the_hidden_journey_catches_is_a_false_completion` shows the limit: the program's own checks can pass a defect that only the hidden journey test catches |

Known open bug on the re-check path: a re-checked workstream whose files already
conform stalls when its run plans an implementation task
([docs/bugs/2026-10-06-program-recheck-implement-stall.md](../bugs/2026-10-06-program-recheck-implement-stall.md)).
The scenario's scripted planner plans validation there, so the scenario does not show
it.

Known open bug on retirement: retiring the final check's run leaves its edits in the
shared integration worktree, and the program pauses at `PAUSED_INTEGRATION_DIRTY` until
a person commits or discards them
([docs/bugs/2026-10-06-program-integration-retired-leftovers.md](../bugs/2026-10-06-program-integration-retired-leftovers.md)).

## History: the #204 scope texts (2026-10-01)

The rest of this page is the record written for #204, before it merged. Versions of
the two "Proposed edit" texts were posted as comments on #22 and #23 on 2026-10-01 and
2026-10-02. Their "Deferred" lists are superseded by the sections above, and their
single-run claims are corrected there. They are kept as written.

### Proposed edit for #22 (workstreams / project agreement)

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

### Proposed edit for #23 (interface contracts / walking skeleton)

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

### Version-one gates (#204)

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

### Verification notes (#204)

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
`autopilot.py`; no new import cycle is permitted. At the time, all
implementation changes were local and uncommitted and these issue updates were
unpublished; #204 has since merged and the updates were posted.
