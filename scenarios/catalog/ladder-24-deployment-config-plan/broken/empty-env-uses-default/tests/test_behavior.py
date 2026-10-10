import tempfile
import unittest
from pathlib import Path

from deployment import plan, write_plan


class DeploymentTests(unittest.TestCase):
    def test_database_precedes_service_and_resolves_env(self):
        manifest = {
            "services": {
                "api": {"depends_on": ["db"], "ports": [8080], "env": {"URL": "http://${HOST}:80"}},
                "db": {"ports": [5432]},
            }
        }
        result = plan(manifest, {"HOST": "localhost"})
        self.assertEqual([row["name"] for row in result], ["db", "api"])
        self.assertEqual(result[1]["env"], {"URL": "http://localhost:80"})
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "plan.json"
            self.assertEqual(write_plan(manifest, {"HOST": "localhost"}, output), result)
            self.assertTrue(output.read_text().endswith("\n"))
