"""The invoice the customer receives."""

from dataclasses import dataclass

from billing.cart import line_subtotal
from billing.tax import tax_on


@dataclass
class InvoiceLine:
    sku: str
    amount: float
    tax: float


@dataclass
class Invoice:
    lines: list
    total: float


def build_invoice(cart: list) -> Invoice:
    lines = []
    for line in cart:
        subtotal = line_subtotal(line)
        lines.append(InvoiceLine(line["sku"], round(subtotal, 2), round(tax_on(subtotal), 2)))
    return Invoice(lines, round(sum(line.amount + line.tax for line in lines), 2))
