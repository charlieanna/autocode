"""Charges renewals.

Charges live in the `charges` table, keyed by event_id, so a redelivered event is never charged
twice, even after a crash or a restart: inserting an event_id that is already there does nothing.
The charge and its event_id are one row, written in one transaction.
"""

RENEW_PRICE_CENTS = 1200
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS charges ("
    "event_id TEXT PRIMARY KEY, domain TEXT NOT NULL, amount_cents INTEGER NOT NULL)"
)


class Billing:
    def __init__(self, db):
        self.db = db
        self.db.execute(SCHEMA)

    def charge(self, event) -> bool:
        """Charge a renew once; return False when this event_id was already charged."""
        with self.db:
            inserted = self.db.execute(
                "INSERT INTO charges (event_id, domain, amount_cents) VALUES (?, ?, ?) "
                "ON CONFLICT (event_id) DO NOTHING",
                (event.event_id, event.domain, RENEW_PRICE_CENTS),
            ).rowcount
        return inserted == 1
