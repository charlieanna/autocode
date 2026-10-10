import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import migrate


class MigrationTests(unittest.TestCase):
    def test_version_and_rows_survive_upgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "people.db"
            with sqlite3.connect(path) as db:
                db.executescript(
                    "CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT); PRAGMA user_version=1;"
                )
                db.execute("INSERT INTO people VALUES (9, 'Ada', 'ada@example.org')")
            self.assertEqual(migrate(path), 2)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT * FROM people").fetchall(), [(9, "Ada", "ada@example.org")])
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
