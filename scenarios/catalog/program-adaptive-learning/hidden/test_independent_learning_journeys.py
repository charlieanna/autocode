"""Independent scoring, sticky-hint and cross-process durability checks, not Builder input."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from learning.engine import evaluate
from learning.flow import Session


class HiddenJourneys(unittest.TestCase):
    def test_non_catalog_problem_also_distinguishes_hint_use(self):
        expected = [{"outcome": "hint-assisted", "points": 1, "mastered": False},
                    {"outcome": "independent", "points": 2, "mastered": True},
                    {"outcome": "wrong", "points": 0, "mastered": False}]
        self.assertEqual(expected, [evaluate({"answer": "17"}, "17", True),
                                    evaluate({"answer": "17"}, "17", False), evaluate({"answer": "17"}, "16", True)])

    def test_hint_is_sticky_until_answer_and_wrong_answers_never_grant_mastery(self):
        learner = Session("Ada")
        learner.read()
        learner.hint()
        learner.hint()
        learner.read("addition")
        result = learner.answer("5")
        self.assertEqual((True, "hint-assisted", 1, False),
                         (result["hinted"], result["outcome"], result["points"], result["mastered"]))
        learner.read("subtraction")
        self.assertEqual("independent", learner.answer("3")["outcome"])
        learner.hint()
        learner.read("subtraction")
        wrong = learner.answer("99")
        self.assertEqual(("wrong", 0, False), (wrong["outcome"], wrong["points"], wrong["mastered"]))

    def test_sqlite_survives_separate_writer_and_reader_processes(self):
        writer = """import sys
from learning.flow import Session
for student, hinted in ((sys.argv[2], True), (sys.argv[3], False)):
    learner = Session(student, sys.argv[1])
    learner.read()
    if hinted:
        learner.hint()
    learner.answer('5')
"""
        reader = """import json, sys
from learning.flow import Session
print(json.dumps([{'progress': Session(student, sys.argv[1]).progress(),
                  'next': Session(student, sys.argv[1]).next()} for student in sys.argv[2:]]))
"""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "durable.sqlite"
            students = [uuid.uuid4().hex, uuid.uuid4().hex]
            for script in (writer, reader):
                result = subprocess.run([sys.executable, "-c", script, str(database), *students], cwd=Path.cwd(),
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual([
                {"progress": [{"lesson": "addition", "hinted": True, "outcome": "hint-assisted", "points": 1,
                               "mastered": False}], "next": "addition"},
                {"progress": [{"lesson": "addition", "hinted": False, "outcome": "independent", "points": 2,
                               "mastered": True}], "next": "subtraction"}], json.loads(result.stdout))
            self.assertEqual(b"SQLite format 3\0", database.read_bytes()[:16])
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                self.assertEqual(("ok",), connection.execute("PRAGMA integrity_check").fetchone())
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                self.assertGreater(sum(connection.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                                       for (name,) in tables), 0)
