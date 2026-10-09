import unittest
import leasequeue

class ImportTests(unittest.TestCase):
    def test_package_imports(self):
        self.assertEqual(leasequeue.__name__, "leasequeue")
