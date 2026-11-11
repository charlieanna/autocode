"""A fresh proof settles a runner finding whose earlier proof only missed a passing unittest suite (#3037)."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_completion_result as completion
import autocode_git_delivery as delivery
import autocode_proof_findings as findings
import autocode_regression as regression
import autocode_source_scope as source_scope
import autocode_util as util
import autocode_verify_judgment as judgment
import autocode_workflow as workflow

TESTS = ("test_rate", "test_free_over_fifty", "test_zero_weight")
REFUSAL = judgment.BASE_DEFINITION_UNKNOWN.format("cd dist does not name a directory in the base tree")
# The saved command is plain unittest; execution added -v, but the old collector chose pytest.
SAVED = "python -m unittest discover -s tests"
EXECUTED = "python -m unittest discover -v -s tests"


def unittest_log(names=TESTS, *, first="ok", summary=True):
    statuses = [first] + ["ok"] * (len(names) - 1)
    lines = [
        f"{name} (tests.test_shipping.ShippingTests.{name}) ... {s}" for name, s in zip(names, statuses, strict=True)
    ]
    if summary:
        lines += ["", "-" * 70, f"Ran {len(names)} tests in 0.004s", "", "OK"]
    return "\n".join(lines) + "\n"


class RecognitionRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Path(tmp.name).resolve()
        self.contract = {"hash": "contract", "body": {"milestones": [{"id": "M1"}]}}
        context = {"contract_identity": util.digest(self.contract), "test_command": EXECUTED}
        self.old = self.proof_folder("proof-01-old")
        self.new = self.proof_folder("proof-02-new")
        self.old_receipt = {
            "verdict": "UNVERIFIED",
            "failures": [],
            "unverified": [REFUSAL],
            "source_revision": "rev",
            "base": "base",
            "base_patch": None,
            "case_scope": ["AC7", "AC8"],
            "framework": {"name": "pytest", "suite": SAVED},
            "commands": {"suite": SAVED, "suite_source": "explicit", "regression": SAVED},
            "checks": {
                label: self.check(self.old, label, unittest_log(), results=None)
                for label in ("regression_on_base", "regression_on_candidate", "suite_on_base", "suite_on_candidate")
            },
        }
        self.new_receipt = {
            "verdict": "PASS",
            "failures": [],
            "unverified": [],
            "source_revision": "rev",
            "base": "base",
            "base_patch": None,
            "case_scope": ["AC7", "AC8"],
            "framework": {"name": "unittest", "suite": EXECUTED},
            "commands": {"suite": EXECUTED, "suite_source": "explicit", "regression": "python -m unittest -v guards"},
            "checks": {
                label: self.check(self.new, label, unittest_log(), results=self.passed(TESTS))
                for label in ("regression_on_base", "regression_on_candidate", "suite_on_base", "suite_on_candidate")
            },
        }
        self.write()
        old_path, new_path = str(self.old / "verification.json"), str(self.new / "verification.json")
        self.state = {
            "version": 3,
            "workspace": "/work",
            "run_dir": str(self.run),
            "status": "RUNNING",
            "next_stage": "review_completion",
            "goal_contract": self.contract,
            "current_task": {"id": "task", "milestone_id": "M1"},
            "stages": [
                self.stage(old_path, "2026-01-01T00:00:00+00:00"),
                self.stage(new_path, "2026-01-01T00:05:00+00:00"),
            ],
            "regression_proofs": [
                {"verdict": "UNVERIFIED", "source_revision": "rev", "path": old_path, "proof_context": "old"},
                {"verdict": "PASS", "source_revision": "rev", "path": new_path, "proof_context": util.digest(context)},
            ],
            "regression_proof": {"verdict": "UNVERIFIED", "source_revision": "rev", "path": old_path},
        }
        self.row = findings.reconcile(self.state, "rev", required=True, proved=False, reason=REFUSAL)
        self.row["scope"] = {"milestone_id": "M1"}
        self.state["regression_proof"] = {
            "verdict": "PASS",
            "source_revision": "rev",
            "path": new_path,
            "receipt_sha256": util.file_hash(new_path),
            "proved_at": "2026-01-01T00:05:00+00:00",
            "execution_context": context,
        }

    def proof_folder(self, name):
        folder = self.run / "regression" / name
        folder.mkdir(parents=True)
        return folder

    @staticmethod
    def passed(names):
        return {
            "passed": sorted(f"tests.test_shipping.ShippingTests.{n}" for n in names),
            "failed": [],
            "skipped": [],
            "collection_errors": [],
            "uncollected": [],
            "total": len(names),
            "complete": True,
        }

    @staticmethod
    def check(folder, label, text, *, results):
        log = folder / (label + ".log")
        log.write_text(text)
        return {
            "command": EXECUTED,
            "exit_code": 0,
            "timed_out": False,
            "output": str(log),
            "output_sha256": util.file_hash(log),
            "results": results,
            "results_expected": results is not None,
        }

    @staticmethod
    def stage(path, finished_at):
        return {"stage": "regression_proof", "runner_owned": True, "output": path, "finished_at": finished_at}

    def write(self):
        (self.old / "verification.json").write_text(json.dumps(self.old_receipt))
        (self.new / "verification.json").write_text(json.dumps(self.new_receipt))

    def rewrite_new(self):
        self.write()
        self.state["regression_proof"]["receipt_sha256"] = util.file_hash(self.new / "verification.json")

    def review(self):
        with (
            patch.object(regression, "before_review"),
            patch.object(regression, "required", return_value=True),
            patch.object(regression, "complete", return_value=True),
            patch.object(source_scope, "snapshot", return_value={"revision": "rev"}),
            patch.object(delivery, "requirement", return_value=None),
            patch.object(workflow, "enabled", return_value=False),
        ):
            completion.before_review(self.state, "review_completion", "/work", self.run)

    def test_fresh_pass_settles_only_the_runner_finding_from_a_recognition_only_refusal(self):
        self.state["findings_ledger"].append({"id": "tester", "source": "tester", "status": "open", "blocking": True})
        receipt_bytes = (self.old / "verification.json").read_bytes()
        before = copy.deepcopy(self.state)
        self.review()
        self.assertEqual("resolved", self.row["status"])
        self.assertEqual(self.state["regression_proof"]["path"], self.row["resolved_in"])
        self.assertIn(str(self.old / "verification.json"), self.row["resolution_evidence"])
        self.assertIn("did not recognize the passing unittest suite", self.row["resolution_evidence"])
        self.assertEqual(["rev"], self.row["failed_revisions"])
        self.assertEqual("open", self.state["findings_ledger"][1]["status"])
        self.assertEqual(receipt_bytes, (self.old / "verification.json").read_bytes())
        for key in ("current_task", "regression_proof", "regression_proofs", "stages"):
            self.assertEqual(before[key], self.state[key])

    def test_a_suite_written_for_the_runs_package_is_recognized_inside_it(self):
        self.state["settings"] = {"package": "dist"}
        self.old_receipt["commands"]["suite"] = "cd dist && " + SAVED
        self.new_receipt["commands"]["suite"] = "cd dist && " + EXECUTED
        for receipt in (self.old_receipt, self.new_receipt):
            for check in receipt["checks"].values():
                check["command"] = "cd dist && " + EXECUTED
        self.rewrite_new()
        self.review()
        self.assertEqual("resolved", self.row["status"])

    def test_a_fresh_receipt_changed_after_its_proof_keeps_the_finding(self):
        self.new_receipt["notes"] = ["edited after the proof"]
        self.write()
        self.review()
        self.assertEqual("open", self.row["status"])

    def test_without_a_current_passing_proof_the_finding_stays(self):
        with (
            patch.object(regression, "before_review"),
            patch.object(regression, "required", return_value=True),
            patch.object(regression, "complete", return_value=False),
            patch.object(regression, "rejection", return_value=REFUSAL),
            patch.object(source_scope, "snapshot", return_value={"revision": "rev"}),
            patch.object(delivery, "requirement", return_value=None),
            patch.object(workflow, "enabled", return_value=False),
        ):
            completion.before_review(self.state, "review_completion", "/work", self.run)
        self.assertEqual("open", self.row["status"])

    def test_any_other_failure_capture_custody_context_or_inventory_keeps_the_finding(self):
        old_check = lambda label: self.old_receipt["checks"][label]  # noqa: E731

        def relog(label, text):
            log = Path(old_check(label)["output"])
            log.write_text(text)
            old_check(label)["output_sha256"] = util.file_hash(log)

        def earlier_failure():
            folder = self.proof_folder("proof-00-earlier")
            receipt = {**copy.deepcopy(self.old_receipt), "verdict": "FAIL", "failures": ["Tests fail"]}
            receipt["checks"] = {"suite_on_candidate": self.check(folder, "suite", unittest_log(), results=None)}
            (folder / "verification.json").write_text(json.dumps(receipt))
            path = str(folder / "verification.json")
            self.state["stages"].insert(0, self.stage(path, "2025-12-31T00:00:00+00:00"))
            self.state["regression_proofs"].insert(0, {"verdict": "FAIL", "source_revision": "rev", "path": path})

        def outside():
            moved = self.run.parent / (self.run.name + "-outside.json")
            self.addCleanup(moved.unlink, missing_ok=True)
            (self.old / "verification.json").rename(moved)
            self.state["regression_proofs"][0]["path"] = self.state["stages"][0]["output"] = str(moved)
            self.row["last_reported_in"] = str(moved)

        def symlinked():
            real = self.run / "regression" / "real-old"
            self.old.rename(real)
            self.old.symlink_to(real, target_is_directory=True)

        edits = {
            # Behavior: a failing, erroring or skipped test, or an earlier failed proof of the source.
            "failing_test": lambda: relog("regression_on_candidate", unittest_log(first="FAIL")),
            "skipped_test": lambda: relog("regression_on_base", unittest_log(first="skipped 'later'")),
            "nonzero_exit": lambda: old_check("regression_on_base").update(exit_code=1),
            "earlier_failed_proof": earlier_failure,
            "fail_verdict": lambda: self.old_receipt.update(verdict="FAIL"),
            "failures_listed": lambda: self.old_receipt.update(failures=["Tests that pass on base fail"]),
            "other_refusal": lambda: self.old_receipt["unverified"].append("A named test is missing"),
            "no_refusal_named": lambda: self.old_receipt.update(unverified=[]),
            # Capture: unknown or incomplete results, a timeout, results the old run did parse.
            "no_summary": lambda: relog("suite_on_candidate", unittest_log(summary=False)),
            "incomplete": lambda: relog("regression_on_candidate", unittest_log(TESTS[:2]).replace("Ran 2", "Ran 3")),
            "keeper_error": lambda: old_check("suite_on_candidate").update(error="keeper stopped"),
            "timed_out": lambda: old_check("suite_on_candidate").update(timed_out=True),
            "no_candidate_suite": lambda: self.old_receipt["checks"].pop("suite_on_candidate"),
            "parsed_before": lambda: old_check("suite_on_candidate").update(results=self.passed(TESTS)),
            "already_unittest": lambda: self.old_receipt.update(framework={"name": "unittest"}),
            "unrecognized_command": lambda: old_check("suite_on_base").update(command="make test"),
            "pytest_command": lambda: old_check("suite_on_base").update(command="python -m pytest -q tests"),
            "script_suite": lambda: (
                self.old_receipt["commands"].update(suite="./run_tests.sh"),
                self.new_receipt["commands"].update(suite="./run_tests.sh"),
            ),
            # Custody: tampered bytes, an unowned or later receipt, a path outside the run.
            "tampered_log": lambda: Path(old_check("suite_on_base")["output"]).write_text(unittest_log() + "x\n"),
            "unowned": lambda: self.state["stages"][0].update(runner_owned=False),
            "after_pass": lambda: self.state["stages"][0].update(finished_at="2026-01-01T00:06:00+00:00"),
            "outside": outside,
            "hard_link": lambda: os.link(self.old / "verification.json", self.run / "copy.json"),
            "symlinked_folder": symlinked,
            # History: a missing receipt, a finding citing another, a newer failure than the pass.
            "missing_receipt": lambda: (self.old / "verification.json").unlink(),
            "no_path": lambda: self.state["regression_proofs"][0].update(path=None),
            "other_citation": lambda: self.row.update(last_reported_in=str(self.run / "elsewhere.json")),
            "newer_failure": lambda: self.state["regression_proofs"].append(
                {**self.state["regression_proofs"][1], "verdict": "UNVERIFIED", "path": str(self.run / "later.json")}
            ),
            # Context: another source, base or case scope, a newly approved plan, an unknown milestone.
            "source": lambda: self.old_receipt.update(source_revision="other"),
            "base": lambda: self.old_receipt.update(base="other"),
            "case_scope": lambda: self.old_receipt.update(case_scope=["AC7"]),
            "proof_swapped": lambda: self.state["regression_proof"]["execution_context"].update(timeout=1),
            "new_plan": lambda: self.state.update(goal_contract={"hash": "approved later"}),
            "unknown_scope": lambda: (self.state["current_task"].pop("milestone_id"), self.row.pop("scope")),
            # Protected suite: another selector, or a different passing inventory.
            "selector": lambda: self.new_receipt["commands"].update(suite="python -m unittest discover -v -s other"),
            "fresh_not_unittest": lambda: self.new_receipt.update(framework={"name": "pytest"}),
            "inventory": lambda: self.new_receipt["checks"]["suite_on_candidate"].update(
                results=self.passed((*TESTS, "test_new"))
            ),
        }
        for name, edit in edits.items():
            with self.subTest(name=name):
                self.setUp()
                edit()
                if (self.old / "verification.json").exists():
                    self.rewrite_new()
                self.review()
                self.assertEqual("open", self.row["status"])


if __name__ == "__main__":
    unittest.main()
