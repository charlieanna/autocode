"""Pure cause categories for pauses shown by the public run view.

The view supplies the current need; this module neither validates saved request
ledgers nor changes pause authority. A newly named runtime pause must be added
to one explicit group. Unknown pauses use AutoCode until their cause is known.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

LABELS = MappingProxyType(
    {
        "your_decision": "Your decision",
        "your_environment": "Your environment",
        "model_or_provider": "Model or provider",
        "autocode": "AutoCode",
    }
)

STATUS_GROUPS = MappingProxyType(
    {
        # Approval, an explicit intervention, or input only a person can supply.
        "your_decision": frozenset(
            {
                "AWAITING_GOAL_APPROVAL",
                "BLOCKED_HUMAN",
                "PAUSED_APPROVAL_DEFERRED",
                "PAUSED_CRITERIA_CHANGE",
                "PAUSED_DESIGN_CONFLICT",
                "PAUSED_DESIGN_INPUT",
                "PAUSED_DESIGN_INPUT_CHANGED",
                "PAUSED_DESIGN_REFERENCE",
                "PAUSED_GOAL_UNAPPROVED",
                "PAUSED_INTERFACE_CHANGE",
                "PAUSED_INTERVENTION",
                "PAUSED_INTERVENTION_ACK",
                "PAUSED_INTERVENTION_PENDING",
                "PAUSED_MERGE_CONFLICT",
                "PAUSED_MILESTONE_HUMAN_REVIEW",
                "PAUSED_PERMISSION",
                "PAUSED_REQUESTED",
                "PAUSED_UNANSWERED_QUESTION",
                "PAUSED_WORKFLOW_APPROVAL",
                "PAUSED_WORKFLOW_CONFLICT",
                "WAITING_FOR_USER",
            }
        ),
        # Host access, workspace ownership, or a configured provider transport.
        "your_environment": frozenset(
            {
                "PAUSED_AUTH",
                "PAUSED_BILLING_ROUTE",
                "PAUSED_CROSS_MODEL",
                "PAUSED_INTEGRATION_DIRTY",
                "PAUSED_INTERRUPTED",
                "PAUSED_METADATA",
                "PAUSED_OPENCODE_SNAPSHOT",
                "PAUSED_ORCHESTRATOR_GIT",
                "PAUSED_ORCHESTRATOR_OWNERSHIP",
                "PAUSED_OWNERSHIP",
                "PAUSED_PERMISSION_RECONCILIATION",
                "PAUSED_PLANNING_ROUTE",
                "PAUSED_PROCESS_CHECK",
                "PAUSED_REGISTRY",
                "PAUSED_RUN_BUSY",
                "PAUSED_TOOL_CONTAINMENT",
                "PAUSED_TRANSPORT_CHANGED",
                "PAUSED_TRANSPORT_MIGRATION",
                "PAUSED_TRANSPORT_UNVERIFIED",
                "PAUSED_WORKSPACE_BUSY",
            }
        ),
        # A refused or unavailable provider, or a stage's unusable result.
        "model_or_provider": frozenset(
            {
                "PAUSED_COMPLETION_REVIEW",
                "PAUSED_CONTENT_FILTER",
                "PAUSED_DISCOVERY_WRITE",
                "PAUSED_INVALID_OUTPUT",
                "PAUSED_JOB_FAILURE",
                "PAUSED_MILESTONE_STALLED",
                "PAUSED_NO_PROGRESS",
                "PAUSED_OUTPUT_CAP",
                "PAUSED_PROVIDER_CAPACITY",
                "PAUSED_PROVIDER_TIMEOUT",
                "PAUSED_PROVIDER_UNCERTAIN",
                "PAUSED_QUOTA",
                "PAUSED_RATE_LIMIT",
                "PAUSED_REPEATED_FAILURE",
                "PAUSED_REPORT_REPAIR_LIMIT",
                "PAUSED_TOOL",
            }
        ),
        # Saved bounds, evidence, orchestration and AutoCode integrity guards.
        "autocode": frozenset(
            {
                "PAUSED_ASSIGNMENT_SCOPE",
                "PAUSED_BUDGET",
                "PAUSED_BUILDER_CLASSIFICATION",
                "PAUSED_BUILDER_OPERATIONAL",
                "PAUSED_BUILDER_RETRY_LIMIT",
                "PAUSED_COMPLETION_GATE",
                "PAUSED_COMPONENT_PLAN",
                "PAUSED_FUTURE_REASON",
                "PAUSED_INHERITANCE",
                "PAUSED_INTEGRATION_CHECK",
                "PAUSED_INVALID_PREDECESSOR",
                "PAUSED_ITERATION_LIMIT",
                "PAUSED_JOURNEY_UNVERIFIED",
                "PAUSED_LEGACY_COMPLETION_UNVERIFIED",
                "PAUSED_MILESTONE_BUDGET",
                "PAUSED_MILESTONE_EVIDENCE",
                "PAUSED_MILESTONE_REPLAN",
                "PAUSED_MILESTONE_TASK",
                "PAUSED_MILESTONE_TIME_LIMIT",
                "PAUSED_ORCHESTRATOR_DRIFT",
                "PAUSED_ORCHESTRATOR_WORKER",
                "PAUSED_ORCHESTRATOR_WORKERS",
                "PAUSED_OR_BLOCKED",
                "PAUSED_PLANNING_BUDGET",
                "PAUSED_PROCESS_CLEANUP",
                "PAUSED_PROGRESSIVE_AUTHORITY",
                "PAUSED_PROGRESSIVE_BUDGET",
                "PAUSED_PROGRESSIVE_TRANSITION",
                "PAUSED_REPORT_REPAIR_INPUT",
                "PAUSED_RESOLVER",
                "PAUSED_RESOLVER_OPERATIONAL",
                "PAUSED_RESOLVER_STATE",
                "PAUSED_REVIEWER_FALLBACK",
                "PAUSED_SKELETON_UNVERIFIED",
                "PAUSED_STAGE_ABANDONED",
                "PAUSED_STAGE_TIMEOUT",
                "PAUSED_STALE_GOAL",
                "PAUSED_STALE_HANDOFF",
                "PAUSED_STALE_REPORT_ROUTE",
                "PAUSED_STALE_TASK",
                "PAUSED_STALE_VALIDATION",
                "PAUSED_TASK_PREFLIGHT",
                "PAUSED_TIMEOUT_RECOVERY",
                "PAUSED_TIME_LIMIT",
                "PAUSED_UNCERTAIN_STAGE",
                "PAUSED_UNSUPPORTED_CHECKPOINT",
                "PAUSED_USAGE_UNKNOWN",
                "PAUSED_VERIFICATION_UNCERTAIN",
                "PAUSED_VISUAL_EVIDENCE",
                "PAUSED_WORKFLOW_ROLLBACK",
                "RESOLVER_PENDING",
            }
        ),
    }
)

_STATUS_CATEGORY = {status: cause for cause, statuses in STATUS_GROUPS.items() for status in statuses}
_OPERATIONAL_SCOPES = frozenset({"blocker", "operational_exhaustion"})


def _token(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def category(status: object, needs: Mapping[str, object] | None = None) -> str | None:
    """Return a stable pause cause, using only the supplied current projection.

    Model-route answers remain provider causes, including output exhaustion and quota's historical
    generic budget status. Operational answers retain their typed pause origin;
    an unknown origin stays AutoCode. Ordinary questions need a current resolver
    token before they can change another pause's category to a human decision.
    """
    if not isinstance(status, str):
        return None
    cause = _STATUS_CATEGORY.get(status)
    if cause is None:
        return "autocode" if status == "PAUSED" or status.startswith("PAUSED_") else None
    if not isinstance(needs, Mapping):
        return cause

    route = needs.get("route")
    if isinstance(route, Mapping) and route.get("cause") in ("quota", "content_filter", "output_limit"):
        return "model_or_provider"

    scope = needs.get("resolver_scope")
    if needs.get("kind") == "answer" and isinstance(scope, str) and scope in _OPERATIONAL_SCOPES:
        origin = needs.get("pause_origin")
        if isinstance(origin, str) and origin.startswith("PAUSED_") and origin in _STATUS_CATEGORY:
            return _STATUS_CATEGORY[origin]
        # WAITING_FOR_USER describes publication, not the operational cause.
        return cause if cause in {"your_environment", "model_or_provider"} else "autocode"

    kind = needs.get("kind")
    if kind in ("review", "approve_plan") and _token(needs.get("token")):
        return "your_decision"
    if kind == "answer" and _token(needs.get("resolver_token")) and not needs.get("action"):
        questions = needs.get("questions")
        if isinstance(questions, (list, tuple)) and any(
            isinstance(question, Mapping) and _token(question.get("id")) for question in questions
        ):
            return "your_decision"
    return cause
