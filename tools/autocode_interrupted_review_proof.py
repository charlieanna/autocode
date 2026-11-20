"""Read-only eligibility for archiving a cleanly stopped, non-writing attempt."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_launch_binding as handoff
    from . import autocode_liveness as liveness
    from . import autocode_recovery_accounting as accounting
    from . import autocode_recovery_limits as limits
    from . import autocode_roles as roles
    from . import autocode_source_scope as source_scope
    from . import autocode_stage_access as access
    from . import autocode_stop as stop
    from . import autocode_util as util
except ImportError:
    import autocode_launch_binding as handoff
    import autocode_liveness as liveness
    import autocode_recovery_accounting as accounting
    import autocode_recovery_limits as limits
    import autocode_roles as roles
    import autocode_source_scope as source_scope
    import autocode_stage_access as access
    import autocode_stop as stop
    import autocode_util as util


def proof(state: RunState, workspace, inspection):
    """Read retained facts only; source changes or uncertain cleanup grant nothing."""
    record = state.get("active_stage") or {}
    stage = record.get("stage")
    if (
        state.get("status") not in ("RUNNING", "PAUSED_UNCERTAIN_STAGE")
        or not (
            (record.get("planning") is True and stage in roles.ALL_PLANNING_STAGES) or stage in access.JUDGING_STAGES
        )
        or record.get("report_only")
        or record.get("finished_at")
        or any(
            state.get(key)
            for key in ("active_runner_check", "pending_report_repair", "uncertain_artifacts", "job_failure")
        )
        or stop.applied_stop(state) is not None
        or (state.get("pause_intent") and not state["pause_intent"].get("acknowledged_at"))
        or limits.stop_reason(state, accounting.spent(state), accounting.MAX_AUTOMATIC_RECOVERIES)
    ):
        return None
    fresh = liveness.classify(record.get("supervision"), inspection)
    receipt = fresh.get("receipt") or {}
    keeper_clean = (
        receipt.get("phase") == "stopped"
        and receipt.get("cause") == "owner_lost"
        and receipt.get("cleanup_error") is None
    )
    backup = (record.get("supervision") or {}).get("family_backup")
    family_clean = (
        isinstance(inspection, dict)
        and (
            (receipt.get("phase") == "armed" and receipt.get("family_backup") == backup)
            or (
                receipt.get("phase") == "stopping"
                and receipt.get("cause") == "owner_lost"
                and receipt.get("cleanup_error") is None
                and receipt.get("family_backup") == backup
            )
            or (not receipt and inspection.get("family_receipt_missing") is True)
        )
        and inspection.get("family_cleanup") is True
        and isinstance(backup, dict)
    )
    # A host restart ends the attempt with every process it started, whatever its receipt last said.
    host_restarted = isinstance(inspection, dict) and inspection.get("boot_ended") is True
    if not (keeper_clean or family_clean or host_restarted) or not all(
        fresh[role]["checked"] and fresh[role]["alive"] is False for role in liveness.ROLES
    ):
        return None
    try:
        before = util.read(Path(record["before_ref"]))
        current = source_scope.snapshot(workspace, state)
        binding = handoff.preserved_launch_binding(record)
        if not binding or before["revision"] != current["revision"] or handoff._drift(binding, state, current):
            return None
        supervision = record["supervision"]
        cleanup = (
            {
                "kind": "capture_family",
                "attempt_nonce": supervision["nonce"],
                "boot_id": supervision["family_backup"]["boot_id"],
                "directory": supervision["family_backup"]["directory"],
            }
            if family_clean
            else {"kind": "supervisor", "receipt": supervision["receipt"]}
            if keeper_clean
            else {"kind": "host_restart", "receipt": supervision["receipt"]}
        )
        return {"source": current, "launch_binding": binding, "receipt": receipt, "cleanup": cleanup}
    except (KeyError, OSError, ValueError, TypeError, util.Paused):
        return None
