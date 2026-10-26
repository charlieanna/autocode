"""The typed shape of run state (#695, AGENTS.md rule 4).

Run state is still written as a plain dict; this module names every key it
uses so a reader can tell who writes a field and who reads it. Replacing the
dict in one module at a time is the next step; until then the dict stays the
runtime and this is the contract beside it. Consumers import ``RunState`` only
under ``TYPE_CHECKING`` with a relative import, so installed and flat imports
do not load this contract merely to resolve an annotation.

Prefer existing keys. If you must add one, add it here with its writer and
reader in the same change; ``tests/test_run_state.py`` fails on a key tools/
touches that is not named here.
"""

from __future__ import annotations

from typing import Any, TypedDict


class InteractionTiming(TypedDict):
    """The timing record written by interaction_timing and read by the view.

    Keep its shape beside RunState so the contract does not import a writer.
    """

    launched_at: str
    first_question_at: str | None
    first_plan_at: str | None
    first_builder_at: str | None


# Every key tools/ reads or writes on a run state dict.
# Generated from the source; add a new one here in the same change that introduces it.
KEYS: frozenset[str] = frozenset(
    {
        "_authorized_bound_change",
        "runner",
        "_fixture_accepted",
        "_fixture_attempt",
        "_fixture_interventions",
        "_fixture_model_confirm_mode",
        "_fixture_model_reconcile_proposed",
        "_fixture_model_reconcile_reads",
        "_fixture_model_reconcile_role",
        "_fixture_monitor",
        "_fixture_pause_reconcile_reads",
        "_fixture_saved_diff",
        "_fixture_verification",
        "acceptance_criteria",
        "acceptance_tests",
        "active_runner_check",
        "active_seconds",
        "active_stage",
        "active_stage_workers",
        "affected_paths",
        "agent_request",
        "alive",
        "answer",
        "answers",
        "applied_interventions",
        "automatic_capacity_recoveries",
        "automatic_permission_recoveries",
        "automatic_recoveries_since_resume",
        "automatic_timeout_recoveries",
        "base_commit",
        "brief_feedback",
        "builder_failure_hold",
        "builder_retries",
        "builder_retry_decisions",
        "builder_retry_key",
        "changed_files",
        "changeset_approval",
        "checked",
        "checkpoint_continuation",
        "checkpoint_history",
        "checkpoint_restores",
        "clarification_episode",
        "code_checkpoints",
        "git_delivery",
        "completed_at",
        "completion_archive",
        "completion_actor",
        "completion_sent_back",
        "configuration_changes",
        "consecutive_timeout_recoveries",
        "consultation_reports",
        "continued",
        "contract",
        "contract_history",
        "conversation_handoff",
        "conversation_id",
        "created_at",
        "criteria_revision",
        "current_task",
        "decisions",
        "deferred_backlog",
        "deferred_obligations",
        "dependency_wait",
        "design_check",
        "design_constraint",
        "design_input_changes",
        "design_intake",
        "design_review",
        "diagnosis_request",
        "diff_ref",
        "direct_rework_assignments",
        "discovery_summary",
        "displayed_goal",
        "displayed_handoff",
        "displayed_review",
        "efficiency_observations",
        "engine",
        "error",
        "events",
        "evidence_export",
        "evidence_locations",
        "execution_checkpoints",
        "execution",
        "failure_history",
        "failure_retry_authorizations",
        "figma_file",
        "final_audit_request",
        "final_decision",
        "findings_ledger",
        "findings_seq",
        "finished_at",
        "generated_sources_at_start",
        "goal_base_check",
        "goal_contract",
        "history",
        "human_review_archive",
        "human_reviews",
        "implementation",
        "implementation_handoffs",
        "inherited_spend",
        "input",
        "intake_input_hash",
        "intervention_ack_pending",
        "interaction_timing",
        "intervention_capability",
        "investigation",
        "investigation_request",
        "iteration",
        "job_failure",
        "job_retry_authorization",
        "key",
        "kind",
        "lanes",
        "last_decision",
        "launch_sources",
        "machine_resolutions",
        "manifest_sha256",
        "metadata",
        "migration",
        "milestone_activation_requests",
        "milestone_blocker",
        "milestone_carry_forward",
        "milestone_progress",
        "models",
        "name",
        "needs",
        "next_action",
        "next_stage",
        "no_progress_batches",
        "no_progress_reports",
        "orchestration_batch",
        "orchestration_history",
        "output",
        "outputs",
        "package_outside_at_launch",
        "parent_batch",
        "parent_run",
        "pause",
        "pause_acknowledgements",
        "pause_intent",
        "pause_requested",
        "paused_at",
        "pending_builder_failure",
        "pending_context_metrics",
        "pending_planning_artifacts",
        "pending_planning_outputs",
        "pending_questions",
        "pending_report_repair",
        "permission_reuse_context",
        "permission_reuses",
        "phase",
        "plan",
        "plan_source",
        "planning",
        "planning_artifact_history",
        "planning_artifact_reconciliation",
        "planning_artifacts",
        "planning_final",
        "planning_final_archive",
        "planning_history",
        "planning_iteration",
        "planning_migrations",
        "pre_goal_checkpoint",
        "private_source_exceptions",
        "progress",
        "progress_checkpoint",
        "progress_messages",
        "progress_sequence",
        "progressive",
        "project_memory",
        "project_worked_in_place",
        "project_workspace",
        "prompts_hash",
        "reasoning_escalations",
        "reconciliation_notes",
        "recovery",
        "recovery_context",
        "recovery_context_archive",
        "recovery_grants",
        "regression_baseline",
        "regression_proof",
        "regression_proofs",
        "repair_plan",
        "report_repair_archive",
        "report_repair_history",
        "report_repair_lifetime_attempts",
        "reports",
        "replay_result",
        "requirements_artifact_token",
        "requirements_body",
        "requirements_handoff",
        "requirements_history",
        "resolution_history",
        "resolution_request",
        "resolver",
        "resolver_human_proposal",
        "resolver_human_request",
        "retained_candidate_handoffs",
        "review",
        "run_dir",
        "session_rotations",
        "sessions",
        "settings",
        "source_log",
        "source_revision",
        "source_snapshot",
        "stages",
        "status",
        "stop_intent",
        "stop_reason",
        "stuck_investigation",
        "stuck_investigations",
        "targeted_consultation",
        "task",
        "task_archive",
        "task_branch",
        "task_id",
        "task_preflight",
        "task_sha256",
        "tasks",
        "turns",
        "ui_run",
        "uncertain_artifacts",
        "unit_handoffs",
        "unresolved_findings",
        "updated_at",
        "user_events",
        "user_request",
        "validation",
        "validation_archive",
        "validation_only_rounds",
        "validator_rechecks",
        "verification_count",
        "verifications",
        "version",
        "visual_acceptance_receipts",
        "workflow",
        "workspace",
        "workspace_scope",
        "workspace_bootstrap",
    }
)


