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

    def saved_meanwhile(self, other):
        """Run ``other`` (another window's save) once, after the caller's discovery and before its locked write."""
        real_lock, ran = util.store_lock, []

        def lock(path):
            if not ran:
                ran.append(True)
                other()
            return real_lock(path)

        return mock.patch.object(util, "store_lock", lock)

    def test_a_choice_is_checked_against_the_models_another_window_allowed_meanwhile(self):
        narrowed = ["zai-coding-plan/glm-5.3", "openai/gpt-6-sol"]
        with self.saved_meanwhile(lambda: choices.allow("opencode", narrowed, **self.kw)):
            with self.assertRaisesRegex(ValueError, "does not offer opencode/plain"):
                choices.choose("opencode", {"task_planner": ("opencode/plain", choices.OWN_DEFAULT)}, **self.kw)
        self.assertEqual(tuple(narrowed), choices.allowed("opencode", self.stages))
        self.assertNotIn("task_planner", choices.load(self.stages).get("opencode", {}))

    def test_a_choice_is_checked_against_the_stages_another_window_saved_meanwhile(self):
        # A Sonnet Builder may run beside an Opus Tester, but not beside the Sonnet Tester saved meanwhile.
        choices.save({"claude": {"tester": {"model": OPUS, "effort": "high"}}}, self.stages)
        with self.saved_meanwhile(lambda: choices.choose("claude", {"tester": (SONNET, "high")}, **self.kw)):
            with self.assertRaises(choices.CrossCheckError):
                choices.choose("claude", {"builder": (SONNET, "high")}, **self.kw)
        self.assertEqual({"claude": {"tester": {"model": SONNET, "effort": "high"}}}, choices.load(self.stages))

    def test_a_saved_choice_returns_its_view_without_another_discovery(self):
        calls = []

        def discover(provider, workspace):
            calls.append(provider)
            return LISTING

        kw = {**self.kw, "discover": discover}
        allowed = ["zai-coding-plan/glm-5.3", "openai/gpt-6-sol", "opencode/plain"]
        with self.saved_meanwhile(lambda: choices.allow("opencode", allowed, **kw)):
            shown = choices.choose("opencode", {"task_planner": ("opencode/plain", choices.OWN_DEFAULT)}, **kw)
        self.assertEqual(2, len(calls), "one discovery for the choice, one for the other window's list")
        # The view shows the list saved meanwhile, read under the lock, not the one discovery saw.
        self.assertEqual(allowed, shown["models"]["allowed"])
        self.assertEqual(set(allowed), {option["model"] for option in shown["models"]["options"]})
        [planner] = [stage for stage in shown["stages"] if stage["id"] == "task_planner"]
        self.assertEqual({"model": "opencode/plain", "effort": None}, planner["chosen"])
        self.assertEqual(allowed, [option["model"] for option in planner["options"]])

    def test_resetting_a_backend_repairs_an_unreadable_file(self):
        self.stages.parent.mkdir(parents=True)
        self.stages.write_text("[claude\nbuilder = ", encoding="utf-8")
        with self.assertRaises(ValueError):
            choices.reset("claude", "builder", **self.kw)  # one stage's reset still refuses to touch it
        choices.reset("claude", **self.kw)
        self.assertEqual({}, choices.load(self.stages))
        self.assertNotIn("[claude", self.stages.read_text(encoding="utf-8"))

    def test_resetting_a_backends_last_stage_removes_its_table(self):
        choices.save(
            {
                "claude": {"tester": {"model": OPUS, "effort": "high"}},
                "opencode": {"task_planner": {"model": "opencode/plain", "effort": None}},
            },
            self.stages,
        )
        choices.reset("claude", "tester", **self.kw)
        text = self.stages.read_text(encoding="utf-8")
        self.assertNotIn("[claude]", text)
        self.assertIn("[opencode]", text)
        self.assertEqual({"opencode"}, set(choices.load(self.stages)))

    def test_overlapping_allow_and_choose_keep_both(self):
        # A choice already saved on the backend whose list changes is kept too.
        choices.save({"opencode": {"task_planner": {"model": "opencode/plain", "effort": None}}}, self.stages)
        allowed = ["zai-coding-plan/glm-5.3", "openai/gpt-6-sol", "opencode/plain"]
        self.overlap(
            choices,
            "save",
            lambda: choices.allow("opencode", allowed, **self.kw),
            lambda: choices.choose("claude", {"builder": (SONNET, "high"), "tester": (OPUS, "high")}, **self.kw),
        )
        saved = choices.load(self.stages)
        self.assertEqual({"model": "opencode/plain", "effort": None}, saved["opencode"]["task_planner"])
        self.assertEqual({"model": SONNET, "effort": "high"}, saved.get("claude", {}).get("builder"))
        self.assertEqual({"model": OPUS, "effort": "high"}, saved.get("claude", {}).get("tester"))
        self.assertEqual(tuple(allowed), choices.allowed("opencode", self.stages))

    def test_overlapping_allows_on_two_backends_keep_both_lists(self):
        allowed = ["zai-coding-plan/glm-5.3", "openai/gpt-6-sol", "opencode/plain"]
        self.overlap(
            choices,
            "save",
            lambda: choices.allow("opencode", allowed, **self.kw),
            lambda: choices.allow("claude", [SONNET, OPUS], **self.kw),
        )
        self.assertEqual(tuple(allowed), choices.allowed("opencode", self.stages))
        self.assertEqual((SONNET, OPUS), choices.allowed("claude", self.stages))
        saved = choices.load(self.stages)
        self.assertEqual({"model": SONNET, "effort": "medium"}, saved["claude"]["builder"])


if __name__ == "__main__":
    unittest.main()
