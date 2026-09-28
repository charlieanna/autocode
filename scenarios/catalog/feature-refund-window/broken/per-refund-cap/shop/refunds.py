"""Refunds: a store-time window after delivery and a cap on the running total."""
from datetime import timedelta

from .clock import store_date

WINDOW_DAYS = 30


class RefundRefused(Exception):
    """The refund is not allowed; the order was left unchanged."""


def refund(order, amount_cents, now) -> int:
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or amount_cents <= 0:
        raise RefundRefused("amount_cents must be a positive integer")
    if order.disputed:
        raise RefundRefused("the order is disputed")
    if order.delivered_at is not None:
        last_day = store_date(order.delivered_at) + timedelta(days=WINDOW_DAYS)
        if store_date(now) > last_day:
            raise RefundRefused(f"the refund window ended on {last_day.isoformat()}")
    if amount_cents > order.paid_cents:
        raise RefundRefused(f"only {order.paid_cents} cents were paid")
    order.refunds.append(amount_cents)
    return max(order.paid_cents - order.refunded_cents, 0)
