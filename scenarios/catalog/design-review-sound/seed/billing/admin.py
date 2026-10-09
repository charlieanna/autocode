"""Admin CLI: record a manual charge (goodwill adjustments, offline renewals)."""
from . import ledger


def manual_charge(db, domain, amount_cents):
    ledger.charge(db, domain, None, amount_cents)
