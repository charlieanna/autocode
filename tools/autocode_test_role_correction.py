"""Subtract only authenticated original product entries from a saved oracle set.

No current inventory or candidate metadata supplies authority. The original Git
pin establishes ownership; every removed byte/mode must match its retained
original. The old binding and proof remain evidence, never current authority.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_python_test_roles as policy
    from . import autocode_suite_definition_inputs as definition_inputs
    from . import autocode_test_role_inventory as inventory
    from . import autocode_util as util
except ImportError:
    import autocode_python_test_roles as policy
    import autocode_suite_definition_inputs as definition_inputs
    import autocode_test_role_inventory as inventory
    import autocode_util as util


def proposed(record, workspace, base, is_test):
    """Return the exact remaining inventory, or None when nothing is proven product."""
    entries = inventory.entries(workspace, base)
    blobs = {name: (value[0], value[2]) for name, value in entries.items()}
    with definition_inputs.blob_reader(workspace, blobs, None) as read:
        roles = inventory.from_entries(workspace, entries, read, is_test)
        removed = {name for name in record["files"] if name in roles.original_products}
        for name in removed:
            expected = record["files"][name]
            mode = entries[name][0]
            if mode == "120000":
                original = {"symlink": read(name)}
            elif mode in {"100644", "100755"}:
                # Git's UTF-8 replacement reader is sufficient for syntax, but
                # subtraction must compare the exact original bytes.
                raw = inventory.blob(workspace, entries[name][2])
                original = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "mode": int(mode, 8) & 0o777}
            else:
                raise ValueError("Original product entry has unsupported mode: " + name)
            if original != expected:
                raise ValueError("Original product role does not match the protected archive: " + name)
    if not removed:
        return None
    return {name: value for name, value in record["files"].items() if name not in removed}, roles


def reconcile(state: RunState, settings, workspace, run_dir, *, is_test, verify, retain, opened, original_inputs):
    previous = settings.get("protected_tests")
    base = state.get("base_commit")
    if (
        not previous
        or not base
        or any(
            state.get(key)
            for key in (
                "active_stage",
                "active_runner_check",
                "runner_check",
                "uncertain_artifacts",
                "pending_report_repair",
            )
        )
    ):
        return
    verify(previous)
    correction = proposed(previous, workspace, base, is_test)
    if correction is None:
        return
    if original_inputs is None:
        raise ValueError("Correcting original product roles requires authenticated launch inputs")
    inputs = original_inputs()
    if inputs and (inputs.unverified or inputs.generated or inputs.vendored):
        raise ValueError("Original product roles are not established with ignored launch-source overlays")
    files, roles = correction
    with opened(previous, workspace) as original:
        current = retain(original, run_dir, files, previous["command"])
    event = {
        "kind": "protected_tests_roles_corrected",
        "actor": "core",
        "at": util.now(),
        "base_commit": base,
        "role_policy": policy.VERSION,
        "original_metadata": roles.metadata,
        "previous": previous,
        "current": current,
        "reason": "Original runtime product entries are not test oracles; all remaining original bytes and command are retained",
        "previous_regression_proof": state.get("regression_proof"),
    }
    state.setdefault("user_events", []).append(event)
    settings["protected_tests"] = current
    state.pop("regression_proof", None)
