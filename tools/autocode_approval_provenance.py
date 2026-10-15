"""Who approved: the provenance each approval, goal edit and resume records (#722).

An approval token says what was approved, never who agreed to it. When a person
approves a plan (``--approve-goal`` or "yes" in the terminal conversation) or
accepts a reviewed artifact (``--approve-review`` or "y" at the review prompt),
the approval event gains ``provenance``. So does the draft a person installs with
``--edit-goal`` (on the contract revision itself), and an explicit resume
(``--resume-paused`` or ``autocode resume``) that releases the pause holding the
run is saved with its own in ``pause_acknowledgements``. Each provenance holds:

    git_user_name, git_user_email   ``git config user.name`` / ``user.email`` as the
                                    workspace resolves them, None when unset
    host                            the machine's host name
    command                         ``autocode``, the ``resume`` command word when it was
                                    typed, and the option names of the invocation, never
                                    their values: tokens stay out of it as they stay out
                                    of argv
    claimed_as                      the ``--as NAME`` the person gave, or None

Nothing here is verified. Git identity is whatever the checkout configures and a
``--as`` name is the person's own claim; both are recorded for audit and never
grant or refuse authority. The approval checks remain the token checks.

Bottom layer: imports nothing from AutoCode, so the CLI steps, the terminal
conversation, the build loop, the status view and the completion summary can all
use it.
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
# The origin of a contract revision a person wrote with --edit-goal (autocode_native_test_names.USER_EDIT).
EDIT_ORIGIN = "user_cli_edit"
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


def invocation(argv, *, claimed_as: str | None = None, word: str | None = None) -> dict:
    """The command an approval arrived through: option names in order, never their values.

    ``word`` is the command word the person typed (``resume``), which argv cannot tell from task
    text or an option's value; the caller that parsed it names it.
    """
    names = [word] if word else []
    for argument in argv:
        if argument == "--":
            break
        if argument.startswith("--") and len(argument) > 2:
            names.append(argument.split("=", 1)[0])
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


def _revision(contract) -> tuple:
    return (contract.get("revision"), contract.get("hash"))


def stamp(saved: RunState, candidate: RunState, called: dict, *, record=capture) -> int:
    """Record provenance on each approval, and the goal edit, the decision just added to ``candidate``.

    New means a user's approval event absent from the saved run's events, so an
    approval repeated with the same token keeps the provenance of the first. Call it
    straight after the decision, before the request is consumed: the consumed
    response and later request bindings then copy the event with its provenance.
    The approval event is the same object the contract and the review binding hold,
    so they gain it too. An edit is the draft revision --edit-goal installed, which is
    never the saved contract: a later decision on that draft does not restamp it, and
    an edit saved before AutoCode recorded editors keeps none. Its provenance sits
    beside the contract's seal (task_id, revision, body), so the hash and token stay
    as they were. Returns how many records were stamped.
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
    contract, before_contract = candidate.get("goal_contract"), saved.get("goal_contract")
    if (
        isinstance(contract, dict)
        and contract.get("origin") == EDIT_ORIGIN
        and "provenance" not in contract
        and not (isinstance(before_contract, dict) and _revision(before_contract) == _revision(contract))
    ):
        fresh.append(contract)
    if fresh:
        provenance = record(candidate.get("workspace"), called)
        for event in fresh:
            event["provenance"] = deepcopy(provenance)
    return len(fresh)


def decide(decision, saved: RunState, candidate: RunState, argv, *, record=capture) -> None:
    """Apply one terminal-conversation ``decision`` to ``candidate``, then ``stamp`` the approvals it made."""
    decision(candidate)
    stamp(saved, candidate, invocation(argv), record=record)


def acknowledge(state: RunState, pause_status: str | None, called: dict, *, at: str, record=capture) -> bool:
    """Record who resumed the run out of ``pause_status`` in ``pause_acknowledgements``; False when nothing was.

    ``pause_status`` is the pause an explicit resume found holding the run (None when it found
    none). Call it once the run is RUNNING and has passed every check that could hold it at that
    pause, just before it launches work: a pause still in force records nothing. It never touches
    ``user_events``, which every AutoResolver request binds and the deferred-approval bound
    counts as the person's input.
    """
    if not pause_status or state.get("status") != "RUNNING":
        return False
    state.setdefault("pause_acknowledgements", []).append(
        {"at": at, "pause_status": pause_status, "provenance": record(state.get("workspace"), called)}
    )
    return True


def _row(kind, at, actor, provenance, *, contract_token=None, criterion=None, pause_status=None) -> dict:
    return {
        "kind": kind,
        "at": at,
        "actor": actor,
        "contract_token": contract_token,
        "criterion": criterion,
        "pause_status": pause_status,
        "provenance": {key: provenance.get(key) for key in FIELDS} if isinstance(provenance, dict) else None,
    }


def view(state: RunState) -> list[dict]:
    """Every approval, goal edit and pause-releasing resume the run recorded, oldest first (``approvals``).

    A history, not the approvals in force: an approval stays listed after a newer plan
    or a rejection replaces it. ``provenance`` is None for a record saved before
    AutoCode kept it, and for an approval made under the workflow policy (``actor``
    ``workflow_policy``), which no person typed.
    """
    rows = []
    for event in state.get("user_events") or []:
        if isinstance(event, dict) and event.get("kind") in KINDS:
            kind = KINDS[event["kind"]]
            rows.append(
                _row(
                    kind,
                    event.get("at"),
                    event.get("actor"),
                    event.get("provenance"),
                    contract_token=event.get("token") if kind == "plan" else None,
                    criterion=event.get("criterion") if kind == "review" else None,
                )
            )
    seen = set()
    for contract in [*(state.get("contract_history") or []), state.get("goal_contract")]:
        if isinstance(contract, dict) and contract.get("origin") == EDIT_ORIGIN and _revision(contract) not in seen:
            seen.add(_revision(contract))
            token = f"r{contract.get('revision')}:{contract.get('hash')}"  # autocode_contract_identity.token
            rows.append(
                _row("edit", contract.get("created_at"), "user_cli", contract.get("provenance"), contract_token=token)
            )
    for entry in state.get("pause_acknowledgements") or []:
        if isinstance(entry, dict):
            rows.append(
                _row(
                    "resume",
                    entry.get("at"),
                    "user_cli",
                    entry.get("provenance"),
                    pause_status=entry.get("pause_status"),
                )
            )
    return sorted(rows, key=lambda row: str(row["at"] or ""))  # stable: same-time records keep their order


def _who(row: dict) -> str:
    provenance = row["provenance"]
    if provenance is None:
        if row["actor"] == "workflow_policy":
            return "under the workflow policy"
        return "by someone AutoCode did not record (saved before it recorded who approved)"
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
        what = {
            "plan": f"Plan {row['contract_token']} approved",
            "review": f"Review of {row['criterion']} approved",
            "edit": f"Edited plan {row['contract_token']} saved",
            "resume": f"Resumed from {row['pause_status']}",
        }[row["kind"]]
        out.append(f"  {what} {row['at']} {_who(row)}.")
    return out
