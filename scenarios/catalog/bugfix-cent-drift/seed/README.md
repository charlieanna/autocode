# billing

Order billing for a small shop: what we invoice, what we charge the card, and
what we refund.

- `billing/cart.py`: cart lines, `{"sku", "price" (dollars as text, e.g. "19.99"), "qty", "discount_pct"}`.
- `billing/tax.py`: the sales tax rate.
- `billing/invoice.py`: `build_invoice(cart)` -> `Invoice(lines=[InvoiceLine(sku, amount, tax)], total)`.
- `billing/payment.py`: `charge_amount(cart)`, the amount charged to the card.
- `billing/refunds.py`: `refund_line(cart, index)`, the amount refunded when a customer returns one line.

All amounts are in dollars. The accounting rules are in `docs/accounting.md`.

## Tests

    python3 -m unittest discover -s tests -t .
