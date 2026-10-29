"""JUnit baseline artifacts authenticate independently of changed-code obligations."""

import copy
import hashlib
import shlex
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import autocode_junit as junit
import autocode_regression as regression
import autocode_util as util
import autocode_verification_schedule as schedule
import autocode_verify as verify

from .test_junit_proof import C1, C2, GATE, GUARD, POM, PRODUCT, REGRESSION, REPORT, SOURCE, TEST
from .test_verify import Project


class JUnitProofArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="junit-proof-artifacts-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.project = Project({"pom.xml": POM, PRODUCT: SOURCE, TEST: GUARD, ".gitignore": "target/\n"})
        cls.addClassCleanup(cls.project.close)
        cls.root = Path(cls.temporary.name).resolve()
        runner = cls.root / "mvn"
        runner.write_text(f"#!{sys.executable}\n" + GATE)
        runner.chmod(0o700)
        command = shlex.join([str(runner), "test"])
        framework = verify.command_framework(command, test_report="junit:" + REPORT)
        directory = cls.root / "regression"
        cls.baseline_root = directory / ("baseline-" + uuid.uuid4().hex)
        cls.proof_root = directory / ("proof-" + uuid.uuid4().hex)
        baseline = verify.baseline(
            cls.project.root,
            cls.project.base,
            cls.baseline_root,
            framework=framework,
            suite_command=command,
            timeout=15,
        )
        cls.project.write({PRODUCT: SOURCE.replace("PARTIAL = 1", "PARTIAL = 2"), TEST: REGRESSION})
        cls.result = verify.verify(
            cls.project.root,
            cls.project.base,
            cls.proof_root,
            framework=framework,
            suite_command=command,
            base_suite=baseline,
            timeout=15,
        )
        if cls.result["verdict"] != verify.PASS:
            raise AssertionError(cls.result)
        cls.result.update(case_tests={"C1": [C1], "C2": [C2]}, case_scope=["C1", "C2"])
        cls.proof_path = cls.proof_root / "verification.json"
        util.atomic_json(cls.proof_path, cls.result)
        cls.state = {
            "goal_contract": {
                "body": {
                    "acceptance_criteria": [
                        {
                            "id": "C1",
                            "criterion": "partial pages count",
                            "verification_method": "test: test_c1_partial",
                        },
                        {
                            "id": "C2",
                            "criterion": "exact pages stay unchanged",
                            "verification_method": "guard: test_c2_exact",
                        },
                    ]
                }
            },
            "regression_proof": {
                "verdict": verify.PASS,
                "source_revision": cls.result["source_revision"],
                "path": str(cls.proof_path),
                "receipt_sha256": util.file_hash(cls.proof_path),
            },
        }

    def test_owned_sibling_baseline_capture_remains_valid_without_inventing_changed_code_evidence(self):
        baseline = self.result["checks"]["suite_on_base"]
        self.assertTrue(schedule.intact(baseline, root=self.baseline_root))
        self.assertFalse(schedule.intact(baseline, root=self.proof_root))
        self.assertTrue(schedule.proof_intact(self.result["checks"], root=self.proof_root))
        # This direct verify.verify fixture records JUnit receipts, not the
        # runner's changed-line measurements or independently accepted reasons.
        self.assertTrue(
            regression.complete(self.state, self.result["source_revision"], all_due=True, require_changed_code=False)
        )
        self.assertFalse(regression.complete(self.state, self.result["source_revision"], all_due=True))

    def test_editing_the_retained_baseline_xml_invalidates_the_whole_proof_and_completion(self):
        baseline = self.result["checks"]["suite_on_base"]
        artifact = Path(baseline["test_report"]["path"])
        original = artifact.read_bytes()
        self.addCleanup(artifact.write_bytes, original)
        artifact.write_bytes(original.replace(b"test_c2_exact", b"test_c2_forged"))
        self.assertFalse(schedule.proof_intact(self.result["checks"], root=self.proof_root))
        self.assertFalse(
            regression.complete(self.state, self.result["source_revision"], all_due=True, require_changed_code=False)
        )
        self.assertFalse(regression.complete(self.state, self.result["source_revision"], all_due=True))

    def capture(self, folder):
        """A lightweight legacy receipt with independently valid XML/stdout hashes."""
        folder.mkdir(parents=True)
        source = self.root / ("source-" + uuid.uuid4().hex)
        source.mkdir()
        (source / "results.xml").write_text(
            '<testsuite tests="1"><testcase classname="Original" name="guard"/></testsuite>'
        )
        metadata, results = junit.capture(source, "junit:results.xml", folder / "capture.xml")
        output = folder / "output.log"
        data = b"Ran 1 test\n"
        output.write_bytes(data)
        return {
            "command": "mvn test",
            "exit_code": 0,
            "timed_out": False,
            "output": str(output),
            "output_sha256": hashlib.sha256(data).hexdigest(),
            "results_format": "junit",
            "results": results,
            "test_report": metadata,
            "report_status": "captured",
        }

    def test_an_arbitrary_sibling_cannot_expand_the_proofs_artifact_boundary(self):
        sibling = self.root / "regression" / ("unrelated-" + uuid.uuid4().hex)
        receipt = self.capture(sibling / "baseline")
        self.assertTrue(schedule.intact(receipt, root=sibling))
        self.assertFalse(schedule.proof_intact({"suite_on_base": receipt}, root=self.proof_root))

    def test_only_the_base_suite_label_may_use_the_owned_baseline_sibling(self):
        receipt = self.result["checks"]["suite_on_base"]
        self.assertTrue(schedule.intact(receipt, root=self.baseline_root))
        for label in ("regression_on_base", "suite_on_candidate", "stability-base-2"):
            with self.subTest(label=label):
                self.assertFalse(schedule.proof_intact({label: receipt}, root=self.proof_root))

    def test_a_matching_baseline_directory_name_outside_the_proof_parent_is_not_owned(self):
        outside = self.root / "other-run" / ("baseline-" + uuid.uuid4().hex)
        receipt = self.capture(outside / "baseline")
        self.assertTrue(schedule.intact(receipt, root=outside))
        self.assertFalse(schedule.proof_intact({"suite_on_base": receipt}, root=self.proof_root))

    def test_a_symlink_cannot_alias_an_owned_baseline_directory(self):
        alias = self.baseline_root.parent / ("baseline-" + uuid.uuid4().hex)
        alias.symlink_to(self.baseline_root, target_is_directory=True)
        receipt = copy.deepcopy(self.result["checks"]["suite_on_base"])
        receipt["output"] = str(alias / "baseline" / Path(receipt["output"]).name)
        self.assertFalse(schedule.proof_intact({"suite_on_base": receipt}, root=self.proof_root))


if __name__ == "__main__":
    unittest.main()
