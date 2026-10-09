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

    def test_snapshot_tail_and_corruption_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); journal=Journal(root/'events'); snapshot=root/'snapshot'
            journal.append('one','score',5); journal.snapshot(snapshot)
            journal.append('two','score',-2)
            self.assertEqual(journal.state(snapshot),{'score':3})
            snapshot.write_text('{corrupt')
            self.assertEqual(journal.state(snapshot),{'score':3})
            self.assertEqual(snapshot.read_text(),'{corrupt')
