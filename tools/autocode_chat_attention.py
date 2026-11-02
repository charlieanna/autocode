"""A bounded display inbox over public run/status views, never task authority."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    from . import autocode_chat_attention_action as action
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_chat_attention_action as action
    import autocode_client_actions as actions

LIMIT = 256


def key(row):
    workspace, run = row.get("workspace"), row.get("run_dir")
    if not all(
        isinstance(value, str) and "\0" not in value and Path(value).is_absolute() for value in (workspace, run)
    ):
        return None
    return workspace, run


def item(row):
    value = row.get("attention") or {}
    candidate = value.get("item") if isinstance(value, dict) else None
    if (
        not isinstance(candidate, dict)
        or not isinstance(candidate.get("id"), str)
        or re.fullmatch(r"[0-9a-f]{64}", candidate["id"]) is None
    ):
        return None
    return candidate if key(candidate) == key(row) else None


LOADING = "Reading saved attention"


class Inbox:
    def __init__(self):
        self.rows = {}
        self.status_at = {}
        self.error = LOADING
        self.overflow = 0
        self.seen = {}
        self.pending = {}
        self.initialized = False
        self.shown = None  # the (workspace, run_dir) of the chat on screen, set by its client

    def listing(self, raw, error, *, started):
        try:
            rows = json.loads(raw)["runs"] if not error else None
            if not isinstance(rows, list):
                raise ValueError("run inventory is unavailable")
        except (ValueError, KeyError, TypeError):
            self.error = error or "Saved attention could not be read"
            return
        incoming = {key(row): copy.deepcopy(row) for row in rows if isinstance(row, dict) and key(row)}
        # A list started before a newer status result cannot replace that run.
        for identity, old in self.rows.items():
            if identity in self.status_at and (identity not in incoming or self.status_at[identity] > started):
                incoming[identity] = old
        self.overflow = max(0, len(incoming) - LIMIT)
        self.rows = dict(list(incoming.items())[:LIMIT])
        self.status_at = {identity: when for identity, when in self.status_at.items() if identity in self.rows}
        self.error = ""
        for row in self.rows.values():
            self._track(row)
        self.initialized = True
        self.seen = {identity: value for identity, value in self.seen.items() if identity in self.rows}

    def observe(self, view, *, received):
        identity = key(view)
        if identity is None:
            return
        self.rows[identity] = {
            "workspace": identity[0],
            "run_dir": identity[1],
            "attention": copy.deepcopy(view.get("attention")),
            "availability": "available",
            "needs": (view.get("needs") or {}).get("kind"),
            "status": view.get("status"),
        }
        self.status_at[identity] = received
        self._track(self.rows[identity])
        while len(self.rows) > LIMIT:
            removed = next(iter(self.rows))
            self.rows.pop(removed)
            self.status_at.pop(removed, None)
            self.seen.pop(removed, None)

    def _track(self, row):
        identity, current = key(row), item(row)
        if current is not None:
            if self.initialized and self.seen.get(identity) != current["id"] and self.read_state(row) == "unread":
                self.pending[identity] = copy.deepcopy(row)
            self.seen[identity] = current["id"]

    def notifications(self):
        rows = [
            row
            for identity, row in self.pending.items()
            if (item(self.rows.get(identity, {})) or {}).get("id") == (item(row) or {}).get("id")
            and self.read_state(self.rows[identity]) == "unread"
        ]
        self.pending.clear()
        return rows

    def unavailable(self, identity):
        if identity in self.rows:
            self.rows[identity]["availability"] = "unavailable"

    def entries(self):
        entries = []
        for row in self.rows.values():
            current = item(row)
            if (
                current is not None
                or row.get("availability") != "available"
                or row.get("needs") not in (None, "continue", "dependency")
            ):
                entries.append(copy.deepcopy(row))
        return sorted(
            entries,
            key=lambda row: (
                self.read_state(row) == "acknowledged",
                (item(row) or {}).get("kind") == "completion",
                key(row),
            ),
        )

    @staticmethod
    def read_state(row):
        value = row.get("attention")
        receipt = value.get("acknowledgement") if isinstance(value, dict) else None
        state = receipt.get("state") if isinstance(receipt, dict) else None
        return state if state in ("unread", "acknowledged") else "unknown"

    def summary(self):
        entries = self.entries()
        here = [row for row in entries if self.shown is not None and key(row) == self.shown]
        return {
            "unread": sum(self.read_state(row) == "unread" for row in entries),
            "unknown": sum(
                self.read_state(row) == "unknown" or row.get("availability") != "available" for row in entries
            ),
            # The chat on screen already shows its own question or result, so its alert is no news
            # to the badge; the inbox (F8) still lists it with the others.
            "shown_unread": sum(self.read_state(row) == "unread" for row in here),
            "count": len(entries),
            "error": self.error,
            "loading": self.error == LOADING,  # the first read has not finished; nothing is wrong yet
            "overflow": self.overflow,
        }

    def acknowledged(self, item_id, receipt):
        for row in self.rows.values():
            if (item(row) or {}).get("id") == item_id:
                row["attention"]["acknowledgement"] = {
                    "available": True,
                    "state": "acknowledged",
                    "receipt": copy.deepcopy(receipt),
                }


@dataclass
class Menu:
    rows: list[dict]
    summary: dict
    open: Callable[[dict], None]
    acknowledge: Callable[[dict], str]
    refresh: Callable[[], Menu]
    selected: int = 0
    note: str = "Saved attention; opening a run reads its current status."


def label(row):
    current = item(row) or {}
    state = Inbox.read_state(row)
    if row.get("availability") != "available":
        state = "unavailable; last saved alert"
    name = current.get("summary") or Path(row["run_dir"]).name
    return f"[{state}] {current.get('label') or 'Inspect current status'} | {Path(row['workspace']).name}: {name}"


def acknowledge(client, row):
    """Dispatch only the shown acknowledgement. An uncertain reply is not resent."""
    try:
        if client._attention_action is not None:
            return "An acknowledgement is still pending; typing and other reads remain available."
        command = actions.attention_acknowledgement(
            row.get("attention") or {}, Path(row["workspace"]), Path(row["run_dir"])
        )
        client._attention_action = action.Pending(
            client.command, command.argv, row, env=client.env, cwd=client.workspace
        )
        return "Acknowledgement pending; you can close this drawer and keep typing."
    except (actions.ClientGap, OSError, ValueError, TypeError):
        return "Acknowledgement reply is unavailable. Inspect or acknowledge this same alert again to reconcile; no task action was sent."


def poll_acknowledgement(client):
    pending = client._attention_action
    if pending is None or (result := pending.poll()) is None:
        return
    client._attention_action = None
    if result["acknowledged"]:
        receipt = result["receipt"]
        client._attention.acknowledged(receipt["item_id"], receipt)
        # Cancel reads begun before the saved receipt. They must not resurrect unread state.
        client._poller.refresh((str(client.workspace), None))
        client._poller.refresh((receipt["workspace"], receipt["run_dir"]))
        note = "Alert acknowledged. Its task still needs its own current answer or approval."
    else:
        note = result["reason"]
    client.say(note)
    if client.attention_changed is not None:
        client.attention_changed({**client._attention.summary(), "notice": note})
