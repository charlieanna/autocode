"""A durable local order store with a transactional SQLite outbox."""

import sqlite3
import uuid


class Store:
    """Store orders and unpublished events in a SQLite database at *path*."""

    def __init__(self, path):
        self.path = path
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    amount BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    amount BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    order_id TEXT NOT NULL,
                    amount BLOB NOT NULL,
                    published INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _validate_amount(amount):
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValueError("amount must be a positive integer")

    @staticmethod
    def _encode_amount(amount):
        return amount.to_bytes((amount.bit_length() + 7) // 8, "big")

    @staticmethod
    def _decode_amount(amount):
        return int.from_bytes(amount, "big")

    @staticmethod
    def _validate_limit(limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")

    def create_order(self, order_id, amount, key):
        """Atomically create an order, key association, and unpublished event."""
        self._validate_amount(amount)
        encoded_amount = self._encode_amount(amount)

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing_key = connection.execute(
                    "SELECT order_id, amount FROM idempotency_keys WHERE key = ?", (key,)
                ).fetchone()
                if existing_key is not None:
                    if existing_key[0] == order_id and existing_key[1] == encoded_amount:
                        connection.commit()
                        return False
                    raise ValueError("idempotency key conflicts with an existing order")

                if connection.execute("SELECT 1 FROM orders WHERE order_id = ?", (order_id,)).fetchone() is not None:
                    raise ValueError("order id already exists")

                connection.execute(
                    "INSERT INTO orders (order_id, amount) VALUES (?, ?)",
                    (order_id, encoded_amount),
                )
                connection.execute(
                    "INSERT INTO idempotency_keys (key, order_id, amount) VALUES (?, ?, ?)",
                    (key, order_id, encoded_amount),
                )
                connection.execute(
                    "INSERT INTO outbox_events (event_id, order_id, amount) VALUES (?, ?, ?)",
                    (uuid.uuid4().hex, order_id, encoded_amount),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def orders(self):
        with self._connect() as connection:
            rows = connection.execute("SELECT order_id, amount FROM orders ORDER BY rowid").fetchall()
        return {order_id: self._decode_amount(amount) for order_id, amount in rows}

    def pending(self, limit=100):
        self._validate_limit(limit)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, order_id, amount
                FROM outbox_events
                WHERE published = 0
                ORDER BY sequence
                LIMIT ?
                """,
                (min(limit, 2**63 - 1),),
            ).fetchall()
        return [
            {"event_id": event_id, "order_id": order_id, "amount": self._decode_amount(amount)}
            for event_id, order_id, amount in rows
        ]

    def publish(self, sink, limit=100):
        """Deliver up to *limit* events, acknowledging each only after success."""
        events = self.pending(limit)
        for event in events:
            sink(event)
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE outbox_events SET published = 1 WHERE event_id = ? AND published = 0",
                    (event["event_id"],),
                )
                connection.commit()
        return len(events)
