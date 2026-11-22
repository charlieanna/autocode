"""Bind a capture family a provider attempt left unsettled to that attempt's run (#4028).

A stage's tools may run ``autocode capture --compare-base``. When that separate
CLI cannot confirm its supervised command stopped, it keeps its private custody
journal and exits with error text; nothing reached the run that launched it.
Core does not take the CLI's word, an environment value or a model's report for
this. It reads the custody store the provider's own environment names, once
before the attempt and once after the attempt's process tree is collected, and a
journal that is still unsettled afterwards and was not the one held before is
this attempt's hold: the run pauses with a typed ``CustodyUncertain`` bound to
the exact journal Core read, and recovers only when ``operational_block.guard``
authenticates that journal's cleanup.

A hold present before the attempt already refused every later admission, so it
is not attributed to this run. Two runs sharing one ambient store can both see
a new journal; each then pauses until that journal's cleanup is authenticated,
which fails closed and never releases a hold. Nothing here retries, repairs,
archives or deletes a custody file.
"""

from __future__ import annotations

from collections.abc import Mapping

try:
    from . import autocode_metadata_custody as custody
    from .autocode_util import Paused
except ImportError:
    import autocode_metadata_custody as custody
    from autocode_util import Paused

STATUS = "PAUSED_PROCESS_CLEANUP"


def before(environment: Mapping[str, str]) -> dict:
    """What the store held as the attempt started: its unsettled group, or that it could not be read."""
    try:
        held = custody.unsettled(environment)
    except custody.CustodyUncertain:
        return {"readable": False, "group": None}
    return {"readable": True, "group": held[1]["group_nonce"] if held else None}


def after(start: dict, environment: Mapping[str, str]) -> custody.CustodyUncertain | None:
    """The typed hold this attempt left, or None.

    A store that became unreadable during the attempt is a hold with no binding,
    which no later cleanup can authenticate; one unreadable before and after
    proves nothing about this attempt, and is left to the shared admission hold.
    """
    try:
        held = custody.unsettled(environment)
    except custody.CustodyUncertain as error:
        return error if start.get("readable") else None
    if held is None:
        return None
    root, pending = held
    if start.get("readable") and pending["group_nonce"] == start.get("group"):
        return None
    return custody.bound(root, pending)


def describe(hold: custody.CustodyUncertain) -> dict:
    """The stage record's locator for the hold; never evidence or authority by itself."""
    binding = hold.binding or {}
    pending = binding.get("pending") or {}
    return {"root": binding.get("root"), "group_nonce": pending.get("group_nonce"), "bound": bool(binding)}


def require_settled(start: dict, environment: Mapping[str, str], record: dict, persist) -> None:
    """Pause the attempt's run when it left a hold, once its provider's process tree was collected.

    The stage record keeps the hold's locator and is saved first; the pause carries the typed hold as its
    cause, so ``operational_block.record`` retains it as the run's metadata denial. It outranks the exit.
    """
    if hold := after(start, environment):
        record["capture_custody"] = describe(hold)
        persist()
        raise Paused(STATUS, str(hold)) from hold
