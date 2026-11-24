"""Admission for tasks that can edit the AutoCode controller's own repository.

This policy reads only the file system and never changes a checkout or run. Interfaces
supply the executing module path; no controller or workflow imports belong here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_git_location as git_location
    from . import autocode_stage_roles as stage_roles
    from .autocode_util import Paused
except ImportError:
    import autocode_git_location as git_location
    import autocode_stage_roles as stage_roles
    from autocode_util import Paused


REQUIREMENTS_STAGES = ("requirements_gather", "requirements")
BUILD_AUTHORITY_KEYS = (
    "acceptance_criteria",
    "acceptance_tests",
    "builder_failure_hold",
    "builder_retries",
    "builder_retry_decisions",
    "builder_retry_key",
    "criteria_revision",
    "current_task",
    "direct_rework_assignments",
    "goal_contract",
    "human_reviews",
    "implementation",
    "implementation_discrepancies",
    "milestone_progress",
    "orchestration_batch",
    "parent_batch",
    "pending_builder_failure",
    "progressive",
    "resolution_request",
    "resolve_repair_request",
    "validation",
)


def is_requirements_stage(stage) -> bool:
    """Both the legacy and joint-planning spellings of the Requirements stage and its report repair."""
    return isinstance(stage, str) and stage.removesuffix("_report_repair") in REQUIREMENTS_STAGES


def entry_stage(state: RunState) -> str | None:
    """The stage a saved invocation would actually enter, including same-stage report repair."""
    active = state.get("active_stage")
    if active:
        stage = active.get("stage") if isinstance(active, dict) else active
        return stage if isinstance(stage, str) else None
    repair = state.get("pending_report_repair") or {}
    original = repair.get("original") or {}
    if original.get("stage") and original.get("stage") == state.get("next_stage"):
        return original["stage"]
    return state.get("next_stage")


def saved_requirements_only(
    state: RunState, *, allow_task_preflight: bool = False, allow_fresh_start: bool = False
) -> bool:
    """Whether a checkpoint has no build authority or provisioning for this invocation to carry forward."""
    active = state.get("active_stage")
    if active:
        if not isinstance(active, dict):
            if not is_requirements_stage(active):
                return False
        elif not is_requirements_stage(active.get("stage")):
            return False
        else:
            original_stage = active["stage"].removesuffix("_report_repair")
            if active.get("role") not in (None, stage_roles.role_for(state, original_stage)):
                return False
            if active.get("route_role") not in (None, "requirements"):
                return False
            if active.get("original_stage") not in (None, original_stage):
                return False
    workflow = state.get("workflow") or {}
    if workflow.get("kind") not in (None, "discuss"):
        return False
    if (
        state.get("build_worktree") is not None
        or "base_commit" in state
        or "project_workspace" in state
        or "task_branch" in state
        or "workspace_bootstrap" in state
        or state.get("launch_sources")
        or any(state.get(key) for key in BUILD_AUTHORITY_KEYS)
    ):
        return False
    settings = state.get("settings") or {}
    if settings.get("allow_uncontained_tools") is True:
        return False
    if not allow_task_preflight and (settings.get("task_preflight") or state.get("task_preflight")):
        return False
    for row in state.get("stages") or []:
        if row.get("runner_owned"):
            continue
        stage = str(row.get("original_stage") or row.get("stage") or "").removesuffix("_report_repair")
        if stage and not is_requirements_stage(stage):
            return False
    repair = state.get("pending_report_repair") or {}
    if repair:
        original = repair.get("original") or {}
        stage = original.get("stage")
        if not is_requirements_stage(stage) or state.get("next_stage") != stage:
            return False
        if original.get("role") not in (None, stage_roles.role_for(state, stage)):
            return False
        if original.get("route_role", original.get("role")) != "requirements":
            return False
        latest = repair.get("latest_rejected")
        if latest is not None:
            if not isinstance(latest, dict) or latest.get("report_only") is not True:
                return False
            if latest.get("stage") != f"{stage}_report_repair" or latest.get("original_stage", stage) != stage:
                return False
            if latest.get("role") not in (None, stage_roles.role_for(state, stage)):
                return False
            if latest.get("route_role", latest.get("role")) != "requirements":
                return False
    if is_requirements_stage(entry_stage(state)) and is_requirements_stage(state.get("next_stage")):
        return True
    if (
        allow_fresh_start
        and state.get("status") == "RUNNING"
        and not state.get("active_stage")
        and not state.get("workflow")
        and not state.get("stages")
        and state.get("next_stage") == "plan_task"
    ):
        return True
    questions = state.get("pending_questions") or []
    if (
        state.get("status") == "WAITING_FOR_USER"
        and state.get("next_stage") in ("draft_plan", "plan")
        and questions
        and all(str(row.get("id") or "").startswith("requirements-review-") for row in questions)
        and isinstance((state.get("requirements_handoff") or {}).get("report"), dict)
    ):
        return True
    return state.get("status") == "TASK_COMPLETE"


def require_route(state: RunState, stage: str, role, route_role, allow_write, sandbox, local_requirements) -> None:
    if not local_requirements:
        return
    if role != stage_roles.role_for(state, stage) or allow_write or sandbox != "read-only":
        raise Paused("PAUSED_DISCOVERY_WRITE", "Planning must use its assigned role read-only")
    if route_role != "requirements":
        raise Paused("PAUSED_DISCOVERY_WRITE", "Requirements must use its assigned planning route read-only")


def local_requirements(workspace: Path, state: RunState) -> bool:
    """Whether this controller may enter only Requirements in its own checkout, with no build setup."""
    if not saved_requirements_only(state):
        return False
    setup = setup_view(Path(workspace), Path(__file__))
    return not setup["ready"] and setup["requirements_ready"]


def require_local_requirements(
    workspace: Path, state: RunState, *, stage=None, allow_fresh_start: bool = False
) -> None:
    if stage is not None and not is_requirements_stage(stage):
        raise ValueError(
            f"Requirements-only discussion cannot dispatch {stage}. "
            "Select an AutoCode CLI installed outside this checkout before building."
        )
    if not saved_requirements_only(state, allow_fresh_start=allow_fresh_start):
        raise ValueError(
            "Requirements-only discussion in the AutoCode controller cannot continue into file changes. "
            "Select an AutoCode CLI installed outside this checkout before building."
        )
    setup = setup_view(Path(workspace), Path(__file__))
    if setup["ready"] or not setup["requirements_ready"]:
        raise ValueError(
            "Requirements-only discussion in the AutoCode controller cannot continue into file changes. "
            "Select an AutoCode CLI installed outside this checkout before building."
        )


def is_autocode_checkout(workspace: Path) -> bool:
    """Whether ``workspace`` is an AutoCode checkout this controller could be built from.

    A project that vendors an old copy of tools/autocode.py beside its own AGENTS.md (idlecampus
    kept one from 2026-10-12) is not AutoCode: it lacks this admission module, which every
    checkout that can refuse a self-build has.
    """
    tools = workspace / "tools"
    return all(
        (path).is_file() for path in (tools / "autocode.py", tools / Path(__file__).name, workspace / "AGENTS.md")
    )


def setup_view(workspace: Path, controller_file: Path) -> dict:
    """Read-only admission and the public client action for selecting an isolated controller."""
    workspace = workspace.resolve()
    unsafe = is_autocode_checkout(workspace) and controller_file.resolve().is_relative_to(workspace)
    try:
        requirements_ready = not unsafe or (
            git_location.root(workspace) == workspace and git_location.has_ref(workspace, "HEAD")
        )
    except ValueError:
        requirements_ready = False
    requirements_detail = (
        "Requirements may run in this folder with its assigned planning role, a read-only sandbox and verified "
        "tool containment. A request that needs file changes still requires an isolated AutoCode CLI."
        if unsafe and requirements_ready
        else "Requirements can start with this controller. Full-task readiness still controls any later build."
        if not unsafe
        else "Requirements-only discussion needs an existing Git checkout with HEAD. Select an AutoCode CLI "
        "installed outside this checkout, or open a checkout with a commit."
    )
    detail = (
        "Unsafe AutoCode self-build: the executing controller is inside the task workspace, "
        "so the task could edit the code that runs it. A new worktree does not isolate the controller "
        "during startup. Select an AutoCode CLI installed outside this checkout, then retry your chat. "
        "Choose an existing non-editable installation, or one sourced from a separate checkout; "
        "nothing will be installed automatically."
        if unsafe
        else "This controller is outside the task workspace or the workspace is not an AutoCode self-build."
    )
    return {
        "ready": not unsafe,
        "requirements_ready": requirements_ready,
        "requirements_detail": requirements_detail,
        "detail": detail,
        "actions": [
            {
                "id": "select_cli",
                "label": "Select isolated AutoCode CLI",
                "next_command": ["worktrees", "list", "--workspace", str(workspace), "--json"],
            }
        ]
        if unsafe
        else [],
    }


def require_isolated(workspace: Path, controller_file: Path) -> None:
    """Refuse an AutoCode self-build whose edits could change the controller running it.

    An AutoCode checkout is an ordinary project to a controller installed outside it:
    the task can edit the checkout in place, as any other project, and the running
    controller stays as it was (owner decision 2026-10-20).
    """
    setup = setup_view(workspace, controller_file)
    if not setup["ready"]:
        raise ValueError(setup["detail"])
