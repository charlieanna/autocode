"""Public launch profile for related tasks, preserving the selected routes/limits.

Only configuration is copied. Approvals, usage, sessions, recovery grants and
workspace-specific inputs never become a new task's authority. The coordinator
stores the profile outside provider workspaces and pins its exact bytes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

# Explicit whitelist: new settings do not silently propagate across projects.
KEYS = (
    "roles",
    "engine",
    "provider",
    "transport_identity",
    "transport_identities",
    "single_model_mode",
    "budget_origins",
    "builder_retry",
    "route_selection",
    "route_ladders",
    "route_origins",
    "effort_origins",
    "effort_fits",
    "orchestration",
    "report_repair",
    "evidence_provenance",
    "milestone_checkpoints",
    "context_soft_tokens",
    "rotation_after_input_tokens",
    "limits",
    "joint_planning",
    "planning_flow",
    "adaptive_planning",
    "plan_review_full",
    "planning_review_call_limit",
    "planning_review_call_limit_origin",
    "spec_first",
    "tool_containment",
    "output_policy",
    "require_approver",
    "utility",
    "utility_route",
    "headroom",
)


def add_arguments(parser):
    parser.add_argument(
        "--related-profile", type=Path, help="Start with a pinned related-run routing and limit profile"
    )
    parser.add_argument("--related-profile-sha256", help="Exact SHA-256 of --related-profile")
    parser.add_argument(
        "--assign-linked-scope", help="Accept the core's exact repository assignment, without approving a plan"
    )


def validate_arguments(args, parser):
    if bool(args.related_profile) != bool(args.related_profile_sha256):
        parser.error("--related-profile and --related-profile-sha256 must be supplied together")
    if args.related_profile and args.run_dir:
        parser.error("--related-profile is only available for a fresh run")
    if args.assign_linked_scope and not args.run_dir:
        parser.error("--assign-linked-scope requires an existing --run-dir")


def project(state: RunState):
    settings = state.get("settings") or {}
    return {
        "version": 1,
        "settings": {key: copy.deepcopy(settings[key]) for key in KEYS if key in settings},
    }


def load(path, digest):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or util.file_hash(path) != digest:
        raise ValueError("Related-run profile changed or is unavailable")
    value = json.loads(path.read_text())
    settings = value.get("settings")
    if value.get("version") != 1 or not isinstance(settings, dict) or set(settings) - set(KEYS):
        raise ValueError("Unsupported related-run profile")
    if not settings.get("roles") or settings.get("engine") not in ("codex", "opencode"):
        raise ValueError("Related-run profile has no saved role routes")
    # Never share a project-specific command, baseline patch or protected paths.
    settings["regression"] = {}
    return settings


def install(state: RunState, args):
    selected = getattr(args, "related_profile", None)
    if not selected:
        return
    if args.run_dir or not getattr(args, "related_profile_sha256", None):
        raise ValueError("A related profile requires a fresh run and its exact SHA-256")
    settings = load(selected, args.related_profile_sha256)
    # A target project's approval policy cannot be weakened by inheritance.
    required = getattr(args, "require_approver", None)
    if required:
        parent = settings.get("require_approver")
        if parent and parent != required:
            raise ValueError("Related projects have different required approvers; reconcile their policies first")
        settings["require_approver"] = required
    if getattr(args, "sensitive_paths", None) is not None:
        settings["sensitive_paths"] = args.sensitive_paths
    if getattr(args, "serve", None):
        settings["serve"] = args.serve
    state["settings"] = settings
    settings["coordination_member"] = True
