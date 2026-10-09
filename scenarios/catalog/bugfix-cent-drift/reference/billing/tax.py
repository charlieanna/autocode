"""Sales tax, rounded per line (docs/accounting.md, rule 3)."""
from decimal import Decimal

from billing.money import to_cents

TAX_RATE = Decimal("0.0825")


def tax_on(amount: Decimal) -> Decimal:
    return to_cents(amount * TAX_RATE)
