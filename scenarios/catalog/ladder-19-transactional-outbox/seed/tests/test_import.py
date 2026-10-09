import unittest

import outbox


class ImportTests(unittest.TestCase):
    def test_package_imports(self):
        self.assertEqual(outbox.__name__, "outbox")
