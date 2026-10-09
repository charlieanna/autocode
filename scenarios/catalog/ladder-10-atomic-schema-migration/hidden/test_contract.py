import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import migrate


class MigrationContract(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "people.db"

    def seed(self, rows, version=1):
        with sqlite3.connect(self.path) as db:
            db.executescript("CREATE TABLE people(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT); CREATE TABLE notes(body TEXT); INSERT INTO notes VALUES ('keep me');")
            db.execute(f"PRAGMA user_version={version}")
            db.executemany("INSERT INTO people VALUES (?, ?, ?)", rows)

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            return (db.execute("PRAGMA user_version").fetchone()[0], db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall(), db.execute("SELECT * FROM people ORDER BY id").fetchall(), db.execute("SELECT * FROM notes").fetchall())

    def test_normalization_constraints_and_rerun(self):
        self.seed([(8, "Åda", "  ADA@EXAMPLE.ORG "), (2, "Ben", "Ben@example.org")])
        self.assertEqual(migrate(self.path), 2)
        expected = [(2, "Ben", "ben@example.org"), (8, "Åda", "ada@example.org")]
        self.assertEqual(self.snapshot()[2], expected)
        before = self.snapshot()
        self.assertEqual(migrate(self.path), 2)
        self.assertEqual(self.snapshot(), before)
        with sqlite3.connect(self.path) as db:
            for email in (None, "ada@example.org"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO people VALUES (100, 'Other', ?)", (email,))
        self.assertEqual(self.snapshot()[3], [("keep me",)])

    def test_colliding_emails_leave_no_partial_schema_or_rows(self):
        self.seed([(1, "First", "one@example.org"), (2, "Second", " ONE@example.org ")])
        before = self.snapshot()
        with self.assertRaises(ValueError):
            migrate(self.path)
        self.assertEqual(self.snapshot(), before)

    def test_missing_email_rolls_back_after_valid_row(self):
        self.seed([(1, "First", "one@example.org"), (4, "Other", None)])
        before = self.snapshot()
        with self.assertRaises(ValueError):
            migrate(self.path)
        self.assertEqual(self.snapshot(), before)

    def test_unsupported_version_is_untouched(self):
        self.seed([(3, "Future", "future@example.org")], version=7)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            migrate(self.path)
        self.assertEqual(self.snapshot(), before)

    def test_unrelated_table_named_people_v2_is_preserved(self):
        self.seed([(1, "Ada", " ADA@EXAMPLE.ORG ")])
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE people_v2(note TEXT)")
            db.execute("INSERT INTO people_v2 VALUES ('unrelated data')")
        self.assertEqual(migrate(self.path), 2)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT * FROM people").fetchall(), [(1, "Ada", "ada@example.org")])
            self.assertEqual(db.execute("SELECT * FROM people_v2").fetchall(), [("unrelated data",)])
            self.assertEqual(db.execute("SELECT * FROM notes").fetchall(), [("keep me",)])
            self.assertEqual({row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}, {"people", "people_v2", "notes"})
