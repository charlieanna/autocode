"""Evaluator-owned Arena catalog and append-only SQLite attempt ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


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

    def initialize(self):
        if self.catalog.exists() or self.database.exists():
            raise ArenaError("Arena already exists; refusing to overwrite")
        write_json(self.catalog, {"schema_version": 1, "cases": []})
        with sqlite3.connect(self.database) as db:
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
        if any(c["id"] == case["id"] or
               (c["repository"], c["base_commit"], c["issue_sha256"]) ==
               (case["repository"], case["base_commit"], case["issue_sha256"]) for c in cases):
            raise ArenaError("duplicate case id or workload")
        case["sha256"] = digest(encode(case).encode())
        write_json(self.catalog, {"schema_version": 1, "cases": [*cases, case]})

    def insert(self, row: dict):
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO attempts VALUES (?, ?)", (row["id"], encode(row)))

    def finish(self, row: dict):
        with sqlite3.connect(self.database) as db:
            existing = db.execute("SELECT payload FROM attempts WHERE id=?", (row["id"],)).fetchone()
            if not existing or json.loads(existing[0])["verdict"] != "RUNNING":
                raise ArenaError("only a running attempt can be finalized")
            db.execute("UPDATE attempts SET payload=? WHERE id=?", (encode(row), row["id"]))

    def rows(self, cohort: str | None = None) -> list[dict]:
        with sqlite3.connect(self.database) as db:
            rows = [json.loads(r[0]) for r in db.execute("SELECT payload FROM attempts ORDER BY rowid")]
        return [r for r in rows if cohort is None or r["cohort"] == cohort]
