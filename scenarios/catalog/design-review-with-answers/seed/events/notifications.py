"""Sends one notification per event.

The event_ids already notified are kept in the `notifications_sent` table, so a redelivered event
is skipped, even after a restart. A crash between sending and recording can send one notification
twice: notifications are informational, and a rare duplicate is accepted.
"""

SCHEMA = "CREATE TABLE IF NOT EXISTS notifications_sent (event_id TEXT PRIMARY KEY)"


class Notifier:
    def __init__(self, db, send):
        self.db = db
        self.send = send
        self.db.execute(SCHEMA)

    def notify(self, event) -> bool:
        if self.db.execute("SELECT 1 FROM notifications_sent WHERE event_id = ?", (event.event_id,)).fetchone():
            return False
        self.send(event)
        with self.db:
            self.db.execute("INSERT INTO notifications_sent (event_id) VALUES (?) ON CONFLICT (event_id) DO NOTHING",
                            (event.event_id,))
        return True
