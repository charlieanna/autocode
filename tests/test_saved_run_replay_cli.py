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
from scenarios.harness import fake_changed_code

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
        self.assertEqual(["requirements_gather"], [row["stage"] for row in records])
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row.get("runner_owned")]
        self.assertEqual(1, len(attempts))
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
        self.assertEqual(1, current["replay_progress"]["consumed"])
        self.assertEqual("end", current["replay_progress"]["next_dispatch"])
        self.assertFalse(marker.exists(), "offline replay invoked a live provider")
        self.assertEqual(original, evidence_bytes(task.run_dir))

    def test_legacy_requirements_triage_saved_run_exports_and_replays_cleanly(self):
        project = self.root / "triage-project"
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
                "Synthetic triage replay fixture",
            ],
            check=True,
            env=git_env,
            timeout=30,
        )
        binary = self.root / "triage-fixture-bin"
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
            XDG_CONFIG_HOME=str(self.root / "triage-xdg"),
            CODEX_HOME=str(self.root / "triage-codex"),
            AUTOCODE_HOME=str(self.root / "triage-registry"),
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
        saved_path = task.run_dir / "state.json"
        saved = json.loads(saved_path.read_text())
        questions = saved["pending_questions"]
        triage_report = {
            "summary": "Triage: ask how the greeting is invoked before drafting requirements",
            "intended_outcome": "Build a greeting tool",
            "ready": False,
            "open_questions": questions,
        }
        triage_json = json.dumps(triage_report, indent=2, sort_keys=True) + "\n"
        stage0 = saved["stages"][0]
        Path(stage0["output"]).write_text(triage_json)
        if stage0.get("reported_output") and Path(stage0["reported_output"]).is_file():
            Path(stage0["reported_output"]).write_text(triage_json)
        if stage0.get("response_text") and Path(stage0["response_text"]).is_file():
            Path(stage0["response_text"]).write_text(json.dumps(triage_report))
        events_path = Path(stage0["events"])
        rewritten_events = []
        for raw_line in events_path.read_text().splitlines():
            if not raw_line.strip():
                continue
            event = json.loads(raw_line)
            if event.get("type") == "text" and isinstance(event.get("part"), dict):
                event["part"]["text"] = json.dumps(triage_report)
            elif event.get("type") == "tool_use":
                continue
            rewritten_events.append(json.dumps(event))
        events_path.chmod(0o600)
        events_path.write_text("\n".join(rewritten_events) + "\n")
        events_path.chmod(0o400)
        saved["settings"]["requirements_triage"] = True
        saved["settings"]["joint_planning"] = True
        saved["settings"]["roles"]["requirements"]["reasoning_effort"] = "medium"
        saved["settings"]["declared_variants"] = {"zai-coding-plan/glm-5.3": ["low", "medium", "high"]}
        stage0["reasoning_effort"] = "low"
        stage0["launch_route"]["reasoning_effort"] = "low"
        stage0["execution_identity"]["reasoning_effort"] = "low"
        saved["requirements_triage"] = {
            "pass": 1,
            "outcome": "questions",
            "questions": len(questions),
            "at": "2026-11-08T00:00:00+00:00",
        }
        saved_path.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n")

        status_check = subprocess.run(
            [*command, "--run-dir", str(task.run_dir), "--status"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(0, status_check.returncode, status_check.stderr)
        status_view = json.loads(status_check.stdout)["view"]
        # Rewriting the retained report and settings expires the old question.
        # Export still preserves those legacy inputs; only a fresh replay may
        # publish its own question, with its own decision binding.
        self.assertEqual("RESOLVER_PENDING", status_view["status"])
        self.assertEqual("continue", status_view["needs"]["kind"])
        self.assertIsNone(status_view["chat"]["input"])
        status_view = task.advance_until_input()
        self.assertEqual("WAITING_FOR_USER", status_view["status"])
        self.assertEqual(
            [(q["id"], q["question"]) for q in questions],
            [(q["id"], q["question"]) for q in status_view["needs"]["questions"]],
        )
        self.assertEqual(1, status_view["usage"]["accounting"]["attempt_count"], "Refresh cannot replay Requirements")

        exported = subprocess.run(
            [*command, "replay", str(task.run_dir), "--export", str(self.root / "triage-recording"), "--json"],
            capture_output=True,
            text=True,
            env=env,
            cwd=project,
            timeout=180,
        )
        self.assertEqual(0, exported.returncode, exported.stderr)
        manifest = Path(json.loads(exported.stdout)["manifest"])
        document = json.loads(manifest.read_text())
        self.assertIs(True, document["launch"]["settings"].get("requirements_triage"))

        offline = self.root / "triage-offline-bin"
        offline.mkdir()
        marker = self.root / "triage-provider-called"
        for name in ("opencode", "codex", "kilo"):
            path = offline / name
            path.write_text(
                f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(77)\n"
            )
            path.chmod(0o755)
        env["PATH"] = str(offline) + os.pathsep + str(Path(sys.executable).parent) + ":/usr/bin:/bin"

        pre_3652_dir = self.root / "triage-recording-pre-3652"
        shutil.copytree(manifest.parent, pre_3652_dir)
        pre_3652_manifest = pre_3652_dir / manifest.name
        pre_3652_doc = json.loads(pre_3652_manifest.read_text())
        pre_3652_doc["launch"]["settings"].pop("requirements_triage", None)
        pre_3652_doc["launch"]["settings"].pop("declared_variants", None)
        pre_3652_manifest.write_text(json.dumps(pre_3652_doc, indent=2, sort_keys=True) + "\n")

        for label, target_manifest in (("exported", manifest), ("pre-3652", pre_3652_manifest)):
            with self.subTest(bundle=label):
                destination = self.root / f"replayed-{label}/.autocode/runs/triage"
                replayed = subprocess.run(
                    [*command, "replay", str(target_manifest), "--into", str(destination), "--json"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=180,
                )
                self.assertEqual(0, replayed.returncode, replayed.stderr)
                replayed_view = json.loads(replayed.stdout)["view"]
                self.assertEqual("WAITING_FOR_USER", replayed_view["status"])
                self.assertEqual(status_view["needs"]["questions"], replayed_view["needs"]["questions"])
                self.assertEqual(1, replayed_view["replay_progress"]["consumed"])
                self.assertEqual("end", replayed_view["replay_progress"]["next_dispatch"])
                self.assertFalse(marker.exists(), "offline replay invoked a live provider")

    def run_reference(self, scenario_id, solution="reference"):
        args = SimpleNamespace(
            provider=None,
            hybrid=False,
            fake=True,
            fake_solution=solution,
            profile=None,
            out=self.root / "original",
            autocode=[sys.executable, str(ROOT / "tools/autocode.py")],
            max_steps=None,
            timeout_minutes=None,
            max_seconds=None,
            max_stage_seconds=None,
            max_iterations=None,
            i_authorize_live_model_spend=False,
        )
        scenario = scenario_runner.catalog.load(scenario_id)
        initial = scenario_runner.run_one(scenario, args, extra_flags=("--max-parallel-builders", "1"))
        self.assertEqual(initial["verdict"], "PASS", initial.get("summary"))
        self.assertEqual(initial["runner_status"], "TASK_COMPLETE")
        return scenario, Path(initial["run_dir"])

    def run_citing_greeting(self):
        """The greeting CLI with one line whose surviving mutant the Tester judges, citing a killed capture.

        The runner settles a line with no mutation site itself (#4218), so the plain reference leaves the
        Tester no decision and its recording no mutation citation for replay to re-bind.
        """
        solution = self.root / "flushed-greeting"
        shutil.copytree(scenario_runner.catalog.load("greenfield-greeting-cli").dir / "reference", solution)
        (solution / "greet.py").write_text(fake_changed_code.GREETING_FLUSHED_SOURCE)
        return self.run_reference("greenfield-greeting-cli", str(solution))

    def test_testers_mutation_citation_is_authenticated(self):
        _, source = self.run_citing_greeting()
        status = subprocess.run(
            [sys.executable, str(ROOT / "tools/autocode.py"), "--run-dir", str(source), "--status"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, status.returncode, status.stderr)
        view = json.loads(status.stdout)["view"]
        evidence = view["verification"]["changed_code_evidence"]
        self.assertTrue(
            any(
                "/mutation-check/" in ref
                for obligation in evidence["obligations"]
                for ref in (obligation.get("exception") or {}).get("evidence", [])
            ),
            "the fixture must actually cite a killed mutant capture",
        )
        self.assertFalse(any(row["report_only"] for row in view["usage"]["accounting"]["attempts"]))

    def test_completed_fake_run_replays_with_real_checks_and_preserves_original_evidence(self):
        root = self.root
        scenario, source = self.run_citing_greeting()
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
        document = json.loads(Path(json.loads(exported.stdout)["manifest"]).read_text())
        self.assertTrue(
            any(
                binding["symbol"] == "mutation_capture"
                for dispatch in document["dispatches"]
                for binding in dispatch["bindings"]
            ),
            "the recording must include a mutation citation to exercise fresh-path rebinding",
        )
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

    def test_multi_question_answer_many_and_legacy_five_key_actions_export_and_replay_cleanly(self):
        project = self.root / "batch-replay-project"
        project.mkdir()
        (project / "greet.py").write_text("print('hi')\n")
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
        subprocess.run(["git", "-C", str(project), "add", "greet.py"], check=True, env=git_env, timeout=30)
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
                "Seed greeting fixture",
            ],
            check=True,
            env=git_env,
            timeout=30,
        )
        xdg = self.root / "batch-xdg"
        providers = xdg / "autocode" / "providers"
        providers.mkdir(parents=True)
        provider_script = xdg / "batch_provider.py"
        provider_script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            "if sys.argv[1:] == ['login', 'status']:\n"
            "    print('Logged in (offline fixture)')\n"
            "    raise SystemExit(0)\n"
            "prompt = sys.stdin.read()\n"
            "schema = json.loads(Path(sys.argv[sys.argv.index('--output-schema') + 1]).read_text())\n"
            "report_path = Path(sys.argv[sys.argv.index('-o') + 1])\n"
            "props = schema.get('properties', {})\n"
            "q_props = ((props.get('open_questions') or {}).get('items') or {}).get('properties') or {}\n"
            "data = json.loads(prompt.split('CURRENT HANDOFF DATA\\n', 1)[1])\n"
            "saved = data.get('saved_answers') or {}\n"
            "by_qid = {k: v.get('text') for k, v in saved.items() if isinstance(v, dict) and v.get('text')}\n"
            "na = {'status': 'not_applicable', 'refs': []}\n"
            "base = {\n"
            "    'project_conventions': [], 'ignored_requirements': [], 'machine_resolutions': [],\n"
            "    'access_blockers': [], 'task_kind': 'build',\n"
            "    'workspace_scope': {'spans_repositories': False, 'external_parts': []},\n"
            "}\n"
            "if not {'Q1', 'Q2'} <= set(by_qid):\n"
            "    questions = [\n"
            "        {'id': 'Q1', 'question': 'Which output format?', 'why': 'Sets CLI output.',\n"
            "         'options': ['Plain text', 'JSON'], 'proposed_default': 'Plain text',\n"
            "         'kind': 'decision', 'category': 'behavior', 'delegable': True, 'convention_ref': ''},\n"
            "        {'id': 'Q2', 'question': 'Where should history live?', 'why': 'Sets storage path.',\n"
            "         'options': ['SQLite', 'JSONL'], 'proposed_default': 'SQLite',\n"
            "         'kind': 'decision', 'category': 'behavior', 'delegable': True, 'convention_ref': ''},\n"
            "    ]\n"
            "    full = {\n"
            "        **base, 'job': 'build', 'design_document': '', 'clarity': 'vague',\n"
            "        'summary': 'Clarify output format and storage.', 'intended_outcome': 'Greeting CLI',\n"
            "        'requirements': [], 'required_behaviors': [], 'acceptance_tests': [], 'constraints': [],\n"
            "        'source_refs': [], 'proposed_assumptions': [],\n"
            "        'open_questions': [{k: v for k, v in q.items() if not q_props or k in q_props} for q in questions],\n"
            "        'ignored_statements': [], 'conflicts': [], 'proposed_reframes': [],\n"
            "        'approach': {'summary': '', 'pieces': [], 'band': '', 'alternatives': [], 'out_of_scope': []},\n"
            "        'ambiguity_audit': {\n"
            "            'outcome_scope': {'status': 'open_question', 'refs': ['Q1', 'Q2']},\n"
            "            'inputs_and_edge_cases': dict(na), 'error_and_failure_behavior': dict(na),\n"
            "            'compatibility_and_callers': dict(na), 'ux_and_output_contract': dict(na),\n"
            "            'state_and_side_effects': dict(na),\n"
            "        },\n"
            "    }\n"
            "else:\n"
            "    full = {\n"
            "        **base, 'job': 'build', 'design_document': '', 'clarity': 'clear',\n"
            "        'summary': f\"Format={by_qid['Q1']}, Storage={by_qid['Q2']}.\",\n"
            "        'intended_outcome': f\"Emit {by_qid['Q1']} greetings stored in {by_qid['Q2']}\",\n"
            "        'requirements': [\n"
            "            {'id': 'R1', 'text': f\"Emit {by_qid['Q1']}\", 'source_quote': by_qid['Q1']},\n"
            "            {'id': 'R2', 'text': f\"Persist to {by_qid['Q2']}\", 'source_quote': by_qid['Q2']},\n"
            "        ],\n"
            "        'required_behaviors': [f\"Emit {by_qid['Q1']}\", f\"Store in {by_qid['Q2']}\"],\n"
            "        'acceptance_tests': [f\"Running greet.py writes to {by_qid['Q2']}\"],\n"
            "        'constraints': [], 'source_refs': ['greet.py:1'],\n"
            "        'proposed_assumptions': [{\n"
            "            'id': 'A1', 'text': 'Create parent directory automatically if missing',\n"
            "            'kind': 'inferable', 'category': 'behavior', 'convention_ref': 'greet.py:1',\n"
            "            'rationale': 'First-run CLI should not fail on missing directory', 'supports': ['R2'],\n"
            "        }],\n"
            "        'open_questions': [], 'ignored_statements': [], 'conflicts': [], 'proposed_reframes': [],\n"
            "        'approach': {'summary': '', 'pieces': [], 'band': '', 'alternatives': [], 'out_of_scope': []},\n"
            "        'ambiguity_audit': {\n"
            "            'outcome_scope': {'status': 'specified_in_source', 'refs': ['task', 'Q1', 'Q2']},\n"
            "            'inputs_and_edge_cases': dict(na), 'error_and_failure_behavior': dict(na),\n"
            "            'compatibility_and_callers': dict(na), 'ux_and_output_contract': dict(na),\n"
            "            'state_and_side_effects': dict(na),\n"
            "        },\n"
            "    }\n"
            "report_path.write_text(json.dumps({k: v for k, v in full.items() if k in props}))\n"
            "print(json.dumps({'type': 'item.completed', 'item': {'id': 'read-sources', 'type': 'command_execution',\n"
            "    'command': 'head -n 1 greet.py', 'exit_code': 0, 'aggregated_output': ''}}))\n"
            "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 100, 'output_tokens': 50}}))\n"
        )
        provider_script.chmod(0o755)
        roles_toml = "\n".join(
            f'{role} = {{ model = "fake/{role}", effort = "medium" }}'
            for role in ("task_planner", "planner", "plan_reviewer", "builder", "tester", "completion")
        )
        cmd_json = json.dumps(
            [
                sys.executable,
                str(provider_script),
                "--output-schema",
                "{schema}",
                "-o",
                "{report}",
                "--model",
                "{model}",
                "-",
            ]
        )
        (providers / "fake.toml").write_text(
            f'name = "fake"\noutput = "report_file"\ncommand = {cmd_json}\n\n[roles]\n{roles_toml}\n'
        )
        env.update(
            XDG_CONFIG_HOME=str(xdg),
            CODEX_HOME=str(self.root / "batch-codex"),
            AUTOCODE_HOME=str(self.root / "batch-registry"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        command = (sys.executable, str(ROOT / "tools/autocode.py"))
        task = TaskRun.start(
            project,
            "Build a greeting CLI with history",
            command=command,
            env=env,
            cwd=project,
            timeout=180,
            options=("--provider", "fake", "--evidence-provenance", "fake", "--max-parallel-builders", "1"),
        )
        paused = task.advance_until_input()
        self.assertEqual(["Q1", "Q2"], [q["id"] for q in paused["needs"]["questions"]])
        task.answer_many(
            {"Q1": "Plain text", "Q2": "Custom DuckDB file"},
            resolver_token=paused["needs"]["resolver_token"],
        )
        after = task.advance_until_input()
        self.assertEqual("WAITING_FOR_USER", after["status"])
        self.assertEqual(
            "Emit Plain text greetings stored in Custom DuckDB file",
            after["requirements"]["intended_outcome"],
        )

        recording_dir = self.root / "batch-recording"
        exported = subprocess.run(
            [*command, "replay", str(task.run_dir), "--export", str(recording_dir), "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        self.assertEqual(0, exported.returncode, exported.stdout + exported.stderr)
        manifest = Path(json.loads(exported.stdout)["manifest"])
        doc = json.loads(manifest.read_text())
        self.assertEqual(2, len(doc["actions"]))
        self.assertEqual(["Q1", "Q2"], [q["id"] for q in doc["actions"][0]["match"]["questions"]])
        self.assertEqual([True, True], [q["allow_custom"] for q in doc["actions"][0]["match"]["questions"]])
        self.assertEqual(["Q2"], [q["id"] for q in doc["actions"][1]["match"]["questions"]])
        self.assertEqual([True], [q["allow_custom"] for q in doc["actions"][1]["match"]["questions"]])

        legacy_dir = self.root / "batch-recording-pre-3685"
        shutil.copytree(recording_dir, legacy_dir)
        legacy_manifest = legacy_dir / manifest.name
        legacy_doc = json.loads(legacy_manifest.read_text())
        for act in legacy_doc["actions"]:
            for q in act["match"].get("questions") or []:
                q.pop("allow_custom", None)
        legacy_manifest.write_text(json.dumps(legacy_doc, indent=2, sort_keys=True) + "\n")

        for label, target_manifest in (("exported", manifest), ("pre-3685-five-key", legacy_manifest)):
            with self.subTest(bundle=label):
                dest = self.root / f"replayed-{label}/.autocode/runs/reproduction"
                replayed = subprocess.run(
                    [*command, "replay", str(target_manifest), "--into", str(dest), "--json"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=180,
                )
                self.assertEqual(0, replayed.returncode, replayed.stdout + replayed.stderr)
                payload = json.loads(replayed.stdout)
                self.assertEqual("WAITING_FOR_USER", payload["view"]["status"])
                self.assertEqual("end", payload["view"]["replay_progress"]["next_dispatch"])
                self.assertEqual(
                    "Emit Plain text greetings stored in Custom DuckDB file",
                    payload["view"]["requirements"]["intended_outcome"],
                )


if __name__ == "__main__":
    unittest.main()
