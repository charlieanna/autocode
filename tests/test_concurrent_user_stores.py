"""Overlapping saves of the user-level stores keep every successful update (#3858).

The Stage Models panel, two editor windows or two probes finishing together can each read
``stages.toml`` or ``measured-routes.json``, change their part and write the whole file. Each case
holds the first writer between its read and its write until the second has read the file too or is
waiting for the store lock, so the two always overlap; both updates must survive. No model is called:
the stage offers come from the stage-choice test fixture.
"""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import autocode_measured_routes as measured_routes
import autocode_stage_choices as choices
import autocode_util as util

from tests.test_stage_choices import LISTING, OPUS, SONNET, resolve

# A hang guard, never a pace: each wait ends as soon as the other writer gets there.
WAIT_SECONDS = 30


def measured(model, effort, passed=9, total=9):
    grades = [{"request": f"R{i + 1}", "passed": i < passed, "seconds": 40.0, "cost_usd": 0.05} for i in range(total)]
    return measured_routes.result(model, effort, grades, date="2026-11-15")


class ConcurrentUserStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "autocode"
        self.stages = self.root / "stages.toml"
        self.measured = self.root / "measured-routes.json"
        self.kw = {"file": self.stages, "resolve": resolve, "discover": lambda provider, workspace: LISTING}

    def overlap(self, owner, write, first, second):
        """Run ``first`` (thread A) and ``second`` (thread B) with A held between its read and its write.

        ``owner.write`` is the store's write. A waits in it, after it has read the file, until B has
        reached its own write (so B has read the file too) or is waiting for the store lock.
        """
        real_write, real_lock = getattr(owner, write), util.lock_file
        a_writing, b_ready = threading.Event(), threading.Event()
        errors = {}

        def run(name, call):
            try:
                call()
            except BaseException as error:  # reported by the test thread below
                errors[name] = error

        a = threading.Thread(target=run, args=("A", first), name="writer-A", daemon=True)
        b = threading.Thread(target=run, args=("B", second), name="writer-B", daemon=True)

        def held_write(*args, **kwargs):
            if threading.current_thread() is a and not a_writing.is_set():
                a_writing.set()
                if not b_ready.wait(WAIT_SECONDS):
                    raise AssertionError("the second writer never read the file or waited for the lock")
            elif threading.current_thread() is b:
                b_ready.set()
            return real_write(*args, **kwargs)

        def watched_lock(handle, *args, **kwargs):
            if threading.current_thread() is b:
                b_ready.set()  # B is waiting for the lock A holds
            return real_lock(handle, *args, **kwargs)

        with mock.patch.object(owner, write, held_write), mock.patch.object(util, "lock_file", watched_lock):
            a.start()
            self.assertTrue(a_writing.wait(WAIT_SECONDS), "the first writer never reached its write")
            b.start()
            a.join(WAIT_SECONDS)
            b.join(WAIT_SECONDS)
        self.assertFalse(a.is_alive() or b.is_alive(), "a writer did not finish")
        self.assertEqual({}, errors)

    def test_ac14_overlapping_choices_on_two_backends_keep_both(self):
        self.overlap(
            choices,
            "save",
            lambda: choices.choose("claude", {"builder": (SONNET, "high"), "tester": (OPUS, "high")}, **self.kw),
            lambda: choices.choose("opencode", {"task_planner": ("opencode/plain", choices.OWN_DEFAULT)}, **self.kw),
        )
        saved = choices.load(self.stages)
        self.assertEqual({"model": SONNET, "effort": "high"}, saved.get("claude", {}).get("builder"))
        self.assertEqual({"model": OPUS, "effort": "high"}, saved.get("claude", {}).get("tester"))
        self.assertEqual({"model": "opencode/plain", "effort": None}, saved.get("opencode", {}).get("task_planner"))

    def test_ac15_overlapping_measurements_of_two_stages_keep_both(self):
        self.overlap(
            util,
            "atomic_json",
            lambda: measured_routes.record("requirements", [measured("x/m-1", "low")], file=self.measured),
            lambda: measured_routes.record("tester", [measured("x/m-2", "low")], file=self.measured),
        )
        roles = measured_routes.load(self.measured)["roles"]
        self.assertEqual({"requirements", "tester"}, set(roles))
        self.assertEqual([("x/m-1", "low")], [(r["model"], r["effort"]) for r in roles["requirements"]["results"]])
        self.assertEqual([("x/m-2", "low")], [(r["model"], r["effort"]) for r in roles["tester"]["results"]])

    def test_ac16_overlapping_measurements_of_two_models_keep_both(self):
        self.overlap(
            util,
            "atomic_json",
            lambda: measured_routes.record("requirements", [measured("x/m-1", "low")], file=self.measured),
            lambda: measured_routes.record("requirements", [measured("x/m-2", "low")], file=self.measured),
        )
        results = measured_routes.load(self.measured)["roles"]["requirements"]["results"]
        self.assertEqual(2, len(results))
        self.assertEqual([("x/m-1", "low"), ("x/m-2", "low")], [(r["model"], r["effort"]) for r in results])

    def test_ac17_overlapping_reset_and_choose_keep_both_updates(self):
        choices.save({"claude": {"tester": {"model": OPUS, "effort": "high"}}}, self.stages)
        self.overlap(
            choices,
            "save",
            lambda: choices.reset("claude", "tester", **self.kw),
            lambda: choices.choose("opencode", {"task_planner": ("opencode/plain", choices.OWN_DEFAULT)}, **self.kw),
        )
        saved = choices.load(self.stages)
        self.assertNotIn("tester", saved.get("claude", {}))
        self.assertEqual({"model": "opencode/plain", "effort": None}, saved.get("opencode", {}).get("task_planner"))


if __name__ == "__main__":
    unittest.main()
