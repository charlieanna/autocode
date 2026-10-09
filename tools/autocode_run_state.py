"""The typed shape of run state (#695, AGENTS.md rule 4).

Run state is still written as a plain dict; this module names every key it
uses so a reader can tell who writes a field and who reads it. Replacing the
dict in one module at a time is the next step; until then the dict stays the
runtime and this is the contract beside it.

Prefer existing keys. If you must add one, add it here with its writer and
reader in the same change; ``tests/test_run_state.py`` fails on a key tools/
touches that is not named here.
"""
from __future__ import annotations

from typing import Any, TypedDict

# Every key tools/ reads or writes on a run state dict.
# Generated from the source; add a new one here in the same change that introduces it.
KEYS: frozenset[str] = frozenset({
    "_authorized_bound_change",
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
    "active_runner_check",
    "active_seconds",
    "active_stage",
    "active_stage_workers",
    "affected_paths",
    "agent_request",
    "agreement",
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
    "change_requests",
    "changed_files",
    "checked",
    "checkpoint_continuation",
    "checkpoint_history",
    "checkpoint_restores",
    "clarification_episode",
    "code_checkpoints",
    "completed_at",
    "completion_archive",
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
    "evidence_locations",
    "execution_checkpoints",
    "failure_history",
    "failure_retry_authorizations",
    "figma_file",
    "final_audit_request",
    "final_decision",
    "findings_ledger",
    "findings_seq",
    "generated_sources_at_start",
    "goal_base_check",
    "goal_contract",
    "history",
    "human_review_archive",
    "human_reviews",
    "implementation",
    "implementation_handoffs",
    "input",
    "intake_input_hash",
    "integration",
    "interfaces",
    "intervention_ack_pending",
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
    "next_action",
    "next_stage",
    "no_progress_batches",
    "no_progress_reports",
    "orchestration_batch",
    "orchestration_history",
    "output",
    "outputs",
    "parent_batch",
    "parent_run",
    "pause",
    "pause_intent",
    "pause_requested",
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
    "project_worked_in_place",
    "project_workspace",
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
    "requirements_artifact_token",
    "requirements_body",
    "requirements_handoff",
    "requirements_history",
    "resolution_history",
    "resolution_request",
    "resolver",
    "resolver_human_request",
    "retained_candidate_handoffs",
    "retired_integrations",
    "review",
    "run_dir",
    "session_rotations",
    "sessions",
    "settings",
    "skeleton",
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
    "tasks",
    "turns",
    "ui_run",
    "uncertain_artifacts",
    "unit_handoffs",
    "unresolved_findings",
    "user_events",
    "user_request",
    "validation",
    "validation_archive",
    "validator_rechecks",
    "verification_count",
    "verifications",
    "version",
    "visual_acceptance_receipts",
    "workflow",
    "workspace",
    "workstreams",
})


class RunState(TypedDict, total=False):
    """What a run state dict holds. total=False: most keys appear only sometimes.

    Writers and readers are named for the core keys; a stage-local key names its
    module. ``version`` is the state shape version (2 or 3).
    """

    # --- core (status view / most stages) ---
    version: int                          # written at run start; read by every loader
    task: str                             # the original request; read by goals, briefing
    task_id: str                          # current task id; written by goals
    workspace: str                        # the project (or task worktree) root
    project_workspace: str                # the project a task worktree belongs to
    run_dir: str                          # this run's .autocode/runs/<id>
    status: str                           # RUNNING / PAUSED_* / TASK_COMPLETE; written by the runner
    phase: str                            # PLANNING / EXECUTING / ...; written by the runner
    stop_reason: str                      # why a pause happened; read by explain/status
    iteration: int                        # the planning/execution iteration
    next_stage: str                       # which stage runs next; written by the controller
    sessions: dict                        # provider session ids by role
    history: list                         # finished stage records (autocode_run_view)
    stages: list                          # saved stage records; written by the runner
    active_stage: dict | None             # the in-flight stage record
    settings: dict                        # roles, budgets, engine, workflow mode
    acceptance_criteria: list             # the approved criteria; written by goals
    criteria_revision: str                # hash of the criteria definition
    goal_contract: dict | None            # the approved contract; written by goals
    current_task: dict | None             # the task the Builder is on
    user_events: list                     # answers, feedback, approvals (goals)
    answers: dict                         # question id -> answer text
    pending_questions: list               # open questions for a person
    user_request: dict | None             # a pending decision request
    validation: dict | None               # the latest independent validation (autoreview)
    validation_archive: list              # superseded validations
    findings_ledger: list                 # open/closed findings (autocode_findings)
    implementation: dict | None           # the Builder build-candidate record
    base_commit: str                      # the revision a bug fix is proven against

    # --- recovery / diagnosis ---
    recovery_context: dict | None         # what the last pause knew (autocode_recovery)
    resolution_request: dict | None       # a pending Resolver request
    stuck_investigation: dict | None      # an in-flight Investigator request
    stuck_investigations: list            # the investigation history
    pending_report_repair: dict | None    # a queued report-repair attempt
    uncertain_artifacts: list             # attempts whose cleanup is unverified
    job_failure: dict | None              # a failed workflow job awaiting retry
    failure_history: dict                 # failure identity -> attempts (builder_failure)
    builder_retries: dict                 # retry allowances by lane
    no_progress_batches: int              # consecutive no-progress batches

    # --- human review / approvals ---
    human_reviews: dict                   # criterion id -> the review binding
    brief_feedback: list                  # user feedback on the brief
    last_decision: dict | None            # the latest plan decision
    contract_history: list                # prior contract revisions

    # --- orchestration / progressive ---
    orchestration_batch: dict | None      # a parallel Builder batch
    workstreams: list                     # a program workstreams
    parent_run: str | None                # the parent of a child run
    progressive: dict | None              # progressive-planning record
    milestone_progress: dict              # per-milestone progress (autocode_milestones)

    # --- others (see KEYS for the full set) ---
    workflow: dict | None                 # recognised workflow kind/source
    integration: dict | None              # program integration state
    turns: list                           # follow-up turns of a conversation
    regression_proof: dict | None         # the bug-fix fail-before/pass-after proof
    active_runner_check: dict | None      # an in-flight runner-owned check

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
    agreement: Any
    alive: Any
    answer: Any
    applied_interventions: Any
    automatic_capacity_recoveries: Any
    automatic_permission_recoveries: Any
    automatic_recoveries_since_resume: Any
    automatic_timeout_recoveries: Any
    builder_failure_hold: Any
    builder_retry_decisions: Any
    builder_retry_key: Any
    change_requests: Any
    changed_files: Any
    checked: Any
    checkpoint_continuation: Any
    checkpoint_history: Any
    checkpoint_restores: Any
    clarification_episode: Any
    code_checkpoints: Any
    completed_at: Any
    completion_archive: Any
    completion_sent_back: Any
    configuration_changes: Any
    consecutive_timeout_recoveries: Any
    consultation_reports: Any
    continued: Any
    contract: Any
    conversation_handoff: Any
    conversation_id: Any
    created_at: Any
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
    interfaces: Any
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
    recovery_grants: Any
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
    resolver_human_request: Any
    retained_candidate_handoffs: Any
    retired_integrations: Any
    review: Any
    session_rotations: Any
    skeleton: Any
    source_revision: Any
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
    validator_rechecks: Any
    verification_count: Any
    verifications: Any
    visual_acceptance_receipts: Any


__all__ = ["KEYS", "RunState"]