class RunState(TypedDict, total=False):
    """What a run state dict holds. total=False: most keys appear only sometimes.

    Writers and readers are named for the core keys; a stage-local key names its
    module. ``version`` is the state shape version (2 or 3).
    """

    # --- core (status view / most stages) ---
    version: int  # written at run start; read by every loader
    task: str  # the original request; read by goals, briefing
    task_sha256: str  # written by task_input.assign; run_view derives it from saved text, including legacy runs
    task_id: str  # current task id; written by goals
    workspace: str  # the project (or task worktree) root
    workspace_scope: dict  # workspace_scope_state.record owns validated current-turn external work; Requirements/Investigator and scope gate read it
    changeset_approval: dict  # changeset_barrier.approve writes exact set/standalone grant; checkpoint publication and execution guard read it
    project_workspace: str  # the project a task worktree belongs to
    # The folder a launch selected that held no Git repository, and the project AutoCode created to
    # run in: {selected, project}, equal when an empty folder became the project itself. Written once
    # by autocode_run_setup when it bootstraps; read only by autocode_chat_history, which says so once
    # in the conversation instead of leaving a client to infer it from workspace (#1908).
    workspace_bootstrap: dict
    project_memory: dict  # project_memory_lifecycle owns launch input/tags/publication; Requirements reads context
    run_dir: str  # this run's .autocode/runs/<id>
    status: str  # RUNNING / PAUSED_* / TASK_COMPLETE; written by the runner
    phase: str  # PLANNING / EXECUTING / ...; written by the runner
    stop_reason: str  # why a pause happened; read by explain/status
    paused_at: str  # pause/owner writers; read and cleared by run_setup on resume
    iteration: int  # the planning/execution iteration
    next_stage: str | None  # controller queues a stage; completion clears it
    sessions: dict  # provider session ids by role
    history: list  # finished stage records (autocode_run_view)
    stages: list  # saved stage records; written by the runner
    active_stage: dict | None  # the in-flight stage record
    settings: dict  # roles, budgets, engine, workflow mode; configure owns effort_origins (reviewer effort policy)
    prompts_hash: str  # written once by run_setup; read by run_view/evidence_export; launch prompt set
    acceptance_criteria: list  # the approved criteria; written by goals
    acceptance_tests: dict  # spec-first receipts, redirect and contract-bound pending test names; route pins names, job writes receipts; read by job/check/run_view
    criteria_revision: str | None  # hash of the criteria; follow_up clears it before a fresh plan
    # The approved contract; written by goals. A revision loaded with --goal-file also names that file in
    # its record's plan_file (autocode_goal_file writes it, and reads it for the approval stop;
    # autocode_plan_file_origin reads it for run_view's approved_contract and the evidence report).
    goal_contract: dict | None
    current_task: dict | None  # the task the Builder is on
    user_events: list  # goals writes answers/approvals; plan_chat writes plan_message intent/reply_after/forwarded receipts; technical_questions and timeouts append receipts
    answers: dict  # question id -> answer text
    pending_questions: list  # lifecycle writes questions; operational_change pins offered timeout settings
    user_request: dict | None  # a pending decision request
    validation: dict | None  # the latest independent validation (autoreview)
    validation_archive: list  # superseded validations
    # One {attempt, frontier, at} per admitted validation-only Validator round; appended and read only by
    # autocode_validation_rounds.admit (via its KEY constant, which the key scan in tests/test_run_state.py follows).
    validation_only_rounds: list[dict]
    findings_ledger: list  # open/closed findings (autocode_findings)
    implementation: dict | None  # the Builder build-candidate record
    # What the run a checkpoint continuation restored from spent: {usd, unpriced_stages, partial_stages}
    # (written by autocode_checkpoint_continuation.create; read by autocode_cost, so the money cap counts it).
    inherited_spend: dict
    base_commit: str  # the revision a bug fix is proven against
    # What a package run's checkout held outside the package at launch that base_commit cannot hold (an
    # untracked Git repository, a submodule's uncommitted work): {path: added or modified}, {} when nothing.
    # Written once by autocode_package_writes.at_launch, in every new package run once its base is saved
    # (autocode_run_setup, or autocode_regression.start for an in-place run and one that found its checkout
    # busy); read only by autocode_package_writes, whose check leaves out what it names while it is still a
    # Git repository. A package run started before the record existed has none: its check leaves out every
    # nested repository and submodule work it finds.
    package_outside_at_launch: dict[str, str]

    # --- recovery / diagnosis ---
    recovery_context: dict | None  # what the last pause knew (autocode_recovery)
    # Pending Resolver request; resolver_recovery.retain_diagnosis writes its
    # diagnosis_binding, read by autopilot.queue_resolution before reuse.
    resolution_request: dict | None
    stuck_investigation: dict  # stuck_job writes a request; removed when no investigation is active
    stuck_investigations: list  # the investigation history
    # autocode_report_repair_queue.queue writes it for a rejected completed report (autocode.reject_completed_stage,
    # native PlanningCalls.reject), stage_recovery for truncated reviews and missing executed-event evidence;
    # execute_report_repair runs it, repaired_result, the resume and recovery archives and native
    # PlanningCalls.archive_rerouted_repair (its route changed) consume it;
    # stuck_job holds and restores it around an investigation.
    pending_report_repair: dict  # Requirements chat reads original.events for provisional display during repair.
    uncertain_artifacts: list  # attempts whose cleanup is unverified
    job_failure: dict  # job_failure writes the classified failure; quota_route reads it
    failure_history: dict  # failure identity -> attempts (builder_failure)
    builder_retries: dict  # retry allowances by lane
    no_progress_batches: int  # consecutive no-progress batches

    # --- human review / approvals ---
    human_reviews: dict  # criterion id -> the review binding
    brief_feedback: list  # user feedback on the brief
    # Append-only hashed log of human source texts (#595): run_setup seeds it from
    # the initial task; goals/follow_up append at the answer/feedback ledger
    # boundaries; autocode_brief_obligations.sources reads it (legacy runs: positional).
    source_log: list
    last_decision: dict | None  # the latest plan decision
    contract_history: list  # prior contract revisions; chat_history reads plans matched to approval receipts
    # Who resumed the run out of each pause (#722): {at, pause_status, provenance}, appended by
    # autocode_approval_provenance.acknowledge when, after an explicit resume, the build loop passes every check
    # that could hold the run at that pause and launches work; read by its view (the status view's ``approvals``,
    # the completion summary). Not user_events: every AutoResolver request binds those, and the deferred-approval
    # bound counts them as the person's input.
    pause_acknowledgements: list

    # --- orchestration / progressive ---
    orchestration_batch: dict | None  # a parallel Builder batch
    parent_run: str | None  # the parent of a child run
    progressive: dict | None  # progressive-planning record
    milestone_progress: dict  # per-milestone progress (autocode_milestones)

    # --- others (see KEYS for the full set) ---
    workflow: dict  # workflows.begin/apply/pin write the recognition record; absent before recognition
    turns: list  # follow_up writes; chat_history reads previous.chat_response for retained completed-turn text
    replay_result: dict | None  # replay_checks owns archival obligation/result; view reads it, never current proof
    regression_proof: dict | None  # the bug-fix fail-before/pass-after proof
    active_runner_check: dict | None  # an in-flight runner-owned check
    evidence_export: dict | None  # written only by evidence_export.record; read by public view/TaskRun
    finished_at: str | None  # legacy completion time read by evidence_export; no new writer

    _authorized_bound_change: Any
    _fixture_accepted: Any
    _fixture_attempt: Any
    _fixture_interventions: Any
    _fixture_model_confirm_mode: Any
    _fixture_model_reconcile_proposed: Any
    _fixture_model_reconcile_reads: Any
    _fixture_model_reconcile_role: Any
    _fixture_monitor: Any
    _fixture_pause_reconcile_reads: Any
    _fixture_saved_diff: Any
    _fixture_verification: Any
    active_seconds: Any
    active_stage_workers: Any
    affected_paths: Any
    agent_request: Any
    alive: Any
    answer: Any
    applied_interventions: Any
    automatic_capacity_recoveries: Any
    automatic_permission_recoveries: Any
    automatic_recoveries_since_resume: int  # legacy name: recovery_accounting tracks failures since valid saved stage
    automatic_timeout_recoveries: list[dict]  # stage_recovery appends; recovery_accounting reads legacy spend
    builder_failure_hold: Any
    builder_retry_decisions: Any
    builder_retry_key: Any
    changed_files: Any
    checked: Any
    checkpoint_continuation: Any
    checkpoint_history: Any
    checkpoint_restores: Any
    clarification_episode: Any
    code_checkpoints: Any
    completed_at: Any
    completion_archive: Any
    completion_actor: Any  # who declared completion; autocode.py writes, pop clears it on rework/stop
    completion_sent_back: Any
    configuration_changes: Any
    consecutive_timeout_recoveries: int  # stage_recovery increments; recovery_accounting clears/reads
    consultation_reports: Any
    continued: Any
    contract: Any
    conversation_handoff: Any
    conversation_id: Any
    created_at: Any
    # Written only by autocode_interaction_timing; read by view/evidence and sweep timing reports.
    interaction_timing: InteractionTiming
    decisions: Any
    deferred_backlog: Any
    deferred_obligations: Any
    dependency_wait: Any
    design_check: Any
    design_constraint: Any
    design_input_changes: Any
    design_intake: Any
    design_review: Any
    diagnosis_request: Any
    diff_ref: Any
    direct_rework_assignments: Any
    discovery_summary: Any
    displayed_goal: Any
    displayed_handoff: Any
    displayed_review: Any
    efficiency_observations: Any
    engine: Any
    error: Any
    events: Any
    evidence_locations: Any
    execution_checkpoints: Any
    execution: dict  # worker_binding alone writes service launch/owner identity; status/view read it
    # Retry writers retain audit records; Resolver retries may include exact resolver_route_change
    # before/after provenance, read only by recovery binding normalization (never spending credit).
    failure_retry_authorizations: Any
    figma_file: Any
    final_audit_request: Any
    final_decision: Any
    findings_seq: Any
    generated_sources_at_start: Any
    goal_base_check: Any
    human_review_archive: Any
    implementation_handoffs: Any
    input: Any
    intake_input_hash: Any
    intervention_ack_pending: Any
    intervention_capability: Any
    investigation: Any
    investigation_request: Any
    job_retry_authorization: Any
    key: Any
    kind: Any
    lanes: Any
    launch_sources: Any
    machine_resolutions: Any
    manifest_sha256: Any
    metadata: Any
    migration: Any
    milestone_activation_requests: Any
    milestone_blocker: Any
    milestone_carry_forward: Any
    models: Any
    name: Any
    next_action: Any
    no_progress_reports: Any
    orchestration_history: Any
    output: Any
    outputs: Any
    parent_batch: Any
    pause: Any
    pause_intent: Any
    pause_requested: Any
    pending_builder_failure: Any
    pending_context_metrics: Any
    pending_planning_artifacts: Any
    pending_planning_outputs: Any
    permission_reuse_context: Any
    permission_reuses: Any
    plan: Any
    plan_source: Any
    planning: Any
    planning_artifact_history: Any
    planning_artifact_reconciliation: Any
    planning_artifacts: Any
    planning_final: Any
    planning_final_archive: Any
    planning_history: Any
    planning_iteration: Any
    planning_migrations: Any
    pre_goal_checkpoint: Any
    private_source_exceptions: Any
    progress: Any
    progress_checkpoint: Any
    progress_messages: Any
    progress_sequence: Any
    project_worked_in_place: Any
    reasoning_escalations: Any
    reconciliation_notes: Any
    recovery: Any
    recovery_context_archive: Any
    recovery_grants: list[dict]  # recovery_accounting appends issued grant receipts; recovery reads
    regression_baseline: Any
    regression_proofs: Any
    repair_plan: Any
    report_repair_archive: Any
    report_repair_history: Any
    report_repair_lifetime_attempts: Any
    reports: Any
    requirements_artifact_token: Any
    requirements_body: Any
    requirements_handoff: Any
    requirements_history: Any
    resolution_history: Any
    resolver: Any
    resolver_human_proposal: Any  # a Resolver proposal awaiting a human; goals writes, dependency pops
    resolver_human_request: Any
    retained_candidate_handoffs: Any
    review: Any  # review_job writes outcome, report digest and post-report source_revision; status inspects freshness
    session_rotations: Any
    source_revision: Any
    git_delivery: Any  # controller-owned commit + receipt pin; completion gate reads it
    source_snapshot: Any
    stop_intent: Any
    targeted_consultation: Any
    task_archive: Any
    task_branch: Any
    task_preflight: Any
    tasks: Any
    ui_run: Any
    unit_handoffs: Any
    unresolved_findings: Any
    # Saved-ready dashboard fixture timestamp: workspace_visual_fixture writes it;
    # dashboard_app.js reads run.updated_at as a display timestamp fallback.
    updated_at: str
    validator_rechecks: Any
    verification_count: Any
    verifications: Any
    visual_acceptance_receipts: Any


__all__ = ["KEYS", "RunState"]
