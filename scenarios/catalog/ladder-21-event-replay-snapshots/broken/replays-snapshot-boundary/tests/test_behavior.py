import tempfile
import unittest
from pathlib import Path
from journal import Journal

class JournalTests(unittest.TestCase):
    def test_replay_and_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'events.db'; journal=Journal(path)
            self.assertTrue(journal.append('e1','visits',4))
            self.assertFalse(journal.append('e1','visits',4))
            journal.append('e2','visits',-1)
            self.assertEqual(Journal(path).state(),{'visits':3})
