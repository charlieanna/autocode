"""Completed black-box fake run, portable export, and public offline replay."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from autocode_taskrun import TaskRun

from scenarios import run as scenario_runner

from .opencode_fixture_cli import entrypoint

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

    def test_opencode_requirements_frontier_replays_without_live_model_inventory(self):
        project = self.root / "original-project"
        project.mkdir()
        (project / "README.md").write_text("A synthetic greeting CLI fixture.\n")
        (project / "greet.py").write_text("# Synthetic greeting source inspected by the offline fixture.\n")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "AUTOCODE_FIXTURE_", "BUILD_AUDIT_LIVE_", "REVIEW_AUDIT_LIVE_"))
            and key
            not in (
                "AUTOCODE_PROVIDER",
                "AUTOCODE_TEST_CLI",
                "AUTOCODE_TEST_WHEEL",
                "OPENAI_API_KEY",
                "CODEX_API_KEY",
                "OPENAI_BASE_URL",
                "OPENCODE_CONFIG_CONTENT",
            )
        }
        git_env = {**env, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        subprocess.run(["git", "init", "-q", str(project)], check=True, env=git_env, timeout=30)
        for name, value in (("maintenance.auto", "false"), ("gc.auto", "0")):
            subprocess.run(["git", "-C", str(project), "config", name, value], check=True, env=git_env, timeout=30)
        subprocess.run(["git", "-C", str(project), "add", "README.md", "greet.py"], check=True, env=git_env, timeout=30)
        subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-qm",
                "Synthetic replay fixture",
            ],
            check=True,
            env=git_env,
            timeout=30,
        )
        top = subprocess.check_output(
            ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
            env=git_env,
            text=True,
            timeout=30,
        ).strip()
        self.assertEqual(project, Path(top).resolve())
        binary = self.root / "fixture-bin"
        binary.mkdir()
        for source, name in (
            ("fake_opencode.py", "opencode"),
            ("fake_codex.py", "codex"),
            ("goal_fixtures.py", "goal_fixtures.py"),
        ):
            shutil.copy2(ROOT / "tools" / source, binary / name)
        for name in ("opencode", "codex"):
            (binary / name).chmod(0o755)
        env.update(
            PATH=str(binary) + os.pathsep + os.environ["PATH"],
            XDG_CONFIG_HOME=str(self.root / "xdg-config"),
            CODEX_HOME=str(self.root / "codex-home"),
            AUTOCODE_HOME=str(self.root / "registry"),
            AUTOCODE_FIXTURE_MODE="no-human",
            PYTHONDONTWRITEBYTECODE="1",
        )
        command = (sys.executable, str(ROOT / "tools/autocode.py"))
        task = TaskRun.start(
            project,
            "Build a greeting tool; ask whether it should be a CLI or web endpoint first.",
            command=entrypoint(command),
            env=env,
            cwd=project,
            timeout=180,
            options=(
                "--engine",
                "opencode",
                "--provider",
                "opencode",
                "--single-model",
                "zai-coding-plan/glm-5.3",
                "--workflow",
                "build",
                "--unit",
                "autoplanner",
                "--max-parallel-builders",
                "1",
                "--max-iterations",
                "2",
                "--max-seconds",
                "180",
                "--max-stage-seconds",
                "60",
            ),
        )
        view = task.status(inspect_evidence=True)
        self.assertEqual("WAITING_FOR_USER", view["status"], task.last_advance)
        self.assertFalse(view["done"])
        self.assertEqual("answer", view["needs"]["kind"])
        self.assertEqual(1, len(view["needs"]["questions"]))
        records = view["replay_source"]["records"]
        self.assertEqual(["requirements_gather", "astra_discovery"], [row["stage"] for row in records])
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row.get("runner_owned")]
        self.assertEqual(2, len(attempts))
        self.assertTrue(all(row["engine"] == "opencode" and row["exit_code"] == 0 for row in attempts))
        original = evidence_bytes(task.run_dir)
        exported = subprocess.run(
            [*command, "replay", str(task.run_dir), "--export", str(self.root / "opencode-recording"), "--json"],
            capture_output=True,
            text=True,
            env=env,
            cwd=project,
            timeout=180,
        )
        self.assertEqual(0, exported.returncode, exported.stderr)
        manifest = Path(json.loads(exported.stdout)["manifest"])
        document = json.loads(manifest.read_text())
        self.assertEqual("opencode", document["launch"]["settings"]["engine"])
        self.assertEqual("build", document["launch"]["workflow_pin"])
        self.assertTrue(all(row["output_mode"] == "opencode_events" for row in document["dispatches"]))
        offline = self.root / "offline-bin"
        offline.mkdir()
        marker = self.root / "provider-called"
        for name in ("opencode", "codex", "kilo"):
            path = offline / name
            path.write_text(
                f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(77)\n"
            )
            path.chmod(0o755)
        env["PATH"] = str(offline) + os.pathsep + str(Path(sys.executable).parent) + ":/usr/bin:/bin"
        destination = self.root / "replayed-project/.autocode/runs/frontier"
        replayed = subprocess.run(
            [*command, "replay", str(manifest), "--into", str(destination), "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        (self.root / "opencode-replay-stdout.json").write_text(replayed.stdout)
        (self.root / "opencode-replay-stderr.log").write_text(replayed.stderr)
        self.assertEqual(0, replayed.returncode, replayed.stderr)
        current = json.loads(replayed.stdout)["view"]
        self.assertEqual("WAITING_FOR_USER", current["status"])
        self.assertFalse(current["done"])
        self.assertEqual(view["needs"]["questions"], current["needs"]["questions"])
        self.assertEqual(("build", "user"), (current["workflow"], current["workflow_source"]))
        self.assertEqual(2, current["replay_progress"]["consumed"])
        self.assertEqual("end", current["replay_progress"]["next_dispatch"])
        self.assertFalse(marker.exists(), "offline replay invoked a live provider")
        self.assertEqual(original, evidence_bytes(task.run_dir))

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
        self.assertTrue(Path(json.loads(exported.stdout)["manifest"]).is_file())
        # A bug-report bundle of the run carries the same recording, and it is the one replayed below (#716).
        bundled = subprocess.run(
            [sys.executable, str(ROOT / "tools/autocode.py"), "bundle", str(source), "--out", str(root / "run.tar.gz")]
            + ["--json"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(bundled.returncode, 0, bundled.stderr)
        self.assertEqual({}, json.loads(bundled.stdout)["manifest"]["notes"])
        with tarfile.open(root / "run.tar.gz") as archive:
            archive.extractall(root / "bundle", filter="data")
        (manifest,) = (root / "bundle").glob("*/recording/recording.json")
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
