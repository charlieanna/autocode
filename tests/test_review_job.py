"""The review workflow: a recognized review goes straight to the Reviewer, which
changes nothing and leaves review/findings.json behind."""

import hashlib
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
    return {
        "version": 3,
        "task": task,
        "workspace": workspace,
        "status": "RUNNING",
        "stages": [],
        "settings": {
            "joint_planning": True,
            "roles": {
                "requirements": {"model": "r"},
                "glm": {"model": "g"},
                "plan_reviewer": {"model": "p"},
                "astra": {"model": "a"},
                "terra": {"model": "t"},
                "sol": {"model": "s"},
            },
        },
    }


def report(verdict="request_changes", findings=None, delivered_tests=()):
    return {
        "verdict": verdict,
        "summary": "one regression",
        "change_under_review": "pr-184.patch",
        "findings": findings
        if findings is not None
        else [
            {
                "id": "F1",
                "severity": "blocking",
                "file": "regclient/client.py",
                "lines": [26, 28],
                "summary": "resends without reconciling",
                "evidence": "README Retries",
                "example": "Given a timeout after the registry applied a renew; when renew() retries; then it "
                "sends a second renew (expected: it polls first)",
                "untestable": "The README's retry rule is the contract; the fixture has no registry to replay",
            }
        ],
        "change_patch": "pr-184.patch",
        "tests_run": ["python3 -m unittest"],
        "delivered_tests": list(delivered_tests),
    }


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
            review_job.prove(value, delivered, lambda tests, patch: {"results": {"failed": [test_id]}})
        # Keep the same finding and file; naming the actual method proves the finding.
        named_test = test_id.replace("test_real_path", "test_f1_real_path")
        proof = review_job.prove(value, delivered, lambda tests, patch: {"results": {"failed": [named_test]}})
        self.assertEqual({"F1": [named_test]}, proof["finding_tests"])

    def workspace(self, test_body):
        import subprocess

        root = Path(tempfile.mkdtemp(prefix="review-proof-"))
        (root / "calc.py").write_text(SEED["calc.py"])
        (root / "pr-1.patch").write_text(PATCH)
        for args in (
            ["init", "-q"],
            ["add", "-A"],
            ["-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed"],
        ):
            subprocess.run(["git", *args], cwd=root, check=True)
        (root / "review" / "tests").mkdir(parents=True)
        (root / "review" / "tests" / "test_double.py").write_text(
            "import unittest\nfrom calc import double\n\n\nclass DoubleTests(unittest.TestCase):\n" + test_body
        )
        return root

    def apply(self, root, findings):
        import autocode_review_proof as review_proof
        import autocode_verify as verify

        state = state_for(workspace=str(root))
        value = {**report(findings=findings), "change_patch": "pr-1.patch", "delivered_tests": []}
        review_job.apply(
            state,
            value,
            {"changed_files": ["review/tests/test_double.py"], "output": str(root / "o.json")},
            root,
            run_tests=lambda tests, patch: review_proof.collect(
                root, Path(tempfile.mkdtemp()), tests, patch, verify.scratch_run, timeout=120
            ),
        )
        return state

    def finding(self, **overrides):
        return {
            "id": "F1",
            "severity": "blocking",
            "file": "calc.py",
            "lines": [2, 2],
            "summary": "adds instead",
            "evidence": "double(3) is 5",
            "example": "Given the patch; when double(3) runs; then it returns 5 (expected 6)",
            "untestable": "",
            **overrides,
        }

    def test_a_blocking_finding_is_accepted_when_its_test_fails_on_the_change(self):
        root = self.workspace("    def test_f1_doubles(self):\n        self.assertEqual(6, double(3))\n")
        state = self.apply(root, [self.finding()])
        self.assertEqual(
            {"F1": ["review.tests.test_double.DoubleTests.test_f1_doubles"]}, state["review"]["finding_tests"]
        )
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
            raw = (Path(workspace) / "review" / "findings.json").read_bytes()
            written = json.loads(raw)
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(digest, state["review"]["report_sha256"])
        self.assertEqual("pr-184.patch", written["change_patch"])
        self.assertEqual("request_changes", written["verdict"])
        self.assertEqual(["F1"], [f["id"] for f in written["findings"]])
        view = run_view.view(state)
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])
        self.assertEqual("review", view["workflow"])
        self.assertEqual(digest, view["evidence"]["workflow_result"]["artifact_sha256"])
        self.assertEqual({"blocking": 1, "advisory": 0}, {k: state["review"][k] for k in ("blocking", "advisory")})
        self.assertIn("1 blocking, 0 advisory", review_job.render(state))

    def test_legacy_review_projection_keeps_unknown_artifact_hash(self):
        state = state_for()
        state.update(workflow={"kind": "review"}, review={"verdict": "approve", "report_path": "review/findings.json"})
        self.assertEqual(
            {
                "outcome": "approve",
                "artifact": "review/findings.json",
                "artifact_sha256": None,
                "summary": "0 blocking, 0 advisory findings",
            },
            run_view.view(state)["evidence"]["workflow_result"],
        )

    def test_a_review_that_changed_the_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            with self.assertRaisesRegex(ValueError, "must not change the repository.*regclient/client.py"):
                review_job.apply(
                    state, report(), {"changed_files": ["regclient/client.py", "review/notes.md"]}, workspace
                )
            self.assertFalse((Path(workspace) / "review" / "findings.json").exists())
        self.assertEqual("RUNNING", state["status"])

    def test_writing_under_review_is_allowed(self):
        self.assertEqual([], review_job.stray_changes(["review/findings.json", "review/tests/test_x.py"]))
        self.assertEqual(["tests/test_x.py"], review_job.stray_changes(["review/a.json", "tests/test_x.py"]))

    def test_model_approval_is_corrected_when_a_blocking_finding_remains(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            review_job.apply(state, report(verdict="approve"), {"changed_files": []}, workspace)
            self.assertEqual("request_changes", state["review"]["verdict"])
            self.assertEqual("approve", state["review"]["submitted_verdict"])
            self.assertTrue(state["review"]["verdict_adjustment"])

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
        self.assertEqual(
            ["review/tests/test_at_policy.py", "review/tests/test_forgotten.py"], written["delivered_tests"]
        )
        self.assertIn("Targeted test delivered: review/tests/test_at_policy.py", review_job.render(state))

    def test_a_report_that_claims_an_undelivered_or_misplaced_test_is_rejected(self):
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(ValueError, "not delivered"):
                review_job.apply(
                    state_for(workspace=workspace),
                    report(delivered_tests=["review/tests/test_missing.py"]),
                    {"changed_files": []},
                    workspace,
                )
            with self.assertRaisesRegex(ValueError, "must live under review/tests/"):
                review_job.apply(
                    state_for(workspace=workspace),
                    report(delivered_tests=["tests/test_x.py"]),
                    {"changed_files": []},
                    workspace,
                )

    def test_a_clean_approval_completes_with_no_findings(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace=workspace)
            review_job.apply(state, report(verdict="approve", findings=[]), {"changed_files": []}, workspace)
            written = json.loads((Path(workspace) / "review" / "findings.json").read_text())
        self.assertEqual(("approve", []), (written["verdict"], written["findings"]))
        self.assertEqual("TASK_COMPLETE", state["status"])


if __name__ == "__main__":
    unittest.main()


class AttributionTests(unittest.TestCase):
    """Named runner evidence drives verdicts; no commands or providers run here."""

    TEST = "review.tests.test_example.Probes.test_f1_empty"
    OTHER = "review.tests.test_example.Probes.test_f1_another"

    def run_receipt(self, *, passed=(), failed=(), skipped=(), details=None, **extra):
        return {
            "exit_code": 1 if failed else 0,
            "timed_out": False,
            "error": "",
            "results": {
                "passed": list(passed),
                "failed": list(failed),
                "skipped": list(skipped),
                "total": len(passed) + len(failed) + len(skipped),
                "complete": True,
            },
            "failure_details": details
            or {name: {"format": "unittest", "traceback": "same full failure"} for name in failed},
            "proof": {"path": "/proof.json", "sha256": "a" * 64},
            **extra,
        }

    def compare(self, base, changed=None, *, patch="change.patch", **extra):
        value = report(findings=[{**report()["findings"][0], "untestable": ""}])
        value["change_patch"] = patch
        changed = changed or self.run_receipt(failed=[self.TEST])
        return review_job.prove(
            value,
            ["review/tests/test_example.py"],
            lambda *_: {
                "base": base,
                "changed": changed,
                "inputs": {"path": "/source.json", "sha256": "b" * 64},
                "changed_locations": {"regclient/client.py": {"line_count": 40, "changed": []}},
                **extra,
            },
        )["dispositions"]["F1"]

    def test_base_pass_change_fail_is_introduced(self):
        self.assertEqual("introduced", self.compare(self.run_receipt(passed=[self.TEST]))["kind"])

    def test_equivalent_complete_failures_are_pre_existing(self):
        import autocode_review_attribution as policy

        row = self.compare(self.run_receipt(failed=[self.TEST]))
        self.assertEqual("pre_existing", row["kind"])
        self.assertFalse(policy.blocking({"severity": "blocking", "disposition": row}))
        self.assertEqual("inconclusive", self.compare(self.run_receipt(failed=[self.TEST]), patch="")["kind"])

    def test_identical_failures_do_not_exempt_findings_on_changed_source(self):
        base = self.run_receipt(failed=[self.TEST])
        for locations in (
            None,
            {},
            {"regclient/client.py": {"line_count": 40, "changed": [[26, 26]]}},
            {"regclient/client.py": {"line_count": 40, "changed": [[27, 28]]}},
        ):
            with self.subTest(locations=locations):
                row = self.compare(base, changed_locations=locations)
                self.assertEqual("inconclusive", row["kind"])
                self.assertIn("blocker retained", row["reason"])
        self.assertEqual(
            "pre_existing",
            self.compare(base, changed_locations={"regclient/client.py": {"line_count": 40, "changed": [[1, 2]]}})[
                "kind"
            ],
        )
        self.assertEqual(
            "introduced", self.compare(self.run_receipt(passed=[self.TEST]), changed_locations=None)["kind"]
        )

    def test_unknown_alias_or_out_of_range_locations_never_exempt(self):
        import autocode_review_attribution as policy

        locations = {"calc.py": {"line_count": 10, "changed": [[2, 2]]}}
        for path, lines in (
            ("./calc.py", [2]),
            ("../calc.py", [2]),
            ("/calc.py", [2]),
            ("unknown.py", [1]),
            ("calc.py", [11]),
            ("calc.py", [0]),
            ("calc.py", [True]),
        ):
            with self.subTest(path=path, lines=lines):
                self.assertFalse(policy.unchanged_location({"file": path, "lines": lines}, locations))
        self.assertTrue(policy.unchanged_location({"file": "calc.py", "lines": [5]}, locations))

    def test_different_site_type_or_diagnostic_is_inconclusive(self):
        for detail in ({}, {self.TEST: {"format": "unittest", "traceback": "different site"}}):
            base = self.run_receipt(failed=[self.TEST])
            base["failure_details"] = detail
            self.assertEqual("inconclusive", self.compare(base)["kind"])

    def test_regression_wins_for_multiple_probes_and_unresolved_probe_retains_blocker(self):
        changed = self.run_receipt(failed=[self.TEST, self.OTHER])
        mixed = self.run_receipt(failed=[self.TEST], passed=[self.OTHER])
        self.assertEqual("introduced", self.compare(mixed, changed)["kind"])
        missing = self.run_receipt(failed=[self.TEST], skipped=[self.OTHER])
        self.assertEqual("inconclusive", self.compare(missing, changed)["kind"])
        self.assertEqual("pre_existing", self.compare(changed, changed)["kind"])

    def test_baseline_infrastructure_missing_setup_skipped_and_incomplete_never_exempt(self):
        for update in ({"error": "runner broke"}, {"timed_out": True}, {"interrupted": True}, {"exit_code": 2}):
            with self.subTest(update=update):
                self.assertEqual("inconclusive", self.compare(self.run_receipt(failed=[self.TEST], **update))["kind"])
        for update in (
            {"complete": False},
            {"total": 2},
            {"collection_errors": [self.TEST]},
            {"setup_errors": {self.TEST: "missing mock"}},
            {"failed": [], "skipped": [self.TEST]},
            {"failed": [], "total": 0},
        ):
            base = self.run_receipt(failed=[self.TEST])
            base["results"].update(update)
            with self.subTest(update=update):
                self.assertEqual("inconclusive", self.compare(base)["kind"])
        self.assertEqual("inconclusive", self.compare({})["kind"])

    def test_invalid_changed_proof_is_rejected_even_with_equivalent_baseline(self):
        for update in ({"timed_out": True}, {"error": "runner failed"}):
            with self.assertRaises(ValueError):
                self.compare(self.run_receipt(failed=[self.TEST]), self.run_receipt(failed=[self.TEST], **update))
        with self.assertRaises(ValueError):
            self.compare(self.run_receipt(failed=[self.TEST]), self.run_receipt(passed=[self.TEST]))

    def test_legacy_and_model_supplied_dispositions_cannot_exempt(self):
        import autocode_review_attribution as policy

        for row in (None, "pre_existing", {"kind": "pre_existing"}, {"runner": policy.RUNNER, "kind": "pre_existing"}):
            self.assertTrue(policy.blocking({"severity": "blocking", "disposition": row}))

    def test_untestable_finding_cannot_supply_its_own_pre_existing_disposition(self):
        with tempfile.TemporaryDirectory() as temp:
            finding = report()["findings"][0]
            finding["disposition"] = self.compare(self.run_receipt(failed=[self.TEST]))
            state = state_for(workspace=temp)
            review_job.apply(state, report(findings=[finding]), {"changed_files": []}, temp)
            saved = json.loads((Path(temp) / "review/findings.json").read_text())
            self.assertEqual("inconclusive", saved["findings"][0]["disposition"]["kind"])
            self.assertEqual("request_changes", saved["verdict"])
            self.assertEqual(1, state["review"]["blocking"])

    def test_report_state_view_and_followup_use_postclassification_verdict(self):
        import autocode_follow_up as follow_up

        for additional in ([], [{**report()["findings"][0], "id": "F2"}]):
            with self.subTest(mixed=bool(additional)), tempfile.TemporaryDirectory() as temp:
                workspace = Path(temp)
                test = workspace / "review/tests/test_example.py"
                test.parent.mkdir(parents=True)
                test.write_text("# delivered probe")
                state = state_for(workspace=str(workspace))
                state["workflow"] = {"kind": "review"}
                value = report(
                    findings=[{**report()["findings"][0], "untestable": ""}, *additional],
                    delivered_tests=["review/tests/test_example.py"],
                )
                run = self.run_receipt(failed=[self.TEST])
                review_job.apply(
                    state,
                    value,
                    {"changed_files": []},
                    workspace,
                    run_tests=lambda *_: {
                        "base": run,
                        "changed": run,
                        "inputs": {"path": "/source.json", "sha256": "b" * 64},
                        "changed_locations": {"regclient/client.py": {"line_count": 40, "changed": []}},
                    },
                )
                saved = json.loads((workspace / "review/findings.json").read_text())
                wanted = "request_changes" if additional else "approve"
                self.assertEqual(wanted, saved["verdict"])
                self.assertEqual(wanted, state["review"]["verdict"])
                view = run_view.view(state)["evidence"]["workflow_result"]
                self.assertEqual(wanted, view["outcome"])
                self.assertEqual(1, view["review_attribution"]["pre_existing"])
                carried = follow_up.carried_review(state, workspace)
                self.assertEqual([f["id"] for f in additional], [f["id"] for f in carried["blocking"]])
                self.assertEqual(saved["findings"][0]["disposition"], carried["pre_existing"][0]["disposition"])
                self.assertIn("1 pre-existing observations", review_job.render(state))
