import unittest
import deployment

class ImportTests(unittest.TestCase):
    def test_package_imports(self):
        self.assertEqual(deployment.__name__, "deployment")
