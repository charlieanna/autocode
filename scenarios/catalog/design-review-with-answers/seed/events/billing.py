"""Charges renewals. A redelivered event is never charged twice: charges are keyed by event_id."""

RENEW_PRICE_CENTS = 1200


class Billing:
    def __init__(self):
        self.charges: dict[str, tuple[str, int]] = {}  # event_id -> (domain, amount in cents)

    def charge(self, event) -> bool:
        """Charge a renew once; return False when this event_id was already charged."""
        if event.event_id in self.charges:
            return False
        self.charges[event.event_id] = (event.domain, RENEW_PRICE_CENTS)
        return True
