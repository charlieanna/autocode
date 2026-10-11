"""Ordinary CLI owner exit, public recovery and birth-bound safety controls."""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_controller_owner as owners
import autocode_process as processes
import autocode_taskrun as taskrun
import autocode_util as util

TOOLS = Path(__file__).resolve().parents[1] / "tools"
BRIEF = (
    "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
    "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
    "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
    "Python standard library only."
)
OPTIONS = (
    "--evidence-provenance",
    "fake",
    "--engine",
    "codex",
    "--workflow",
    "build",
    "--joint-planning",
    "--astra-model",
    "gpt-6-astra",
    "--terra-model",
    "gpt-5.6-terra",
    "--sol-model",
    "gpt-5.6-sol",
    "--completion-model",
    "gpt-6-astra",
    "--glm-model",
    "gpt-5.6-sol",
    "--plan-reviewer-model",
    "gpt-6-astra",
)


class OwnerInspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-owner-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.run_dir = self.workspace / ".autocode/runs/one"
        self.run_dir.mkdir(parents=True)
        self.state = {
            "task": "A task",
            "workspace": str(self.workspace),
            "status": "RUNNING",
            "next_stage": "astra_discovery",
            "phase": "DISCOVERING",
            "stages": [],
        }
        self.persist(self.run_dir / "state.json", self.state)

    def persist(self, path, state):
        util.atomic_json(path, state)
        owners.checkpoint(path, state)

    def test_live_birth_bound_owner_is_never_reconciled_as_absent(self):
        with util.run_lock(self.run_dir), owners.Owner(self.state, self.run_dir, self.persist) as owner:
            observed = owners.inspect(self.state, self.run_dir, self.workspace)
            self.assertEqual("running", observed["kind"])
            self.assertTrue(observed["alive"])
            self.assertIs(self.state, owners.project(self.state, observed))
            owner.result(0, ready=True)

    def test_deliberate_ready_receipt_is_bound_to_the_exact_checkpoint(self):
        self.state["stages"] = [{"stage": "requirements_gather"}]
        self.persist(self.run_dir / "state.json", self.state)
        with util.run_lock(self.run_dir), owners.Owner(self.state, self.run_dir, self.persist) as owner:
            owner.result(0, ready=True)
        self.assertEqual("ready", owners.inspect(self.state, self.run_dir, self.workspace)["kind"])
        altered = {**self.state, "next_stage": "terra"}
        with patch.object(processes, "process_table", return_value={}):
            observed = owners.inspect(altered, self.run_dir, self.workspace)
            self.assertEqual("ended", observed["kind"])
            reason = owners.project(altered, observed)["stop_reason"]
            self.assertIn("the last saved checkpoint", reason)
            self.assertNotIn("requirements_gather", reason)
            self.assertNotIn("exit code 0", reason)

    def test_reused_pid_never_establishes_a_live_controller(self):
        with util.run_lock(self.run_dir), owners.Owner(self.state, self.run_dir, self.persist) as owner:
            owner.result(0, ready=True)
        receipt = util.read(self.run_dir / owners.FILE)
        receipt.update(disposition="running", exit_code=None, exited_at=None)
        util.atomic_json(self.run_dir / owners.FILE, receipt)
        replaced = {**receipt["identity"], "birth_identity": receipt["identity"]["birth_identity"] + 1, "state": "S"}
        with patch.object(processes, "process_table", return_value={replaced["pid"]: replaced}):
            observed = owners.inspect(self.state, self.run_dir, self.workspace)
        self.assertEqual("ended", observed["kind"])
        self.assertIs(observed["alive"], False)

    def test_sibling_artifact_persistence_does_not_replace_the_state_checkpoint(self):
        with util.run_lock(self.run_dir), owners.Owner(self.state, self.run_dir, self.persist) as owner:
            before = (self.run_dir / owners.FILE).read_bytes()
            self.persist(self.run_dir / "stage-result.json", {"stage": "different", "status": "TASK_COMPLETE"})
            self.assertEqual(before, (self.run_dir / owners.FILE).read_bytes())
            self.state["next_stage"] = "terra"
            self.persist(self.run_dir / "state.json", self.state)
            self.assertEqual(util.digest(self.state), util.read(self.run_dir / owners.FILE)["checkpoint"])
            self.assertNotEqual(before, (self.run_dir / owners.FILE).read_bytes())
            owner.result(0, ready=True)

    def test_exception_after_ready_result_is_never_a_successful_handoff(self):
        for code in (0, 2):
            with self.subTest(code=code):
                self.state["status"] = "RUNNING"
                self.persist(self.run_dir / "state.json", self.state)
                with self.assertRaises(KeyboardInterrupt), util.run_lock(self.run_dir):
                    with owners.Owner(self.state, self.run_dir, self.persist) as owner:
                        owner.result(code, ready=True)
                        raise KeyboardInterrupt("Interrupted while returning")
                receipt = util.read(self.run_dir / owners.FILE)
                self.assertEqual("unexpected", receipt["disposition"])
                self.assertIsNone(receipt["exit_code"])
                self.assertEqual("KeyboardInterrupt", receipt["reason"])
                self.assertEqual(owners.STATUS, util.read(self.run_dir / "state.json")["status"])

    def test_owner_exit_does_not_replace_retained_attempt_recovery(self):
        self.state["active_stage"] = {"stage": "terra", "exit_code": 2}
        self.persist(self.run_dir / "state.json", self.state)
        with util.run_lock(self.run_dir), owners.Owner(self.state, self.run_dir, self.persist) as owner:
            owner.result(2)
        self.assertEqual("RUNNING", util.read(self.run_dir / "state.json")["status"])
        self.assertEqual("unexpected", util.read(self.run_dir / owners.FILE)["disposition"])

    def test_nonzero_return_retains_a_durable_pause_and_exit_event(self):
        for code, requested_ready in ((2, False), (5, True)):
            with self.subTest(code=code, requested_ready=requested_ready):
                self.state["status"] = "RUNNING"
                self.persist(self.run_dir / "state.json", self.state)
                with (
                    util.run_lock(self.run_dir),
                    owners.Owner(self.state, self.run_dir, self.persist) as owner,
                ):
                    owner.result(code, ready=requested_ready)
                self.assertEqual(owners.STATUS, util.read(self.run_dir / "state.json")["status"])
                self.assertEqual(code, util.read(self.run_dir / owners.FILE)["exit_code"])
                event = json.loads((self.run_dir / "activity.jsonl").read_text().splitlines()[-1])
                self.assertEqual(("controller_exit", code), (event["event"], event["exit_code"]))

    def test_legacy_projection_is_read_only_then_writer_saves_the_pause(self):
        before = (self.run_dir / "state.json").read_bytes()
        observed = owners.inspect(self.state, self.run_dir, self.workspace)
        self.assertEqual("ended", observed["kind"])
        self.assertEqual(owners.STATUS, owners.project(self.state, observed)["status"])
        self.assertEqual(before, (self.run_dir / "state.json").read_bytes())
        with util.run_lock(self.run_dir):
            owners.reconcile(self.state, self.run_dir, self.workspace, self.persist)
        self.assertEqual(owners.STATUS, util.read(self.run_dir / "state.json")["status"])

    def test_busy_run_lock_and_live_provider_never_prove_an_orphan(self):
        with util.run_lock(self.run_dir):
            observed = owners.inspect(self.state, self.run_dir, self.workspace)
            self.assertEqual("busy", observed["kind"])
            self.assertIs(self.state, owners.project(self.state, observed))
        checkout_lock = self.workspace / ".autocode/writer.lock"
        with checkout_lock.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed = owners.inspect(self.state, self.run_dir, self.workspace)
            self.assertEqual("busy", observed["kind"])
            self.assertIs(self.state, owners.project(self.state, observed))
        util.atomic_json(self.run_dir / "active-processes.json", {"processes": [{"pid": 123, "birth_identity": 1}]})
        with patch.object(processes, "recorded_worker_state", return_value={"checked": True, "alive": True}):
            observed = owners.inspect(self.state, self.run_dir, self.workspace)
            self.assertEqual("busy", observed["kind"])
            self.assertIs(self.state, owners.project(self.state, observed))
        with patch.object(processes, "recorded_worker_state", return_value={"checked": False, "alive": None}):
            observed = owners.inspect(self.state, self.run_dir, self.workspace)
            self.assertEqual("unknown", observed["kind"])
            self.assertEqual("PAUSED_PROCESS_CHECK", owners.project(self.state, observed)["status"])

    def test_unsaved_preview_does_not_replace_the_durable_ready_checkpoint(self):
        checkpoint = (self.run_dir / "state.json").read_bytes()
        with util.run_lock(self.run_dir), owners.Owner(self.state, self.run_dir, self.persist) as owner:
            # A rejected command can normalize a preview without saving it.
            self.state["unsaved_preview"] = "not durable state"
            owner.result(2, ready=True)
        self.assertEqual(checkpoint, (self.run_dir / "state.json").read_bytes())
        saved = json.loads(checkpoint)
        observed = owners.inspect(saved, self.run_dir, self.workspace)
        self.assertEqual("ready", observed["kind"])
        self.assertEqual(util.digest(saved), observed["receipt"]["checkpoint"])
        self.assertEqual(2, observed["receipt"]["exit_code"])

    def test_uncertain_inspection_and_malformed_receipt_never_prove_absence(self):
        with util.run_lock(self.run_dir), owners.Owner(self.state, self.run_dir, self.persist) as owner:
            owner.result(0, ready=True)
        with patch.object(processes, "process_table", side_effect=processes.ProcessError("Unavailable")):
            self.assertEqual("unknown", owners.inspect(self.state, self.run_dir, self.workspace)["kind"])
        (self.run_dir / owners.FILE).write_text('{"schema":1,"schema":1}')
        observed = owners.inspect(self.state, self.run_dir, self.workspace)
        self.assertEqual("unknown", observed["kind"])
        self.assertEqual("PAUSED_PROCESS_CHECK", owners.project(self.state, observed)["status"])

    def test_incomplete_or_malformed_ownership_metadata_cannot_prove_absence(self):
        with util.run_lock(self.run_dir), owners.Owner(self.state, self.run_dir, self.persist) as owner:
            owner.result(0, ready=True)
        valid = util.read(self.run_dir / owners.FILE)
        for changed in (
            None,
            {**valid, "schema": True},
            {**valid, "checkpoint": "not-a-digest"},
            {**valid, "id": "z" * 32},
        ):
            with self.subTest(receipt=changed):
                util.atomic_json(self.run_dir / owners.FILE, changed)
                self.assertEqual("unknown", owners.inspect(self.state, self.run_dir, self.workspace)["kind"])
        for key in valid:
            with self.subTest(missing=key):
                util.atomic_json(
                    self.run_dir / owners.FILE, {field: value for field, value in valid.items() if field != key}
                )
                self.assertEqual("unknown", owners.inspect(self.state, self.run_dir, self.workspace)["kind"])


class ControllerExitCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="controller-exit-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        bindir = self.root / "bin"
        bindir.mkdir()
        shutil.copy2(TOOLS / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.command = (sys.executable, "-B", str(TOOLS / "autocode.py"))

    def test_exit_after_completed_requirements_reopens_with_precise_recovery(self):
        fault = self.root / "fault.py"
        fault.write_text(
            textwrap.dedent(f"""\
            import os, sys
            from pathlib import Path
            sys.path.insert(0, {str(TOOLS)!r})
            import autocode
            persist = autocode.write_json
            def after_save(path, state):
                result = persist(path, state)
                if (Path(path).name == 'state.json' and state.get('status') == 'RUNNING'
                        and state.get('next_stage') == 'astra_discovery'
                        and any(row.get('stage') == 'requirements_gather' for row in state.get('stages', []))):
                    os._exit(71)
                return result
            autocode.write_json = after_save
            raise SystemExit(autocode.cli())
        """)
        )
        with self.assertRaises(taskrun.TaskRunError) as failure:
            taskrun.TaskRun.start(
                self.workspace,
                BRIEF,
                options=OPTIONS,
                command=(sys.executable, "-B", str(fault)),
                env=self.env,
                timeout=300,
            )
        self.assertEqual(71, failure.exception.process.returncode, failure.exception.process.stderr)
        run = taskrun.TaskRun.attach(self.workspace, command=self.command, env=self.env, timeout=300)
        checkpoint = (run.run_dir / "state.json").read_bytes()
        view = run.status()
        self.assertEqual(owners.STATUS, view["status"], view)
        self.assertEqual("resume", view["needs"]["kind"])
        self.assertIn("--resume-paused", view["next_command"])
        self.assertEqual("ended", view["controller"]["kind"])
        self.assertIsNone(view["controller"]["receipt"]["exit_code"], "An uncollected hard exit remains unknown")
        self.assertEqual("requirements_gather", view["controller"]["receipt"]["last_stage"])
        self.assertEqual(checkpoint, (run.run_dir / "state.json").read_bytes(), "Status is read-only")
        resumed = run.resume_paused()
        self.assertEqual("approve_plan", resumed["needs"]["kind"], resumed)

    def test_approval_and_unit_handoff_remain_ready_without_a_new_provider(self):
        run = taskrun.TaskRun.start(
            self.workspace,
            BRIEF,
            options=("--unit", "autoplanner"),
            start_options=OPTIONS,
            command=self.command,
            env=self.env,
            timeout=300,
        )
        waiting = run.status()
        approved = run.approve_plan(waiting["needs"]["token"])
        self.assertEqual("RUNNING", approved["status"], approved)
        self.assertEqual("ready", approved["controller"]["kind"])
        attempts = approved["usage"]["accounting"]["attempts"]
        checkpoint = (run.run_dir / "state.json").read_bytes()
        rejected = subprocess.run(
            [
                *self.command,
                "--workspace",
                str(self.workspace),
                "--run-dir",
                str(run.run_dir),
                "--answer",
                "obsolete-question=An invalid answer",
            ],
            env={**os.environ, **self.env},
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(2, rejected.returncode, rejected.stderr)
        self.assertIn("Input rejected:", rejected.stderr)
        unchanged = run.status()
        self.assertEqual("RUNNING", unchanged["status"], unchanged)
        self.assertEqual("ready", unchanged["controller"]["kind"])
        self.assertEqual(2, unchanged["controller"]["receipt"]["exit_code"])
        self.assertEqual(approved["approved_contract"], unchanged["approved_contract"])
        self.assertEqual(attempts, unchanged["usage"]["accounting"]["attempts"])
        self.assertEqual(checkpoint, (run.run_dir / "state.json").read_bytes())
        self.assertEqual(util.digest(json.loads(checkpoint)), unchanged["controller"]["receipt"]["checkpoint"])
        handoff = run.advance()
        self.assertEqual("ready", handoff["controller"]["kind"])
        self.assertEqual(attempts, handoff["usage"]["accounting"]["attempts"])

    def test_dead_retained_attempts_keep_the_existing_public_recovery_actions(self):
        run_dir = self.workspace / ".autocode/runs/retained-attempt"
        run_dir.mkdir(parents=True)
        for key in ("active_stage", "active_runner_check"):
            with self.subTest(attempt=key):
                record = {
                    "stage": "astra_challenge",
                    "iteration": 1,
                    "role": "astra",
                    "started_at": "now",
                    "pid": 2**22 - 1,
                    "processes": [{"pid": 2**22 - 1, "birth_identity": 1}],
                    "output": str(run_dir / "iterations/001/plan-reviewer-02.json"),
                    "events": str(run_dir / "iterations/001/plan-reviewer-02.jsonl"),
                }
                util.atomic_json(
                    run_dir / "state.json",
                    {
                        "version": 3,
                        "task": "Retained attempt",
                        "workspace": str(self.workspace),
                        "status": "RUNNING",
                        "iteration": 1,
                        "sessions": {},
                        "stages": [],
                        "next_stage": "astra_challenge",
                        key: record,
                    },
                )
                before = (run_dir / "state.json").read_bytes()
                result = subprocess.run(
                    [*self.command, "--workspace", str(self.workspace), "--run-dir", str(run_dir), "--status"],
                    env={**os.environ, **self.env},
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual("RUNNING", payload["status"], payload)
                self.assertTrue(payload["stale"], payload)
                self.assertEqual("ended", payload["view"]["controller"]["kind"])
                if key == "active_stage":
                    self.assertIn("AutoResolver must reconcile retained attempt", payload["next_action"])
                    self.assertIn(payload["attempt_id"], payload["next_action"])
                else:
                    self.assertEqual("Inspect the retained runner check and resume the run", payload["next_action"])
                self.assertEqual(before, (run_dir / "state.json").read_bytes(), "Status is read-only")


if __name__ == "__main__":
    unittest.main()
