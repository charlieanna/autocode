"""The invoice the customer receives: the single source of every billed amount."""
from dataclasses import dataclass
from decimal import Decimal

from billing.cart import line_subtotal
from billing.money import to_cents
from billing.tax import tax_on


@dataclass
class InvoiceLine:
    sku: str
    amount: Decimal
    tax: Decimal


@dataclass
class Invoice:
    lines: list
    total: Decimal


def build_invoice(cart: list) -> Invoice:
    lines = []
    for line in cart:
        amount = to_cents(line_subtotal(line))
        lines.append(InvoiceLine(line["sku"], amount, tax_on(amount)))
    return Invoice(lines, sum((line.amount + line.tax for line in lines), Decimal(0)))
