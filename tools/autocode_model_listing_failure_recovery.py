"""Settle an observed roster timeout without another listing/model or a person."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_model_listing_failure as failure
    from . import autocode_recovery_limits as limits
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_model_listing_failure as failure
    import autocode_recovery_limits as limits
    import autocode_run_status as run_status
    import autocode_util as util


def settle(runtime, state: RunState, run_dir, workspace, error):
    if type(error) is not failure.ModelListingTimeout:
        return False
    unsettled = [key for key in limits.UNSETTLED if state.get(key)]
    bound = error.workspace == str(Path(workspace).resolve()) and bool(
        re.fullmatch(r"[0-9a-f]{64}", error.config_sha256 or "")
    )
    evidence = {
        "operation": "model_listing",
        "binding": {
            "workspace": str(Path(workspace).resolve()),
            "run_dir": str(Path(run_dir).resolve()),
            "task_id": state.get("task_id"),
            "contract_hash": util.digest(state.get("goal_contract")),
            "settings_hash": util.digest(state.get("settings")),
            "next_stage": state.get("next_stage"),
        },
        "listing": {
            "provider": error.provider,
            "command": list(error.command),
            "attempt_bounds": list(error.attempt_bounds),
            "config_sha256": error.config_sha256,
            "direct_child_collected": error.direct_child_collected,
            "descendant_custody": "unconfirmed",
            "provider_request_started": False,
            "workspace_binding_matches": bound,
        },
        "unsettled": unsettled,
    }
    reason = util.redact(str(error))[:2000]
    reason += " AutoCode is blocked: descendant cleanup of this model-free check is unconfirmed; retained work is kept. No new stage is started."
    resolver = state.setdefault("resolver", {})
    identities = resolver.setdefault("operational_diagnostics", [])
    receipts = resolver.setdefault("operational_receipts", {})
    # Local append-only journal ordinal, not a token or model-citable replay
    # identity. This denial cannot authorize source acceptance or another call.
    identity = "model-listing-" + str(len(identities) + 1)
    at = util.now()
    record = {
        "stage": "resolver",
        "role": "resolver",
        "engine": "runner",
        "runner_owned": True,
        "iteration": state.get("iteration"),
        "finished_at": at,
        "exit_code": 0,
        "runner_calls": 0,
        "summary": reason,
        "metrics": {"provider_tokens": {"input_tokens": 0, "output_tokens": 0}},
        "decision": {"action": "blocked", "rationale": reason},
        "receipt": {"id": identity, "callbacks_used": [], "scope": "model_listing", "evidence": evidence},
    }
    receipts[identity] = deepcopy(record)
    identities.append(identity)
    state.setdefault("stages", []).append(deepcopy(record))
    state.setdefault("history", []).append(deepcopy(record))
    run_status.pause(state, error.status, reason)
    runtime.write_json(Path(run_dir) / "state.json", state)
    return True
