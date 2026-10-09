"""The single consumer: handle the oldest unprocessed event, then mark it processed."""
from . import processor

NEXT = "SELECT id, event_id, domain, kind FROM events WHERE processed_at IS NULL ORDER BY id LIMIT 1"


def run_once(db, notifier):
    """Handle one event. Returns False when there is nothing to do. An exception leaves
    the event unprocessed, so the next call handles it again (at-least-once)."""
    row = db.execute(NEXT).fetchone()
    if row is None:
        return False
    event = {"id": row[0], "event_id": row[1], "domain": row[2], "kind": row[3]}
    processor.process(db, event, notifier)
    db.execute("UPDATE events SET processed_at = datetime('now') WHERE id = ?", (event["id"],))
    db.commit()
    return True
