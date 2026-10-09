import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from journal import Journal


class ReplayContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'db'; self.snapshot=Path(self.tmp.name)/'snapshot.json'; self.j=Journal(self.path)
    def test_snapshot_boundary_tail_and_zero_totals(self):
        self.j.append('1','a',7); self.j.append('2','b',2); self.j.snapshot(self.snapshot)
        snap=json.loads(self.snapshot.read_text())
        self.assertEqual(snap['sequence'],2); self.assertEqual(snap['totals'],{'a':7,'b':2})
        encoded=json.dumps({'sequence':2,'totals':{'a':7,'b':2}},sort_keys=True,separators=(',',':')).encode()
        self.assertEqual(snap['checksum'],hashlib.sha256(encoded).hexdigest())
        self.assertEqual(self.j.state(self.snapshot),{'a':7,'b':2})
        self.j.append('3','a',-7); self.j.append('4','b',3)
        self.assertEqual(Journal(self.path).state(self.snapshot),{'a':0,'b':5})
        self.assertEqual(self.j.state(),self.j.state(self.snapshot))
    def test_invalid_snapshots_recover_without_writing(self):
        self.j.append('1','a',4); self.j.snapshot(self.snapshot)
        valid=json.loads(self.snapshot.read_text()); bad=dict(valid); bad['totals']={'a':900}
        for content in ('{bad','[]',json.dumps(bad),json.dumps(dict(valid,sequence=-1)),json.dumps(dict(valid,sequence=900)),json.dumps(dict(valid,totals={'a':True}))):
            self.snapshot.write_text(content)
            self.assertEqual(self.j.state(self.snapshot),{'a':4})
            self.assertEqual(self.snapshot.read_text(),content)
        self.snapshot.unlink(); self.assertEqual(self.j.state(self.snapshot),{'a':4})
    def test_conflicts_and_empty_snapshot(self):
        self.j.snapshot(self.snapshot); self.assertEqual(self.j.state(self.snapshot),{})
        self.j.append('id','x',2)
        with self.assertRaises(ValueError): self.j.append('id','x',3)
        with self.assertRaises((TypeError,ValueError)): self.j.append('id2','x',True)
        self.assertEqual(self.j.state(self.snapshot),{'x':2})

    def test_checksum_valid_but_invalid_snapshot_metadata_is_ignored(self):
        self.j.append('one','a',4)
        for sequence,totals in ((-1,{'a':999}),(900,{'a':999}),(True,{'a':999}),(1,{'a':True}),(1,{'a':'999'})):
            with self.subTest(sequence=sequence,totals=totals):
                body={'sequence':sequence,'totals':totals}
                encoded=json.dumps(body,sort_keys=True,separators=(',',':')).encode()
                body['checksum']=hashlib.sha256(encoded).hexdigest()
                self.snapshot.write_text(json.dumps(body)); before=self.snapshot.read_bytes()
                self.assertEqual(self.j.state(self.snapshot),{'a':4})
                self.assertEqual(self.snapshot.read_bytes(),before)

    def test_large_signed_deltas_snapshot_and_restart_are_exact(self):
        large=2**120+43
        self.assertTrue(self.j.append('positive','x',large))
        self.assertTrue(self.j.append('negative','y',-large-7))
        reopened=Journal(self.path)
        self.assertFalse(reopened.append('positive','x',large))
        with self.assertRaises(ValueError): reopened.append('positive','x',large+1)
        self.assertEqual(reopened.state(),{'x':large,'y':-large-7})
        reopened.snapshot(self.snapshot)
        saved=json.loads(self.snapshot.read_text())
        self.assertEqual(saved['totals'],{'x':large,'y':-large-7})
        self.assertIs(type(saved['totals']['x']),int)
        self.assertIs(type(saved['totals']['y']),int)
        self.j.append('cancel','x',-large)
        self.j.append('tail','y',large+8)
        self.assertEqual(Journal(self.path).state(self.snapshot),{'x':0,'y':1})
        self.assertEqual(Journal(self.path).state(),{'x':0,'y':1})

    def test_canonical_checksum_matches_json_for_signed_zero_and_unicode(self):
        totals={'snowman ☃':-17,'é':0,'quote"\\':23,'𝄞':4}
        for i,(key,delta) in enumerate(totals.items()): self.j.append(str(i),key,delta)
        self.j.snapshot(self.snapshot)
        saved=json.loads(self.snapshot.read_text())
        canonical=json.dumps({'sequence':4,'totals':totals},sort_keys=True,separators=(',',':')).encode()
        self.assertEqual(saved['checksum'],hashlib.sha256(canonical).hexdigest())
        self.assertEqual(saved['totals'],totals)
        self.assertEqual(Journal(self.path).state(self.snapshot),totals)

    def test_5000_digit_snapshot_checksum_and_read_preserve_integer_types(self):
        large=10**4999; digits='1'+'0'*4999
        self.j.append('huge','negative',-large)
        self.j.append('huge2','positive',large)
        self.j.snapshot(self.snapshot)
        # Expected bytes are independent of the implementation's number encoder.
        canonical='{"sequence":2,"totals":{"negative":-'+digits+',"positive":'+digits+'}}'
        expected=hashlib.sha256(canonical.encode()).hexdigest()
        saved=json.loads(self.snapshot.read_text(),parse_int=lambda value:('integer',value))
        self.assertEqual(saved['sequence'],('integer','2'))
        self.assertEqual(saved['totals'],{'negative':('integer','-'+digits),'positive':('integer',digits)})
        self.assertEqual(saved['checksum'],expected)
        before=self.snapshot.read_bytes()
        reopened=Journal(self.path)
        self.assertEqual(reopened.state(self.snapshot),{'negative':-large,'positive':large})
        reopened.append('zero','negative',large)
        self.assertEqual(reopened.state(self.snapshot),{'negative':0,'positive':large})
        self.assertEqual(reopened.state(),{'negative':0,'positive':large})
        self.assertEqual(self.snapshot.read_bytes(),before)
