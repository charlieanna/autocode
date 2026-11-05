"""Whose call each PAUSED_* status is: one table over every pause code AutoCode raises (#2755).

The owner rule (AGENTS.md, 2026-10-29) asks a person only about requirements, the plan and genuine
ambiguity, and keeps spending caps and model routes the owner's. Each pause code therefore belongs
to exactly one of four categories:

- ``person``: requirements, plan or design approval, a person's own stop or intervention, genuine
  ambiguity, and the model route the owner chose for a stage.
- ``machinery``: evidence, retries, recovery, report repair, orchestration and integrity guards.
  AutoCode settles these itself; one reaching a person is a defect.
- ``budget``: a spending cap or time limit the owner set, and spend AutoCode cannot account for
  against that cap.
- ``environment``: the host, workspace, credentials, provider transport or provider limits.

This is a different axis from ``autocode_pause_category``, which is the status view's display cause
(``your_decision``, ``your_environment``, ``model_or_provider``, ``autocode``) and stays its
contract. Status strings are unchanged; this table only names them. ``tests/test_pause_codes.py``
fails when ``tools/`` names a PAUSED_* status that is missing here.

Utility layer: plain data, imports nothing from AutoCode.
"""

from __future__ import annotations

from types import MappingProxyType

CATEGORIES = ("person", "machinery", "budget", "environment")

