"""Asynchronous /runs snapshots over the terminal's shared bounded read pool.

Listing a run never subscribes to its status. Numbered rows change only when
the person explicitly inspects /runs, so an arriving refresh cannot retarget
an /open number they are typing.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from . import autocode_chat_panel as panel
    from . import autocode_chat_polling as polling
except ImportError:
    import autocode_chat_panel as panel
    import autocode_chat_polling as polling


class Overview:
    def __init__(self, poller, say):
        self.poller, self.say = poller, say
        self.snapshots: dict[polling.Key, list[dict]] = {}
        self.errors: dict[polling.Key, str] = {}
        self.pending: set[polling.Key] = set()
        self.first = None
        self.after = None
        self.requested = ()

    def cancel_navigation(self):
        self.first, self.after = None, None

    def targets(self):
        return {key: polling.INVENTORY_SECONDS for key in self.pending}

    def snapshot(self, history, current=None):
        workspace = str(history.workspace)
        keys: tuple[polling.Key, ...] = (polling.ProjectRuns(workspace), (workspace, None))
        ready = all(key in self.snapshots for key in keys)
        if ready:
            listed = [row for key in keys for row in self.snapshots[key]]
            here = Path(workspace).resolve()
            history.rows, history.left_out = panel.run_rows(listed, here=here), panel.hidden(listed, here)
            text = panel.runs_text(history.rows, current, here=here, left_out=history.left_out)
        else:
            text = "Reading the run list."
        errors = [self.errors[key] for key in keys if key in self.errors]
        if errors:
            text += "\nRun list unavailable: " + "; ".join(errors)
            if ready:
                text += ". Displaying the last snapshot."
        return text

    def request(self, history, current=None, *, then=None):
        self.first = None
        workspace = str(history.workspace)
        keys: tuple[polling.Key, ...] = (polling.ProjectRuns(workspace), (workspace, None))
        text = (
            self.snapshot(history, current)
            if then is None
            else "Reading the run list before opening the selected chat."
        )
        self.after = then
        self.requested = keys
        if not history.rows or then is not None:
            self.first = history, current
        for key in keys:
            if key not in self.pending and key not in self.poller.active:
                self.poller.refresh(key)
            self.pending.add(key)
        return text + "\nRefresh requested; /runs shows the new snapshot once read. Listed chats are not opened tabs."

    def accept(self, key, raw, error):
        try:
            rows = json.loads(raw)["runs"] if not error else None
            if not error and (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)):
                error = "run list response has no valid runs"
        except (ValueError, KeyError, TypeError):
            rows, error = None, "run list response has no valid runs"
        if error:
            self.errors[key] = error
        else:
            self.snapshots[key] = rows
            self.errors.pop(key, None)
        if key in self.pending:
            self.pending.remove(key)
            if not self.pending:
                if self.first is not None:
                    history, current = self.first
                    self.first = None
                    self.say(self.snapshot(history, current))
                self.say(
                    "Run list read finished. Type /runs to inspect its snapshot; /open keeps the displayed numbers."
                )
                after, self.after = self.after, None
                if after is not None and not any(key in self.errors for key in self.requested):
                    after()
