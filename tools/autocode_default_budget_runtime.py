"""Persist a pure default-budget decision after checking its current source and output.

One runtime adapter, with the runner services injected so public mocks keep the
same ownership and persistence boundary. It performs no stage dispatch.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_repair_provenance as repair_provenance
except ImportError:
    import autocode_repair_provenance as repair_provenance


def recover(runner, state: RunState, run_dir, workspace, kind):
    """AutoResolver may extend a proven internal default, never erase its usage."""
    with runner.interventions.serialized(run_dir):
        if (
            state.get(runner.resolver_human.PRIVATE)
            or runner.resolver_human.current(state)
            or (Path(run_dir) / "pause-requested").exists()
            or runner.interventions.pending(run_dir)
        ):
            return False
        latest = next((row for row in reversed(state.get("stages", [])) if not row.get("runner_owned")), None)
        if (
            not latest
            or latest.get("source_revision")
            != runner.source_scope.snapshot(workspace, state, base_snapshot=runner.support.snapshot)["revision"]
        ):
            return False
        candidate = copy.deepcopy(state)
        if not runner.budget_recovery.recover(candidate, kind=kind, now=runner.now()):
            return False
        extension = candidate["resolver"]["budget_extensions"][-1]
        output = extension["evidence"].get("output")
        if not output or not Path(output).is_file():
            return False
        if extension["evidence"].get("source") == "accepted_corrective_plan":
            try:
                repair_provenance.witness(candidate, "astra_discovery", output)
            except (ValueError, OSError, KeyError, TypeError):
                return False
        if kind == "planning_review_call_limit":
            state["planning"]["review_call_limit"] = candidate["planning"]["review_call_limit"]
            state["planning"]["review_call_limit_origin"] = "runner_default"
        elif kind == "milestone_max_seconds":
            state["settings"]["milestone_checkpoints"]["max_seconds"] = extension["to"]
        else:
            state["settings"]["limits"][kind] = extension["to"]
        state.setdefault("resolver", {})["budget_extensions"] = candidate["resolver"]["budget_extensions"]
        receipt_id = runner.resolver_runtime._operational_receipt(
            state,
            run_dir,
            "extend_default_budget",
            f"AutoResolver extended internal {kind} from {extension['from']} to {extension['to']} "
            "once after verified progress; usage and failure history are retained.",
            extension,
        )
        runner.progressive_state.apply_recovery_limit(state, extension, receipt_id)
        runner.write_json(Path(run_dir) / "state.json", state)
        return True
