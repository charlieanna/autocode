"""Charges for registry operations. The charges table lives in the same database as the events."""


def charge(db, domain, event_id, amount_cents):
    """Record one charge and commit it. event_id is None for manual charges."""
    db.execute("INSERT INTO charges (domain, event_id, amount_cents) VALUES (?, ?, ?)",
               (domain, event_id, amount_cents))
    db.commit()
