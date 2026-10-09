import unittest

import ledger


class ImportTests(unittest.TestCase):
    def test_package_imports(self):
        self.assertEqual(ledger.__name__, "ledger")
