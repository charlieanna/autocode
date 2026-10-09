# Moving registry event processing to Kafka

Status: proposed. Author: platform team.

## Goals

- **Throughput.** 5,000 events/s at peak (the single Postgres-table consumer
  manages about 800/s today).
- **Replay.** A consumer can re-read the last 7 days of events after a bug fix.
- **Independent consumers.** Billing, notifications and audit each read the
  same stream at their own pace without slowing each other down.

## Design

**Producer.** The registry gateway publishes one message per registry event to
the topic `registry-events` (24 partitions). Messages are keyed by **event
kind** (`create`, `renew`, `transfer`, `delete`). A custom partitioner gives
each kind its own six partitions (create 0-5, renew 6-11, transfer 12-17,
delete 18-23) and spreads that kind's messages round-robin across them, so all
24 partitions carry traffic. A consumer that only cares about one kind (billing
only needs renews) assigns itself that kind's six partitions.

**Consumers.** Each service is its own consumer group: `billing`,
`notifications`, `audit`. Delivery is at-least-once: a consumer commits offsets
after processing a batch; on failure it retries the message up to 5 times and
then moves it to `registry-events.dlq`.

**Retention.** 7 days on `registry-events`, 30 days on the DLQ.

**Sizing.** 24 partitions × ~400 events/s per partition consumer gives
headroom above the 5,000/s target. At peak no kind exceeds 1,500 events/s, and
each kind's six partitions handle 2,400/s. Brokers are the managed 3-node
cluster.

## Migration

Dual-write for two weeks: the gateway writes every event to both the existing
Postgres queue table and Kafka. Consumers switch from the table to Kafka by
feature flag, one service at a time. After two weeks the table and the old
consumer are removed.

## Non-goals

- Exactly-once delivery.
- Changing the event schema.
