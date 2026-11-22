"""Protected replay observes original Node tests cancelled behind a replaced case."""

import json
import os
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_check_replay as check_replay
import autocode_protected_oracles as guard
import autocode_util as util
import autocode_verify as verify

from . import test_superseded_tests as fixture
from .test_verify import Project

BUILD = """const fs = require('node:fs');
fs.mkdirSync('out-test', {recursive: true});
fs.copyFileSync('src/shipping.js', 'out-test/shipping.js');
"""
LOAD_BUILT = ("../src/shipping.js", "../out-test/shipping.js")
HANG_ON_FIX = """test('a fifty dollar order pays the fee', async () => {
  if (shippingFee(5000) === 0) await new Promise(() => {});
"""


@unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "real Node and npm are required")
class NodeProtectedReplayTests(unittest.TestCase):
    approve = fixture.Gate.approve

    def prepare(self, original):
        package = {
            "name": "shipping",
            "private": True,
            "scripts": {"test": "node scripts/build.cjs && node --test tests/*.test.js"},
        }
        project = Project(
            {
                "package.json": json.dumps(package),
                ".gitignore": "out-test/\n",
                "scripts/build.cjs": BUILD,
                "src/shipping.js": fixture.JS_FEE,
                fixture.JS_ENTRY["file"]: original.replace(*LOAD_BUILT),
            }
        )
        self.addCleanup(project.close)
        self.state = {"settings": {}, "workspace": str(project.root), "user_events": []}
        self.run_dir = project.root / ".autocode" / "runs" / "superseded-node"
        guard.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            project.root,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: verify.detect_framework(project.root).suite,
        )
        self.approve([fixture.JS_ENTRY], method="test: a fifty dollar order pays the fee")
        project.write(
            {"src/shipping.js": fixture.JS_FIXED, fixture.JS_ENTRY["file"]: fixture.JS_UPDATED.replace(*LOAD_BUILT)}
        )
        return project.root

    def replay(self, root):
        return guard.replay(self.state, root, self.run_dir / "replays", verify.scratch_run, timeout=60)

    def test_original_replay_builds_the_same_package_before_running_selected_tests(self):
        root = self.prepare(fixture.JS_ORIGINAL)
        result = self.replay(root)
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(
            ["tests/shipping.test.js::a small order pays the fee"], result["original"]["results"]["passed"]
        )

    def test_node_checks_ignore_the_parent_test_context(self):
        root = self.prepare(fixture.JS_ORIGINAL)
        with patch.dict(os.environ, {"NODE_TEST_CONTEXT": "child-v8"}):
            replay = self.replay(root)
        self.assertTrue(replay["candidate"]["results_file_written"])
        self.assertTrue(replay["original"]["results_file_written"])
        self.assertIn("tests/shipping.test.js::a small order pays the fee", replay["original"]["results"]["passed"])

    def test_original_collateral_cancellation_requires_a_fresh_pass(self):
        original = fixture.JS_ORIGINAL.replace("test('a fifty dollar order pays the fee', () => {\n", HANG_ON_FIX)
        root = self.prepare(original)
        result = self.replay(root)
        self.assertEqual("PASS", result["verdict"])
        self.assertIn("tests/shipping.test.js::a small order pays the fee", result["original"]["results"]["failed"])
        self.assertEqual(
            ["tests/shipping.test.js::a small order pays the fee"], result["original_recheck"]["results"]["passed"]
        )
        self.state["validation"] = {
            "check_replay": {"verdict": "PASS", "source_revision": result["source_revision"], "protected_tests": result}
        }
        self.assertTrue(guard.ready(self.state, result["source_revision"]))
        capture = Path(result["original_recheck"]["node_capture"]["path"])
        capture.write_bytes(capture.read_bytes() + b"\n")
        self.assertFalse(guard.ready(self.state, result["source_revision"]))

    def test_scheduled_replay_binds_the_original_and_recheck_attempts(self):
        original = fixture.JS_ORIGINAL.replace("test('a fifty dollar order pays the fee', () => {\n", HANG_ON_FIX)
        root = self.prepare(original)
        events = self.run_dir / "tester.events.jsonl"
        events.write_text('{"type": "fixture Tester execution"}\n')

        def identity(workspace, **_kwargs):
            return {"source_revision": util.snapshot(workspace)["revision"], "reuse_supported": False}

        proof = check_replay.replay(
            [],
            root,
            self.run_dir,
            {"stage": "tester", "events": str(events), "source_revision": identity(root)["source_revision"]},
            verify.scratch_run,
            timeout=60,
            approved_state=self.state,
            execution_identity=identity,
        )
        self.state["validation"] = {"check_replay": proof}
        pair = proof["protected_tests"]
        self.assertEqual("PASS", pair["verdict"])
        self.assertGreater(proof["scheduling"]["executed_count"], 2)
        self.assertEqual("execute", pair["original_recheck"]["scheduling"]["action"])
        self.assertTrue(guard.ready(self.state, proof["source_revision"]))
        path = Path(pair["original_recheck"]["scheduling"]["receipt"])
        pins = check_replay.evidence_pins(proof)
        for name in ("original", "original_recheck"):
            capture = pair[name]["node_capture"]
            self.assertEqual(capture["sha256"], pins[capture["path"]])
        self.assertEqual(pair["original_recheck"]["scheduling"]["receipt_sha256"], pins[str(path)])
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertFalse(guard.ready(self.state, proof["source_revision"]))

    def test_an_actual_failure_after_cancellation_is_not_excused(self):
        original = fixture.JS_ORIGINAL.replace("test('a fifty dollar order pays the fee', () => {\n", HANG_ON_FIX)
        root = self.prepare(original)
        (root / "src/shipping.js").write_text(fixture.JS_FIXED.replace("0 : 499", "0 : 500"))
        (root / fixture.JS_ENTRY["file"]).write_text(
            fixture.JS_UPDATED.replace(*LOAD_BUILT).replace("shippingFee(1000), 499", "shippingFee(1000), 500")
        )
        with self.assertRaises(guard.commands.OriginalGateFailure) as stopped:
            self.replay(root)
        check = stopped.exception.receipt["original_recheck"]
        self.assertEqual(1, check["exit_code"])
        self.assertEqual(["tests/shipping.test.js::a small order pays the fee"], check["results"]["failed"])
        self.assertIn(
            "Failing original tests: tests/shipping.test.js::a small order pays the fee", str(stopped.exception)
        )


if __name__ == "__main__":
    unittest.main()
