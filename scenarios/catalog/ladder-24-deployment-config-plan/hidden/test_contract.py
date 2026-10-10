import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deployment import plan, write_plan


class DeploymentContract(unittest.TestCase):
    def test_deterministic_order_defaults_and_no_mutation(self):
        manifest = {
            "services": {
                "z": {},
                "b": {"depends_on": ["a"], "env": {"X": "${MISSING:-fallback}/${EMPTY:-wrong}/${HOST}"}},
                "a": {},
            }
        }
        original = copy.deepcopy(manifest)
        env = {"EMPTY": "", "HOST": "local"}
        result = plan(manifest, env)
        self.assertEqual([row["name"] for row in result], ["a", "b", "z"])
        self.assertEqual(result[1]["env"]["X"], "fallback//local")
        self.assertEqual(manifest, original)
        self.assertEqual(env, {"EMPTY": "", "HOST": "local"})

    def test_rejects_graph_ports_structure_and_templates(self):
        cases = [
            {"services": {"a": {}, "b": {"depends_on": ["c"]}, "c": {"depends_on": ["b"]}}},
            {"services": {"a": {"depends_on": ["missing"]}}},
            {"services": {"a": {}, "b": {"depends_on": ["a", "a"]}}},
            {"services": {"a": {"ports": [8000]}, "b": {"ports": [8000]}}},
            {"services": {"a": {"ports": [1, 1]}}},
            {"services": {"a": {"ports": [True]}}},
            {"services": {"a": {"ports": [65536]}}},
            {"services": {"a": {"env": {"X": "${NOPE}"}}}},
            {"services": {"a": {"env": {"X": "${unclosed"}}}},
            {"services": {"a": {"env": {"X": "${9bad}"}}}},
            {"services": {"a": {"unknown": 1}}},
            {"services": []},
            {"services": {"a": []}},
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan(value, {})

    def test_cli_success_invalid_input_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "manifest.json"
            env = root / "env.json"
            out = root / "plan.json"
            env.write_text("{}")
            manifest.write_text(json.dumps({"services": {"api": {"ports": [8000]}}}))
            command = [sys.executable, "-m", "deployment", str(manifest), "--env", str(env), "--output", str(out)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(out.read_text())[0]["name"], "api")
            before = out.read_bytes()
            for text in ("{invalid", json.dumps({"services": {"api": {"env": {"X": "${MISSING}"}}}})):
                manifest.write_text(text)
                result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr.strip())
                self.assertEqual(out.read_bytes(), before)
            with self.assertRaises(ValueError):
                write_plan({"services": {"a": {"ports": [0]}}}, {}, out)
            self.assertEqual(out.read_bytes(), before)
