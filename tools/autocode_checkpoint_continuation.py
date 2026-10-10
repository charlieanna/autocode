"""Pure construction of an unverified continuation under the SAME approved plan.

The original run remains intact. Only explicit approved inputs, accounting and
failure ledgers are inherited as live state; the full original checkpoint is
retained separately as history. No old implementation, validation, completion,
human artifact acceptance or provider session can authorize the new candidate.
"""

from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_contract_identity as contract
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as contract
    import autocode_util as util

INPUTS = (
    "version",
    "target",
    "task",
    "task_id",
    "goal_contract",
    "settings",
    "requirements_body",
    "requirements_artifact_token",
    "answers",
    "brief_feedback",
    "user_events",
    "deferred_backlog",
    "deferred_obligations",
    "acceptance_criteria",
    "criteria_revision",
    "planning",
    "workflow",
    "discovery_summary",
    "base_commit",
    "iteration",
    "active_seconds",
    "builder_retries",
    "builder_retry_key",
    "builder_retry_decisions",
    "failure_history",
    "no_progress_batches",
    "reasoning_escalations",
    "configuration_changes",
    "timeout_recoveries",
    "budget_extensions",
    "turns",
    "milestone_active_seconds",
    "automatic_recoveries_since_resume",
    "consecutive_timeout_recoveries",
    "consecutive_timeouts",
    "automatic_timeout_recoveries",
    "automatic_capacity_recoveries",
    "automatic_permission_recoveries",
    "recovery_grants",
    "recovery_context",
    "project_worked_in_place",
)


def create(original, checkpoint, workspace, run_dir, branch, project, operation, *, at=None):
    if not contract.approved(original):
        raise ValueError("An authenticated approved plan is required")
    child = {key: deepcopy(original[key]) for key in INPUTS if key in original}
    at = at or util.now()
    child.update(
        workspace=str(workspace),
        run_dir=str(run_dir),
        task_branch=branch,
        project_workspace=str(project),
        created_at=at,
        status="PAUSED_REQUESTED",
        phase="READY_TO_EXECUTE",
        next_stage="astra_plan",
        sessions={},
        history=[],
        stages=[],
        pending_questions=[],
        human_reviews={},
        stop_reason="Checkpoint restored on a new branch. Resume explicitly for fresh implementation and independent checks.",
    )
    # project_worked_in_place: the checkout project_workspace names is one a Builder of this
    # lineage worked in, so it is not independent of the candidate. Restoring an --in-place
    # run (no task worktree metadata, autocode_checkpoint_cli.restore) names the original's own
    # workspace as the project; later restores keep the mark through INPUTS. It is written here
    # only and read only by autocode_regression.proof_dependencies: the regression proof then
    # treats ignored code it reads from that checkout as possibly hidden, so only a README.md-only
    # base may skip preservation (autocode_verify._document_only_base).
    if Path(project) == Path(original["workspace"]):
        child["project_worked_in_place"] = True
    # A new source commit changes candidate identity. All milestones face fresh
    # verification, including earlier ones; time/failure allowances never reset.
    # Preserve the one-time budget-extension fence without inheriting pending
    # resolver proposals or historical human-request authority.
    if "budget_extensions" in original.get("resolver", {}):
        child["resolver"] = {"budget_extensions": deepcopy(original["resolver"]["budget_extensions"])}
    child["milestone_progress"] = {}
    for key, previous in original.get("milestone_progress", {}).items():
        row = {
            name: deepcopy(previous[name])
            for name in (
                "id",
                "milestone_ids",
                "objective",
                "acceptance_criteria",
                "members",
                "contract_hash",
                "seconds",
                "seconds_by_role",
                "replans",
                "reviews_without_progress",
            )
            if name in previous
        }
        row.update(accepted=False, best_passed=[], reviews=[], rolled_back_at=at)
        child["milestone_progress"][key] = row
    earlier = {row["id"]: row for row in checkpoint.get("findings_ledger", [])}
    child["findings_ledger"] = []
    for current in original.get("findings_ledger", []):
        previous = earlier.pop(current.get("id"), None)
        row = deepcopy(previous or current)
        if previous and previous != current:
            row.setdefault("rolled_back_versions", []).append(deepcopy(current))
            row.setdefault("history", []).append(
                {
                    "at": at,
                    "action": "restored_checkpoint_disposition",
                    "checkpoint": checkpoint["id"],
                    "operation": operation,
                }
            )
        elif not previous and row.get("status") == "open":
            row.setdefault("history", []).append(
                {
                    "at": at,
                    "action": "rolled_back",
                    "previous_status": "open",
                    "checkpoint": checkpoint["id"],
                    "operation": operation,
                }
            )
            row.update(status="rolled_back", disposition="no longer applies (rolled back)")
        child["findings_ledger"].append(row)
    child["findings_ledger"].extend(deepcopy(list(earlier.values())))
    child["checkpoint_continuation"] = {
        "version": 1,
        "operation": operation,
        "checkpoint_id": checkpoint["id"],
        "source_run": original["run_dir"],
        "source_workspace": original["workspace"],
        "source_branch": original.get("task_branch"),
        "checkpoint_commit": checkpoint["commit"],
        "at": at,
        "history": str(run_dir / "restoration-history.json"),
        "verification": "Fresh independent checks and any required human artifact acceptance are required.",
    }
    child.setdefault("user_events", []).append(
        {
            "kind": "code_checkpoint_restore",
            "actor": "user_cli",
            "at": at,
            "checkpoint_id": checkpoint["id"],
            "operation": operation,
            "preserved_contract": contract.token(child["goal_contract"]),
        }
    )
    if not contract.approved(child) or child["goal_contract"] != original["goal_contract"]:
        raise ValueError("Continuation did not preserve the exact plan approval")
    return child
