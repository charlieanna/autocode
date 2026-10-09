import tempfile
import unittest
from pathlib import Path
from learning.flow import Session


class BackendTests(unittest.TestCase):
    def test_outcomes_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "progress.sqlite"
            for student, hinted in (("assisted", True), ("independent", False)):
                learner = Session(student, database)
                learner.read()
                if hinted:
                    learner.hint()
                learner.answer("5")
            assisted, independent = Session("assisted", database), Session("independent", database)
            self.assertEqual("hint-assisted", assisted.progress()[0]["outcome"])
            self.assertEqual("independent", independent.progress()[0]["outcome"])
            self.assertEqual(("addition", "subtraction"), (assisted.next(), independent.next()))
