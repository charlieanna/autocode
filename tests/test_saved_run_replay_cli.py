"""Completed black-box fake run, portable export, and public offline replay."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scenarios import run as scenario_runner

ROOT = Path(__file__).resolve().parents[1]


def evidence_bytes(directory):
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


class SavedRunReplayCliTest(unittest.TestCase):
    def setUp(self):
        if artifacts := os.environ.get("BUILD_AUDIT_ARTIFACTS"):
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix="saved-run-replay-", dir=artifacts)).resolve()
        else:
            directory = tempfile.TemporaryDirectory(prefix="saved-run-replay-")
            self.addCleanup(directory.cleanup)
            self.root = Path(directory.name).resolve()

    def test_completed_fake_run_replays_with_real_checks_and_preserves_original_evidence(self):
        root = self.root
        args = SimpleNamespace(
            provider=None,
            hybrid=False,
            fake=True,
            fake_solution="reference",
            profile=None,
            out=root / "original",
            autocode=[sys.executable, str(ROOT / "tools/autocode.py")],
            max_steps=None,
            timeout_minutes=None,
            max_seconds=None,
            max_stage_seconds=None,
            max_iterations=None,
            i_authorize_live_model_spend=False,
        )
        scenario = scenario_runner.catalog.load("greenfield-greeting-cli")
        initial = scenario_runner.run_one(scenario, args, extra_flags=("--max-parallel-builders", "1"))
        self.assertEqual(initial["verdict"], "PASS", initial.get("summary"))
        self.assertEqual(initial["runner_status"], "TASK_COMPLETE")
        source = Path(initial["run_dir"])
        original = evidence_bytes(source)
        command = [sys.executable, str(ROOT / "tools/autocode.py"), "replay"]
        exported = subprocess.run(
            [*command, str(source), "--export", str(root / "recording"), "--json"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        (root / "exported-stdout.json").write_text(exported.stdout)
        (root / "exported-stderr.log").write_text(exported.stderr)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        manifest = Path(json.loads(exported.stdout)["manifest"])
        # PATH-only provider probes would hit these sentinels; replay's owned
        # emitter uses the interpreter directly instead of a provider CLI.
        bindir = root / "offline-bin"
        bindir.mkdir()
        marker = root / "provider-called"
        for name in ("codex", "opencode", "kilo"):
            path = bindir / name
            path.write_text(
                f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(77)\n"
            )
            path.chmod(0o755)
        env = {**os.environ, "PATH": f"{bindir}:{Path(sys.executable).parent}:/usr/bin:/bin"}
        destination = root / "reproduced/.autocode/runs/reproduction"
        replayed = subprocess.run(
            [*command, str(manifest), "--into", str(destination), "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        (root / "replayed-stdout.json").write_text(replayed.stdout)
        (root / "replayed-stderr.log").write_text(replayed.stderr)
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        result = json.loads(replayed.stdout)
        self.assertEqual(result["run_dir"], str(destination))
        self.assertEqual(result["mode"], "fresh")
        self.assertEqual(result["view"]["status"], "TASK_COMPLETE")
        self.assertTrue(result["view"]["done"])
        self.assertEqual(result["view"]["replay_progress"]["next_dispatch"], "end")
        self.assertFalse(marker.exists(), "replay invoked a provider program")
        checks = result["view"]["evidence"]["check_replay"]
        self.assertEqual(checks["verdict"], "PASS")
        self.assertGreater(checks["scheduling"]["executed_count"], 0)
        self.assertEqual(checks["scheduling"]["reused_count"], 0)
        self.assertTrue(all(row["exit_code"] == 0 for row in checks["checks"]))
        oracle = scenario_runner.verdict.evaluate(scenario, destination.parent.parent.parent)
        self.assertTrue(oracle.passed, oracle.summary)
        self.assertEqual(evidence_bytes(source), original)
        simulated_destination = root / "simulated/.autocode/runs/reproduction"
        simulated = subprocess.run(
            [*command, str(manifest), "--into", str(simulated_destination), "--recorded-checks", "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        (root / "simulated-stdout.json").write_text(simulated.stdout)
        (root / "simulated-stderr.log").write_text(simulated.stderr)
        self.assertEqual(simulated.returncode, 0, simulated.stderr)
        simulated_result = json.loads(simulated.stdout)
        view = simulated_result["view"]
        self.assertEqual(simulated_result["mode"], "recorded")
        self.assertEqual(view["status"], "REPLAY_COMPLETE")
        self.assertFalse(view["done"])
        self.assertIs(view["completion_current"], False)
        self.assertIs(view["replay_result"]["simulated"], True)
        self.assertIs(view["replay_result"]["current_proof"], False)
        self.assertEqual(view["replay_result"]["recorded_verdict"], "TASK_COMPLETE")
        declared_checks = [row["id"] for row in json.loads(manifest.read_text())["checks"]]
        self.assertTrue(declared_checks, "the completed recording has no archived check obligations")
        self.assertEqual(view["replay_result"]["consumed_checks"], declared_checks)
        self.assertFalse(marker.exists(), "simulation invoked a provider program")
        self.assertNotEqual((view.get("evidence_report") or {}).get("availability"), "current")
        self.assertEqual(evidence_bytes(source), original)
        # Changing one declared source payload is rejected before another
        # workspace is prepared, rather than silently substituting a tree.
        document = json.loads(manifest.read_text())
        blob = next(iter(document["blobs"].values()))
        payload = manifest.parent / blob["path"]
        payload.write_bytes(payload.read_bytes() + b"damaged")
        refused_destination = root / "refused/.autocode/runs/reproduction"
        refused = subprocess.run(
            [*command, str(manifest), "--into", str(refused_destination), "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        (root / "refused-stdout.json").write_text(refused.stdout)
        (root / "refused-stderr.log").write_text(refused.stderr)
        self.assertEqual(refused.returncode, 3, refused.stderr)
        self.assertEqual(json.loads(refused.stderr)["code"], "REPLAY_REFUSED")
        self.assertFalse(refused_destination.parent.parent.parent.exists())
        self.assertEqual(evidence_bytes(source), original)


if __name__ == "__main__":
    unittest.main()
