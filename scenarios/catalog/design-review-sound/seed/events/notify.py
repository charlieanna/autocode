"""Client for the external notification service.

Contract: at most one delivery per event_id; a repeated send with the same event_id is
accepted and ignored. Calls time out after 2 seconds and raise NotificationError.
"""


class NotificationError(Exception):
    pass


class Notifier:
    def __init__(self, transport):
        self.transport = transport

    def send(self, event_id, event):
        self.transport.post("/notify", {"event_id": event_id, "domain": event["domain"],
                                        "kind": event["kind"]}, timeout=2.0)
