"""Daily delivery report."""
from .clock import store_date


def delivered_on(orders, day):
    """Ids of the orders delivered on the given store calendar day."""
    return sorted(order.id for order in orders if order.delivered_at is not None
                  and store_date(order.delivered_at) == day)
