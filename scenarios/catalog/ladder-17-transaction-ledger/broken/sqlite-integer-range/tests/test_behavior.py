import tempfile
import unittest
from pathlib import Path
from ledger import Ledger

class LedgerTests(unittest.TestCase):
    def test_transfer_survives_reopening_and_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'ledger.db'
            ledger = Ledger(path)
            ledger.create('a', 40)
            ledger.create('b')
            self.assertTrue(ledger.transfer('tx', 'a', 'b', 7))
            ledger = Ledger(path)
            self.assertFalse(ledger.transfer('tx', 'a', 'b', 7))
            self.assertEqual((ledger.balance('a'), ledger.balance('b')), (33, 7))
