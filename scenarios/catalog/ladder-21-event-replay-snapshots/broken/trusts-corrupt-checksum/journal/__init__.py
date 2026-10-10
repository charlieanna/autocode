import hashlib
import json
import os
import sqlite3

# Store caller integers as hexadecimal text; do arithmetic with Python ints.
import tempfile
from contextlib import contextmanager
from pathlib import Path


def _decimal(value):
    # Nine-digit chunks avoid Python's process-global decimal conversion limit.
    sign = "-" if value < 0 else ""
    value = abs(value)
    chunks = []
    while value:
        value, remainder = divmod(value, 10**9)
        chunks.append(remainder)
    if not chunks:
        return "0"
    return sign + str(chunks[-1]) + "".join(f"{part:09d}" for part in reversed(chunks[:-1]))


def _integer(text):
    sign = -1 if text.startswith("-") else 1
    digits = text.lstrip("-")
    value = 0
    for offset in range(0, len(digits), 9):
        chunk = digits[offset : offset + 9]
        value = value * 10 ** len(chunk) + int(chunk)
    return sign * value


def _canonical(value):
    if type(value) is int:
        return _decimal(value)
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key) + ":" + _canonical(item) for key, item in sorted(value.items())) + "}"
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _checksum(sequence, totals):
    return hashlib.sha256(_canonical({"sequence": sequence, "totals": totals}).encode()).hexdigest()


class Journal:
    def __init__(self, path):
        self.path = str(path)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,key TEXT,delta TEXT)"
            )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def append(self, event_id, key, delta):
        if type(delta) is not int:
            raise ValueError("invalid delta")
        with self._db() as db:
            prior = db.execute("SELECT key,delta FROM events WHERE id=?", (event_id,)).fetchone()
            if prior:
                if (prior[0], int(prior[1], 16)) != (key, delta):
                    raise ValueError("conflicting replay")
                return False
            db.execute("INSERT INTO events(id,key,delta) VALUES (?,?,?)", (event_id, key, hex(delta)))
            return True

    def _project(self, snapshot_path=None):
        with self._db() as db:
            last = db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
            start = 0
            totals = {}
            if snapshot_path is not None:
                try:
                    snap = json.loads(Path(snapshot_path).read_text(), parse_int=_integer)
                    sequence = snap["sequence"]
                    values = snap["totals"]
                    valid = (
                        type(sequence) is int
                        and 0 <= sequence <= last
                        and isinstance(values, dict)
                        and all(isinstance(k, str) and type(v) is int for k, v in values.items())
                    )
                    if valid:
                        start = sequence
                        totals = dict(values)
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            for _seq, key, delta in db.execute("SELECT seq,key,delta FROM events WHERE seq>? ORDER BY seq", (start,)):
                totals[key] = totals.get(key, 0) + int(delta, 16)
            return last, totals

    def state(self, snapshot_path=None):
        return self._project(snapshot_path)[1]

    def snapshot(self, path):
        sequence, totals = self._project()
        target = Path(path)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".snapshot-")
        try:
            with os.fdopen(fd, "w") as file:
                file.write(
                    _canonical({"sequence": sequence, "totals": totals, "checksum": _checksum(sequence, totals)})
                )
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
