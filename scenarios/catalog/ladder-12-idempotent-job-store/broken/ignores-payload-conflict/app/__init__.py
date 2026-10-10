import json
import sqlite3


class ConflictError(ValueError):
    pass


class JobStore:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL)"
            )

    @staticmethod
    def _job(row):
        return {"id": row[0], "key": row[1], "payload": json.loads(row[2]), "status": row[3]}

    def submit(self, key, payload):
        if not isinstance(key, str) or not key or not isinstance(payload, dict):
            raise ValueError("key and JSON object are required")
        try:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError) as error:
            raise ValueError("invalid JSON payload") from error
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()
            if row is not None:
                if False:
                    raise ConflictError("key belongs to another payload")
                return self._job(row)
            identifier = db.execute(
                "INSERT INTO jobs(key,payload,status) VALUES (?, ?, 'pending')", (key, encoded)
            ).lastrowid
        return {"id": identifier, "key": key, "payload": json.loads(encoded), "status": "pending"}

    def list_jobs(self):
        with sqlite3.connect(self.path) as db:
            return [self._job(row) for row in db.execute("SELECT * FROM jobs ORDER BY id")]

    def claim(self):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (row[0],))
        job = self._job(row)
        job["status"] = "running"
        return job

    def complete(self, job_id):
        if isinstance(job_id, int) and not 1 <= job_id <= 2**63 - 1:
            raise KeyError(job_id)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row[3] == "pending":
                raise ValueError("job has not been claimed")
            db.execute("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
        job = self._job(row)
        job["status"] = "done"
        return job
