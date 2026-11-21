# Named recovery offers and their executors' admission (#3852, #3880, #3869)

Status: survey done on the unchanged code (the first part of milestone M1, formerly M0). No
file under `tools/` was changed for it. #3852 is fixed in M1 (below); the other fixes are later
milestones, and this note is finished when they land.

## Shared cause

The public status view decides which recovery action to offer with its own, shorter
predicate instead of asking the code that will execute the action. A client that follows
only the view's named actions (VS Code, the terminal chat, the channel bridge, the headless
conformance client) is then offered an action the core refuses, or is not offered one the
worker can take. Each offer below was compared with its executor by observation: a complete
saved state, the card a client reads (`autocode_run_view.view(...)["recovery"]`, and
`needs`/`next_command`), and the CLI's own handling of exactly the card's `args`
(`autocode_run_actions.handle`, the steps one `autocode --run-dir RUN ...` invocation applies
before it builds) on the same state, with no provider launched.

The fix direction (AGENTS.md "Fix the shared cause", "One answer, read from one place"): each
offer reads the one decision its executor uses, rather than the view learning each failing
case.

## How the survey was observed

- `tests/test_offer_admission_agreement.py`, `test_ac22_surveyed_offers_agree_with_their_executor_admission`
  (guard, passes on the unchanged code): for abandon, retry_job, retry_report, retry_builder and
  retry_failed_stage it builds the admitted state, checks that the card offers exactly the
  AC22 `args`, checks the card's recovery token (`autocode_recovery_view.require_token`) and runs
  those args after `--expected-recovery-token`; then it changes one field, checks the card offers
  no action of that kind and that the executor refuses the same args with the refusal that field
  causes.
- A scratch probe over the same fixtures (`SavedRunFixtures` in that test module) and the
  existing fixtures of `tests/test_operational_disposition.py`, `tests/test_accepted_source.py`
  and `tests/test_blocker_next_action.py` recorded the variants that disagree. Its captured run
  is the M0 evidence receipt `m0-offer-survey-probe.json` (receipt sha256
  `f5a22016c8a60c25e2a1571d79899c9163e32fd9dc6f0e4fcc7ba848f9becfb0`) in the run's evidence
  directory. The probe is evidence only and is not part of the suite.

## The five offers outside the reported cases

Each is in the AC22 guard: its admitted state is admitted and its one-field refused variant is
refused with no offer. Each also has observed variants where the card still offers the action
and the executor refuses it. Those variants are **not** asserted by the guard and are listed
here as open disagreements of the same shared cause.

