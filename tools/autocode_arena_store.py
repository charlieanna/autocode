"""Evaluator-owned Arena catalog and append-only SQLite attempt ledger."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path

try:
    from . import autocode_failure_attribution as attribution
except ImportError:
    import autocode_failure_attribution as attribution


class ArenaError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


def read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ArenaError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ArenaError(f"invalid JSON number: {value}")

    value = json.loads(path.read_text(), object_pairs_hook=unique, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ArenaError(f"{path}: expected an object")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(encode(value) + "\n")
    temp.replace(path)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.catalog = self.root / "catalog.json"
        self.database = self.root / "results.sqlite3"

    def exists(self) -> bool:
        """Whether ``initialize`` has created this store."""
        return self.catalog.exists() and self.database.exists()

    @contextlib.contextmanager
    def _transaction(self):
        """One connection per operation: its transaction commits, or rolls back on an error, and the
        connection is always closed. sqlite3's own context manager commits but never closes (#952)."""
        with contextlib.closing(sqlite3.connect(self.database)) as db, db:
            yield db

    def initialize(self):
        if self.catalog.exists() or self.database.exists():
            raise ArenaError("Arena already exists; refusing to overwrite")
        write_json(self.catalog, {"schema_version": 1, "cases": []})
        with self._transaction() as db:
            db.execute("CREATE TABLE attempts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def cases(self) -> list[dict]:
        data = read_json(self.catalog)
        if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
            raise ArenaError("unsupported catalog")
        return data["cases"]

    def case(self, ident: str) -> dict:
        matches = [c for c in self.cases() if c["id"] == ident]
        if len(matches) != 1:
            raise ArenaError(f"expected exactly one case named {ident}")
        case = matches[0]
        content = {k: v for k, v in case.items() if k != "sha256"}
        if digest(encode(content).encode()) != case["sha256"]:
            raise ArenaError("case definition changed")
        for stem in ("issue", "oracle"):
            path = Path(case[stem + "_path"])
            if digest(path.read_bytes()) != case[stem + "_sha256"]:
                raise ArenaError(f"{stem} changed since ingestion")
        return case

    def add(self, case: dict):
        cases = self.cases()
        if any(
            c["id"] == case["id"]
            or (c["repository"], c["base_commit"], c["issue_sha256"])
            == (case["repository"], case["base_commit"], case["issue_sha256"])
            for c in cases
        ):
            raise ArenaError("duplicate case id or workload")
        case["sha256"] = digest(encode(case).encode())
        write_json(self.catalog, {"schema_version": 1, "cases": [*cases, case]})

    def insert(self, row: dict):
        with self._transaction() as db:
            db.execute("INSERT INTO attempts VALUES (?, ?)", (row["id"], encode(row)))

    def finish(self, row: dict):
        with self._transaction() as db:
            existing = db.execute("SELECT payload FROM attempts WHERE id=?", (row["id"],)).fetchone()
            if not existing or json.loads(existing[0])["verdict"] != "RUNNING":
                raise ArenaError("only a running attempt can be finalized")
            db.execute("UPDATE attempts SET payload=? WHERE id=?", (encode(row), row["id"]))

    def annotate(self, ident: str, document: dict, *, reviewer=None):
        """Append an explicit investigation; completed attempt bytes never change."""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", ident) is None:
            raise ArenaError("attempt id must be a simple filename")
        with self._transaction() as db:
            saved = db.execute("SELECT payload FROM attempts WHERE id=?", (ident,)).fetchone()
            if not saved or json.loads(saved[0])["verdict"] == "RUNNING":
                raise ArenaError("only a finished attempt can be annotated")
            value = attribution.validate(document, self.root / "attempts" / ident, reviewer=reviewer)
            record = {
                "id": uuid.uuid4().hex,
                "attempt": ident,
                "attempt_sha256": digest(saved[0].encode()),
                "failure_attribution": value,
            }
            # A lazy additive migration keeps older catalogs readable and their attempts intact.
            db.execute("CREATE TABLE IF NOT EXISTS attributions (id TEXT PRIMARY KEY, attempt TEXT, payload TEXT)")
            db.execute("INSERT INTO attributions VALUES (?, ?, ?)", (record["id"], ident, encode(record)))
        return record

    def rows(self, cohort: str | None = None) -> list[dict]:
        with self._transaction() as db:
            saved = list(db.execute("SELECT id, payload FROM attempts ORDER BY rowid"))
            annotations = {}
            if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='attributions'").fetchone():
                annotations = {
                    ident: payload
                    for ident, payload in db.execute("SELECT attempt, payload FROM attributions ORDER BY rowid")
                }
        rows = []
        for ident, payload in saved:
            row = json.loads(payload)
            # Producer reports, including older rows, cannot confer diagnosis authority.
            row["failure_attribution"] = attribution.unknown()
            if ident in annotations:
                try:
                    record = json.loads(annotations[ident])
                    if record["attempt"] != ident or record["attempt_sha256"] != digest(payload.encode()):
                        raise ArenaError("attribution no longer belongs to this attempt")
                    value = record["failure_attribution"]
                    row["failure_attribution"] = attribution.validate(
                        value, self.root / "attempts" / ident, reviewer=value.get("confirmed_by")
                    )
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                    row["attribution_error"] = str(error)
            rows.append(row)
        return [r for r in rows if cohort is None or r["cohort"] == cohort]
