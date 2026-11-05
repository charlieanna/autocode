"""Follow a provider config that moved with its checkout, without a person.

A run resumed from another AutoCode checkout reads the bundled provider config at a
new path. The bytes are the same, so it is the same transport: the provider's drift
check (``access_identity.path_only``) does not pause, and the saved identity keeps the
path it was bound at, so every binding over the run's settings still holds. This
module records that decision in ``configuration_changes`` as a metadata row, once per
move, and decides when the OpenCode facade's identity may be expanded in place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_access_identity as access_identity
    from . import autocode_providers as providers
    from . import autocode_role_transport as role_transport
except ImportError:
    import autocode_access_identity as access_identity
    import autocode_providers as providers
    import autocode_role_transport as role_transport

REASON = "Provider config read from another location; its bytes match the saved identity"


def expands(current, saved) -> bool:
    """Whether the current identity is a newer identity version of the saved one.

    Only then may the saved identity be replaced in place. An equal version that passed
    the drift check is either the same identity or a path-only move, and replacing it
    would change the run's settings and stale every binding over them.
    """
    return current.get("identity_version", 1) > saved.get("identity_version", 1)


def note(state: RunState, transport, current, saved, *, at) -> bool:
    """Record once that ``transport`` (a ``transport_identities`` key) reads its config elsewhere."""
    if not access_identity.path_only(current, saved):
        return False
    row = {
        "reason": REASON,
        "transport": transport,
        "config_path": current["config_path"],
        "saved_config_path": saved["config_path"],
    }
    changes = state.setdefault("configuration_changes", [])
    if any(isinstance(change, dict) and {key: change.get(key) for key in row} == row for change in changes):
        return False
    changes.append({"at": at, **row})
    return True


def follow(state: RunState, workspace, primary, *, at, resolve=None) -> None:
    """Note each saved tool whose config moved: the run's own (``primary``) and each role-bound tool.

    ``primary`` is the run's tool identity read now; ``resolve`` loads a role-bound tool's facade
    by provider name (``autocode_providers.resolve``). Call only after the drift checks passed.
    """
    resolve = resolve or providers.resolve
    settings = state.get("settings") or {}
    saved = settings.get("transport_identity")
    if isinstance(primary, dict) and isinstance(saved, dict):
        note(state, "opencode", primary, saved, at=at)
    for key, entry in sorted((settings.get("transport_identities") or {}).items()):
        if not (
            key.startswith(role_transport.PREFIX)
            and isinstance(entry, dict)
            and isinstance(entry.get("identity"), dict)
        ):
            continue
        facade = resolve(entry["provider"])
        if getattr(facade, "CONFIGURED", False):
            note(state, key, facade.local_settings(workspace), entry["identity"], at=at)
