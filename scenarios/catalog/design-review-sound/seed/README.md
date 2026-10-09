# events

Registry events are rows of the `events` table in `var/registry.db` (SQLite here;
PostgreSQL in production, with the same SQL). **One consumer process**,
`events/consumer.py`, handles them in insertion order: for each unprocessed row
it runs `events/processor.py`, then marks the row processed. Deployment runs
exactly one consumer replica.

**Billing** (`billing/ledger.py`) writes charges to the `charges` table of the
**same database**. The admin CLI (`billing/admin.py`) also records manual
charges through `billing.ledger.charge`.

**Notifications** go to the external notification service through
`events/notify.py`. The service's contract: a notification is delivered at most
once per `event_id`; repeated sends with the same `event_id` are accepted and
ignored. Calls time out after 2 seconds.

## Known problem

`ledger.charge` commits, and the consumer marks the event processed in a second
commit. A crash between the two (a deploy restart, an out-of-memory kill) leaves
the event unprocessed, so it is handled again and **charged twice**. This
happened twice last quarter (INC-311, INC-342); both were refunded by hand.
See `docs/design/charge-once.md`.

## Tests

    python3 -m unittest discover -s tests -t .
