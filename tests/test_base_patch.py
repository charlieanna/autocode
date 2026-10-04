"""An operator base patch lets a bug-fix test that uses the fix's seam run on the original code (#299).

The patch adds only the seam (here ``replace_file = os.rename``) to the unfixed code, so the same test
builds there and fails because of the bug. It is hash-pinned, may not change tests, must be contained
in the final change, and every proof that uses it asks the Validator to check it. Real unittest runs
in scratch Git worktrees; no provider is launched.
"""
from __future__ import annotations

import difflib
import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_base_patch as base_patch
import autocode_regression as regression
import autocode_verify as verify
from tests.test_proof_seam import FIXED_STORE, SEAM_TEST, STORE, TESTS, bugfix_state
from tests.test_verify import Project
from tests import test_goals
import autocode_goal_lifecycle as lifecycle
import autocode_support as support

SEAM_ONLY = STORE.replace("import os\n", "import os\n\nreplace_file = os.rename\n", 1)


def diff(path, before, after):
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}"))


class BasePatchProofTests(unittest.TestCase):
    def setUp(self):
        self.project = Project({"store.py": STORE, "test_store.py": TESTS})
        self.addCleanup(self.project.close)
        self.outside = Path(tempfile.mkdtemp(prefix="base-patch-"))
        self.addCleanup(shutil.rmtree, self.outside, True)

    def patch(self, text, name="seam.patch"):
        path = self.outside / name
        path.write_text(text)
        return path

    def prove(self, saved):
        state = bugfix_state(self.project)
        state["settings"]["regression"] = {"base_patch": saved}
        run_dir = self.outside / "run"
        return regression.prove(state, self.project.root, run_dir)

    def test_a_seam_test_passes_with_a_patch_that_adds_only_the_seam(self):
        path = self.patch(diff("store.py", STORE, SEAM_ONLY))
        saved = base_patch.pin(path, self.project.root, self.project.base)
        self.project.write({"store.py": FIXED_STORE, "test_store.py": SEAM_TEST})
        proof = self.prove(saved)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["test_store.SaveTests.test_t1_rename_failure_is_raised"], proof["fail_to_pass"])
        self.assertTrue(any("operator's base patch seam.patch" in reason and "changes no behavior" in reason
                            for reason in proof["review_reasons"]), proof["review_reasons"])

    def test_without_the_patch_the_same_test_names_the_base_patch_way_out(self):
        self.project.write({"store.py": FIXED_STORE, "test_store.py": SEAM_TEST})
        proof = regression.prove(bugfix_state(self.project), self.project.root, self.outside / "run")
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertTrue(any("supplies with --base-patch PATH" in failure for failure in proof["failures"]),
                        proof["failures"])

    def test_a_patch_the_final_change_does_not_contain_is_not_used(self):
        planted = SEAM_ONLY.replace("        pass\n", "        raise\n")  # breaks the original code on purpose
        saved = base_patch.pin(self.patch(diff("store.py", STORE, planted)), self.project.root, self.project.base)
        self.project.write({"store.py": FIXED_STORE, "test_store.py": SEAM_TEST})
        proof = self.prove(saved)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"])
        self.assertIn("does not contain the operator base patch seam.patch: store.py lacks 'raise'",
                      " ".join(proof["unverified"]))

    def test_a_patch_changed_after_it_was_pinned_is_not_used(self):
        path = self.patch(diff("store.py", STORE, SEAM_ONLY))
        saved = base_patch.pin(path, self.project.root, self.project.base)
        path.write_text(path.read_text() + "\n")
        self.project.write({"store.py": FIXED_STORE, "test_store.py": SEAM_TEST})
        proof = self.prove(saved)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"])
        self.assertIn("changed after it was set", " ".join(proof["unverified"]))

    def test_a_patch_that_changes_tests_or_does_not_apply_is_refused_when_set(self):
        weakened = TESTS.replace('self.assertEqual("x", handle.read())', "pass")
        with self.assertRaisesRegex(ValueError, "may not change test files.*test_store.py"):
            base_patch.pin(self.patch(diff("test_store.py", TESTS, weakened)), self.project.root, self.project.base)
        with self.assertRaisesRegex(ValueError, "does not apply to the original code"):
            base_patch.pin(self.patch(diff("store.py", FIXED_STORE, FIXED_STORE + "# x\n")),
                           self.project.root, self.project.base)
        with self.assertRaisesRegex(ValueError, "no such file"):
            base_patch.pin(self.outside / "missing.patch", self.project.root, self.project.base)


class BasePatchCLITests(unittest.TestCase):
    # The goal tests' Git fixture and CLI driver; binding the class here would run its tests again.
    setUp, approve, draft, decision, validation, invoke = (getattr(test_goals.GoalTests, name) for name in (
        "setUp", "approve", "draft", "decision", "validation", "invoke"))

    def seam_patch(self):
        greet = (self.root / "greet.py").read_text()
        path = Path(self.temp.name) / "hook.patch"
        path.write_text(diff("greet.py", greet, greet + "\nGREETING_HOOK = None\n"))
        return path

    def paused_before_the_validator(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), support.snapshot(self.root))
        self.state.update(status="PAUSED_INVALID_OUTPUT", phase="PAUSED_OR_BLOCKED", next_stage="sol",
                          stop_reason="Validator report rejected")

    def test_setting_it_on_a_saved_run_needs_an_explicit_resume_at_a_validator_stop(self):
        self.paused_before_the_validator()
        path = self.seam_patch()
        with self.assertRaisesRegex(ValueError, "requires a paused run and --resume-paused"):
            self.invoke("--base-patch", str(path), "--no-chat")
        self.assertNotIn("base_patch", support.read(self.run / "state.json")["settings"].get("regression", {}))

        def stop(**kwargs):
            raise support.Paused("PAUSED_TEST_LAUNCH", "Offline stage admission verified")
        self.invoke("--resume-paused", "--base-patch", str(path), "--no-chat", role=stop)
        saved = self.state["settings"]["regression"]["base_patch"]
        self.assertEqual((str(path.resolve()), ["greet.py"]), (saved["path"], saved["files"]))
        event = self.state["user_events"][-1]
        self.assertEqual(("base_patch_set", "user_cli", saved), (event["kind"], event["actor"], event["current"]))

    def test_it_is_refused_away_from_a_validator_stop(self):
        self.paused_before_the_validator()
        self.state["next_stage"] = "terra"
        with self.assertRaisesRegex(ValueError, "requires a stop before the Validator"):
            self.invoke("--resume-paused", "--base-patch", str(self.seam_patch()), "--no-chat")
        self.assertNotIn("base_patch", support.read(self.run / "state.json")["settings"].get("regression", {}))


if __name__ == "__main__":
    unittest.main()
