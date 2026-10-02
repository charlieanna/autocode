"""The review workflow: a recognized review goes straight to the Reviewer, which
changes nothing and leaves review/findings.json behind."""
import json
import tempfile
import unittest
from pathlib import Path

import autocode_review_job as review_job
import autocode_run_view as run_view
import autocode_workflows as workflows
import autopilot
from units import autoreview


def state_for(task="Review pr-184.patch before I merge it.", workspace="/nowhere"):
    return {"version": 3, "task": task, "workspace": workspace, "status": "RUNNING", "stages": [],
            "settings": {"joint_planning": True, "roles": {
                "requirements": {"model": "r"}, "glm": {"model": "g"}, "plan_reviewer": {"model": "p"},
                "astra": {"model": "a"}, "terra": {"model": "t"}, "sol": {"model": "s"}}}}


def report(verdict="request_changes", findings=None, delivered_tests=()):
    return {"verdict": verdict, "summary": "one regression", "change_under_review": "pr-184.patch",
            "findings": findings if findings is not None else [
                {"id": "F1", "severity": "blocking", "file": "regclient/client.py", "lines": [26, 28],
                 "summary": "resends without reconciling", "evidence": "README Retries",
                 "example": "Given a timeout after the registry applied a renew; when renew() retries; then it "
                            "sends a second renew (expected: it polls first)",
                 "untestable": "The README's retry rule is the contract; the fixture has no registry to replay"}],
            "change_patch": "pr-184.patch", "tests_run": ["python3 -m unittest"],
            "delivered_tests": list(delivered_tests)}


class RoutingTests(unittest.TestCase):
    def test_a_recognized_review_goes_to_the_reviewer_not_requirements(self):
        state = state_for()
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "review", "reason": "", "signals": []}, {"output": "o"})
        self.assertEqual(review_job.STAGE, state["next_stage"])
        self.assertEqual("autoreview", autopilot.unit_for(review_job.STAGE))

    def test_kinds_without_their_own_first_stage_continue_into_the_build_pipeline(self):
        for kind in ("build",):
            state = state_for()
            workflows.begin(state, "requirements_gather")
            workflows.apply(state, {"workflow": kind, "reason": "", "signals": []}, {"output": "o"})
            self.assertEqual("requirements_gather", state["next_stage"], kind)


