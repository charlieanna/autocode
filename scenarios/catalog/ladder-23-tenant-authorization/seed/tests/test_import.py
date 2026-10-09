import unittest
import tenants

class ImportTests(unittest.TestCase):
    def test_package_imports(self):
        self.assertEqual(tenants.__name__, "tenants")