| Offer (card `args`) | Card predicate | Executor admission it was compared with | Guard: admitted / refused variant (agree) | Observed disagreements (card offers, executor refuses) |
| --- | --- | --- | --- | --- |
| abandon (`--abandon-stage 004/builder-02`), PAUSED_PROVIDER_UNCERTAIN | `autocode_recovery_view.project`: an uncertain status (or a matching `needs.abandon_stage`) and an `active_stage` with `iteration` and `output` | `autocode_stage_recovery.abandon_stage` (`records.assert_stage_stopped`, attempt id match), then `operational_admission.abandoned` | exit 0, `PAUSED_STAGE_ABANDONED`; `active_stage` removed: no offer, "--abandon-stage must match the active attempt_id" | the attempt's recorded pid is still alive (exit_code None): the card offers abandon, the executor pauses `PAUSED_WORKSPACE_BUSY` ("Stage process N still exists; wait for it"). This depends on a live-process observation the card does not make. |
| retry_job (`--resume-paused --retry-failed-stage --job-retry-token job-1`), PAUSED_JOB_FAILURE | `run_view.needs`: `quota_route.job_pause_current` and a saved `source_identity` | `job_failure.resume_gate` then `job_failure.authorize` (`_validate_retry`: token, `source.matches_original`, `retry_configuration_matches`) | admitted, RUNNING at `investigate_stuck`; `source_identity` None: no offer (`recover_source`), "Exact retry is unavailable" | workspace source edited after the failed attempt: "Source changed since the failed attempt; retry is stale"; builder model changed: "Provider configuration or limits changed; retry is stale". The need's `recovery_hint` says to restore the source, but the card still names the retry. |
| retry_report (`--resume-paused --retry-report 004/validator-01`), PAUSED_REPEATED_FAILURE | `run_view.needs`: Tester pending repair, retryable error, `report_retry.bounded_failure`, a rejected attempt | `stage_recovery.retry_format_failed_report` (`report_retry_frontier`: schema files, `same_schema`, source revision, contract hash, pins, stale repair; `prepare_exhausted_execution_report_retry`) | admitted, RUNNING at `tester`; a non-retryable pending error: no offer, "--retry-report must match the bounded rejected report-only attempt" | source edited after the rejection ("The source changed after this report was rejected ..."); pending repair bound to another contract hash ("Saved report inputs changed ..."); the rejected repair's schema file missing ("--retry-report must match ..."). |
| retry_builder (`--resume-paused --retry-builder M1`), PAUSED_BUILDER_RETRY_LIMIT | `recovery_view._builder_members`: next stage builder, no active/runner/repair/parent, lane paused with failures | `dispatch.request_retry` (`goals.execution_guard`, legacy-process check) then `builder_recovery.request_serial_retry` | admitted, RUNNING at `builder`; lane action `retry` (not paused): no card offer, "Serial Builder retry requires an exhausted, stopped approved milestone" | contract returned to draft (executor pauses `PAUSED_GOAL_UNAPPROVED`); current task kind `validate` ("requires an implementation task or a pending Resolver repair"); pending `resolution_request` for another contract ("belongs to a different approved contract"). The lane-action-`retry` variant agrees only on the card: `view.next_command` still names `--resume-paused --retry-builder M1` (the status-keyed flags of `run_view.action` over `operational_information.resume_flags`) and the executor refuses it (M0 receipt `m0-lane-retry-probe.json`). |
| retry_failed_stage (`--resume-paused --retry-failed-stage`), PAUSED_REPEATED_FAILURE | `recovery_view.project`: cause PAUSED_REPEATED_FAILURE, no runner check, `recovery_limits.repeated_failure_retry_refusal` is None | `stage_recovery.authorize_failure_retry` (`repeated_failure_retry_refusal`, then `failure_retry_frontier`: repeated failure, current source, stage, identity) | admitted, RUNNING; `uncertain_artifacts` set: no offer, "Reconcile the active attempt or pending report repair ..." | source edited after the failures, or next stage moved ("Retry authorization requires the exact current source, stage and failure identity"); no failure history recorded ("No unchanged repeated failure to authorize; fix the cause, then resume"). |

None of the five is missing from both the guard and this list.

## Other named offers

