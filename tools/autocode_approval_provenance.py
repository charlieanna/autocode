"""Who approved: the provenance each plan and review approval records (#722).

An approval token says what was approved, never who agreed to it. When a person
approves a plan (``--approve-goal`` or "yes" in the terminal conversation) or
accepts a reviewed artifact (``--approve-review`` or "y" at the review prompt),
the approval event gains ``provenance``:

    git_user_name, git_user_email   ``git config user.name`` / ``user.email`` as the
                                    workspace resolves them, None when unset
    host                            the machine's host name
    command                         ``autocode`` and the option names of the invocation,
                                    never their values: tokens stay out of it as they
                                    stay out of argv
    claimed_as                      the ``--as NAME`` the person gave, or None

Nothing here is verified. Git identity is whatever the checkout configures and a
``--as`` name is the person's own claim; both are recorded for audit and never
grant or refuse authority. The approval checks remain the token checks.

Bottom layer: imports nothing from AutoCode, so the CLI steps, the terminal
conversation, the status view and the completion summary can all use it.
"""

from __future__ import annotations

import socket
import subprocess
from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

# The approval events a person makes, and the name the status view gives each.
KINDS = {"goal_approval": "plan", "human_review": "review"}
FIELDS = ("git_user_name", "git_user_email", "host", "command", "claimed_as")
CLAIM_LIMIT = 200


def claim_refusal(name: str | None) -> str | None:
    """Why ``--as NAME`` cannot be recorded, or None when it can (or was not given)."""
    if name is None:
        return None
    if not name.strip():
        return "--as needs a nonempty name"
    if len(name) > CLAIM_LIMIT:
        return f"--as takes at most {CLAIM_LIMIT} characters"
    if any(not character.isprintable() for character in name):
        return "--as takes a name on one line, without control characters"
    return None


def invocation(argv, *, claimed_as: str | None = None) -> dict:
    """The command an approval arrived through: option names in order, never their values."""
    names = []
    for word in argv:
        if word == "--":
            break
        if word.startswith("--") and len(word) > 2:
            names.append(word.split("=", 1)[0])
    return {"command": " ".join(["autocode", *names]), "claimed_as": claimed_as.strip() if claimed_as else None}


def _git_config(workspace, key: str, run) -> str | None:
    if not workspace:
        return None
    try:
        result = run(
            ["git", "-C", str(workspace), "config", "--get", key],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    value = (result.stdout or "").strip() if result.returncode == 0 else ""
    return value or None


def _host(hostname) -> str | None:
    try:
        return hostname() or None
    except OSError:
        return None


def capture(workspace, called: dict, *, run=subprocess.run, hostname=socket.gethostname) -> dict:
    """This machine's account of who is approving, for ``called`` (an ``invocation``)."""
    return {
        "git_user_name": _git_config(workspace, "user.name", run),
        "git_user_email": _git_config(workspace, "user.email", run),
        "host": _host(hostname),
        "command": called.get("command"),
        "claimed_as": called.get("claimed_as"),
    }


def stamp(saved: RunState, candidate: RunState, called: dict, *, record=capture) -> int:
    """Record provenance on each approval the decision just added to ``candidate``.

    New means a user's approval event absent from the saved run's events, so an
    approval repeated with the same token keeps the provenance of the first. Call it
    straight after the decision, before the request is consumed: the consumed
    response and later request bindings then copy the event with its provenance.
    The approval event is the same object the contract and the review binding hold,
    so they gain it too. Returns how many events were stamped.
    """
    before = saved.get("user_events") or []
    fresh = [
        event
        for event in candidate.get("user_events") or []
        if isinstance(event, dict)
        and event.get("kind") in KINDS
        and event.get("actor") == "user_cli"
        and "provenance" not in event
        and event not in before
    ]
    if fresh:
        provenance = record(candidate.get("workspace"), called)
        for event in fresh:
            event["provenance"] = deepcopy(provenance)
    return len(fresh)


def decide(decision, saved: RunState, candidate: RunState, argv, *, record=capture) -> None:
    """Apply one terminal-conversation ``decision`` to ``candidate``, then ``stamp`` the approvals it made."""
    decision(candidate)
    stamp(saved, candidate, invocation(argv), record=record)


def view(state: RunState) -> list[dict]:
    """Every plan and review approval the run recorded, oldest first (the status view's ``approvals``).

    A history, not the approvals in force: an approval stays listed after a newer plan
    or a rejection replaces it. ``provenance`` is None for an approval recorded before
    AutoCode kept it, and for one made under the workflow policy (``actor``
    ``workflow_policy``), which no person typed.
    """
    rows = []
    for event in state.get("user_events") or []:
        if not isinstance(event, dict) or event.get("kind") not in KINDS:
            continue
        kind = KINDS[event["kind"]]
        provenance = event.get("provenance")
        rows.append(
            {
                "kind": kind,
                "at": event.get("at"),
                "actor": event.get("actor"),
                "contract_token": event.get("token") if kind == "plan" else None,
                "criterion": event.get("criterion") if kind == "review" else None,
                "provenance": {key: provenance.get(key) for key in FIELDS} if isinstance(provenance, dict) else None,
            }
        )
    return rows


def _who(row: dict) -> str:
    provenance = row["provenance"]
    if provenance is None:
        if row["actor"] == "workflow_policy":
            return "under the workflow policy"
        return "by someone AutoCode did not record (approved before it recorded approvers)"
    name, email = provenance["git_user_name"], provenance["git_user_email"]
    who = " ".join(part for part in (name, f"<{email}>" if email else None) if part) or "a Git user with no name set"
    text = f"by {who} on {provenance['host'] or 'an unnamed host'} via `{provenance['command']}`"
    if provenance["claimed_as"]:
        text += f", who gave the name {provenance['claimed_as']!r} (a claim, not verified)"
    return text


def lines(state: RunState) -> list[str]:
    """The completion summary's approvals section, or [] for a run with none."""
    rows = view(state)
    if not rows:
        return []
    out = ["Approvals:"]
    for row in rows:
        what = f"Plan {row['contract_token']}" if row["kind"] == "plan" else f"Review of {row['criterion']}"
        out.append(f"  {what} approved {row['at']} {_who(row)}.")
    return out
