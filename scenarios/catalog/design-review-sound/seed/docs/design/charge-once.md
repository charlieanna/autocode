# Charge each billable event exactly once

Status: proposed, revision 3. Author: billing team.

## Problem

`ledger.charge` commits the charge, and `consumer.run_once` then marks the
event processed in a second commit. If the process dies between the two, or
anything after the charge raises (for example the notification call), the event
stays unprocessed and the next `run_once` charges it again. INC-311 and INC-342
were this. `tests/test_consumer.py` reproduces it.

## Goals

1. A billable event is charged **exactly once**, whatever point any consumer
   stops at, including a hard stop of the old version during the deploy.
2. Notifications keep today's delivery: at least once, which the notification
   service turns into at most one delivery per `event_id` (its contract, see
   `events/notify.py`).
3. Every other caller of `ledger.charge` (the admin CLI) behaves as today.
4. No change to any table's columns or data. One additive, non-unique index;
   the previous version ignores it, so rolling back is redeploying the previous
   version.

## Design

The charges and the events are in the same database, so the charge and the
"processed" mark can be one transaction.

1. **Index.** `CREATE INDEX IF NOT EXISTS charges_event_id ON charges (event_id)`
   (in PostgreSQL `CREATE INDEX CONCURRENTLY`, which does not block writes).
   It makes the lookup in step 2 an index lookup, not a scan.
2. **`billing/ledger.py`** gains `record_charge(db, domain, event_id, amount_cents)`,
   which does **not** commit and returns whether it charged:
   - if a charge row with this `event_id` already exists, it inserts nothing
     and returns False;
   - otherwise it inserts the charge and returns True.
   `charge()` becomes `record_charge(...)` followed by `db.commit()`. Manual
   charges have `event_id` NULL and are never looked up, so the admin CLI
   records every manual charge exactly as today.
3. **`events/processor.py`** calls `record_charge` instead of `charge`.
4. **`events/consumer.py`** handles each event as one transaction:
   1. `record_charge` (not committed);
   2. send the notification;
   3. `UPDATE events SET processed_at = ... WHERE id = ? AND processed_at IS NULL`;
   4. if that update changed no row, another consumer already processed the
      event: roll back and return True (the event is handled; the caller moves
      on). Otherwise commit and return True.

   Any exception before the commit rolls back (`db.rollback()` in a
   `try/except` around steps 1-4) and re-raises, exactly as today: the event
   stays unprocessed and is handled again by the next call. `run_once`'s
   contract is otherwise unchanged (False only when nothing is left to do).

## Why this is exactly once

| The consumer stops… | Charge | Event | Next `run_once` |
| --- | --- | --- | --- |
| new version, before the commit (crash, notification error, any exception) | rolled back | still unprocessed | handles it again: one charge; the notification may be sent a second time and the service ignores it by `event_id` |
| new version, after the commit | committed | processed | does not see it again |
| **old version** (during the deploy), between its charge commit and its processed mark | committed by the old code | still unprocessed | the new version finds the charge by `event_id` and does not charge again; it sends the notification (deduplicated) and marks the event |

No state exists in which an event is charged twice or is marked processed
without its charge.

## Questions we expect

- **Two consumers at once?** Deployment runs one replica, as today, but two
  are also safe:
  - *PostgreSQL* (read committed): both may find no charge and both insert;
    the second consumer's guarded `UPDATE` waits for the first transaction's
    row lock, then sees `processed_at` set, changes no row, and rolls back its
    insert.
  - *SQLite*: writers are serialized. The second consumer's insert either waits
    for the first to commit (with a busy timeout) and then its guarded `UPDATE`
    changes no row and it rolls back, or fails with "database is locked", rolls
    back, and on its next call finds the event processed.
  Either way exactly one charge commits.
- **The notification is inside the transaction?** Yes, deliberately: sending
  before the commit means a crash causes a duplicate send (ignored by the
  service), never a lost one. The call times out after 2 seconds, which bounds
  how long the transaction holds its locks. The accepted consequence: while the
  notification service is down, events are retried and neither charged nor
  marked. Charges are delayed, never lost or doubled; today the same outage
  stalls the queue *and* double-charges on every retry.
- **Why not a unique constraint on `charges.event_id`?** The two refunded
  incident duplicates share an `event_id`, so a unique index cannot be built
  without rewriting billing history, which goal 4 rules out. The lookup in
  step 2 plus the guarded update are the guard; a partial unique index after
  archiving the incident rows is a follow-up.

## Rollout and rollback

The old version has no `event_id` check and an unguarded update, so it must
never run **at the same time** as the new one: the two could both charge an
event. The deploy therefore **replaces** the single replica rather than rolling
it: stop the old consumer (a hard stop is fine), wait until its process has
exited, then start the new one. No drain or clean stop is needed, because a
charge the old version committed before being stopped is recognized by
`event_id` (third row of the table above). The service's deploy job is
configured stop-then-start (no surge replica) and refuses to start the new
version while an old consumer process is still alive.

Rolling back is the same replacement in reverse: stop the new consumer, wait
for it to exit, start the previous version. That brings back today's
behaviour, including the double-charge bug; the index stays and is harmless.

## Tests

Extend `tests/test_consumer.py`: a failure after the charge leaves one charge
after the retry; a charge committed by the old code on an unprocessed event is
not repeated; the admin CLI still commits every manual charge. The race test
(a second consumer on the same event commits no second charge) needs two
connections to one database, so it uses a temporary database file rather than
the per-connection `:memory:` database the current tests use, and forces the
interleaving: the second consumer's guarded update runs after the first
commits and must change no row.
