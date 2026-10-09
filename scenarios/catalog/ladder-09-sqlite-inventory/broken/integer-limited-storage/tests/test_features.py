import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class InventoryTests(unittest.TestCase):
    def test_inventory_survives_another_process(self):
        with tempfile.TemporaryDirectory() as folder:
            command = [sys.executable, "-m", "app", "--db", str(Path(folder) / "stock.db")]
            added = subprocess.run(command + ["add", "apple", "5"], capture_output=True, text=True)
            self.assertEqual(added.returncode, 0, added.stderr)
            listed = subprocess.run(command + ["list"], capture_output=True, text=True)
            self.assertEqual(json.loads(listed.stdout), [{"sku": "apple", "quantity": 5}])
