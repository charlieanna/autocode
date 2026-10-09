"""Approval durability under real CLI process death and injected storage errors."""
import json
from pathlib import Path
import shutil

from .harness.adversarial import AdversarialCase


class PersistenceAttacks(AdversarialCase):
    def fault(self, mode):
        hooks = self.root / "io-hooks"
        hooks.mkdir()
        shutil.copy2(Path(__file__).parent / "harness/attack_persistence_hook.py", hooks / "sitecustomize.py")
        spec = self.root / "io-fault.json"
        marker = self.root / "io-injected.json"
        spec.write_text(json.dumps({"mode": mode, "target": str(self.driver.run_dir / "state.json"),
                                    "marker": str(marker)}))
        self.env.update(PYTHONPATH=str(hooks), AUTOCODE_TEST_IO_FAULT=str(spec))
        return marker

    def exercise(self, mode, persisted):
        before = self.start_to_approval()
        token = before["needs"]["token"]
        count = len(self.trace("stage_enter"))
        marker = self.fault(mode)
        result = self.invoke("--approve-goal", token)
        self.assertTrue(marker.is_file(), "Storage fault never reached its atomic-write target")
        self.assertNotEqual(0, result.returncode, "A failed write must never acknowledge approval")
        self.assertEqual(count, len(self.trace("stage_enter")), "Approval must not launch a provider")
        self.env.pop("PYTHONPATH")
        self.env.pop("AUTOCODE_TEST_IO_FAULT")
        after = self.status()  # Public status must remain readable after the interruption.
        self.assertFalse(after["done"], after)
        if mode == "transient_disk_full" and after["needs"]["kind"] == "resume":
            # A later error-checkpoint write can persist the explicitly requested
            # approval. Recovery must retain its token and perform only one build.
            result = self.invoke("--resume-paused")
            self.assertIn(result.returncode, (0, 2), result.stderr)
        elif not persisted:
            self.assertEqual("approve_plan", after["needs"]["kind"], after)
            self.assertEqual(token, after["needs"]["token"], "Failed replacement lost or changed pending approval")
            self.approve(after)
        else:
            self.assertEqual("continue", after["needs"]["kind"], after)
        final = self.finish()
        self.assertTrue(final["done"], final)
        self.assertTrue(final["delivery"]["verified_complete"], final)
        self.assertEqual(token, final["delivery"]["contract"]["approval_event"]["token"])
        self.assertEqual(1, len(self.trace("stage_enter", "terra")), "Recovery duplicated the Builder")

    def test_disk_full_cannot_acknowledge_or_lose_plan_approval(self):
        self.exercise("disk_full", False)

    def test_io_error_cannot_acknowledge_or_lose_plan_approval(self):
        self.exercise("io_error", False)

    def test_controller_death_before_replace_preserves_pending_approval(self):
        self.exercise("crash_before_replace", False)

    def test_controller_death_after_replace_keeps_approval_without_duplicate_build(self):
        self.exercise("crash_after_replace", True)

    def test_transient_disk_error_recovers_the_explicit_approval_once(self):
        self.exercise("transient_disk_full", False)
