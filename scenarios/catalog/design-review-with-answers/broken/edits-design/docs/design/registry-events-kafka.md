# Moving registry event processing to Kafka

Status: proposed. Author: platform team.

## Goals

- **Throughput.** 5,000 events/s at peak. The single Postgres-table consumer
  manages about 800/s today.
- **Replay.** A consumer can re-read the last 7 days of events after a bug fix.
- **Independent consumers.** Billing, notifications and audit each read the
  same stream at their own pace without slowing each other down.

## Design

**Producer.** The registry gateway publishes one message per registry event to
the topic `registry-events` (12 partitions), keyed by `registry_id` with the
default partitioner (a hash of the key).

**Consumers.** Each service is its own consumer group: `billing`,
`notifications`, `audit`. Delivery is at-least-once: a consumer commits its
offset after processing an event. Billing and notifications already skip a
redelivered event by its `event_id` (`events/billing.py`,
`events/notifications.py`); audit records every delivery, duplicates included.

**Failures.** A consumer retries a failing event in place, with backoff, up to
5 times. After that it copies the event to `registry-events.dlq` and stops
consuming that partition: it never skips past an event. Consumption of the
partition resumes once the event in the DLQ has been dealt with.

**Retention.** 7 days on `registry-events`, 30 days on `registry-events.dlq`.

**Sizing.** Each partition's consumer handles about 1,000 events/s, so 12
partitions give 12,000/s against the 5,000/s peak. Keys spread evenly: there
are about 1,400 registries. The largest, `verisign`, peaks at 450 events/s (9%
of the peak), so the partition that carries it, with its share of the other
registries (about 380/s), stays under 1,000/s. Brokers are the managed 3-node
cluster.

## Migration

Dual-write for two weeks: the gateway writes every event to both the
`registry_events` table and Kafka. Consumers switch from the table to Kafka by
feature flag, one service at a time. After two weeks the table and the old
consumer are removed. To roll back, a service flips its flag back; the
table is reconciled against the topic nightly.

## Non-goals

- Exactly-once delivery.
- Changing the event schema.
