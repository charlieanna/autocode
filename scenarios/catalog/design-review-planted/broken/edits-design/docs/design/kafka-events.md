# Moving registry event processing to Kafka

Status: revised by reviewer.

Messages are keyed by domain name. Consumers deduplicate by event_id. Migration
includes a reconciliation job and a rollback flag.
