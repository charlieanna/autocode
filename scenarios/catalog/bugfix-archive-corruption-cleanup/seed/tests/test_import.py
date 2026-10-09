import unittest
import safezip

class ImportTests(unittest.TestCase):
    def test_package_imports(self):
        self.assertEqual(safezip.__name__, "safezip")
