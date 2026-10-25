"""Background public run discovery before a terminal chat has selected a run.

Ownership of the same read pool/inbox transfers to ChatClient after selection.
No run identity, controller or task action is created by opening this console.
"""

import time

try:
    from . import autocode_chat_attention as attention
    from . import autocode_chat_inventory as inventory
    from . import autocode_chat_polling as polling
except ImportError:
    import autocode_chat_attention as attention
    import autocode_chat_inventory as inventory
    import autocode_chat_polling as polling


class Discovery:
    def __init__(self, workspace, *, command, env, say, changed, open_run):
        self.workspace, self.command, self.env = workspace, command, env
        self.say, self.attention_changed, self.open_run = say, changed, open_run
        self._attention = attention.Inbox()
        self._attention_action = None
        self._poller = polling.StatusPoller(command, env)
        self._overview = inventory.Overview(self._poller, say)
        self.transferred = False

    def poll(self):
        attention.poll_acknowledgement(self)
        targets = {(str(self.workspace), None): polling.INVENTORY_SECONDS, **self._overview.targets()}
        for key, raw, error in self._poller.tick(targets):
            self._overview.accept(key, raw, error)
            if not isinstance(key, polling.ProjectRuns):
                self._attention.listing(raw, error, started=self._poller.completed.get(key, time.monotonic()))
        self.attention_changed(self._attention.summary())
        for row in self._attention.notifications():
            self.say("New attention: " + attention.label(row) + ". F8 opens the inbox.")

    def menu(self):
        def refresh():
            self._poller.refresh((str(self.workspace), None))
            menu = self.menu()
            menu.note = "Refresh requested. Press r after the read to replace this snapshot; Esc keeps your draft."
            return menu

        return attention.Menu(
            self._attention.entries(),
            self._attention.summary(),
            self.open_run,
            lambda row: attention.acknowledge(self, row),
            refresh,
        )

    def transfer(self, client):
        client._poller.close()
        client._poller, client._attention, client._attention_action = (
            self._poller,
            self._attention,
            self._attention_action,
        )
        client._overview = self._overview
        self._attention_action, self.transferred = None, True
        if client._status_view is not None:
            client._attention.observe(client._status_view, received=time.monotonic())

    def close(self):
        if not self.transferred:
            if self._attention_action is not None:
                self._attention_action.close()
            self._poller.close()