- **resume** at `PAUSED_REQUESTED`: offered and admitted (the invocation goes on to build).
- **resume** at `PAUSED_REPEATED_FAILURE` whose pending report repair the retry gate will not
  authorize over (#1897): the card names the plain `--resume-paused` with
  `needs.retry_unavailable`; executing it records the operational exhaustion and blocks the run
  (exit 2, "AutoCode could not safely continue its internal recovery"). The executor acts on it
  (the next view is the blocked card), so this is recorded as an open question rather than a
  refusal: the offered continue ends in a block, not progress.
- **information-review resume** (an `operational_exhaustion` request answered with
  provide_information): `tests.test_operational_information.OperationalInformationCLITests.test_accepted_information_is_reevaluated_exactly_once`
  passes on the unchanged code (M0 receipt `m0-information-review-resume-2.json`): the view
  names `--resume-paused`, the explicit resume re-evaluates the answer exactly once and later
  resumes repeat the held decision. Agrees.

## The four reported disagreements, as observed on the unchanged code

### #3852: PAUSED_STAGE_ABANDONED resume

Fixture: `OperationalDispositionTests.approved_contract()` then `checked_abandon()` in
`tests/test_operational_disposition.py`. View: `autocode_run_view.action`; admission:
`autocode_operational_admission.guard` for a plain `--resume-paused`.

| Change to the saved state | `needs.kind` / `next_command` | admission.guard |
| --- | --- | --- |
| none | resume / `... --resume-paused` | None (admitted) |
| approval_event replaced | resume / `... --resume-paused` | 2 (refused) |
| revision 2 with its recomputed hash | resume / `... --resume-paused` | 2 |
| approval_status draft, approval_event null | resume / `... --resume-paused` | 2 |
| goal_contract removed | resume / `... --resume-paused` | 2 |
| current_task changed | none / None | 2 |

Cause: `autocode_operational_disposition.presentation` (`same_ctx`, near line 689) compares
task_id, task, workspace, run_dir and current_task; `autocode_operational_admission._same_context`
(near line 48) also compares the contract identity (`resolver_human._contract`).

Fix (M1): one decision, read from one place. The contract approval identity projection moved from
`resolver_human._contract` to `autocode_contract_identity.approval_identity` (`_contract` delegates
and still raises on an unsealed contract). `autocode_operational_disposition.same_context(state,
binding)` compares task_id, task, workspace, run_dir, current_task and that contract identity.
`autocode_operational_admission._same_context` returns it unchanged (an unsealed contract still
raises there), and the presentation's resume decision calls it, treating ValueError as no match.
Tests: `test_ac1_abandoned_resume_offer_follows_contract_admission` (the four contract changes:
admission 2, no Resume named; fails on the unchanged code),
`test_ac2_valid_abandoned_run_offers_resume_that_admission_accepts` and
`test_ac3_changed_current_task_suppresses_abandoned_resume` in
`tests/test_operational_disposition.py`.

### #3880: accept-source-edit

Fixture: `AcceptReviewedSourceTests` in `tests/test_accepted_source.py` with the generic
recovery-packet reason "Recovery packet: current source, task, settings, contract or scope
changed before admission". Executor: `accepted_source.accept_reviewed_source`.

| Variant | `needs.action` | card | executor |
| --- | --- | --- | --- |
| source s2 only | `--resume-paused --accept-source-edit` | accept_source_edit | accepted |
| settings only (builder model other, s1) | same | accept_source_edit | refused: "The reviewed source already matches this workspace" |
| scope only (criteria C1, C2, s1) | same | accept_source_edit | refused: same |
| mixed (s2 and builder model other) | same | accept_source_edit | refused: "only accepts a source edit; also changed: settings_hash" |
| older generic-reason pause (request with only `source_revision`) | same | accept_source_edit | refused: "does not accept a contract or task change" |

Cause: `autocode_accepted_source.resume_action` (near line 43) offers the command from the
stop reason and the presence of a source revision. The admission
(`autocode_resolver_recovery._dispatch_frontier`, line 1859) writes the one generic reason for
any failed binding or scope check and records no check names.

### #3869: Stop now for a resumed run

`autocode_run_setup` (near line 947) replaces the saved capability with
`{"supported": true, "version": 1}` on every setup, including a resume. `autocode_pause_offer.project`
on a working run then offers the boundary pause and no `stop_now`. Only a state write through
`autocode_stop.state_writer` (near line 194) adds `supports_stop`, `supports_messages`,
`supports_message_context`, `supports_stop_now` and `supports_message_now`; after one such write
`stop_now` is offered. A missing capability, or one without `supported: true`, offers nothing.
So between a resumed run's setup and its controller's first state write (the observed case: a
resumed local regression check), the view offers no Stop now although the worker consumes an
immediate pause. The CLI reproduction with a held baseline is milestone M2's test.

Newer-process stamp, observed: the stamp is unconditional. Any write of a run's `state.json`
through `state_writer` adds `supports_stop_now` even when the saved capability lacked it, so a
newer process writing an older controller's run would make the view offer an action that
controller cannot consume. The stamp stays (tests in `tests/test_run_messages_boundary.py` and
`tests/test_checkout_lock.py` assert it); this direction is **open**.

### Fourth case: answered escalated blocker

Fixture: `BlockerNeedTests.answer_blocker_with_information` in
`tests/test_blocker_next_action.py` (PAUSED_RESOLVER, approved contract, blocker answered with
provide_information and reviewed). The view: `needs.kind` resume, `next_command` names
`--resume-paused`, `recovery.category` "recovery", card actions inspect and feedback,
`chat.input.kind` feedback. Executing the named `--resume-paused`: exit 2, status stays
PAUSED_RESOLVER, "AutoResolver retained the human guidance. No new execution allowance or
changed cause was established ..." (the hold in `autocode_action_authority`, near lines 404-441,
keyed on `resolver_human.response_holds_current_frontier`). The view offers a resume that can
only re-pause.

## Unchanged-code failures in this environment

Captured with the full modules on the unchanged `tools/` (M0 receipts in the run's evidence
directory). A later run with the fix may show these and no others.

- `tests.test_architecture` (receipt `m0-baseline-architecture.json`, exit 1, 18 tests, 1 error):
  `test_star_import_shims_preserve_consumer_compatibility_exports`, `ModuleNotFoundError: No module named 'autocode_cli'`
  (the package is not installed here).
