"""Qualified cases still need their independent writer when evidence rejects their assertions."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import autocode_acceptance_tests as acceptance
import autocode_acceptance_tests_job as job
import autocode_changed_code_evidence as evidence
import autocode_changed_code_review as review
import autocode_changed_code_review_application as application
import autocode_changed_code_rework as rework
import autocode_recovery_history as history

from .test_acceptance_tests import approved


class IndependentEvidenceRepairTests(unittest.TestCase):
    def fixture(self, root, product="src/bind.py", test="tests/test_bind.py"):
        output = root / "review.json"
        output.write_text('{"verdict": "FAIL"}\n')
        archived = root / "accepted-tests"
        archived.write_text("existing independent assertions\n")
        source = root / product
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("def bind():\n    return bound_report\n")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        state = approved(
            {"C1": "test: test_c1_binding", "C2": "guard: test_c2_ordinary"},
            iteration=3,
            task="Repair report binding",
            current_task={
                "id": "task-one",
                "kind": "implement",
                "affected_paths": [product, test],
                "acceptance_criteria": ["C1", "C2"],
            },
        )
        state["goal_contract"]["revision"] = 1
        assessment = evidence.assess(
            [{"path": product, "sha256": source_hash, "lines": [2]}],
            None,
            None,
            source_revision="source-one",
            proof_context="proof-one",
            texts={product: {"sha256": source_hash, "lines": {"2": "return bound_report"}}},
        )
        state["regression_proof"] = {
            "verdict": "PASS",
            "path": str(root / "proof.json"),
            "source_revision": "source-one",
            "changed_code_evidence": assessment,
        }
        record = {
            "stage": "tester",
            "role": "tester",
            "source_revision": "source-one",
            "output": str(output),
            "task_id": "task-one",
            "contract_hash": "h1",
            "contract_revision": 1,
            "exit_code": 0,
            "accounted": True,
            "changed_files": [],
        }
        gap = assessment["obligations"][0]
        decision = {
            **{key: gap[key] for key in ("gap_id", "path", "sha256", "line", "source_line")},
            "source_revision": "source-one",
            "proof_context": "proof-one",
            "accepted": False,
            "reason_kind": "missing_test",
            "reason": "Register the missing-bound and unrelated-output assertions in the selected tests.",
            "evidence": [str(output)],
            "reviewer": {"stage": "tester", "role": "tester", "output": str(output)},
        }
        state["validation"] = {
            "verdict": "FAIL",
            "reviewer_role": "tester",
            "source_revision": "source-one",
            "task_id": "task-one",
            "contract_hash": "h1",
            "contract_revision": 1,
            "output": str(output),
            "evidence_hashes": {str(output): hashlib.sha256(output.read_bytes()).hexdigest()},
            "changed_code_decisions": [decision],
        }
        state["stages"] = [record]
        acceptance.record_receipt(
            state,
            acceptance.due(state),
            {
                "contract_hash": "h1",
                "source_revision": "source-one",
                "case_ids": ["C1", "C2"],
                "output": str(root / "first-writer.json"),
                "files": {
                    test: {"archive": str(archived), "sha256": hashlib.sha256(archived.read_bytes()).hexdigest()}
                },
            },
        )
        self.assertEqual([], acceptance.due(state))
        rework.route(state, record, investigation_enabled=False, rejected=[decision])
        return state, record

    def test_missing_evidence_returns_covered_cases_to_the_independent_writer(self):
        for product, test in (
            ("src/bind.py", "tests/test_bind.py"),
            ("lib/bind.js", "spec/bind.spec.js"),
            ("bind.go", "bind_test.go"),
        ):
            with self.subTest(product=product), tempfile.TemporaryDirectory() as directory:
                state, _ = self.fixture(Path(directory), product, test)
                before = copy.deepcopy(state)
                acceptance.route(state)
                self.assertEqual(acceptance.STAGE, state["next_stage"])
                self.assertEqual(["C1", "C2"], [case["id"] for case in acceptance.job_cases(state)])
                for key in ("goal_contract", "current_task", "settings", "regression_proof"):
                    self.assertEqual(before[key], state[key], key)

    def test_a_missing_test_failure_must_decide_the_rest_of_the_offered_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, record = self.fixture(root)
            assessment = state["regression_proof"]["changed_code_evidence"]
            first = assessment["obligations"][0]
            extra = copy.deepcopy(first)
            extra.update(
                gap_id="second-gap",
                handle="L2",
                line=3,
                source_line="return another_report",
                mutation_outcomes=["survived"],
            )
            assessment["obligations"].append(extra)
            schema = review.generation_schema(
                {"properties": {review.FIELD: copy.deepcopy(review.SCHEMA)}}, assessment, "tester"
            )
            schema_file = root / "schema.json"
            schema_file.write_text(json.dumps(schema))
            record["schema"] = str(schema_file)
            partial = {
                "verdict": "FAIL",
                review.FIELD: [{"gap_id": first["gap_id"], "decision": "reject", "reason_kind": "missing_test"}],
            }
            with self.assertRaisesRegex(ValueError, "another_report|second-gap|L2"):
                application.require_tester_decisions(state, partial, record)
            complete = copy.deepcopy(partial)
            complete[review.FIELD].append({"gap_id": "L2", "decision": "accept"})
            application.require_tester_decisions(state, complete, record)
            # A repaired FAIL keeps the Builder/Investigator fallback: its repair cannot carry the whole matrix (#68).
            self.assertIsNone(application.require_tester_decisions(state, partial, {**record, "report_repaired": True}))
            for change in ({"source_revision": "old"}, {"schema": None}):
                application.require_tester_decisions(state, partial, {**record, **change})
            application.require_tester_decisions(state, {"verdict": "FAIL", review.FIELD: []}, record)

    def test_a_missing_test_failure_is_not_asked_about_lines_whose_mutants_never_ran(self):
        """Board #68: a ``not_run`` line has no mutation result; it is the runner's gap, not the Tester's."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, record = self.fixture(root)
            assessment = state["regression_proof"]["changed_code_evidence"]
            first = assessment["obligations"][0]
            for handle, line, outcomes in (("L2", 3, ["not_run"]), ("L3", 4, ["not_run", "not_run"])):
                extra = copy.deepcopy(first)
                extra.update(
                    gap_id=f"gap-{handle}",
                    handle=handle,
                    line=line,
                    source_line=f"x = {line}",
                    mutation_outcomes=outcomes,
                )
                assessment["obligations"].append(extra)
            schema = review.generation_schema(
                {"properties": {review.FIELD: copy.deepcopy(review.SCHEMA)}}, assessment, "tester"
            )
            schema_file = root / "schema.json"
            schema_file.write_text(json.dumps(schema))
            record["schema"] = str(schema_file)
            partial = {
                "verdict": "FAIL",
                review.FIELD: [{"gap_id": first["gap_id"], "decision": "reject", "reason_kind": "missing_test"}],
            }
            self.assertIsNone(application.require_tester_decisions(state, partial, record))
            # A line with any real outcome is still the Tester's to decide.
            assessment["obligations"][-1]["mutation_outcomes"] = ["not_run", "survived"]
            with self.assertRaisesRegex(ValueError, "L3") as raised:
                application.require_tester_decisions(state, partial, record)
            self.assertNotIn("L2 ", str(raised.exception))

    def test_stale_or_unaccepted_evidence_cannot_send_qualified_cases_back(self):
        import autocode_acceptance_evidence_repair as repair

        with tempfile.TemporaryDirectory() as directory:
            state, _ = self.fixture(Path(directory))
            variants = [
                ("goal_contract", "approval_status", "draft"),
                ("current_task", "id", "other"),
                ("current_task", "kind", "validate"),
                ("regression_proof", "source_revision", "other"),
                ("regression_proof", "verdict", "FAIL"),
                ("validation", "source_revision", "other"),
                ("validation", "reviewer_role", "builder"),
                ("validation", "evidence_hashes", {}),
                ("validation", "changed_code_decisions", []),
            ]
            for owner, key, value in variants:
                with self.subTest(owner=owner, key=key):
                    altered = copy.deepcopy(state)
                    altered[owner][key] = value
                    self.assertEqual([], acceptance.due(altered))
            other_plan = copy.deepcopy(state)
            other_plan["goal_contract"]["hash"] = "other"
            self.assertIsNone(repair.pending(other_plan, [{"id": "C1"}]))
            for key, value in (
                ("accounted", False),
                ("exit_code", 1),
                ("changed_files", ["src/bind.py"]),
                ("role", "builder"),
                ("rejected", True),
                ("source_revision", "other"),
            ):
                with self.subTest(record=key):
                    altered = copy.deepcopy(state)
                    altered["stages"][0][key] = value
                    self.assertEqual([], acceptance.due(altered))
            for records in ([], [state["stages"][0], state["stages"][0]]):
                with self.subTest(records=len(records)):
                    altered = copy.deepcopy(state)
                    altered["stages"] = records
                    self.assertEqual([], acceptance.due(altered))
            for key, value in (
                ("proof_context", "other"),
                ("sha256", "b" * 64),
                ("reason_kind", "instrumentation_unavailable"),
                ("accepted", True),
            ):
                with self.subTest(decision=key):
                    altered = copy.deepcopy(state)
                    altered["validation"]["changed_code_decisions"][0][key] = value
                    self.assertEqual([], acceptance.due(altered))
            Path(state["validation"]["output"]).write_text("tampered report")
            self.assertEqual([], acceptance.due(state))

    def test_repair_respects_scope_and_keeps_the_existing_two_round_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            state, record = self.fixture(Path(directory))
            state["current_task"]["acceptance_criteria"] = ["C1"]
            self.assertEqual(["C1"], [case["id"] for case in acceptance.due(state)])
            before_iteration = state["iteration"]
            acceptance.route(state)
            acceptance.route(state)
            self.assertEqual(before_iteration, state["iteration"])
            self.assertEqual(1, len(state["regression_proofs"]))
            entries = history.project(state)
            self.assertTrue(
                any(
                    row["headline"] == "Independent test strengthening (round 1 of 2)" and row["by"] == "Tester"
                    for row in entries
                ),
                entries,
            )
            # The shared rework policy still owns rounds, not the writer receipt.
            for number, action in ((2, "builder"), (3, "investigator")):
                later = {**record, "output": f"review-{number}"}
                rework.route(state, later, investigation_enabled=False)
                self.assertEqual(action, state["regression_proofs"][-1]["action"])
            state["next_stage"] = "builder"
            state["regression_proofs"][0]["action"] = "investigator"
            self.assertEqual([], acceptance.due(state))

    def test_replacing_the_pinned_review_cannot_reopen_qualified_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            state, record = self.fixture(Path(directory))
            self.assertEqual(["C1", "C2"], [case["id"] for case in acceptance.due(state)])
            Path(record["output"]).write_text("different review bytes")
            self.assertEqual([], acceptance.due(state))
            Path(record["output"]).unlink()
            self.assertEqual([], acceptance.due(state))

    def test_reviewed_product_bytes_must_still_match_before_repair_is_routed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, _ = self.fixture(root)
            product = root / "src/bind.py"
            product.parent.mkdir(exist_ok=True)
            product.write_text("def bind():\n    return bound_report\n")
            digest = hashlib.sha256(product.read_bytes()).hexdigest()
            state["workspace"] = str(root)
            state["regression_proof"]["changed_code_evidence"]["obligations"][0]["sha256"] = digest
            state["validation"]["changed_code_decisions"][0]["sha256"] = digest
            self.assertEqual(["C1", "C2"], [case["id"] for case in acceptance.due(state)])
            product.write_text("def bind():\n    return another_report\n")
            self.assertEqual([], acceptance.due(state))

    def test_qualified_strengthening_fulfills_once_and_preserves_the_previous_receipt(self):
        import autocode_acceptance_evidence_repair as repair

        with tempfile.TemporaryDirectory() as directory:
            state, _ = self.fixture(Path(directory))
            acceptance.route(state)
            cases = acceptance.job_cases(state)
            previous = copy.deepcopy(next(iter(state[acceptance.KEY]["receipts"].values())))
            pending = repair.packet(state)
            self.assertEqual(1, pending["open_line_count"])
            handoff = job.packet(state)
            self.assertEqual(pending, handoff["evidence_repair"])
            self.assertEqual("proof-one", pending["rejected"][0]["proof_context"])
            text, _ = job.prompt(state)
            self.assertIn("complete current proof", text)
            self.assertIn("selected named tests", text)
            receipt = {**previous, "output": str(Path(directory) / "second-writer.json")}
            acceptance.record_receipt(state, cases, receipt)
            self.assertEqual([previous], state[acceptance.KEY]["receipt_history"])
            self.assertEqual([], acceptance.due(state))
            self.assertIsNone(repair.packet(state))
            acceptance.record_receipt(state, cases, receipt)
            self.assertEqual([previous], state[acceptance.KEY]["receipt_history"])
            saved = next(iter(state[acceptance.KEY]["receipts"].values()))
            self.assertEqual(pending["binding"], saved[repair.FIELD])
            with self.assertRaisesRegex(ValueError, "cannot change"):
                acceptance.record_receipt(state, cases, {**receipt, "command": "different"})

    def test_a_stale_pending_handoff_cannot_fulfill_a_different_review(self):
        import autocode_acceptance_evidence_repair as repair

        with tempfile.TemporaryDirectory() as directory:
            state, _ = self.fixture(Path(directory))
            acceptance.route(state)
            state["goal_contract"]["revision"] += 1
            self.assertIsNone(repair.packet(state))

    def writer_inputs(self, root):
        state, _ = self.fixture(root)
        acceptance.route(state)
        path = root / "tests/test_bind.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text("def test_c1_binding():\n    assert True\ndef test_c2_ordinary():\n    assert True\n")
        value = {
            "summary": "strengthened binding",
            "tests": [
                {"case_id": cid, "test_name": name, "file": "tests/test_bind.py"}
                for cid, name in (("C1", "test_c1_binding"), ("C2", "test_c2_ordinary"))
            ],
            "cannot_write": [],
            "already_passing": [],
            "tests_run": [],
        }
        record = {
            "output": str(root / "writer.json"),
            "changed_files": ["tests/test_bind.py"],
            "source_revision": "strengthened-source",
        }
        return state, value, record

    def test_no_change_writer_uses_bounded_rewrite_without_running_checks_or_changing_plan(self):
        import autocode_acceptance_evidence_repair as repair

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, value, record = self.writer_inputs(root)
            record["changed_files"] = []
            checks = Mock(side_effect=AssertionError("No repeated checks before stronger assertions"))
            before = copy.deepcopy(state)
            for _ in range(job.MAX_REWRITES):
                job.apply(state, value, record, root, run_tests=checks)
                self.assertEqual(acceptance.STAGE, state["next_stage"])
                self.assertIsNotNone(repair.packet(state))
            with self.assertRaisesRegex(job.TestsNeedRewriting, "already sent these tests back 2"):
                job.apply(state, value, record, root, run_tests=checks)
            checks.assert_not_called()
            for key in ("goal_contract", "current_task", "settings", "regression_proofs"):
                self.assertEqual(before[key], state[key], key)

    def test_stronger_tests_still_require_original_failure_before_fulfilling_repair(self):
        import autocode_acceptance_evidence_repair as repair

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, value, record = self.writer_inputs(root)
            run = {
                "command": "python -m unittest",
                "exit_code": 0,
                "results": {
                    "complete": True,
                    "total": 2,
                    "passed": ["test_c1_binding", "test_c2_ordinary"],
                    "failed": [],
                    "skipped": [],
                },
            }
            checks = Mock(return_value=run)
            original = Mock(return_value={"verdicts": {"C1": "fails"}})
            job.apply(state, value, record, root, run_tests=checks, run_reference=original)
            checks.assert_called_once_with(["tests/test_bind.py"])
            original.assert_called_once()
            self.assertEqual([], acceptance.due(state))
            self.assertEqual("builder", state["next_stage"])
            self.assertEqual("UNVERIFIED", state["regression_proof"]["changed_code_evidence"]["status"])
            # An original pass cannot fulfill the repair or silently weaken its test: criterion.
            state, value, record = self.writer_inputs(root)
            changed_plan = Mock()
            job.apply(
                state,
                value,
                record,
                root,
                run_tests=checks,
                run_reference=lambda *args: {"verdicts": {"C1": "passes"}},
                send_back=changed_plan,
            )
            changed_plan.assert_called_once()
            self.assertIsNotNone(repair.packet(state))

    def test_cannot_write_a_repair_returns_to_bounded_recovery_without_asking_the_person(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, value, record = self.writer_inputs(root)
            value["tests"] = []
            value["cannot_write"] = [{"case_id": cid, "reason": "Need to inspect the binding"} for cid in ("C1", "C2")]
            checks = Mock(side_effect=AssertionError("No checks without authored tests"))
            job.apply(state, value, record, root, run_tests=checks)
            self.assertEqual("RUNNING", state["status"])
            self.assertEqual(acceptance.STAGE, state["next_stage"])
            self.assertEqual(1, len(state[acceptance.KEY]["rewrites"]))
            checks.assert_not_called()
