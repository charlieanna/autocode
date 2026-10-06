"""Sends one notification per event. A redelivered event is skipped: sent event_ids are remembered."""


class Notifier:
    def __init__(self, send):
        self.send = send
        self.sent: set[str] = set()

    def notify(self, event) -> bool:
        if event.event_id in self.sent:
            return False
        self.send(event)
        self.sent.add(event.event_id)
        return True
