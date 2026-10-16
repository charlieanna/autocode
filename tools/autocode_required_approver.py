"""Who may approve: a project's ``require-approver`` setting (#722).

A team that wants only named people to approve a plan or accept a reviewed artifact lists their Git email
addresses as ``require-approver`` in its project settings (``.autocode/config.toml``, or ``[tool.autocode]`` in
``pyproject.toml``: autocode_project_config). A new run saves the list in its settings (``SETTING``) when it
starts, so a changed file never alters a run in progress, and no flag sets or overrides it.

On such a run each approval a person makes, a plan approval or an artifact acceptance (by flag, through
``autocode review`` or TaskRun, or in the terminal conversation), is checked against the Git identity its
provenance records (autocode_approval_provenance.capture): ``git config user.email`` as the approving checkout
resolves it, compared without regard to case. An approver not on the list is refused before anything is approved
or saved, with a message naming the required and the actual identity, and the run stays at the same stop. An
``--as`` name is the person's claim and never counts. A goal edit installs a draft that still needs approval, a
resume releases a pause rather than approving anything, and an approval made under a workflow policy is made by
no person, so none of those is checked.

The Git identity is whatever the approving checkout configures: the setting keeps approvals to the people a team
named, by convention, and is not authentication.

Bottom layer: imports nothing from AutoCode.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

KEY = "require-approver"  # the project-file key (autocode_project_config.FILE_ONLY)
SETTING = "require_approver"  # the run's settings key, and the attribute autocode_project_config sets on args
ADDRESS_LIMIT = 254
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+")


class Refused(ValueError):
    """An approval the run's require-approver does not allow: nothing was approved."""


def checked(value, source: str) -> list[str]:
    """The addresses a project file's ``require-approver`` names, in order and without repeats.

    It takes one Git email address or a list of them; anything else raises ValueError naming ``source``.
    """
    entries = [value] if isinstance(value, str) else value
    if not isinstance(entries, list) or not entries or not all(isinstance(entry, str) for entry in entries):
        raise ValueError(f'{source}: {KEY} takes a Git email address or a list of them, such as ["ana@example.com"]')
    addresses: list[str] = []
    for entry in entries:
        address = entry.strip()
        if len(address) > ADDRESS_LIMIT or not address.isprintable() or not _ADDRESS.fullmatch(address):
            raise ValueError(
                f"{source}: {KEY} {entry!r} is not an email address; it lists the Git user.email of each approver"
            )
        if address.casefold() not in {known.casefold() for known in addresses}:
            addresses.append(address)
    return addresses


def required(state: RunState) -> list[str]:
    """The Git email addresses allowed to approve this run; empty when anyone may (no setting, or an older run)."""
    return list((state.get("settings") or {}).get(SETTING) or [])


def _identity(provenance: dict) -> str:
    name, email = provenance.get("git_user_name"), provenance.get("git_user_email")
    if email:
        return f"{name} <{email}>" if name else f"<{email}>"
    return f"{name}, with no user.email set" if name else "unset: neither user.name nor user.email is set"


def refusal(state: RunState, provenance: dict) -> str | None:
    """Why the approver ``provenance`` records may not approve this run, or None when they may."""
    allowed = required(state)
    email = (provenance.get("git_user_email") or "").strip().casefold()
    if not allowed or email in {address.casefold() for address in allowed}:
        return None
    text = (
        f"Approval refused: this run takes approvals only from Git user.email {' or '.join(allowed)} "
        f"({KEY} in the project settings it started with); this checkout's Git identity is "
        f"{_identity(provenance)}. Nothing was approved and the run is still waiting at the same stop."
    )
    if provenance.get("claimed_as"):
        text += f" The --as name {provenance['claimed_as']!r} is a claim and does not count."
    return text


def enforce(state: RunState, provenance: dict) -> None:
    """Raise Refused when the run's require-approver does not allow the approver ``provenance`` records."""
    reason = refusal(state, provenance)
    if reason:
        raise Refused(reason)
