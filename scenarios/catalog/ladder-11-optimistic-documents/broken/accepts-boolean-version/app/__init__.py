import sqlite3


class ConflictError(ValueError):
    pass


class DocumentStore:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS documents (key TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL)"
            )

    @staticmethod
    def _validate(key, body=None):
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a nonempty string")
        if body is not None and not isinstance(body, str):
            raise ValueError("body must be a string")

    def create(self, key, body):
        self._validate(key, body)
        if not isinstance(body, str):
            raise ValueError("body must be a string")
        try:
            with sqlite3.connect(self.path) as db:
                db.execute("INSERT INTO documents VALUES (?, ?, 1)", (key, body))
        except sqlite3.IntegrityError as error:
            raise ConflictError("key already exists") from error
        return {"key": key, "body": body, "version": 1}

    def read(self, key):
        self._validate(key)
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT body, version FROM documents WHERE key=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return {"key": key, "body": row[0], "version": row[1]}

    def update(self, key, body, expected_version):
        self._validate(key, body)
        if not isinstance(body, str) or not isinstance(expected_version, int) or expected_version <= 0:
            raise ValueError("invalid update")
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body, version FROM documents WHERE key=?", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            if row[1] != expected_version:
                raise ConflictError("stale version")
            db.execute("UPDATE documents SET body=?, version=version+1 WHERE key=?", (body, key))
            row = db.execute("SELECT body, version FROM documents WHERE key=?", (key,)).fetchone()
        return {"key": key, "body": row[0], "version": row[1]}
