"""Additional execution-boundary scenarios with real externally crashed processes."""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_builder_policy as builder_policy
import autocode_process as processes

from . import build_timeout_fault_fixture as timeout_fault
from . import test_build_blackbox as bb

# Characters of each output a startup failure keeps, as BuildBlackbox.invoke keeps a timed-out call's (#1058).
OUTPUT_TAIL = 1500


def startup_failure(facts, returncode, stdout, stderr):
    """The message for a controller that started no Builder in time, with its own bounded output (#994).

    returncode None means the controller was still running when its cleanup bound ran out.
    """
    report = dict(facts, returncode=returncode, stdout_tail=stdout[-OUTPUT_TAIL:], stderr_tail=stderr[-OUTPUT_TAIL:])
    return f"No Builder started within {facts['bound']} s of the controller's launch:\n{json.dumps(report, indent=1)}"


class RecoveryBlackbox(unittest.TestCase):
    setUp = bb.BuildBlackbox.setUp
    command = bb.BuildBlackbox.command
    invoke = bb.BuildBlackbox.invoke
    seed = bb.BuildBlackbox.seed
    state = bb.BuildBlackbox.state
    events = bb.BuildBlackbox.events
    build = bb.BuildBlackbox.build
    candidate = bb.BuildBlackbox.candidate

    def fault(self, kind):
        hooks = self.root / "hooks"
        hooks.mkdir()
        shutil.copy2(self.source / "build_audit_fault_hooks.py", hooks / "sitecustomize.py")
        self.env.update(PYTHONPATH=str(hooks), BUILD_AUDIT_CONTROLLER_FAULT=kind, BUILD_AUDIT_FAULT_ROOT=str(self.root))

    def until(self, found, message, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = found()
            if value:
                return value
            time.sleep(0.02)
        self.fail(message)

    def worker_row(self, milestone):
        return next(r for r in self.state()["orchestration_batch"]["workers"] if r["milestone_id"] == milestone)

    def lifeline_receipt(self, row):
        path = Path(row["supervision"]["receipt"])
        return json.loads(path.read_text()) if path.is_file() else {}

    def await_writer(self, controller, seconds):
        """None once a Builder has started; else, at the bound or the controller's exit, how far it got (#994).

        A Builder starts only under a live controller, so its exit ends the wait early. The facts are read
        before the test releases anything and are bounded, so the failure stays diagnosable after the
        fixture, with the controller log saved in it, is removed.
        """
        started = time.monotonic()
        while True:
            exited = controller.poll() is not None  # read first: a Builder started just before the exit counts
            try:
                if self.events():
                    return None
            except ValueError:
                pass  # a Builder is appending its start line right now; the next read sees it whole
            waited = time.monotonic() - started
            if exited or waited >= seconds:
                return self.startup_facts(controller, seconds, waited)
            time.sleep(0.02)

    def startup_facts(self, controller, seconds, waited):
        """What the run had published when no Builder had started: events, run status and each worker's log."""
        facts = dict(bound=seconds, waited=round(waited, 1), controller_exit=controller.poll())
        try:
            lines = (self.root / "events.jsonl").read_text().splitlines()
        except OSError as error:
            lines = [repr(error)]
        events = []
        for line in lines:
            try:
                row = json.loads(line)
                events.append(f"{row.get('stage')} {row.get('event')} {row.get('milestone') or '-'}")
            except (ValueError, AttributeError):
                events.append(line[:200])  # a line still being appended, or not an event
        facts.update(event_count=len(events), last_events=events[-10:])
        try:
            state = self.state()
        except (OSError, ValueError) as error:
            facts["state"] = repr(error)
            return facts
        facts["state"] = {key: str(state.get(key))[:500] for key in ("status", "phase", "stop_reason")}
        workers = []
        for row in ((state.get("orchestration_batch") or {}).get("workers") or [])[:6]:
            log = Path(row["run_dir"], "worker.log") if row.get("run_dir") else None
            workers.append(
                dict(
                    milestone=row.get("milestone_id"),
                    status=row.get("status"),
                    exit_code=row.get("exit_code"),
                    log_tail=log.read_text(errors="replace")[-500:] if log and log.is_file() else None,
                )
            )
        facts["workers"] = workers
        return facts

    def finish(self, controller, seconds, not_started):
        """The controller's output within its cleanup bound; a startup failure's facts outlive that bound (#994)."""
        try:
            return controller.communicate(timeout=seconds)
        except subprocess.TimeoutExpired as error:
            if not_started is None:
                raise
            stdout, stderr = (
                part.decode(errors="replace") if isinstance(part, bytes) else part or ""
                for part in (error.stdout, error.stderr)
            )
            raise AssertionError(startup_failure(not_started, None, stdout, stderr)) from error

    def test_08_live_guarded_worker_blocks_second_writer(self):
        # A worker whose keeper has not yet acted on owner loss (frozen here) still owns its milestone (#454).
        self.seed()
        self.env["BUILD_AUDIT_FAULT"] = "hold"
        controller = subprocess.Popen(
            self.command("autocode_build", ["--run-dir", str(self.run), "--no-chat"]),
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        owned = []  # psutil handles keep birth identity: cleanup never signals a reused PID

        def capture():
            if controller.poll() is None:  # unreaped: its PID cannot have been reused
                with contextlib.suppress(processes.psutil.Error):
                    owned.extend(processes.psutil.Process(controller.pid).children(recursive=True))

        try:
            provider = self.until(
                lambda: next((e for e in self.events() if e["milestone"] == "M1"), None), "M1 provider never started"
            )

            def siblings_finished():
                # A child publishes result.json before its controller adopts it and it exits.
                # Kill only after both unrelated siblings are recorded complete by the owner.
                status = json.loads(self.invoke("autocode", ["--run-dir", str(self.run), "--status"]).stdout)
                workers = (status.get("orchestration_batch") or {}).get("workers", [])
                finished = {row["milestone_id"] for row in workers if row["status"] == "BUILT"}
                return {"M2", "M3"} <= finished

            self.until(siblings_finished, "Controller never recorded M2 and M3 complete")
            row = self.worker_row("M1")
            self.assertIn("supervision", row, "M1 was launched without a lifeline")

            def retained():
                receipt = self.lifeline_receipt(row)
                return receipt if any(p["pid"] == provider["pid"] for p in receipt.get("processes", [])) else None

            receipt = self.until(retained, "M1's keeper never retained its provider")
            capture()
            keeper = processes.psutil.Process(receipt["keeper"]["pid"])
            worker = processes.psutil.Process(receipt["provider"]["pid"])
            self.assertTrue(processes.live_processes([receipt["keeper"], receipt["provider"]]))
            keeper.suspend()
            controller.kill()
            stdout, stderr = controller.communicate(timeout=10)
            (self.root / "killed-controller.log").write_text(stdout + stderr)
            refused = self.invoke(
                "autocode_build",
                ["--run-dir", str(self.run), "--no-chat", "--resume-paused", "--retry-builder", "M1"],
                2,
            )
            self.assertIn("PAUSED_ORCHESTRATOR_WORKERS", refused.stderr)
            self.assertEqual(1, sum(e["milestone"] == "M1" for e in self.events()), "Duplicate writer was launched")
            self.assertTrue(worker.is_running(), "The guarded worker was stopped before its keeper acted")
            keeper.resume()
            self.until(
                lambda: self.lifeline_receipt(row).get("phase") == "stopped", "Keeper never cleaned up after owner loss"
            )
            final = self.lifeline_receipt(row)
            self.assertEqual(("owner_lost", None), (final["cause"], final["cleanup_error"]), final)
            # The keeper publishes its receipt, then exits.
            self.until(
                lambda: not processes.live_processes([receipt["keeper"], receipt["provider"]]),
                "The keeper or its worker outlived the recorded cleanup",
                seconds=10,
            )
            (self.root / "release").touch()
            self.build(extra=["--resume-paused", "--retry-builder", "M1"])
            self.candidate()
            self.assertEqual(2, sum(e["milestone"] == "M1" for e in self.events()))
        finally:
            # Also when the test fails before its barrier: the tree is taken before the controller dies,
            # and waited for, so nothing writes into the test's directory once it is removed.
            capture()
            if controller.poll() is None:
                controller.kill()
            (self.root / "release").touch()
            for process in owned:
                try:
                    process.resume()
                    process.kill()  # psutil checks birth identity before signalling
                except processes.psutil.Error:
                    pass
            processes.psutil.wait_procs(owned, timeout=10)
            controller.communicate(timeout=10)

    def test_08_actual_timeout_then_controller_crash_keeps_live_writer_exclusive(self):
        spec = bb.plan(
            [([], "Create output.txt containing done", ["output.txt"])],
            {"M1": {"output.txt": "done"}},
            {"M1": "from pathlib import Path; assert Path('output.txt').read_text()=='done'"},
            "Verify timeout is not proof of termination",
        )
        self.seed(spec)
        # Reach the Builder through the public checkpoint before applying its one-second limit.
        self.build(2, extra=["--pause-after-stage"])
        self.assertEqual([], self.events())
        hooks = timeout_fault.prepare(self.root, self.source)
        env = dict(
            self.env, PYTHONPATH=str(hooks), BUILD_AUDIT_FAULT="hold_timeout", BUILD_AUDIT_TIMEOUT_ROOT=str(self.root)
        )
        controller = subprocess.Popen(
            self.command(
                "autocode_build",
                ["--run-dir", str(self.run), "--no-chat", "--resume-paused", "--max-stage-seconds", "1"],
            ),
            cwd=self.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        owned = []
        try:
            deadline = time.monotonic() + 35
            ready = [self.root / name for name in ("provider-ready", "controller-deadline-ready")]
            while not all(path.exists() for path in ready) and time.monotonic() < deadline:
                self.assertIsNone(controller.poll(), "Controller exited before the Builder timeout barrier")
                time.sleep(0.01)
            self.assertTrue(all(path.exists() for path in ready), "Held Builder and actual watchdog did not start")
            starts = self.events()
            self.assertEqual(1, len(starts))
            self.assertEqual("M1", starts[0]["milestone"])
            self.assertEqual(starts[0]["pid"], int(ready[0].read_text()))
            self.assertEqual(controller.pid, int(ready[1].read_text()))
            # Advance the real deadline callbacks only after the correct provider is held.
            timeout_fault.advance(self.root, 2)
            publications = [self.root / name for name in ("controller-timeout.json", "keeper-timeout.json")]
            while not all(path.exists() for path in publications) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(all(path.exists() for path in publications), "Actual timeout was not durably published")
            timeout, receipt = [json.loads(path.read_text()) for path in publications]
            self.assertEqual(("terra", "stage"), (timeout["stage"], timeout["activity"]["timeout_kind"]))
            self.assertEqual(("stopping", "stage_deadline"), (receipt["phase"], receipt["cause"]))
            metadata = timeout["supervision"]
            self.assertEqual(metadata, {key: receipt[key] for key in metadata})
            self.assertEqual(controller.pid, metadata["owner"]["pid"])
            self.assertEqual(starts[0]["pid"], metadata["provider"]["pid"])
            owned = [metadata["keeper"], metadata["provider"]]
            self.assertTrue(all(bb._alive(row) for row in owned))
            # A stopped keeper explicitly models unavailable cleanup; normal keepers kill timed-out writers.
            os.kill(owned[0]["pid"], signal.SIGSTOP)
            controller.kill()
            stdout, stderr = controller.communicate(timeout=5)
            (self.root / "timeout-controller.log").write_text(stdout + stderr)
            self.assertEqual(-signal.SIGKILL, controller.returncode)
            self.assertTrue(bb._alive(owned[1]))
            os.kill(owned[1]["pid"], signal.SIGCONT)
            self.build(2, extra=["--resume-paused"])
            self.assertTrue(bb._alive(owned[1]), "The held writer disappeared before exclusivity was checked")
            self.assertEqual(
                starts, self.events(), "A second writer started while the timed-out provider remained alive"
            )
            status = self.invoke("autocode", ["--run-dir", str(self.run), "--status"])
            public = json.loads(status.stdout)
            view = public["view"]
            # The live attempt stays RUNNING; liveness identifies its interrupted controller.
            self.assertEqual("RUNNING", view["status"])
            self.assertEqual({"checked": True, "alive": False}, view["liveness"]["owner"])
            self.assertEqual({"checked": True, "alive": True}, view["liveness"]["keeper"])
            self.assertEqual(("interrupted", "stage_deadline"), (view["liveness"]["kind"], view["liveness"]["reason"]))
            self.assertFalse(view["done"])
            self.assertTrue(view["liveness"]["provider"]["alive"])
            self.assertNotIn("autocode", public["unit_handoffs"])
        finally:
            # Release the provider and both cleanup threads, including failures before marker collection.
            (self.root / "release").touch()
            (self.root / "cleanup-release").touch()
            for row in bb.supervised_processes(self.root):
                if bb._alive(row):
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(
                            row["pid"], signal.SIGCONT
                        )  # cleanup only; disappearance never satisfies a test assertion
            if controller.poll() is None:
                controller.kill()
                controller.communicate(timeout=5)
            bb.await_supervised_exit(self.root)

    def test_10_crash_before_spawn_recovery_launches_once(self):
        self.seed()
        self.fault("before_worker")
        self.invoke("autocode_build", ["--run-dir", str(self.run), "--no-chat"], 97)
        self.assertEqual([], self.events())
        self.build(extra=["--resume-paused"])
        self.candidate()
        self.assertEqual(3, len(self.events()))

    def test_11_crash_after_spawn_before_pid_checkpoint(self):
        self.seed()
        self.fault("after_worker")
        self.invoke("autocode_build", ["--run-dir", str(self.run), "--no-chat"], 97)
        # The worker launched just before the crash finds its Orchestrator gone and does no work (#454).
        directory = Path(self.worker_row("M1")["run_dir"])
        log = directory / "worker.log"
        self.until(
            lambda: log.is_file() and "Builder lifeline:" in log.read_text(),
            "Orphaned worker did not refuse its lifeline",
        )
        self.assertEqual([], self.events())
        self.assertFalse((directory / "result.json").exists())
        self.build(extra=["--resume-paused"])
        self.candidate()
        self.assertEqual(3, len(self.events()))
        self.assertEqual(1, sum(e["milestone"] == "M1" for e in self.events()))

    def test_12_crash_after_patch_application_is_not_reapplied(self):
        self.seed()
        self.fault("after_integration")
        self.invoke("autocode_build", ["--run-dir", str(self.run), "--no-chat"], 97)
        before = {p: (self.project / p).read_bytes() for paths in self.spec["payloads"].values() for p in paths}
        self.build(extra=["--resume-paused"])
        self.candidate()
        self.assertEqual(3, len(self.events()))
        self.assertEqual(before, {p: (self.project / p).read_bytes() for p in before})

    def test_14_conflict_during_integration_preserves_both_sides(self):
        self.seed()
        self.fault("integration_conflict")
        self.build(2)
        self.assertEqual("USER_CONFLICT = True\n", (self.project / "server/health.py").read_text())
        self.assertEqual(3, len(self.events()))
        self.assertTrue(list(self.project.glob(".autocode/builders/*/*/server/health.py")))
        self.assertNotIn("autocode", self.state()["unit_handoffs"])

    def parent_edit_preserved(self, name):
        self.seed()
        self.fault("parent_edit_before_collection")
        self.env["BUILD_AUDIT_PARENT_EDIT"] = name
        self.build(2)
        self.assertTrue(
            (self.root / "fault-parent_edit_before_collection").is_file(),
            (self.root / f"cli-{self.counter}.json").read_text(),
        )
        self.assertEqual("User-authored content must survive\n", (self.project / name).read_text())
        self.assertTrue(list(self.project.glob(".autocode/builders/*/*/server/health.py")))
        self.assertNotIn("autocode", self.state().get("unit_handoffs", {}))
        before = self.events()
        self.build(2, extra=["--resume-paused"])
        self.assertEqual(before, self.events(), "Drift recovery must not replay successful Builders")
        self.assertEqual("User-authored content must survive\n", (self.project / name).read_text())
        backup = self.root / "user-preserved.txt"
        (self.project / name).rename(backup)  # The operator, not the runner, reconciles the parent edit.
        self.build(extra=["--resume-paused"])
        self.candidate()
        self.assertEqual(before, self.events())
        self.assertEqual("User-authored content must survive\n", backup.read_text())

    def test_new_user_file_at_builder_output_path_is_preserved(self):
        self.parent_edit_preserved("server/health.py")

    def test_new_user_file_under_builder_owned_directory_is_preserved(self):
        self.parent_edit_preserved("server/user-notes.txt")

    def deferred_retirement_crash(self, fault):
        spec = bb.independent()
        spec["contract"]["milestones"][2]["depends_on"] = ["M1", "M2"]
        strong = builder_policy.DEFAULTS["strong_model"]
        self.seed(spec, checker=strong)
        self.env.update(BUILD_AUDIT_FAULT="escalate_success", BUILD_AUDIT_FAULT_MILESTONES="M1,M2")
        self.fault(fault)
        self.invoke("autocode_build", ["--run-dir", str(self.run), "--no-chat"], 97)
        before = self.events()
        self.assertEqual(["gpt-6-luna"] * 4, [event["model"] for event in before])
        self.assertNotIn("autocode", self.state().get("unit_handoffs", {}))
        return before

    def deferred_retirement_recovery(self, fault):
        before = self.deferred_retirement_crash(fault)
        strong = "gpt-6-sol"
        self.build(extra=["--resume-paused"])
        self.candidate()
        self.assertEqual(before, self.events()[: len(before)])
        self.assertEqual(
            ["gpt-6-luna", "gpt-6-luna", strong],
            [event["model"] for event in self.events() if event["milestone"] == "M1"],
        )
        self.assertEqual(["gpt-6-luna"] * 2, [event["model"] for event in self.events() if event["milestone"] == "M2"])
        history = self.state()["orchestration_history"]
        self.assertEqual(1, len(history))
        for row in history[0]["workers"]:
            self.assertTrue((Path(row["run_dir"]) / "state.json").is_file())
            self.assertFalse((Path(row["workspace"]) / ".git").exists())
        after = self.events()
        self.build()
        self.assertEqual(after, self.events())
        self.assertEqual(history, self.state()["orchestration_history"])
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        self.build()
        self.candidate()
        for milestone in ("M1", "M2"):
            self.assertEqual(
                ["gpt-6-luna", "gpt-6-luna", strong],
                [event["model"] for event in self.events() if event["milestone"] == milestone],
            )

    def test_all_deferred_batch_resumes_during_worktree_retirement(self):
        self.deferred_retirement_recovery("during_retirement")

    def test_all_deferred_batch_resumes_after_worktree_retirement(self):
        self.deferred_retirement_recovery("after_retirement")

    def test_deferred_retirement_preserves_parent_edits_on_resume(self):
        before = self.deferred_retirement_crash("during_retirement")
        edit = self.project / "user.txt"
        edit.write_text("Keep the parent edit\n")
        self.build(2, extra=["--resume-paused"])
        self.assertEqual("Keep the parent edit\n", edit.read_text())
        self.assertEqual(before, self.events())
        edit.rename(self.root / "preserved-parent-edit.txt")
        self.build(extra=["--resume-paused"])
        self.candidate()

    def test_deferred_retirement_preserves_surviving_checkout_edits(self):
        before = self.deferred_retirement_crash("during_retirement")
        row = next(
            row for row in self.state()["orchestration_batch"]["workers"] if (Path(row["workspace"]) / ".git").exists()
        )
        edit = Path(row["workspace"]) / "user.txt"
        edit.write_text("Keep the worker edit\n")
        self.build(2, extra=["--resume-paused"])
        self.assertEqual("Keep the worker edit\n", edit.read_text())
        self.assertEqual(before, self.events())
        edit.rename(self.root / "preserved-worker-edit.txt")
        self.build(extra=["--resume-paused"])
        self.candidate()

    def test_revised_plan_preserves_edits_then_supersedes_deferred_batch(self):
        before = self.deferred_retirement_crash("during_retirement")
        old = self.state()["orchestration_batch"]
        row = next(row for row in old["workers"] if (Path(row["workspace"]) / ".git").exists())
        edit = Path(row["workspace"]) / "user.txt"
        edit.write_text("Keep the worker edit across plan replacement\n")
        revised = dict(self.spec["contract"])
        revised["constraints"] = [*revised["constraints"], "Keep the health response compatible"]
        path = self.root / "revision-two.json"
        path.write_text(json.dumps(revised))
        self.env.pop("BUILD_AUDIT_FAULT")
        self.env.pop("BUILD_AUDIT_FAULT_MILESTONES")
        self.invoke("autoplanner", ["--run-dir", str(self.run), "--edit-goal", str(path), "--no-chat"])
        self.invoke(
            "autoplanner", ["--run-dir", str(self.run), "--approve-goal", self.state()["displayed_goal"], "--no-chat"]
        )
        self.original_contract = self.state()["goal_contract"]
        self.build(2)
        self.assertEqual("Keep the worker edit across plan replacement\n", edit.read_text())
        self.assertEqual(before, self.events())
        edit.rename(self.root / "preserved-worker-edit.txt")
        self.build(extra=["--resume-paused"])
        self.candidate()
        self.assertEqual(
            ["SUPERSEDED", "INTEGRATED"], [batch["status"] for batch in self.state()["orchestration_history"]]
        )
        self.assertEqual(["gpt-6-luna"] * 2, [event["model"] for event in self.events()[len(before) :]])
        for row in old["workers"]:
            self.assertTrue((Path(row["run_dir"]) / "state.json").is_file())

    def test_integrated_batch_resumes_during_worktree_retirement(self):
        self.seed()
        self.fault("during_retirement")
        self.invoke("autocode_build", ["--run-dir", str(self.run), "--no-chat"], 97)
        before = self.events()
        self.build(extra=["--resume-paused"])
        self.candidate()
        self.assertEqual(before, self.events())
        for row in self.state()["orchestration_history"][0]["workers"]:
            self.assertTrue((Path(row["run_dir"]) / "state.json").is_file())

    def test_17_behavioral_drift_five_retries_fails_three_retry_contract(self):
        spec = bb.plan(
            [([], "Set MAX_RETRIES to exactly 3", ["retry.py"])],
            {"M1": {"retry.py": "MAX_RETRIES = 5\n"}},
            {"M1": "from retry import MAX_RETRIES; assert MAX_RETRIES == 3"},
            "Configure a strict maximum of three retries",
        )
        self.seed(spec)
        self.build()
        self.candidate()
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        self.assertEqual("FAIL", self.state()["validation"]["verdict"])
        self.assertNotEqual("TASK_COMPLETE", self.state()["status"])
        self.assertEqual(self.original_contract, self.state()["goal_contract"])

    def test_30_duplicate_durable_result_consumed_once_after_crash(self):
        self.seed()
        self.fault("after_integration")
        self.invoke("autocode_build", ["--run-dir", str(self.run), "--no-chat"], 97)
        for p in self.project.glob(".autocode/builders/*/*/.autocode/runs/*/result.json"):
            p.write_bytes(p.read_bytes())  # Same delivery repeated; no edited identities.
        self.build(extra=["--resume-paused"])
        self.candidate()
        first = self.state()["orchestration_history"]
        self.build()
        self.assertEqual(first, self.state()["orchestration_history"])
        attempts = [r["worker_attempt"] for r in self.state()["stages"] if r.get("worker_attempt")]
        self.assertEqual(len(attempts), len(set(attempts)))

    def test_31_late_failed_attempt_result_cannot_replace_integrated_retry(self):
        self.seed()
        self.env["BUILD_AUDIT_FAULT"] = "crash"
        self.build(2)
        worker = next(w for w in self.state()["orchestration_batch"]["workers"] if w["milestone_id"] == "M1")
        receipt = Path(worker["run_dir"]) / "result.json"
        old_receipt = receipt.read_bytes()
        self.env.pop("BUILD_AUDIT_FAULT")
        self.build(extra=["--resume-paused", "--retry-builder", "M1"])
        candidate = self.candidate()
        source = (self.project / "server/health.py").read_bytes()
        receipt.write_bytes(old_receipt)
        self.build()
        self.assertEqual(candidate, self.candidate())
        self.assertEqual(source, (self.project / "server/health.py").read_bytes())
        self.assertEqual(4, len(self.events()))

    def test_19_permission_denial_preserved_on_next_handoff(self):
        spec = bb.plan(
            [([], "Write the assigned health function only if permitted", ["server/health.py"])],
            {"M1": {"server/health.py": "def health(): return {'status':'ok'}\n"}},
            {"M1": "from server.health import health; assert health()['status']=='ok'"},
            "Implement a scoped health function",
        )
        self.seed(spec)
        self.env["BUILD_AUDIT_FAULT"] = "permission"
        self.build()
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"], 2)
        state = self.state()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        qid = state["pending_questions"][0]["id"]
        token = state["resolver_human_request"]["request_token"]  # answers name the published request
        self.invoke(
            "autocode_build",
            [
                "--run-dir",
                str(self.run),
                "--answer",
                qid + "=No. Do not access any external account.",
                "--resolver-token",
                token,
                "--no-chat",
            ],
        )
        self.assertIn("No. Do not access", json.dumps(self.state()["answers"]))
        self.assertEqual(self.original_contract, self.state()["goal_contract"])
        self.assertFalse((self.project / "server/health.py").exists())
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"], 2)
        self.assertFalse((self.project / "server/health.py").exists())
        self.assertEqual([], self.state().get("pending_questions", []), "Repeated request asked the user again")

    def test_32_plan_cannot_mutate_active_workers_and_revision_invalidates_old_approval(self):
        self.seed()
        self.env["BUILD_AUDIT_FAULT"] = "hold"
        revised = dict(self.spec["contract"])
        revised["constraints"] = [*revised["constraints"], "Revision two: keep existing health response"]
        path = self.root / "revision-two.json"
        path.write_text(json.dumps(revised))
        process = subprocess.Popen(
            self.command("autocode_build", ["--run-dir", str(self.run), "--no-chat"]),
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        not_started = None
        try:
            not_started = self.await_writer(process, 20)
            if not_started is None:
                self.invoke("autoplanner", ["--run-dir", str(self.run), "--edit-goal", str(path), "--no-chat"], 2)
                self.assertEqual(self.original_contract, self.state()["goal_contract"])
        finally:
            (self.root / "release").touch()
            stdout, stderr = self.finish(process, 40, not_started)
            (self.root / "active-revision.log").write_text(stdout + stderr)
        if not_started is not None:  # reported only now, with the controller's own output (#994)
            self.fail(startup_failure(not_started, process.returncode, stdout, stderr))
        self.assertEqual(0, process.returncode, stdout + stderr)
        self.invoke("autoplanner", ["--run-dir", str(self.run), "--edit-goal", str(path), "--no-chat"])
        state = self.state()
        self.assertNotEqual(self.original_contract["hash"], state["goal_contract"]["hash"])
        self.assertNotEqual("approved", state["goal_contract"]["approval_status"])
        self.invoke("autocode_build", ["--run-dir", str(self.run), "--no-chat"], 2)
        self.assertEqual(3, len(self.events()))

    def test_prelaunch_recovery_rejects_unrecorded_workspace_edits(self):
        self.seed()
        self.fault("before_worker")
        self.invoke("autocode_build", ["--run-dir", str(self.run), "--no-chat"], 97)
        worker = self.state()["orchestration_batch"]["workers"][0]
        (Path(worker["workspace"]) / "unrecorded.txt").write_text("Keep this unexplained work")
        self.build(2, extra=["--resume-paused"])
        self.assertEqual("Keep this unexplained work", (Path(worker["workspace"]) / "unrecorded.txt").read_text())
        self.assertFalse(any(e["milestone"] == "M1" for e in self.events()))

    def checkpoint_spec(self):
        files = {f"screens/s{i}.txt": f"state {i}\n" for i in range(21)}
        c1 = "from pathlib import Path; assert all(Path(f'screens/s{i}.txt').read_text()==f'state {i}\\n' for i in range(21))"
        c2 = "from pathlib import Path; assert Path('screens/regression.txt').read_text()=='PASS\\n'"
        spec = bb.plan(
            [([], "Implement 21 screen-state fixtures, then pass the regression checkpoint", ["screens/"])],
            {"M1": files},
            {"M1": c1 + "; " + c2},
            "Implement and checkpoint a 21-state dashboard fixture",
        )
        spec["contract"]["acceptance_criteria"][0]["criterion"] = "All 21 state artifacts match the baseline"
        spec["contract"]["acceptance_criteria"].append(
            dict(id="C2", criterion="Final regression marker passes", verification_method=c2, human_review=False)
        )
        spec["contract"]["milestones"][0]["acceptance_criteria"].append("C2")
        spec["criterion_checks"] = {"C1": c1, "C2": c2}
        spec["checkpoint_files"] = list(files)
        return spec

    def test_23_long_build_exposes_durable_progress_before_it_finishes(self):
        self.seed(self.checkpoint_spec())
        self.env["BUILD_AUDIT_FAULT"] = "checkpoints"
        process = subprocess.Popen(
            self.command("autocode_build", ["--run-dir", str(self.run), "--no-chat"]),
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 25
            while not self.events("checkpoints_saved") and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(21, len(self.events("checkpoint")))
            self.assertIsNone(process.poll(), "Progress was only available after completion")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                checkpoints = self.state().get("execution_checkpoints", {})
                if any(r.get("completed_tools", 0) >= 21 for r in checkpoints.get("rows", [])):
                    break
                time.sleep(0.05)
            self.assertEqual("running", checkpoints["rows"][0]["status"])
            self.assertGreaterEqual(checkpoints["rows"][1]["completed_tools"], 21)
            self.assertTrue(
                all(r["status"] == "not_verified" for r in checkpoints["rows"] if r["id"].startswith("criterion:"))
            )
            status = self.invoke("autocode_build", ["--run-dir", str(self.run), "--status"])
            self.assertIn("terra", status.stdout)
            event_files = list(self.run.glob("iterations/*/builder-*.jsonl")) + list(
                self.run.glob("iterations/*/terra-*.jsonl")
            )
            self.assertTrue(event_files)
            self.assertEqual(
                21, sum("checkpoint-screens/" in line for p in event_files for line in p.read_text().splitlines())
            )
            self.assertEqual(21, len(list((self.project / "screens").glob("s*.txt"))))
        finally:
            (self.root / "release").touch()
            stdout, stderr = process.communicate(timeout=30)
            (self.root / "checkpoint-cli.log").write_text(stdout + stderr)
        self.assertEqual(0, process.returncode, stdout + stderr)
        self.candidate()

    def correct_failed_checkpoint(self, regression=False):
        spec = self.checkpoint_spec()
        self.seed(spec)
        self.build()
        baseline = {p: p.stat().st_mtime_ns for p in (self.project / "screens").glob("s*.txt")}
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        initial = self.state()["validation"]
        self.assertEqual(
            {"C1": "PASS", "C2": "NOT_VERIFIED"}, {r["id"]: r["status"] for r in initial["criterion_results"]}
        )
        self.invoke("autoresolver", ["--run-dir", str(self.run), "--no-chat"])
        spec["payloads"]["M1"] = {"screens/regression.txt": "PASS\n"}
        if regression:
            spec["payloads"]["M1"]["screens/s0.txt"] = "BROKEN\n"
        (self.root / "plan.json").write_text(json.dumps(spec))
        self.build()
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat", "--pause-after-stage"], 2)
        self.assertEqual(2, len(self.events()))
        if not regression:
            self.assertEqual(baseline, {p: p.stat().st_mtime_ns for p in baseline})
        self.assertTrue(
            any(
                v["validation"]["source_revision"] == initial["source_revision"]
                for v in self.state()["validation_archive"]
            )
        )
        return self.state()["validation"]

    def test_24_failed_later_checkpoint_retains_prior_work_and_evidence(self):
        final = self.correct_failed_checkpoint()
        self.assertEqual("PASS", final["verdict"])
        self.assertNotEqual("TASK_COMPLETE", self.state()["status"])

    def test_25_regression_of_previously_passing_criterion_is_not_hidden(self):
        final = self.correct_failed_checkpoint(regression=True)
        self.assertEqual("FAIL", final["verdict"])
        self.assertEqual(
            {"C1": "NOT_VERIFIED", "C2": "PASS"}, {r["id"]: r["status"] for r in final["criterion_results"]}
        )
        self.assertNotEqual("TASK_COMPLETE", self.state()["status"])


class WriterStartupEvidenceTests(unittest.TestCase):
    """#994: test_32 failed with `[] is not true` and kept nothing of the controller that started no Builder."""

    def harness(self, events):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        harness = RecoveryBlackbox("test_32_plan_cannot_mutate_active_workers_and_revision_invalidates_old_approval")
        harness.root = Path(folder.name)
        harness.run = harness.root / "run"
        harness.run.mkdir()
        (harness.root / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        return harness

    def test_a_controller_that_exits_first_is_reported_at_once_with_how_far_it_got(self):
        harness = self.harness([dict(event="start", stage="astra_finalize", milestone="")])
        worker = harness.root / "worker-m1"
        worker.mkdir()
        (worker / "worker.log").write_text("x" * 2000 + "Builder lifeline: owner gone\n")
        batch = dict(workers=[dict(milestone_id="M1", status="INTERRUPTED", run_dir=str(worker))])
        state = dict(status="PAUSED_ORCHESTRATOR_WORKERS", phase="EXECUTING", stop_reason="Builder M1 stopped")
        (harness.run / "state.json").write_text(json.dumps(dict(state, orchestration_batch=batch)))
        facts = harness.await_writer(SimpleNamespace(poll=lambda: 2), 20)
        self.assertLess(facts.pop("waited"), 20)  # the exit, not the bound, ended the wait
        self.assertEqual(
            dict(
                bound=20,
                controller_exit=2,
                event_count=1,
                last_events=["astra_finalize start -"],
                state=state,
                workers=[
                    dict(
                        milestone="M1",
                        status="INTERRUPTED",
                        exit_code=None,
                        log_tail="x" * 471 + "Builder lifeline: owner gone\n",
                    )
                ],
            ),
            facts,
        )
        message = startup_failure(dict(facts, waited=0.1), 2, "y" * 5000 + "Run: r1\n", "preflight refused\n")
        self.assertIn("No Builder started within 20 s", message)
        report = json.loads(message.split("\n", 1)[1])
        self.assertEqual((2, "preflight refused\n"), (report["returncode"], report["stderr_tail"]))
        self.assertEqual(OUTPUT_TAIL, len(report["stdout_tail"]))
        self.assertTrue(report["stdout_tail"].endswith("Run: r1\n"))

    def test_a_live_controller_is_reported_at_the_bound_and_a_half_written_line_is_not_fatal(self):
        harness = self.harness([dict(event="start", stage="astra_finalize", milestone="")])
        with (harness.root / "events.jsonl").open("a") as log:
            log.write('{"event": "start", "stage": "ter')  # a Builder's start line, half appended
        facts = harness.await_writer(SimpleNamespace(poll=lambda: None), 0)
        self.assertEqual((None, 2), (facts["controller_exit"], facts["event_count"]))
        self.assertEqual('{"event": "start", "stage": "ter', facts["last_events"][-1])
        self.assertIn("FileNotFoundError", facts["state"])

    def test_a_started_builder_is_ready(self):
        harness = self.harness([dict(event="start", stage="terra", milestone="M1")])
        self.assertIsNone(harness.await_writer(SimpleNamespace(poll=lambda: 0), 20))

    def test_the_facts_survive_a_controller_that_outlives_its_cleanup_bound(self):
        harness = self.harness([])
        expired = subprocess.TimeoutExpired(["autocode_build"], 40, output=b"Run: r1\n", stderr=b"stage terra\n")

        def communicate(timeout):
            raise expired

        controller = SimpleNamespace(communicate=communicate)
        facts = dict(bound=20, waited=20.0, controller_exit=None)
        with self.assertRaises(AssertionError) as caught:
            harness.finish(controller, 40, facts)
        report = json.loads(str(caught.exception).split("\n", 1)[1])
        self.assertEqual(
            (None, "Run: r1\n", "stage terra\n"), (report["returncode"], report["stdout_tail"], report["stderr_tail"])
        )
        self.assertIs(expired, caught.exception.__cause__)
        with self.assertRaises(subprocess.TimeoutExpired):  # a passing readiness keeps the plain cleanup error
            harness.finish(controller, 40, None)


if __name__ == "__main__":
    unittest.main()
