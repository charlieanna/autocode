import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from configmerge import merge


class HiddenConfigMergeTests(unittest.TestCase):
    def test_type_changes_null_and_empty(self):
        pairs = [
            ({"a": [1]}, {"a": None}, {"a": None}),
            ({"a": None}, {"a": {"b": 1}}, {"a": {"b": 1}}),
            ({"a": {"b": 1}}, {"a": []}, {"a": []}),
            ({"a": {"b": 1}}, {"a": {}}, {"a": {"b": 1}}),
            ({}, {}, {}),
            ({"keep": 1}, {"new": 2}, {"keep": 1, "new": 2}),
        ]
        for base, override, result in pairs:
            self.assertEqual(merge(base, override), result)

    def test_all_mutable_subtrees_are_independent(self):
        base = {"keep": {"nested": [{"v": 1}]}, "edit": {"old": [2]}}
        override = {"add": [{"v": 3}], "edit": {"new": [4]}}
        before = (copy.deepcopy(base), copy.deepcopy(override))
        result = merge(base, override)
        result["keep"]["nested"][0]["v"] = 99
        result["add"][0]["v"] = 99
        result["edit"]["old"].append(99)
        result["edit"]["new"].append(99)
        self.assertEqual((base, override), before)
        other = merge(base, override)
        base["keep"]["nested"].append(99)
        override["add"].append(99)
        self.assertEqual(other["keep"], before[0]["keep"])
        self.assertEqual(other["add"], before[1]["add"])

    def test_root_validation(self):
        for value in [[], None, 1, "a", True]:
            with self.assertRaises(ValueError):
                merge(value, {})
            with self.assertRaises(ValueError):
                merge({}, value)

    def test_cli_sorted_json_and_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "base.json"
            override = Path(folder) / "override.json"
            base.write_text('{"z":1,"b":{"z":3,"a":1}}')
            override.write_text('{"a":2,"b":{"q":null}}')

            def invoke():
                return subprocess.run(
                    [sys.executable, "-m", "configmerge", str(base), str(override)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

            p = invoke()
            self.assertEqual((p.returncode, p.stderr), (0, ""))
            expected = {"a": 2, "b": {"a": 1, "q": None, "z": 3}, "z": 1}
            self.assertEqual(json.loads(p.stdout), expected)
            keys = json.loads(p.stdout, object_pairs_hook=lambda pairs: pairs)
            self.assertEqual([k for k, v in keys], ["a", "b", "z"])
            self.assertEqual([k for k, v in keys[1][1]], ["a", "q", "z"])
            self.assertTrue(p.stdout.endswith("\n"))
            for raw in ["[]", "null", "broken", '{"x":']:
                override.write_text(raw)
                p = invoke()
                self.assertEqual((p.returncode, p.stdout), (2, ""))
                self.assertTrue(p.stderr)
            override.unlink()
            p = invoke()
            self.assertEqual((p.returncode, p.stdout), (2, ""))
            self.assertTrue(p.stderr)
