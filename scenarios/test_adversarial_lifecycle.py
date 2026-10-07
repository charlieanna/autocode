"""Attack task-run ownership, authorization and persisted budgets through CLI.

Every run starts normally and reaches its target gate; no runtime imports,
private state edits, live providers, timing sleeps or expected-failure markers.
The provider FIFO makes interruption/concurrency faults causally reproducible.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import shutil
import psutil

from scenarios.harness.adversarial import AdversarialCase
from scenarios.harness.attack_lifecycle import ProviderBarrier


class LifecycleAttacks(AdversarialCase):
    def assert_completed(self, view):
        self.assertTrue(view["done"], view)
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        check = subprocess.run(["python3", "-m", "unittest", "test_greet.py"],
                               cwd=self.project, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, check.returncode, check.stdout + check.stderr)

    def held_builder(self, *, completed_report=False):
        self.approve(self.start_to_approval())
        barrier = ProviderBarrier(self.root)
        self.addCleanup(barrier.close)
        self.env.update(barrier.environment)
        self.set_fault("lifecycle", "provider_barrier")
        if completed_report:
            hooks = self.root / "completion-hooks"
            hooks.mkdir()
            shutil.copy2(Path(__file__).parent / "harness/attack_lifecycle.py", hooks / "sitecustomize.py")
            self.env.update(PYTHONPATH=str(hooks), LIFECYCLE_CONTROLLER_CHECKPOINT=str(
                self.driver.run_dir / "iterations/001/builder-01.after.json"))
        controller = self.spawn()
        announcement = barrier.wait()
        parent = next(p for p in self.owned if p.pid == controller.pid)
        descendants = parent.children(recursive=True)
        provider = next((p for p in descendants if p.pid == announcement["pid"]), None)
        self.assertIsNotNone(provider, "Announced provider must belong to this controller")
        self.owned.extend(p for p in descendants if p not in self.owned)
        self.assertEqual(1, len(self.trace("attack_injected", "terra")))
        self.assertEqual(1, len(self.trace("stage_enter", "terra")))
        self.assertTrue(provider.is_running())
        return barrier, controller, parent, provider

    def assert_cleaned(self, provider):
        provider.wait(timeout=10)
        self.assertFalse(provider.is_running() and provider.status() != psutil.STATUS_ZOMBIE,
                         "Independent supervision must stop the crashed controller's writer")

    def stopped_receipt(self, *, provider=None, cause=None):
        def receipt():
            path = self.driver.run_dir / "iterations/001/builder-01.supervision.json"
            if not path.exists():
                return None
            row = json.loads(path.read_text())
            if (row.get("phase") == "stopped" and not row.get("cleanup_error")
                    and (provider is None or (row["provider"]["pid"] == provider.pid
                         and row["provider"]["birth_identity"] == provider._ident[1]))
                    and (cause is None or row.get("cause") == cause)):
                return row
        return self.await_condition(receipt, timeout=10, message="verified supervision cleanup receipt")

    def clear_barrier_environment(self):
        for key in ("LIFECYCLE_HOLD_STAGE", "LIFECYCLE_READY_FIFO", "LIFECYCLE_RELEASE_FIFO"):
            self.env.pop(key, None)
        if self.env.pop("LIFECYCLE_CONTROLLER_CHECKPOINT", None):
            self.env.pop("PYTHONPATH", None)

    def second_run_in_same_checkout(self):
        original = self.driver.run_dir
        self.driver.run_dir = None
        try:
            return self.invoke(task=self.scenario.brief, timeout=45)
        finally:
            self.driver.run_dir = original


    def test_control_uninterrupted_run_completes(self):
        self.approve(self.start_to_approval())
        self.assert_completed(self.finish())
        self.assertEqual(1, len(self.trace("stage_enter", "terra")))

    def test_continue_cannot_bypass_unapproved_plan(self):
        approval = self.start_to_approval()
        count = len(self.trace("stage_enter"))
        for arguments in ((), ("--resume-paused",)):
            result = self.invoke(*arguments)
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertEqual(count, len(self.trace("stage_enter")))
            self.assertEqual(approval["needs"], self.status()["needs"])
        self.assertFalse((self.project / "greet.py").exists())
        self.approve()
        self.assert_completed(self.finish())

    def test_feedback_invalidates_previously_displayed_approval(self):
        old = self.start_to_approval()
        count = len(self.trace("stage_enter"))
        feedback = self.invoke("--feedback", "Keep the greeting behavior; produce a fresh plan for review.")
        self.assertEqual(0, feedback.returncode, feedback.stdout + feedback.stderr)
        stale = self.invoke("--approve-goal", old["needs"]["token"])
        self.assertEqual(2, stale.returncode, stale.stdout + stale.stderr)
        self.assertEqual(count, len(self.trace("stage_enter")), "Feedback and rejected approval must not dispatch")
        self.assertFalse((self.project / "greet.py").exists())
        revised = self.driver.until_stopped("needs:approve_plan")
        self.assertEqual("approve_plan", revised["needs"]["kind"], revised)
        self.assertNotEqual(old["needs"]["token"], revised["needs"]["token"])
        replay = self.invoke("--approve-goal", old["needs"]["token"])
        self.assertEqual(2, replay.returncode, replay.stdout + replay.stderr)
        self.assertEqual(revised["needs"], self.status()["needs"])
        self.approve(revised)
        self.assert_completed(self.finish())

    def test_consumed_approval_cannot_authorize_a_second_build(self):
        old = self.start_to_approval()
        self.approve(old)
        self.assert_completed(self.finish())
        count = len(self.trace("stage_enter"))
        replay = self.invoke("--approve-goal", old["needs"]["token"])
        self.assertEqual(2, replay.returncode, replay.stdout + replay.stderr)
        self.assertEqual(count, len(self.trace("stage_enter")))
        self.assertTrue(self.status()["done"])
        self.assertEqual(1, len(self.trace("stage_enter", "terra")))

    def test_concurrent_continue_cannot_start_second_writer(self):
        barrier, controller, _, provider = self.held_builder()
        second = self.invoke(timeout=15)
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        self.assertEqual(1, len(self.trace("stage_enter", "terra")))
        self.assertTrue(provider.is_running(), "Rejected continuation must leave the legitimate writer alive")
        barrier.release()
        self.assertEqual(0, controller.wait(timeout=45))
        self.clear_barrier_environment()
        self.assert_completed(self.status())

    def test_controller_crash_does_not_allow_live_orphan_to_race(self):
        barrier, controller, parent, provider = self.held_builder()
        parent.kill()  # Retained psutil object checks PID and birth identity.
        self.assertLess(controller.wait(timeout=10), 0)
        self.clear_barrier_environment()
        second = self.invoke("--resume-paused", timeout=15)
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        self.assertFalse(self.status()["done"])
        self.assertEqual(1, len(self.trace("stage_enter", "terra")),
                         "An interrupted attempt cannot authorize a competing Builder")
        self.assert_cleaned(provider)
        self.assertEqual([], self.trace("stage_exit", "terra"), "Cleanup must not finish an interrupted report")

    def test_active_run_blocks_new_run_in_same_checkout(self):
        barrier, controller, _, provider = self.held_builder()
        before = len(self.trace("stage_enter"))
        second = self.second_run_in_same_checkout()
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        self.assertEqual(before, len(self.trace("stage_enter")),
                         "A different run must respect checkout ownership before launching providers")
        self.assertTrue(provider.is_running())
        barrier.release()
        self.assertEqual(0, controller.wait(timeout=45))
        self.clear_barrier_environment()
        self.assert_completed(self.status())

    def test_live_orphan_blocks_new_run_in_same_checkout(self):
        barrier, controller, parent, provider = self.held_builder()
        before = len(self.trace("stage_enter"))
        blocked = self.second_run_in_same_checkout()
        self.assertEqual(2, blocked.returncode, blocked.stdout + blocked.stderr)
        self.assertEqual(before, len(self.trace("stage_enter")))
        parent.kill()
        self.assertLess(controller.wait(timeout=10), 0)
        self.stopped_receipt(provider=provider, cause="owner_lost")
        self.assert_cleaned(provider)
        self.clear_barrier_environment()
        second = self.second_run_in_same_checkout()
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        run_lines = [line.removeprefix("Run: ") for line in second.stdout.splitlines()
                     if line.startswith("Run: ")]
        self.assertEqual(1, len(run_lines), second.stdout)
        original = self.driver.run_dir
        self.driver.run_dir = Path(run_lines[0])
        try:
            self.approve()
            self.assert_completed(self.finish())
        finally:
            self.driver.run_dir = original
        self.assertEqual(2, len(self.trace("stage_enter", "terra")),
                         "A new Builder is allowed only after verified cleanup and fresh approval")

    def test_controller_death_before_first_process_receipt_keeps_checkout_locked(self):
        self.approve(self.start_to_approval())
        barrier = ProviderBarrier(self.root)
        self.addCleanup(barrier.close)
        self.env.update(barrier.environment)
        self.set_fault("lifecycle", "provider_barrier")
        hooks = self.root / "startup-hooks"
        hooks.mkdir()
        shutil.copy2(Path(__file__).parent / "harness/attack_persistence_hook.py", hooks / "sitecustomize.py")
        marker = self.root / "startup-crash.json"
        target = self.driver.run_dir / "active-processes.json"
        spec = self.root / "startup-crash-config.json"
        spec.write_text(json.dumps({"mode": "crash_before_replace", "target": str(target), "marker": str(marker)}))
        self.env.update(PYTHONPATH=str(hooks), AUTOCODE_TEST_IO_FAULT=str(spec))
        controller = self.spawn()
        self.assertEqual(97, controller.wait(timeout=15))
        self.assertTrue(marker.is_file(), "The fault must reach the first durable process receipt")
        self.assertFalse(target.exists(), "Controller must die before any provider receipt is published")
        self.assertEqual([], self.trace("stage_enter", "terra"),
                         "Durable admission must precede provider exec, even at the first receipt")
        self.stopped_receipt(cause="owner_lost")
        self.clear_barrier_environment()
        self.env.pop("PYTHONPATH")
        self.env.pop("AUTOCODE_TEST_IO_FAULT")
        second = self.second_run_in_same_checkout()
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        self.assertEqual([], self.trace("stage_enter", "terra"),
                         "No Builder may launch without fresh approval after cleanup")
        run_lines = [line.removeprefix("Run: ") for line in second.stdout.splitlines()
                     if line.startswith("Run: ")]
        self.assertEqual(1, len(run_lines), second.stdout)
        original = self.driver.run_dir
        self.driver.run_dir = Path(run_lines[0])
        try:
            self.assertEqual("approve_plan", self.status()["needs"]["kind"],
                             "Verified bootstrap cleanup must allow fresh planning, not leave a stale lock")
        finally:
            self.driver.run_dir = original

    def test_crashed_provider_is_not_mistaken_for_successful_build(self):
        _, controller, parent, provider = self.held_builder()
        parent.kill()
        self.assertLess(controller.wait(timeout=10), 0)
        provider.kill()
        provider.wait(timeout=10)
        self.clear_barrier_environment()
        result = self.invoke("--resume-paused", timeout=45)
        self.assertIn(result.returncode, (0, 2), result.stdout + result.stderr)
        view = self.status()
        if view["done"]:
            self.assertGreaterEqual(len(self.trace("stage_enter", "terra")), 2,
                                    "The killed provider produced no code; completion requires a new Builder")
            self.assert_completed(view)
        else:
            self.assertIsNotNone(view["needs"], view)
            self.assertNotEqual("TASK_COMPLETE", view["status"])
        self.assertEqual(1, len(self.trace("attack_injected", "terra")))

    def test_finished_orphan_report_resumes_without_second_builder(self):
        barrier, controller, parent, provider = self.held_builder(completed_report=True)
        barrier.release()
        completed = barrier.wait()
        self.assertEqual(controller.pid, completed["pid"], "Controller must publish the completed artifact")
        self.assertEqual(1, len(self.trace("stage_exit", "terra")))
        self.stopped_receipt(provider=provider, cause="provider_stopped")
        parent.kill()
        self.assertLess(controller.wait(timeout=10), 0)
        self.assert_cleaned(provider)
        self.clear_barrier_environment()
        result = self.invoke("--resume-paused", timeout=45)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_completed(self.status())
        self.assertEqual(1, len(self.trace("stage_enter", "terra")),
                         "A completed, current orphan report must not repeat implementation")

    def test_abandon_crashed_attempt_then_resume_reaches_fresh_builder(self):
        _, controller, parent, provider = self.held_builder()
        # Read the documented public --status attempt_id, never private state.
        public_status = json.loads(self.invoke("--status").stdout)
        attempt = public_status["attempt_id"]
        self.assertTrue(attempt, public_status)
        parent.kill()
        self.assertLess(controller.wait(timeout=10), 0)
        provider.kill()
        provider.wait(timeout=10)
        self.clear_barrier_environment()
        abandoned = self.invoke("--abandon-stage", attempt)
        self.assertEqual(0, abandoned.returncode, abandoned.stdout + abandoned.stderr)
        self.assertEqual(1, len(self.trace("stage_enter", "terra")),
                         "Abandoning a response must not launch another model")
        result = self.invoke("--resume-paused", timeout=45)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_completed(self.status())
        self.assertEqual(2, len(self.trace("stage_enter", "terra")),
                         "Exactly one replacement Builder should follow the killed attempt")





if __name__ == "__main__":
    import unittest
    unittest.main()
