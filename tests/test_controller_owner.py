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
import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_visual_profile as profiles

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

    def completed_attempt(self):
        output = self.run_dir / "completed-review.json"
        output.write_text("{}")
        self.state["active_stage"] = {
            "stage": "astra_challenge",
            "iteration": 1,
            "output": str(output),
            "finished_at": owners.now(),
            "duration_seconds": 1.0,
            "exit_code": 0,
            "accounted": True,
            "supervision": {"receipt": "controlled"},
        }
        self.persist(self.run_dir / "state.json", self.state)
        with util.run_lock(self.run_dir), owners.Owner(self.state, self.run_dir, self.persist) as owner:
            owner.result(1)
        return {
            "receipt": {"phase": "stopped", "cause": "provider_stopped", "cleanup_error": None},
            **{key: {"checked": True, "alive": False} for key in ("owner", "keeper", "provider")},
        }

    def test_completed_accounted_report_gets_resume_without_abandonment(self):
        for cause in ("provider_stopped", "controller_finished"):
            with self.subTest(cause=cause):
                observation = self.completed_attempt()
                observation["receipt"]["cause"] = cause
                before = (self.run_dir / "state.json").read_bytes()
                with patch.object(processes, "process_table", return_value={}):
                    with patch("autocode_supervision.observe", return_value=observation):
                        inspected = owners.inspect(self.state, self.run_dir, self.workspace)
                shown = owners.project(self.state, inspected)
                self.assertEqual(owners.STATUS, shown["status"])
                action = run_view.action(shown, run_dir=self.run_dir, workspace=self.workspace)
                self.assertEqual("resume", action["needs"]["kind"])
                self.assertIn("--resume-paused", action["next_command"])
                self.assertNotIn("--abandon-stage", action["next_command"])
                self.assertEqual(self.state["active_stage"], shown["active_stage"])
                self.assertEqual(before, (self.run_dir / "state.json").read_bytes(), "Inspection is read-only")

    def test_completed_report_requires_confirmed_terminal_supervision_and_accounting(self):
        observation = self.completed_attempt()
        for cause in (
            "unknown",
            "live",
            "cleanup",
            "unaccounted",
            "failed",
            "missing",
            "symlink",
            "interrupted",
            "malformed",
            "armed",
        ):
            with self.subTest(cause=cause):
                state = json.loads(json.dumps(self.state))
                seen = json.loads(json.dumps(observation))
                if cause == "unknown":
                    seen["keeper"] = {"checked": False, "alive": None}
                elif cause == "live":
                    seen["provider"]["alive"] = True
                elif cause == "cleanup":
                    seen["receipt"]["cleanup_error"] = "not confirmed"
                elif cause == "unaccounted":
                    state["active_stage"]["accounted"] = False
                elif cause == "failed":
                    state["active_stage"]["exit_code"] = 1
                elif cause == "missing":
                    state["active_stage"]["output"] = str(self.run_dir / "missing.json")
                elif cause == "interrupted":
                    seen["receipt"]["cause"] = "owner_lost"
                elif cause == "malformed":
                    seen["provider"] = None
                elif cause == "armed":
                    seen["receipt"]["phase"] = "armed"
                else:
                    alias = self.run_dir / "alias.json"
                    alias.symlink_to(state["active_stage"]["output"])
                    state["active_stage"]["output"] = str(alias)
                with patch.object(processes, "process_table", return_value={}):
                    with patch("autocode_supervision.observe", return_value=seen):
                        inspected = owners.inspect(state, self.run_dir, self.workspace)
                self.assertIs(state, owners.project(state, inspected))
                self.assertIsNone(run_view.action(state)["next_command"])

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

    def test_uncertain_exit_never_rewrites_or_becomes_ready(self):
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                before = (self.run_dir / "state.json").read_bytes()
                with self.assertRaises(util.JSONCommitUncertain), util.run_lock(self.run_dir):
                    with owners.Owner(self.state, self.run_dir, self.persist) as owner:
                        owner.result(0, ready=True)
                        raise util.JSONCommitUncertain(
                            OSError("controlled persistence uncertainty"),
                            candidate=self.state,
                            replaced=replaced,
                            durable=False,
                        )
                self.assertEqual(before, (self.run_dir / "state.json").read_bytes())
                receipt = owners.inspect(self.state, self.run_dir, self.workspace)["receipt"]
                self.assertEqual("unexpected", receipt["disposition"])
                self.assertIsNone(receipt["exit_code"])
                self.assertEqual("Checkpoint persistence outcome held", receipt["reason"])

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

    def test_large_launch_marker_keeps_birth_bound_worker_inspection(self):
        marker = self.run_dir / "active-processes.json"
        util.atomic_json(
            marker, {"command": ["provider", "x" * 32768], "processes": [{"pid": 123, "birth_identity": 1}]}
        )
        before = marker.read_bytes()
        for checked, alive, expected in ((True, False, "ended"), (True, True, "busy"), (False, None, "unknown")):
            with (
                self.subTest(expected=expected),
                patch.object(processes, "recorded_worker_state", return_value={"checked": checked, "alive": alive}),
            ):
                self.assertEqual(expected, owners.inspect(self.state, self.run_dir, self.workspace)["kind"])
                self.assertEqual(before, marker.read_bytes())
        marker.write_text("x" * (owners.MAX_PROCESS_MARKER_BYTES + 1))
        self.assertEqual("unknown", owners.inspect(self.state, self.run_dir, self.workspace)["kind"])
        marker.unlink()
        # The controller receipt itself keeps the original small bound.
        (self.run_dir / owners.FILE).write_text("x" * (owners.MAX_BYTES + 1))
        self.assertEqual("unknown", owners.inspect(self.state, self.run_dir, self.workspace)["kind"])

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

    def test_exit_with_completed_accounted_report_resumes_without_replaying_provider(self):
        marker = self.root / "provider-stages.jsonl"
        provider = self.root / "bin/codex"
        implementation = provider.read_text()
        point = '    repairing = bool(data.get("report_repair"))\n'
        self.assertEqual(1, implementation.count(point))
        provider.write_text(
            implementation.replace(
                point,
                f"    with open({str(marker)!r}, 'a') as calls: calls.write(json.dumps(stage) + '\\n')\n" + point,
                1,
            )
        )
        fault = self.root / "fault-completed.py"
        fault.write_text(
            textwrap.dedent(f"""\
            import os, sys
            from pathlib import Path
            sys.path.insert(0, {str(TOOLS)!r})
            import autocode
            apply = autocode.apply_result
            def before_apply(state, stage, value, record, workspace, run_dir, **kwargs):
                if (stage == 'requirements_gather' and record.get('finished_at')
                        and record.get('accounted') is True and record.get('exit_code') == 0):
                    assert Path(record['output']).is_file(), 'Fault must occur after report materialization'
                    os._exit(72)
                return apply(state, stage, value, record, workspace, run_dir, **kwargs)
            autocode.apply_result = before_apply
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
        self.assertEqual(72, failure.exception.process.returncode, failure.exception.process.stderr)
        run = taskrun.TaskRun.attach(self.workspace, command=self.command, env=self.env, timeout=300)
        before = (run.run_dir / "state.json").read_bytes()
        stopped = run.status()
        self.assertEqual(owners.STATUS, stopped["status"], stopped)
        self.assertTrue(stopped["controller"]["completed_stage"])
        self.assertIn("--resume-paused", stopped["next_command"])
        self.assertNotIn("--abandon-stage", stopped["next_command"])
        self.assertEqual(before, (run.run_dir / "state.json").read_bytes())
        resumed = run.resume_paused()
        self.assertEqual("approve_plan", resumed["needs"]["kind"], resumed)
        calls = [json.loads(line) for line in marker.read_text().splitlines()]
        self.assertEqual(1, calls.count("requirements_gather"), "A collected report must not replay its provider")

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


class NativeOwnerPersistenceCliTests(unittest.TestCase):
    """Public CLI fault controls; the native origin is controlled, not qualified."""

    setUp = ControllerExitCliTests.setUp

    def assert_uncertain_native_checkpoint(self, *, replaced):
        # Only origin preparation and the action under test are controlled. The
        # original CLI, Owner, PlanningCalls, ParentSink and commit filesystem
        # boundary run; no provider or native/source qualification is claimed.
        profile = {
            "version": 1,
            "mode": "isolated-builtin-openai",
            "opencode_version": "1.18.33",
            "model": "openai/gpt-6-sol",
            "reasoning_effort": "high",
            "sdk_path": ".autocode/sdk",
            "sdk_manifest_sha256": "a" * 64,
            "transport_identity_sha256": "b" * 64,
            "executable_sha256": "c" * 64,
            "max_requests": 8,
            "image_limits": dict(profiles.IMAGE_BOUNDS),
        }
        fault = self.root / "native-persistence-fault.py"
        fault.write_text(
            textwrap.dedent(f"""\
            import os, stat, sys
            from pathlib import Path
            from types import SimpleNamespace
            from unittest.mock import patch
            sys.path.insert(0, {str(TOOLS)!r})
            import autocode as runner
            import autocode_native_launch as launch
            import autocode_visual_factory as factories
            import autocode_util as util

            def controlled_origin(workspace, run_dir, **options):
                (Path(run_dir) / 'visual-native-protection').mkdir(exist_ok=True)
                factory = object.__new__(factories.NativeVisualFactory)
                factory.bind_parent_sink = lambda sink, kind: None
                factory.invalidate_parent_ack = lambda: None
                factory.require_planning_origin = lambda state, *, snapshot: None
                return SimpleNamespace(factory=factory)

            def fail_action(dependencies, args, parser, state, state_path, run_dir, workspace):
                if args.run_dir:
                    raise AssertionError('Retained parent hold reached an action')
                path, directory = Path(state_path), Path(run_dir)
                (directory / 'controlled-action-entered').write_text('once\\n')
                candidate = dict(state, phase='CONTROLLED_PARENT_CHECKPOINT')
                real_replace, real_fsync = util.os.replace, util.os.fsync
                owned = directory.stat()

                def fail_replace(source, destination, *values, **options):
                    if Path(destination) == path:
                        raise OSError('controlled parent replace refused')
                    return real_replace(source, destination, *values, **options)

                def fail_directory_fsync(descriptor):
                    actual = os.fstat(descriptor)
                    if stat.S_ISDIR(actual.st_mode) and (
                        actual.st_dev, actual.st_ino
                    ) == (owned.st_dev, owned.st_ino):
                        raise OSError('controlled parent directory fsync refused')
                    return real_fsync(descriptor)

                try:
                    operation = 'fsync' if {replaced!r} else 'replace'
                    failure = fail_directory_fsync if {replaced!r} else fail_replace
                    with patch.object(util.os, operation, side_effect=failure):
                        dependencies.write_json(path, candidate)
                except util.JSONCommitUncertain as error:
                    if error.replaced is not {replaced!r}:
                        raise AssertionError('Wrong original replacement outcome') from error
                    (directory / 'expected-original-checkpoint.bin').write_bytes(path.read_bytes())
                    raise
                raise AssertionError('Commit fault did not reach the original boundary')

            launch.NativeRunContext = controlled_origin
            runner.run_actions.handle = fail_action
            raise SystemExit(runner.cli())
        """)
        )
        command = (sys.executable, "-B", str(fault))
        run = taskrun.TaskRun.start(
            self.workspace,
            BRIEF + "\n" + profiles.MARKER + json.dumps(profile),
            options=OPTIONS,
            command=command,
            env=self.env,
            timeout=300,
        )
        self.assertEqual(2, run.last_advance.returncode, run.last_advance.stderr)
        self.assertIn("Parent replacement outcome held", run.last_advance.stderr)
        checkpoint = (run.run_dir / "expected-original-checkpoint.bin").read_bytes()
        self.assertEqual(checkpoint, (run.run_dir / "state.json").read_bytes())
        view = run.status()
        receipt = view["controller"]["receipt"]
        self.assertEqual(("unexpected", 2), (receipt["disposition"], receipt["exit_code"]))
        self.assertEqual("Checkpoint persistence outcome held", receipt["reason"])
        self.assertEqual(util.digest(json.loads(checkpoint)), receipt["checkpoint"])
        self.assertFalse(view["done"])
        owner_bytes = (run.run_dir / owners.FILE).read_bytes()
        self.assertEqual(checkpoint, (run.run_dir / "state.json").read_bytes(), "Status must stay read-only")
        with self.assertRaises(taskrun.TaskRunError) as refused:
            run.advance()
        self.assertIn("Native parent sink is held", refused.exception.process.stderr)
        self.assertEqual(checkpoint, (run.run_dir / "state.json").read_bytes(), "Guard must precede reconciliation")
        self.assertEqual(owner_bytes, (run.run_dir / owners.FILE).read_bytes(), "No new owner before native guard")
        self.assertEqual("once\n", (run.run_dir / "controlled-action-entered").read_text())

    def test_native_replace_failure_retains_checkpoint_and_owner_boundary(self):
        self.assert_uncertain_native_checkpoint(replaced=False)

    def test_native_directory_fsync_failure_retains_checkpoint_and_owner_boundary(self):
        self.assert_uncertain_native_checkpoint(replaced=True)


if __name__ == "__main__":
    unittest.main()
