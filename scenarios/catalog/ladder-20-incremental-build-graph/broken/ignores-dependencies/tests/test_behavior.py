import tempfile
import unittest
from pathlib import Path
from buildgraph import Builder,topology

class BuildTests(unittest.TestCase):
    def test_build_and_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.json'; graph={'app':['lib'],'lib':[]}; calls=[]
            def compiler(node,source,deps):
                calls.append(node); return source+''.join(deps.values())
            self.assertEqual(topology(graph),['lib','app'])
            self.assertEqual(Builder(graph,path).build({'lib':'L','app':'A'},compiler),{'lib':'L','app':'AL'})
            calls.clear(); Builder(graph,path).build({'lib':'L','app':'A'},compiler)
            self.assertEqual(calls,[])
