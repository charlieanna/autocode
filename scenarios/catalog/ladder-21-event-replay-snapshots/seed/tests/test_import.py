import unittest
import journal

class ImportTests(unittest.TestCase):
    def test_package_imports(self):
        self.assertEqual(journal.__name__, "journal")