class PrepareTests(unittest.TestCase):
    def test_prompt_distinguishes_test_methods_from_delivered_file_paths(self):
        text, _ = review_job.prompt(state_for())
        self.assertIn("test function or method", text)
        self.assertIn("Naming only the file or class is not enough", text)
        self.assertIn("not dotted test IDs", text)

    def test_reviewer_runs_on_the_validator_route_with_write_access_for_its_scratch_copy(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            request = autoreview.prepare(state, review_job.STAGE, "/run/state.json", None)
        self.assertEqual(("sol", "sol", True), (request.role, request.route_role, request.allow_write))
        self.assertEqual(review_job.SCHEMA, request.schema)
        self.assertIn("Review pr-184.patch", request.prompt)
        self.assertIn("review/findings.json", request.prompt)
        self.assertEqual("REVIEWING", state["phase"])
        # Scratch under .autocode/, never outside the workspace: the provider sandbox denies
        # external directories and the attempt is lost (live run, 2026-10-01).
        self.assertIn(".autocode/scratch", request.prompt)
        self.assertNotIn("OUTSIDE the workspace", request.prompt)


SEED = {"calc.py": "def double(n):\n    return n * 2\n"}
PATCH = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def double(n):
-    return n * 2
+    return n + 2
"""


class ProofTests(unittest.TestCase):
    """The runner applies the change in a scratch copy and runs the delivered tests; no model."""

    def test_filename_only_id_is_rejected_with_method_naming_guidance(self):
        value = report()
        value["findings"][0]["untestable"] = ""
        delivered = ["review/tests/test_f1_behavior.py"]
        test_id = "review.tests.test_f1_behavior.BehaviorTests.test_real_path"
        with self.assertRaisesRegex(ValueError, "test function or method.*not just the file or class"):
            review_job.prove(value, delivered, lambda tests, patch: {
                "results": {"failed": [test_id]}})
        # Keep the same finding and file; naming the actual method proves the finding.
        named_test = test_id.replace("test_real_path", "test_f1_real_path")
        proof = review_job.prove(value, delivered, lambda tests, patch: {
            "results": {"failed": [named_test]}})
        self.assertEqual({"F1": [named_test]}, proof["finding_tests"])

    def workspace(self, test_body):
        import subprocess
        root = Path(tempfile.mkdtemp(prefix="review-proof-"))
        (root / "calc.py").write_text(SEED["calc.py"])
        (root / "pr-1.patch").write_text(PATCH)
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.test",
                                                      "commit", "-qm", "seed"]):
            subprocess.run(["git", *args], cwd=root, check=True)
        (root / "review" / "tests").mkdir(parents=True)
        (root / "review" / "tests" / "test_double.py").write_text(
            "import unittest\nfrom calc import double\n\n\nclass DoubleTests(unittest.TestCase):\n" + test_body)
        return root

    def apply(self, root, findings):
        import autocode_verify as verify
        state = state_for(workspace=str(root))
        value = {**report(findings=findings), "change_patch": "pr-1.patch", "delivered_tests": []}
        review_job.apply(state, value, {"changed_files": ["review/tests/test_double.py"], "output": str(root / "o.json")},
                         root, run_tests=lambda tests, patch: verify.scratch_run(
                             root, Path(tempfile.mkdtemp()), patch=patch, tests=tests, timeout=120))
        return state

    def finding(self, **overrides):
        return {"id": "F1", "severity": "blocking", "file": "calc.py", "lines": [2, 2], "summary": "adds instead",
                "evidence": "double(3) is 5", "example": "Given the patch; when double(3) runs; then it returns 5 "
                "(expected 6)", "untestable": "", **overrides}

    def test_a_blocking_finding_is_accepted_when_its_test_fails_on_the_change(self):
        root = self.workspace("    def test_f1_doubles(self):\n        self.assertEqual(6, double(3))\n")
        state = self.apply(root, [self.finding()])
        self.assertEqual({"F1": ["review.tests.test_double.DoubleTests.test_f1_doubles"]},
                         state["review"]["finding_tests"])
        written = json.loads((root / "review" / "findings.json").read_text())
        self.assertEqual(["review.tests.test_double.DoubleTests.test_f1_doubles"], written["findings"][0]["proven_by"])
        # The workspace itself was never patched.
        self.assertEqual(SEED["calc.py"], (root / "calc.py").read_text())

    def test_an_invented_finding_whose_test_passes_on_the_change_is_rejected(self):
        root = self.workspace("    def test_f1_doubles_zero(self):\n        self.assertEqual(2, double(0))\n")
        with self.assertRaisesRegex(ValueError, r"no delivered test, named after them, that fails.*\['F1'\]"):
            self.apply(root, [self.finding()])

    def test_a_test_not_named_after_the_finding_does_not_count(self):
        root = self.workspace("    def test_doubles(self):\n        self.assertEqual(6, double(3))\n")
        with self.assertRaisesRegex(ValueError, "named after them"):
            self.apply(root, [self.finding()])

    def test_every_blocking_finding_needs_an_example(self):
        root = self.workspace("    def test_f1_doubles(self):\n        self.assertEqual(6, double(3))\n")
        with self.assertRaisesRegex(ValueError, "example of the defect"):
            self.apply(root, [self.finding(example=" ")])

    def test_an_untestable_finding_says_why_and_needs_no_test(self):
        root = self.workspace("    def test_nothing(self):\n        pass\n")
        state = self.apply(root, [self.finding(untestable="The docstring promises O(1); no test can time it here")])
        self.assertEqual({}, state["review"]["finding_tests"])

    def test_a_patch_that_does_not_apply_is_reported(self):
        root = self.workspace("    def test_f1_doubles(self):\n        self.assertEqual(6, double(3))\n")
        (root / "pr-1.patch").write_text(PATCH.replace("n * 2", "n * 9"))
        with self.assertRaisesRegex(ValueError, "could not run the delivered tests.*git apply"):
            self.apply(root, [self.finding()])


class ApplyTests(unittest.TestCase):
    def test_writes_the_findings_file_and_completes_the_run(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            state["workflow"] = {"kind": "review"}
            review_job.apply(state, report(), {"changed_files": [], "output": "/run/review_change-01.json"}, workspace)
            written = json.loads((Path(workspace) / "review" / "findings.json").read_text())
        self.assertEqual("request_changes", written["verdict"])
        self.assertEqual(["F1"], [f["id"] for f in written["findings"]])
        view = run_view.view(state)
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])
        self.assertEqual("review", view["workflow"])
        self.assertEqual({"blocking": 1, "advisory": 0}, {k: state["review"][k] for k in ("blocking", "advisory")})
        self.assertIn("1 blocking, 0 advisory", review_job.render(state))

    def test_a_review_that_changed_the_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            with self.assertRaisesRegex(ValueError, "must not change the repository.*regclient/client.py"):
                review_job.apply(state, report(), {"changed_files": ["regclient/client.py", "review/notes.md"]}, workspace)
            self.assertFalse((Path(workspace) / "review" / "findings.json").exists())
        self.assertEqual("RUNNING", state["status"])

    def test_writing_under_review_is_allowed(self):
        self.assertEqual([], review_job.stray_changes(["review/findings.json", "review/tests/test_x.py"]))
        self.assertEqual(["tests/test_x.py"], review_job.stray_changes(["review/a.json", "tests/test_x.py"]))

    def test_approve_with_a_blocking_finding_is_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(ValueError, "cannot approve"):
                review_job.apply(state_for(workspace=workspace), report(verdict="approve"), {"changed_files": []}, workspace)

    def test_delivered_targeted_tests_are_recorded_when_they_exist(self):
        with tempfile.TemporaryDirectory() as workspace:
            test = Path(workspace) / "review" / "tests" / "test_at_policy.py"
            test.parent.mkdir(parents=True)
            test.write_text("import unittest\n")
            (Path(workspace) / "review" / "tests" / "test_forgotten.py").write_text("import unittest\n")
            state = state_for(workspace=workspace)
            record = {"changed_files": ["review/tests/test_at_policy.py", "review/tests/test_forgotten.py"]}
            review_job.apply(state, report(delivered_tests=["review/tests/test_at_policy.py"]), record, workspace)
            written = json.loads((Path(workspace) / "review" / "findings.json").read_text())
        # The one the stage wrote but the report forgot is recorded too.
        self.assertEqual(["review/tests/test_at_policy.py", "review/tests/test_forgotten.py"], written["delivered_tests"])
        self.assertIn("Targeted test delivered: review/tests/test_at_policy.py", review_job.render(state))

    def test_a_report_that_claims_an_undelivered_or_misplaced_test_is_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(ValueError, "not delivered"):
                review_job.apply(state_for(workspace=workspace),
                                 report(delivered_tests=["review/tests/test_missing.py"]), {"changed_files": []}, workspace)
            with self.assertRaisesRegex(ValueError, "must live under review/tests/"):
                review_job.apply(state_for(workspace=workspace),
                                 report(delivered_tests=["tests/test_x.py"]), {"changed_files": []}, workspace)

    def test_a_clean_approval_completes_with_no_findings(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            review_job.apply(state, report(verdict="approve", findings=[]), {"changed_files": []}, workspace)
            written = json.loads((Path(workspace) / "review" / "findings.json").read_text())
        self.assertEqual(("approve", []), (written["verdict"], written["findings"]))
        self.assertEqual("TASK_COMPLETE", state["status"])


if __name__ == "__main__":
    unittest.main()
