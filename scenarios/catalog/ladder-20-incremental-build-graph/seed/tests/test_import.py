import unittest
import buildgraph

class ImportTests(unittest.TestCase):
    def test_package_imports(self):
        self.assertEqual(buildgraph.__name__, "buildgraph")
