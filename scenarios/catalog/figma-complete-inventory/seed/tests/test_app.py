import unittest
from app import health

class HealthTests(unittest.TestCase):
    def test_functional_health_remains_green(self):
        self.assertEqual("ready",health())