- `tests.test_json_contract` (receipt `m0-baseline-json-contract.json`, exit 1, 5 tests, 1 failure):
  `test_every_command_at_every_pause_validates_and_exits_by_the_documented_table`, the fake run
  reaches `PAUSED_PROCESS_CLEANUP` instead of `WAITING_FOR_USER`.
- `tests.test_taskrun` (receipt `m0-baseline-taskrun.json`, exit 1, 99 tests, 14 failures and 8 errors):
  - failures: `test_a_first_task_that_only_validates_needs_no_paths_to_be_approved`,
    `test_a_first_task_that_writes_must_name_its_paths_before_anyone_approves_it`,
    `test_fresh_run_reaches_plan_approval_without_a_migration_question`,
    `test_fresh_run_still_asks_a_genuine_planning_question`,
    `test_joint_planning_upgrade_before_first_draft_reaches_approval_and_completes`,
    `test_plan_approval_assigns_the_selected_ignored_source_revision`,
    `MixedRoleTransportTaskRunTests.test_public_start_approval_stage_dispatch_reports_sessions_and_reattach`,
    `test_rejected_verification_change_is_reported_to_the_caller`,
    `MixedRoleTransportTaskRunTests.test_saved_override_and_selected_identity_drift_refuse_before_any_launch`,
    `test_start_approve_and_complete`, `test_status_shows_the_planning_depth_decisions` (subtests
    clarity clear and vague), `test_two_answers_use_the_rotated_token_and_status_stays_json`,
    `test_usage_errors_are_not_mistaken_for_a_pause`;
  - errors: `test_a_default_launch_keeps_its_job_in_its_own_task_worktree`,
    `test_checkpoints_enabled_after_drafting_repair_before_approval`,
    `test_documented_answer_loop_uses_each_pass_token_and_refuses_a_stale_one`,
    `test_each_listed_question_is_answered_against_the_current_request`,
    `test_follow_up_can_revise_a_protected_behavior_but_still_needs_new_plan_approval`,
    `test_saved_finalized_first_task_returns_to_its_final_report_repair`,
    `test_saved_task_from_a_repaired_planner_can_be_repaired_again`,
    `test_saved_unassignable_first_task_returns_to_repair_before_approval`.

Cause: supervised fake-provider launches are refused by the process keeper here ("Keeper exited
before acknowledging ownership ... BoundaryError: Invalid or unbound listener descriptor
transfer", printed in nine of the taskrun failure reports, two of them the planning-depth
subtests). The json_contract fake run stops at `PAUSED_PROCESS_CLEANUP` on the same launch path;
its output does not print the keeper error. The other taskrun failures fail later on a state
their fake run never reached (a missing token, contract or question, or a refusal not raised);
they are attributed to the same refused launch, not proven one by one. The architecture error is
`autocode_cli` not being installed.

## VS Code rendering of the answered-blocker view (read from source)

`vscode-extension/src/panel.ts` `stateFor` (near lines 451-458) treats a recovery card of category
`stopped` or `blocked` as stopped: it shows the card's `title` and `what_happened` and never asks
the controller to plan the next command, so no `ClientGap` and no "No action the client can take"
box is rendered for it. The composer (`updateComposer`, near line 2357) is enabled from
`chat.input` alone. A blocked answered-blocker card with a feedback `chat.input` therefore shows
its title and explanation with the feedback composer enabled. The panel is unchanged by this work.

## Open

The packet's failed binding names are kept in the pending request and in a
`recovery_packet_stale` event, without bound values. Source acceptance calls the same
source-only admission as the executor. An answered blocker at the same frontier has
no plain-resume command, and retains feedback only where a real decision admits it.

The stopped-process check for abandon, job-retry source and configuration checks,
report-retry frontier, serial Builder lane and approval checks, and repeated-failure
frontier are now shared with their executors. A refused serial Builder retry also
removes the previously advertised `next_command`.

- The stopped parallel batch member's `retry_builder` admission remains to be shared.
- The resume at a `PAUSED_REPEATED_FAILURE` with `retry_unavailable` ends in an operational block.
- The newer-process `supports_stop_now` stamp (#3869, above).
- Client documentation outside this run's permitted paths: the `docs/task-run.md` resume-need row
  for the planned `needs.resume_unavailable`.
- Verify M2's controller capability against the original stop-now checks and complete
  the parallel member's admission survey before marking M4 complete.
