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

    def held_builder(self):
        self.approve(self.start_to_approval())
        barrier = ProviderBarrier(self.root)
        self.addCleanup(barrier.close)
        self.env.update(barrier.environment)
        self.set_fault("lifecycle", "provider_barrier")
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

    def clear_barrier_environment(self):
        for key in ("LIFECYCLE_HOLD_STAGE", "LIFECYCLE_READY_FIFO", "LIFECYCLE_RELEASE_FIFO"):
            self.env.pop(key, None)

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
        self.assertTrue(provider.is_running(), "The injected fault must leave a live provider")
        self.clear_barrier_environment()
        second = self.invoke("--resume-paused", timeout=15)
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        self.assertFalse(self.status()["done"])
        self.assertEqual(1, len(self.trace("stage_enter", "terra")), "A live orphan must retain workspace exclusivity")
        self.assertTrue(provider.is_running(), "Resume cannot declare a living provider dead")
        barrier.release()
        provider.wait(timeout=10)

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
        parent.kill()
        self.assertLess(controller.wait(timeout=10), 0)
        self.assertTrue(provider.is_running(), "The injected crash must retain the writing provider")
        self.clear_barrier_environment()
        before = len(self.trace("stage_enter"))
        second = self.second_run_in_same_checkout()
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        self.assertTrue(provider.is_running())
        if len(self.trace("stage_enter")) > before:
            # Do not stop at a surprising planning launch. If the bypass reached
            # its normal approval gate, demonstrate whether it authorizes a
            # second actual Builder while the first is still alive.
            run_lines = [line.removeprefix("Run: ") for line in second.stdout.splitlines()
                         if line.startswith("Run: ")]
            self.assertEqual(1, len(run_lines), second.stdout)
            original = self.driver.run_dir
            self.driver.run_dir = Path(run_lines[0])
            try:
                view = self.status()
                if (view.get("needs") or {}).get("kind") == "approve_plan":
                    self.approve(view)
                    other_root = self.root / "competing-builder"
                    other_root.mkdir()
                    other_barrier = ProviderBarrier(other_root)
                    self.addCleanup(other_barrier.close)
                    self.env.update(other_barrier.environment)
                    other_controller = self.spawn()
                    announcement = other_barrier.wait()
                    other_parent = next(p for p in self.owned if p.pid == other_controller.pid)
                    descendants = other_parent.children(recursive=True)
                    other_provider = next(p for p in descendants if p.pid == announcement["pid"])
                    self.owned.extend(p for p in descendants if p not in self.owned)
                    overlap = {
                        "workspace": str(self.project), "original_run": str(original),
                        "competing_run": str(self.driver.run_dir),
                        "original_builder": {"pid": provider.pid, "birth_identity": provider._ident[1],
                                             "cwd": provider.cwd(), "alive": provider.is_running()},
                        "competing_builder": {"pid": other_provider.pid, "birth_identity": other_provider._ident[1],
                                              "cwd": other_provider.cwd(), "alive": other_provider.is_running()},
                    }
                    (self.root / "overlapping-builders.json").write_text(json.dumps(overlap, indent=2))
                    self.assertFalse(provider.is_running() and other_provider.is_running(),
                                     "Two live Builders own the same checkout after controller crash: " + str(overlap))
            finally:
                self.driver.run_dir = original
        self.assertEqual(before, len(self.trace("stage_enter")),
                         "Starting a new run bypassed the live orphan's checkout ownership")
        barrier.release()
        provider.wait(timeout=10)

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
        announcement = barrier.wait()
        self.assertEqual(97, controller.wait(timeout=15))
        self.assertTrue(marker.is_file(), "The fault must reach the first durable process receipt")
        self.assertFalse(target.exists(), "Controller must die before any provider receipt is published")
        trace = next(row for row in self.trace("stage_enter", "terra") if row["pid"] == announcement["pid"])
        provider = psutil.Process(announcement["pid"])
        self.assertEqual(trace["birth_identity"], provider._ident[1])
        self.owned.append(provider)
        self.clear_barrier_environment()
        self.env.pop("PYTHONPATH")
        self.env.pop("AUTOCODE_TEST_IO_FAULT")
        before = len(self.trace("stage_enter"))
        second = self.second_run_in_same_checkout()
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        self.assertEqual(before, len(self.trace("stage_enter")), "No competing provider before the first receipt")
        self.assertTrue(provider.is_running())
        barrier.release()
        provider.wait(timeout=15)

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
        barrier, controller, parent, provider = self.held_builder()
        parent.kill()
        self.assertLess(controller.wait(timeout=10), 0)
        self.assertTrue(provider.is_running())
        barrier.release()
        provider.wait(timeout=15)
        self.assertEqual(1, len(self.trace("stage_exit", "terra")),
                         "The orphan must finish its real report before resuming")
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
