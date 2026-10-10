"""Pure supervision status from birth-bound metadata and supplied fresh observations.

This module never inspects processes, reads receipts or changes run state. The
runtime authenticates the receipt and measures liveness; callers pass that
inspection here. Unknown access never becomes a checked-dead process. A keeper
receipt describes cleanup, not the provider's exit code or product completion.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy

ROLES = ("owner", "keeper", "provider")
PHASES = frozenset({"armed", "stopping", "stopped", "uncertain", "discharged"})
INTERRUPTIONS = frozenset({"owner_lost", "stage_deadline"})
NORMAL_STOPS = frozenset({"provider_stopped", "controller_finished"})
PROCESS_INVENTORY_LIMIT = 20000
CAUSES = INTERRUPTIONS | NORMAL_STOPS | frozenset({"invalid_owner_message", "lifeline_failure", "keeper_failure"})


def _identity(value):
    return (
        isinstance(value, dict)
        and type(value.get("pid")) is int
        and value["pid"] > 0
        and type(value.get("birth_identity")) in (int, float)
        and (type(value["birth_identity"]) is int or math.isfinite(value["birth_identity"]))
        and value["birth_identity"] > 0
    )


def _metadata(value):
    return (
        isinstance(value, dict)
        and type(value.get("schema")) is int
        and value["schema"] == 1
        and isinstance(value.get("nonce"), str)
        and re.fullmatch("[0-9a-f]{32}", value["nonce"]) is not None
        and isinstance(value.get("receipt"), str)
        and bool(value["receipt"])
        and all(_identity(value.get(role)) for role in ROLES)
    )


def _process(value):
    if isinstance(value, dict) and value.get("checked") is True and type(value.get("alive")) is bool:
        return {"checked": True, "alive": value["alive"]}
    return {"checked": False, "alive": None}


def _receipt(metadata, value):
    required = {"schema", "nonce", *ROLES, "phase", "cause", "processes", "cleanup_error", "observed_at"}
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or any(value.get(key) != metadata[key] for key in ("nonce", *ROLES))
        or not isinstance(value["phase"], str)
        or value["phase"] not in PHASES
        or (value["cause"] is not None and (not isinstance(value["cause"], str) or value["cause"] not in CAUSES))
        or (value["cleanup_error"] is not None and not isinstance(value["cleanup_error"], str))
        or not isinstance(value["observed_at"], str)
        or not value["observed_at"]
        or not isinstance(value["processes"], list)
        or len(value["processes"]) > PROCESS_INVENTORY_LIMIT
        or not all(_identity(row) for row in value["processes"])
        or len({row["pid"] for row in value["processes"]}) != len(value["processes"])
        or not any(
            row["pid"] == metadata["provider"]["pid"]
            and row["birth_identity"] == metadata["provider"]["birth_identity"]
            for row in value["processes"]
        )
    ):
        return None
    if (
        value["phase"] == "armed"
        and (value["cause"] is not None or value["cleanup_error"] is not None)
        or value["phase"] == "discharged"
        and value["cause"] != "controller_finished"
    ):
        return None
    return deepcopy(value)


def classify(metadata, inspection=None):
    """Classify one attempt without substituting saved RUNNING for fresh liveness.

    ``metadata`` is active_stage.supervision, written only by the supervision
    launcher. ``inspection`` is autocode_supervision.observe(metadata): each
    role has {checked, alive}, and receipt is already authenticated or None.
    Missing/failed inspection stays unknown. An authenticated interruption can
    describe ongoing cleanup, so it does not assert that any process is dead.
    """
    result = {
        "kind": "unknown",
        "reason": "metadata_missing",
        **{role: {"checked": False, "alive": None} for role in ROLES},
        "receipt": None,
    }
    if not _metadata(metadata):
        if metadata is not None:
            result["reason"] = "metadata_invalid"
        return result
    if not isinstance(inspection, dict):
        result["reason"] = "inspection_unavailable"
        return result
    for role in ROLES:
        result[role] = _process(inspection.get(role))
    receipt = _receipt(metadata, inspection.get("receipt"))
    result["receipt"] = receipt
    if receipt and receipt["phase"] != "armed" and receipt.get("cause") in INTERRUPTIONS:
        result.update(kind="interrupted", reason=receipt["cause"])
        return result
    if not all(result[role]["checked"] for role in ROLES):
        result["reason"] = "inspection_unavailable"
        return result
    if result["provider"]["alive"]:
        if not result["owner"]["alive"]:
            result.update(kind="unsupervised", reason="owner_not_alive")
        elif not result["keeper"]["alive"]:
            result.update(kind="unsupervised", reason="keeper_not_alive")
        elif (
            receipt
            and receipt["phase"] == "armed"
            and receipt.get("cause") is None
            and receipt.get("cleanup_error") is None
        ):
            result.update(kind="supervised", reason="owner_and_keeper_alive")
        else:
            result["reason"] = "ownership_receipt_unavailable"
    elif (
        receipt
        and receipt["phase"] in ("stopped", "discharged")
        and receipt.get("cause") in NORMAL_STOPS
        and receipt.get("cleanup_error") is None
    ):
        result.update(kind="stopped", reason=receipt["cause"])
    else:
        result["reason"] = "stop_receipt_unavailable"
    return result
