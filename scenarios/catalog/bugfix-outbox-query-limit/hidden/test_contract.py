import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from outbox import Store

class OutboxContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'db'; self.store=Store(self.path)
    def test_partial_delivery_preserves_failed_event_identity(self):
        for i in range(3): self.store.create_order(str(i),i+1,str(i))
        original=self.store.pending(); delivered=[]
        def sink(event):
            self.assertEqual(len(self.store.orders()),3)
            delivered.append(event)
            if event['order_id']=='1': raise RuntimeError('after external acceptance')
        with self.assertRaises(RuntimeError): self.store.publish(sink)
        store=Store(self.path)
        self.assertEqual(store.pending(),original[1:])
        unique={event['event_id']:event for event in delivered}
        self.assertEqual(store.publish(lambda event:unique.setdefault(event['event_id'],event)),2)
        self.assertEqual(len(unique),3); self.assertEqual(store.pending(),[])
    def test_rejected_order_does_not_reserve_key_or_event(self):
        self.store.create_order('a',5,'k1')
        with self.assertRaises(ValueError): self.store.create_order('a',5,'k2')
        with self.assertRaises(ValueError): self.store.create_order('b',5,'k1')
        self.assertTrue(self.store.create_order('b',7,'k2'))
        self.assertEqual(self.store.orders(),{'a':5,'b':7})
        self.assertEqual([e['order_id'] for e in self.store.pending()],['a','b'])
        self.assertEqual(len(self.store.pending(1)),1)
        for value in (0,-1,True):
            with self.assertRaises(ValueError): self.store.pending(value)
    def test_concurrent_creation_once(self):
        barrier=threading.Barrier(6)
        def create(i):
            store=Store(self.path); barrier.wait(timeout=5)
            return store.create_order('one',13,'same')
        with ThreadPoolExecutor(max_workers=6) as pool: results=list(pool.map(create,range(6)))
        self.assertEqual(sum(results),1); self.assertEqual(len(self.store.pending()),1)

    def test_large_amount_is_lossless_in_order_event_and_replay(self):
        large=2**120+31
        self.assertTrue(self.store.create_order('large',large,'key'))
        reopened=Store(self.path)
        self.assertEqual(reopened.orders(),{'large':large})
        self.assertIs(type(reopened.orders()['large']),int)
        self.assertFalse(reopened.create_order('large',large,'key'))
        with self.assertRaises(ValueError): reopened.create_order('large',large+1,'key')
        event=reopened.pending()[0]
        self.assertEqual(event['amount'],large); self.assertIs(type(event['amount']),int)
        delivered=[]
        self.assertEqual(reopened.publish(delivered.append),1)
        self.assertEqual(delivered,[event]); self.assertEqual(Store(self.path).pending(),[])

    def test_arbitrarily_large_positive_limit_preserves_order_and_publishing(self):
        for i in range(3): self.store.create_order(str(i),i+1,str(i))
        expected=self.store.pending()
        self.assertEqual(self.store.pending(2**120),expected)
        self.assertEqual(self.store.pending(1),expected[:1])
        delivered=[]
        self.assertEqual(self.store.publish(delivered.append,limit=2**120),3)
        self.assertEqual(delivered,expected)
        self.assertEqual(Store(self.path).pending(2**120),[])
