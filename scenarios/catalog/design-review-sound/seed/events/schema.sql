CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    kind TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS charges (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    event_id TEXT,
    amount_cents INTEGER NOT NULL
);
