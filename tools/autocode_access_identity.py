"""Prove access-receipt identity across a content-identical config relocation.

Only retained settings snapshots can supply an old digest's preimage. This does
not accept transport changes or certify tools: normal live admission still runs.
No receipt or historical settings are rewritten.
"""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from .autocode_util import digest
except ImportError:
    from autocode_util import digest

_FIELDS = {"engine", "identity_version", "executable", "version", "config_path", "config_sha256"}


def _complete(value):
    return (
        isinstance(value, dict)
        and set(value) == _FIELDS
        and type(value["identity_version"]) is int
        and value["identity_version"] == 1
        and all(isinstance(value[key], str) and value[key] for key in ("engine", "executable", "config_path"))
        and PurePath(value["executable"]).is_absolute()
        and PurePath(value["config_path"]).is_absolute()
        and isinstance(value["version"], str)
        and bool(value["version"].strip())
        and isinstance(value["config_sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", value["config_sha256"]) is not None
    )


def _primary(settings):
    if not isinstance(settings, dict):
        return None
    identities = settings.get("transport_identities")
    if identities is not None and not isinstance(identities, dict):
        return None
    value = (identities or {}).get("opencode", settings.get("transport_identity"))
    if "transport_identity" in settings and settings["transport_identity"] != value:
        return None
    return value if _complete(value) else None


def equivalents(state: RunState, identity) -> list[dict]:
    """Current identity plus proven preceding locations, newest first.

    Explicit role-bound transports remain immutable and have no relocation path.
    Walk only the primary tool's saved settings transitions. A real tool or route
    change, a broken chain or an incomplete snapshot ends the proof. Metadata-only
    history entries contain no settings transition and grant no equivalence.
    """
    if not isinstance(identity, dict):
        return []
    result = [identity]
    settings = state.get("settings") or {}
    current = _primary(settings)
    roles = settings.get("roles") if isinstance(settings, dict) else None
    changes = state.get("configuration_changes")
    if (
        current is None
        or not isinstance(roles, dict)
        or identity.get("provider") != current["engine"]
        or identity.get("transport_sha256") != digest(current)
        or not isinstance(changes, list)
    ):
        return result
    for change in reversed(changes):
        if not isinstance(change, dict):
            break
        if "previous" not in change and "selected" not in change:
            continue
        previous, selected = change.get("previous"), change.get("selected")
        before, after = _primary(previous), _primary(selected)
        if (
            not isinstance(previous, dict)
            or not isinstance(selected, dict)
            or before is None
            or after != current
            or previous.get("roles") != roles
            or selected.get("roles") != roles
            or any(before[key] != current[key] for key in _FIELDS - {"config_path"})
        ):
            break
        alternate = {**identity, "transport_sha256": digest(before)}
        if alternate not in result:
            result.append(alternate)
        current = before
    return result
