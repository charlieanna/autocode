"""What handling one event means: charge it if it is billable, then tell the customer."""
from billing import ledger

PRICES_CENTS = {"renew": 1200, "create": 1200}


def process(db, event, notifier):
    price = PRICES_CENTS.get(event["kind"])
    if price is not None:
        ledger.charge(db, event["domain"], event["event_id"], price)
    notifier.send(event["event_id"], event)
