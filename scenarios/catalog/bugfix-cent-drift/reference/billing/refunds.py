"""Refunds when a customer returns one line: the line's invoiced amount plus its tax (docs/accounting.md, rule 5)."""

from decimal import Decimal

from billing.invoice import build_invoice


def refund_line(cart: list, index: int) -> Decimal:
    line = build_invoice(cart).lines[index]
    return line.amount + line.tax
