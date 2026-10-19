"""Review comparison capture and full Python failure diagnostics, without providers."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_review_attribution as attribution
import autocode_review_proof as proof
import autocode_source_snapshot as source_snapshot
import autocode_util as util
import autocode_verify as verify

from tests.test_review_job import PATCH

NAME = "review.tests.test_example.Probes.test_f1_empty"


def synthetic_receipt():
    return {
        "exit_code": 1,
        "timed_out": False,
        "error": "",
        "results": {"passed": [], "failed": [NAME], "skipped": [], "total": 1, "complete": True},
    }


class FailureDetailsTests(unittest.TestCase):
    def details(self, root, *, site=10, exception="AssertionError", message="empty input", fixture=False):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        name = "setUpClass" if fixture else "test_f1_empty"
        output = root / "output.log"
        output.write_text(
            f"{name} (review.tests.test_example.Probes.{name}) ... FAIL\n"
            + "=" * 70
            + f"\nFAIL: {name} (review.tests.test_example.Probes.{name})\n"
            + "-" * 70
            + f'\nTraceback (most recent call last):\n  File "{root}/review/tests/test_example.py", line {site}, in {name}\n'
            + f"    self.assertEqual(1, parse(''))\n{exception}: {message}\n\n"
            + "-" * 70
            + "\nRan 1 test in 0.001s\n\nFAILED (failures=1)\n"
        )
        return attribution.failure_details(
            {"command": "python -m unittest -v review/tests/test_example.py", "output": str(output)}, root, None
        )

    def test_only_known_scratch_paths_are_normalized_full_diagnostics_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self.details(root / "base")
            self.assertIn(NAME, base)
            self.assertEqual(base, self.details(root / "changed"))
            for changes in ({"site": 11}, {"exception": "ValueError"}, {"message": "different input"}):
                self.assertNotEqual(base, self.details(root / "changed", **changes))
            long = "prefix " * 1000 + " final diagnostic"
            self.assertIn(long, self.details(root / "long", message=long)[NAME]["traceback"])

    def test_pytest_full_failure_and_error_are_distinguished(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml = root / "result.xml"
            xml.write_text(
                '<testsuite><testcase classname="review.tests.test_example.Probes" name="test_f1_empty">'
                "<failure>E   AssertionError: empty\nreview/tests/test_example.py:10: AssertionError</failure></testcase></testsuite>"
            )
            receipt = {"command": "python -m pytest review/tests/test_example.py"}
            details = attribution.failure_details(receipt, root, xml)
            self.assertTrue(details)
            xml.write_text(xml.read_text().replace("<failure>", "<error>").replace("</failure>", "</error>"))
            self.assertEqual({}, attribution.failure_details(receipt, root, xml))

    def test_unittest_setup_tracebacks_are_not_application_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.details(root)
            path = root / "output.log"
            path.write_text(path.read_text().replace("in test_f1_empty", "in setUp"))
            errors = attribution.preparation_errors(
                {"command": "python -m unittest -v review/tests/test_example.py", "output": str(path)}, None
            )
            self.assertIn(NAME, errors)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="review-comparison-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace, self.out = self.root / "project", self.root / "evidence"
        self.workspace.mkdir()
        (self.workspace / "calc.py").write_text("def double(n):\n    return n * 2\n")
        (self.workspace / "change.patch").write_text(PATCH)
        test = self.workspace / "review/tests/test_example.py"
        test.parent.mkdir(parents=True)
        test.write_text("# captured delivered tests\n")
        self.tests = ["review/tests/test_example.py"]
        for args in (
            ("init", "-q"),
            ("add", "-A"),
            ("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"),
        ):
            subprocess.run(["git", *args], cwd=self.workspace, check=True, capture_output=True)
        self.calls = []

    def runner(self, workspace, out, **kwargs):
        self.calls.append((out, kwargs))
        tree = verify.prepare_scratch_tree(
            workspace,
            out,
            patch=kwargs["patch"],
            files=kwargs.get("files"),
            source_paths=kwargs.get("source_paths", ()),
            review_source=kwargs.get("review_source"),
        )
        try:
            self.assertEqual("# captured delivered tests\n", (tree / self.tests[0]).read_text())
            self.assertEqual(
                "return n + 2" if kwargs["patch"] else "return n * 2",
                (tree / "calc.py").read_text().splitlines()[-1].strip(),
            )
            receipt = synthetic_receipt()
            receipt["review_locations"] = proof.source_locations(tree, kwargs["review_source"])
            receipt.update(command="python -m unittest -v review/tests/test_example.py", output=str(out / "test.log"))
            (out / "test.log").write_text("synthetic named receipt; no command executed")
            return receipt
        finally:
            verify.remove_tree(workspace, tree)

    def test_identical_captured_inputs_distinct_proof_directories_workspace_unchanged(self):
        before = source_snapshot.snapshot(self.workspace)
        result = proof.collect(self.workspace, self.out, self.tests, "change.patch", self.runner, timeout=10)
        self.assertEqual(before, source_snapshot.snapshot(self.workspace))
        self.assertEqual(["changed", "base"], [out.name for out, _ in self.calls])
        for side in ("changed", "base"):
            path = Path(result[side]["proof"]["path"])
            self.assertTrue(path.is_file())
            self.assertEqual(util.file_hash(path), result[side]["proof"]["sha256"])
        manifest = Path(result["inputs"]["path"])
        self.assertEqual(before["files"], json.loads(manifest.read_text())["files"])
        self.assertEqual((self.workspace / "calc.py").read_bytes(), (manifest.parent / "inputs/calc.py").read_bytes())
        self.assertNotEqual(self.calls[0][0], self.calls[1][0])

    def test_locations_use_actual_applied_bytes_even_when_git_offsets_the_hunk(self):
        original = (self.workspace / "calc.py").read_text()
        (self.workspace / "calc.py").write_text("# extra line\n" * 10 + original)
        (self.workspace / "change.patch").write_text(PATCH.replace("@@ -1,2 +1,2 @@", "@@ -2,2 +2,2 @@"))
        result = proof.collect(self.workspace, self.out, self.tests, "change.patch", self.runner, timeout=10)
        locations = result["changed_locations"]
        self.assertEqual({"line_count": 12, "changed": [[12, 12]]}, locations["calc.py"])
        self.assertTrue(attribution.unchanged_location({"file": "calc.py", "lines": [2]}, locations))
        self.assertFalse(attribution.unchanged_location({"file": "calc.py", "lines": [12]}, locations))

    def test_actual_added_deleted_and_binary_source_locations_are_conservative(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "inputs").mkdir()
            tree = root / "tree"
            tree.mkdir()
            (root / "inputs/modified.py").write_text("a\nb\nc\n")
            (tree / "modified.py").write_text("a\nc\n")
            (tree / "added.py").write_text("new\nline\n")
            (root / "inputs/deleted.py").write_text("old\n")
            (tree / "binary.py").write_bytes(b"\0new")
            manifest = root / "source.json"
            util.atomic_json(
                manifest,
                {
                    "source_lines": {"modified.py": 3, "deleted.py": 1, "binary.py": 1},
                    "patch_changes": [
                        {"path": name} for name in ("modified.py", "added.py", "deleted.py", "binary.py")
                    ],
                },
            )
            self.assertEqual(
                {
                    "modified.py": {"line_count": 2, "changed": [[1, 2]]},
                    "added.py": {"line_count": 2, "changed": [[1, 2]]},
                },
                proof.source_locations(tree, manifest),
            )

    def test_inline_separators_do_not_shift_physical_source_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "inputs").mkdir()
            tree = root / "tree"
            tree.mkdir()
            before = "# prefix\vstill same line\nx = 1\n"
            (root / "inputs/calc.py").write_text(before)
            (tree / "calc.py").write_text(before.replace("x = 1", "x = 2"))
            manifest = root / "source.json"
            util.atomic_json(manifest, {"source_lines": {"calc.py": 2}, "patch_changes": [{"path": "calc.py"}]})
            locations = proof.source_locations(tree, manifest)
            self.assertEqual({"calc.py": {"line_count": 2, "changed": [[2, 2]]}}, locations)
            self.assertFalse(attribution.unchanged_location({"file": "calc.py", "lines": [2]}, locations))

    def test_probe_cannot_change_source_or_its_own_test_during_execution(self):
        def execute(_framework, _command, tree, *_args, **_kwargs):
            (Path(tree) / "calc.py").write_text("modified by delivered probe")
            return synthetic_receipt()

        def runner(workspace, out, **kwargs):
            with patch.object(verify, "run_suite", side_effect=execute):
                return verify.scratch_run(workspace, out, **kwargs)

        with self.assertRaisesRegex(ValueError, "Review probes changed their source"):
            proof.collect(self.workspace, self.out, self.tests, "change.patch", runner, timeout=10)
        self.assertEqual("def double(n):\n    return n * 2\n", (self.workspace / "calc.py").read_text())

    def test_captured_test_bytes_cannot_change_between_proofs(self):
        def runner(workspace, out, **kwargs):
            receipt = self.runner(workspace, out, **kwargs)
            Path(kwargs["files"][self.tests[0]]).write_text("different test")
            return receipt

        with self.assertRaisesRegex(ValueError, "Captured review source or tests changed"):
            proof.collect(self.workspace, self.out, self.tests, "change.patch", runner, timeout=10)

    def test_empty_patch_never_runs_a_self_comparison(self):
        def runner(*_args, **kwargs):
            self.assertIsNone(kwargs["patch"])
            return synthetic_receipt()

        with patch.object(source_snapshot, "snapshot", side_effect=AssertionError("must not infer a base")):
            result = proof.collect(self.workspace, self.out, self.tests, None, runner, timeout=10)
        self.assertEqual({}, result["base"])
        self.assertIsNone(result["inputs"])
        self.assertIn("No explicit change_patch", result["reason"])

    def test_base_runner_error_is_retained_as_inconclusive_evidence(self):
        def runner(workspace, out, **kwargs):
            if out.name == "base":
                raise RuntimeError("baseline runner error")
            return self.runner(workspace, out, **kwargs)

        result = proof.collect(self.workspace, self.out, self.tests, "change.patch", runner, timeout=10)
        self.assertIn("baseline runner error", result["base"]["error"])
        self.assertTrue(Path(result["base"]["proof"]["path"]).is_file())

    def test_source_changes_after_capture_are_refused(self):
        def runner(workspace, out, **kwargs):
            receipt = self.runner(workspace, out, **kwargs)
            (self.workspace / "calc.py").write_text("changed outside proof")
            return receipt

        with self.assertRaisesRegex(ValueError, "Review source changed"):
            proof.collect(self.workspace, self.out, self.tests, "change.patch", runner, timeout=10)

    def test_copied_source_must_match_capture_before_patch(self):
        def runner(workspace, out, **kwargs):
            with patch.object(verify, "make_tree") as make:
                tree = self.root / "different"
                shutil.copytree(self.workspace, tree)
                (tree / "calc.py").write_text("different source")
                make.return_value = tree
                return verify.prepare_scratch_tree(workspace, out, review_source=kwargs["review_source"])

        with self.assertRaisesRegex(ValueError, "Review source changed"):
            proof.collect(self.workspace, self.out, self.tests, "change.patch", runner, timeout=10)

    def test_uncertain_ownership_still_holds_instead_of_becoming_a_baseline_error(self):
        def runner(workspace, out, **kwargs):
            if out.name == "base":
                raise util.Paused("PAUSED_TEST", "ownership uncertain")
            return self.runner(workspace, out, **kwargs)

        with self.assertRaises(util.Paused):
            proof.collect(self.workspace, self.out, self.tests, "change.patch", runner, timeout=10)


class ExternalReviewPolicyTests(unittest.TestCase):
    def test_only_validated_pre_existing_observations_allow_external_acceptance(self):
        from contextlib import ExitStack

        import autocode_verify_record as records

        ref = {"path": "/bound-proof.json", "sha256": "a" * 64}
        disposition = {
            "runner": attribution.RUNNER,
            "kind": "pre_existing",
            "reason": "Equivalent full failures",
            "base_proof": ref,
            "changed_proof": ref,
            "inputs": ref,
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "review").mkdir()
            for name in ("criteria.json", "check-request.json", "review/findings.json"):
                (root / name).write_text("{}")
            for row, accepted in (
                ({"severity": "blocking", "disposition": disposition}, True),
                ({"severity": "blocking"}, False),
                ({"severity": "blocking", "disposition": {"kind": "pre_existing"}}, False),
            ):
                with self.subTest(row=row), ExitStack() as stack:
                    stack.enter_context(patch.object(records, "evidence_pins", return_value={}))
                    stack.enter_context(patch.object(records.completion, "completion_ready", return_value=True))
                    stack.enter_context(patch.object(records.aggregate, "combine", return_value={}))
                    stack.enter_context(patch.object(records.document, "build", return_value={"unverified": []}))
                    stack.enter_context(patch.object(records.export, "write", return_value={}))
                    stack.enter_context(
                        patch.object(
                            records, "read", side_effect=lambda out: json.loads((out / "verification.json").read_text())
                        )
                    )
                    receipt = records.publish(
                        root,
                        contract={"sha256": "contract", "intended_outcome": "change works", "acceptance_criteria": []},
                        current={"revision": "source"},
                        proof={"verdict": "PASS"},
                        replay={"verdict": "PASS", "checks": [{}]},
                        child={
                            "document": {
                                "workflow_result": {"artifact_sha256": "hash"},
                                "attempts": [],
                                "provenance": {},
                                "revision": {"source_revision": "source"},
                            },
                            "json_path": "/child.json",
                            "json_sha256": "hash",
                            "markdown_sha256": "hash",
                        },
                        review={"verdict": "approve", "findings": [row]},
                        review_workspace=root,
                        reasons=[],
                        original={"started_at": "now"},
                        original_index="index",
                        candidate=root,
                    )
                    self.assertEqual("PASS" if accepted else "UNVERIFIED", receipt["verdict"])
                    self.assertEqual(
                        [] if accepted else ["Independent review did not approve the external change"],
                        receipt["reasons"],
                    )
