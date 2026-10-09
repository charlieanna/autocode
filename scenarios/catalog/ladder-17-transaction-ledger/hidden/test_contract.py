import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ledger import Ledger


class LedgerContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'db'; self.ledger=Ledger(self.path)
        self.ledger.create('a',100); self.ledger.create('b')

    def together(self, fn):
        barrier=threading.Barrier(8)
        def invoke(i):
            item=Ledger(self.path)
            barrier.wait(timeout=5)
            return fn(item,i)
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(invoke,range(8)))

    def test_concurrent_overdraft_and_conservation(self):
        def transfer(item,i):
            try: return item.transfer(str(i),'a','b',30)
            except ValueError: return False
        self.assertEqual(sum(self.together(transfer)),3)
        self.assertEqual((self.ledger.balance('a'),self.ledger.balance('b')),(10,90))

    def test_concurrent_duplicate_commits_once(self):
        self.assertEqual(sum(self.together(lambda item,i:item.transfer('same','a','b',11))),1)
        self.assertEqual((self.ledger.balance('a'),self.ledger.balance('b')),(89,11))

    def test_failed_key_reusable_and_conflicting_replay_rejected(self):
        with self.assertRaises(ValueError): self.ledger.transfer('retry','b','a',2)
        self.ledger.transfer('fund','a','b',5)
        self.assertTrue(self.ledger.transfer('retry','b','a',2))
        with self.assertRaises(ValueError): self.ledger.transfer('retry','b','a',1)
        self.assertEqual((self.ledger.balance('a'),self.ledger.balance('b')),(97,3))

    def test_invalid_requests_leave_balances(self):
        for amount in (0,-1,True,1.5):
            with self.assertRaises(ValueError): self.ledger.transfer('x','a','b',amount)
        with self.assertRaises(KeyError): self.ledger.transfer('x','a','missing',3)
        with self.assertRaises(ValueError): self.ledger.transfer('x','a','a',3)
        with self.assertRaises(ValueError): self.ledger.create('a',5)
        with self.assertRaises(ValueError): self.ledger.create('bad',-1)
        self.assertEqual(self.ledger.balance('a'),100)

    def test_transfer_across_sqlite_integer_boundary_keeps_exact_funds(self):
        maximum=2**63-1
        self.ledger.create('edge',maximum)
        self.assertTrue(self.ledger.transfer('cross','a','edge',1))
        reopened=Ledger(self.path)
        self.assertIs(type(reopened.balance('edge')),int)
        self.assertEqual(reopened.balance('edge'),maximum+1)
        self.assertTrue(reopened.transfer('back','edge','a',1))
        self.assertEqual((reopened.balance('edge'),reopened.balance('a')),(maximum,100))

    def test_large_balances_amounts_and_concurrent_replay_survive_restart(self):
        large=2**120+37
        self.ledger.create('large',large+9)
        self.ledger.create('recipient',large)
        commits=self.together(lambda item,i:item.transfer('large-key','large','recipient',large))
        self.assertEqual(sum(commits),1)
        reopened=Ledger(self.path)
        self.assertEqual((reopened.balance('large'),reopened.balance('recipient')),(9,2*large))
        self.assertIs(type(reopened.balance('recipient')),int)
        self.assertFalse(reopened.transfer('large-key','large','recipient',large))
        with self.assertRaises(ValueError): reopened.transfer('large-key','large','recipient',large+1)
