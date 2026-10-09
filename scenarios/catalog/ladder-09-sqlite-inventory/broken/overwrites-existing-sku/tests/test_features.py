import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class InventoryTests(unittest.TestCase):
    def test_inventory_survives_another_process(self):
        with tempfile.TemporaryDirectory() as folder:
            command = [sys.executable, "-m", "app", "--db", str(Path(folder) / "stock.db")]
            added = subprocess.run(command + ["add", "apple", "5"], capture_output=True, text=True, timeout=10)
            self.assertEqual(added.returncode, 0, added.stderr)
            listed = subprocess.run(command + ["list"], capture_output=True, text=True, timeout=10)
            self.assertEqual(json.loads(listed.stdout), [{"sku": "apple", "quantity": 5}])

    def test_integer_boundary_adjustment_is_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            command = [sys.executable, "-m", "app", "--db", str(Path(folder) / "stock.db")]
            for args in (["add", "part", str(2**63 - 1)], ["adjust", "part", "2"]):
                result = subprocess.run(command + args, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(command + ["list"], capture_output=True, text=True, timeout=10)
            self.assertEqual(json.loads(result.stdout), [{"sku": "part", "quantity": 2**63 + 1}])
