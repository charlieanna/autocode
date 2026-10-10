"""Money: exact decimal dollars, rounded half up to the cent (docs/accounting.md, rule 1).

Every module that invoices, charges or refunds goes through here, so they cannot disagree.
"""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def dollars(value) -> Decimal:
    """Exact dollars from text or an int; never from a float."""
    return Decimal(str(value)) if isinstance(value, int) else Decimal(value)


def to_cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)