_GROUPS = {
    "person": (
        "PAUSED_ACCEPTANCE_TESTS",
        "PAUSED_APPROVAL_DEFERRED",
        "PAUSED_BILLING_ROUTE",
        "PAUSED_CHANGESET_APPROVAL",
        "PAUSED_CRITERIA_CHANGE",
        "PAUSED_CROSS_MODEL",
        "PAUSED_DESIGN_CONFLICT",
        "PAUSED_DESIGN_INPUT",
        "PAUSED_DESIGN_INPUT_CHANGED",
        "PAUSED_DESIGN_REFERENCE",
        "PAUSED_EFFORT_UNSUPPORTED",
        "PAUSED_GOAL_UNAPPROVED",
        "PAUSED_INTERFACE_CHANGE",
        "PAUSED_INTERVENTION",
        "PAUSED_INTERVENTION_ACK",
        "PAUSED_INTERVENTION_PENDING",
        "PAUSED_MERGE_CONFLICT",
        "PAUSED_MILESTONE_HUMAN_REVIEW",
        "PAUSED_PERMISSION",
        "PAUSED_PLANNING_ROUTE",
        "PAUSED_REQUESTED",
        "PAUSED_SCOPE_DECISION",
        "PAUSED_UNANSWERED_QUESTION",
        "PAUSED_WORKFLOW_APPROVAL",
        "PAUSED_WORKFLOW_CONFLICT",
    ),
    "machinery": (
        "PAUSED_ASSIGNMENT_SCOPE",
        "PAUSED_BUILDER_CLASSIFICATION",
        "PAUSED_BUILDER_OPERATIONAL",
        "PAUSED_BUILDER_RETRY_LIMIT",
        "PAUSED_COMPLETION_GATE",
        "PAUSED_COMPLETION_REVIEW",
        "PAUSED_COMPONENT_PLAN",
        "PAUSED_CONTROLLER_EXIT",
        "PAUSED_DISCOVERY_WRITE",
        "PAUSED_FUTURE_REASON",
        "PAUSED_INHERITANCE",
        "PAUSED_INTEGRATION_CHECK",
        "PAUSED_INVALID_OUTPUT",
        "PAUSED_INVALID_PREDECESSOR",
        "PAUSED_JOB_FAILURE",
        "PAUSED_JOURNEY_UNVERIFIED",
        "PAUSED_LEGACY_COMPLETION_UNVERIFIED",
        "PAUSED_MILESTONE_EVIDENCE",
        "PAUSED_MILESTONE_REPLAN",
        "PAUSED_MILESTONE_STALLED",
        "PAUSED_MILESTONE_TASK",
        "PAUSED_NO_PROGRESS",
        "PAUSED_ORCHESTRATOR_DRIFT",
        "PAUSED_ORCHESTRATOR_WORKER",
        "PAUSED_ORCHESTRATOR_WORKERS",
        "PAUSED_OR_BLOCKED",  # autocode_run_status.PAUSE_PHASE: the generic phase, its cause named elsewhere
        "PAUSED_PERMISSION_LOOP",
        "PAUSED_PROCESS_CLEANUP",
        "PAUSED_PROGRESSIVE_AUTHORITY",
        "PAUSED_PROGRESSIVE_TRANSITION",
        "PAUSED_REPEATED_FAILURE",
        "PAUSED_REPORT_REPAIR_INPUT",
        "PAUSED_REPORT_REPAIR_LIMIT",
        "PAUSED_RESOLVER",
        "PAUSED_RESOLVER_OPERATIONAL",
        "PAUSED_RESOLVER_STATE",
        "PAUSED_REVIEWER_FALLBACK",
        "PAUSED_SKELETON_UNVERIFIED",
        "PAUSED_STAGE_ABANDONED",
        "PAUSED_STALE_DIAGNOSIS",
        "PAUSED_STALE_GOAL",
        "PAUSED_STALE_HANDOFF",
        "PAUSED_STALE_REPORT_ROUTE",
        "PAUSED_STALE_TASK",
        "PAUSED_STALE_VALIDATION",
        "PAUSED_TASK_PREFLIGHT",
        "PAUSED_TIMEOUT_RECOVERY",
        "PAUSED_UNCERTAIN_STAGE",
        "PAUSED_UNSUPPORTED_CHECKPOINT",
        "PAUSED_VERIFICATION_PREPARATION",
        "PAUSED_VERIFICATION_UNCERTAIN",
        "PAUSED_VISUAL_EVIDENCE",
        "PAUSED_WORKFLOW_ROLLBACK",
    ),
    "budget": (
        "PAUSED_BUDGET",
        "PAUSED_COST_LIMIT",
        "PAUSED_ITERATION_LIMIT",
        "PAUSED_MILESTONE_BUDGET",
        "PAUSED_MILESTONE_TIME_LIMIT",
        "PAUSED_PLANNING_BUDGET",
        "PAUSED_PROGRESSIVE_BUDGET",
        "PAUSED_STAGE_TIMEOUT",
        "PAUSED_TIME_LIMIT",
        "PAUSED_USAGE_UNKNOWN",
    ),
    "environment": (
        "PAUSED_AUTH",
        "PAUSED_CONTENT_FILTER",
        "PAUSED_CONTEXT_WINDOW",
        "PAUSED_INTEGRATION_DIRTY",
        "PAUSED_INTERRUPTED",
        "PAUSED_METADATA",
        "PAUSED_OPENCODE_SNAPSHOT",
        "PAUSED_ORCHESTRATOR_GIT",
        "PAUSED_ORCHESTRATOR_OWNERSHIP",
        "PAUSED_OUTPUT_CAP",
        "PAUSED_OWNERSHIP",
        "PAUSED_PERMISSION_RECONCILIATION",
        "PAUSED_PROCESS_CHECK",
        "PAUSED_PROVIDER_CAPACITY",
        "PAUSED_PROVIDER_TIMEOUT",
        "PAUSED_PROVIDER_UNCERTAIN",
        "PAUSED_QUOTA",
        "PAUSED_RATE_LIMIT",
        "PAUSED_REGISTRY",
        "PAUSED_RUN_BUSY",
        "PAUSED_SOURCE_UNAVAILABLE",
        "PAUSED_TOOL",
        "PAUSED_TOOL_CONTAINMENT",
        "PAUSED_TRANSPORT_CHANGED",
        "PAUSED_TRANSPORT_MIGRATION",
        "PAUSED_TRANSPORT_UNVERIFIED",
        "PAUSED_WORKSPACE_BUSY",
    ),
}

# Each pause code to its category. Read-only; every code appears once.
CODES = MappingProxyType({code: category for category, codes in _GROUPS.items() for code in codes})
