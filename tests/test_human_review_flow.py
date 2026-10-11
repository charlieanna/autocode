"""Pure report classification; no provider, controller, CLI or saved run state."""

import copy
import unittest

import autocode_human_review_flow as review_flow


class HumanReviewFlowTests(unittest.TestCase):
    def setUp(self):
        self.criteria = [
            {"id": "C1", "human_review": True},
            {"id": "J0", "human_review": False},
            {"id": "G1", "human_review": False},
        ]
        self.validation = {
            "verdict": "PASS",
            "criterion_results": [
                {"id": row["id"], "status": "PASS", "evidence_refs": ["check:1"]} for row in self.criteria
            ],
            "unverified_criteria": [],
            "findings": [],
            "end_to_end_result": {
                "status": "NOT_VERIFIED",
                "summary": "Only C1's separate human acceptance remains",
                "evidence_refs": ["check:1"],
                "technical_result": {
                    "status": "PASS",
                    "summary": "The public call, journey and protected tests executed successfully",
                    "evidence_refs": ["check:1"],
                },
                "pending_human_criteria": ["C1"],
            },
        }

    def ready(self, validation=None, criteria=None, criterion="C1"):
        return review_flow.human_only_pending_validation(
            self.criteria if criteria is None else criteria,
            self.validation if validation is None else validation,
            criterion,
        )

    def test_automated_pass_rows_allow_both_observed_human_pending_report_shapes_without_mutation(self):
        for verdict in ("PASS", "BLOCKED"):
            for unverified in ([], ["C1: automated behavior passes; separate human acceptance remains"]):
                with self.subTest(verdict=verdict, unverified=unverified):
                    report = copy.deepcopy(self.validation)
                    report.update(verdict=verdict, unverified_criteria=unverified)
                    before = copy.deepcopy((self.criteria, report))
                    self.assertTrue(self.ready(report))
                    self.assertEqual(before, (self.criteria, report))

    def test_legacy_unverified_human_rows_keep_their_completed_flow_behavior(self):
        report = copy.deepcopy(self.validation)
        report["criterion_results"][0]["status"] = "NOT_VERIFIED"
        report["unverified_criteria"] = ["C1 human acceptance pending"]
        report["end_to_end_result"] = {"status": "PASS", "summary": "Executed flow", "evidence_refs": ["check:1"]}
        self.assertTrue(self.ready(report))
        report["unverified_criteria"] = []
        self.assertFalse(self.ready(report))
        report["unverified_criteria"] = ["C1 human acceptance pending"]
        report["criterion_results"][0]["status"] = "PASS"
        self.assertFalse(self.ready(report))

    def test_multiple_human_rows_need_the_exact_structured_pending_set(self):
        criteria = [*self.criteria, {"id": "C2", "human_review": True}]
        report = copy.deepcopy(self.validation)
        report["criterion_results"].append({"id": "C2", "status": "NOT_VERIFIED", "evidence_refs": ["check:1"]})
        report["end_to_end_result"]["pending_human_criteria"] = ["C1", "C2"]
        self.assertTrue(self.ready(report, criteria))
        for unverified, expected in (
            (["C1 pending", "C2: pending"], True),
            (["C1 pending"], False),
            (["J0 incomplete"], False),
        ):
            with self.subTest(unverified=unverified):
                report["unverified_criteria"] = unverified
                self.assertEqual(expected, self.ready(report, criteria))

    def test_unfinished_or_failed_technical_results_cannot_be_hidden_by_human_ids(self):
        for proof in (
            None,
            {"status": "FAIL", "summary": "Failed journey", "evidence_refs": ["check:1"]},
            {"status": "NOT_VERIFIED", "summary": "Journey not executed", "evidence_refs": ["check:1"]},
            {"status": "PASS", "summary": "Claim only", "evidence_refs": []},
            {"status": "PASS", "summary": "", "evidence_refs": ["check:1"]},
        ):
            with self.subTest(proof=proof):
                report = copy.deepcopy(self.validation)
                report["end_to_end_result"]["technical_result"] = proof
                self.assertFalse(self.ready(report))
        for position in range(len(self.criteria)):
            for status in ("FAIL", "NOT_VERIFIED"):
                if position == 0 and status == "NOT_VERIFIED":
                    continue  # Human acceptance alone may remain unverified.
                with self.subTest(position=position, status=status):
                    report = copy.deepcopy(self.validation)
                    report["criterion_results"][position]["status"] = status
                    self.assertFalse(self.ready(report))

    def test_all_criterion_and_flow_evidence_is_required(self):
        for position in range(len(self.criteria)):
            with self.subTest(criterion=self.criteria[position]["id"]):
                report = copy.deepcopy(self.validation)
                report["criterion_results"][position]["evidence_refs"] = []
                self.assertFalse(self.ready(report))
        for field, value in (("status", "FAIL"), ("status", "PASS"), ("summary", ""), ("evidence_refs", [])):
            with self.subTest(field=field, value=value):
                report = copy.deepcopy(self.validation)
                report["end_to_end_result"][field] = value
                self.assertFalse(self.ready(report))

    def test_unknown_duplicate_missing_and_nonhuman_pending_ids_are_rejected(self):
        for pending in ([], ["J0"], ["C10"], ["C1", "C1"], ["C1", "J0"]):
            with self.subTest(pending=pending):
                report = copy.deepcopy(self.validation)
                report["end_to_end_result"]["pending_human_criteria"] = pending
                self.assertFalse(self.ready(report))
        for unverified in (["J0: not executed"], ["C10 pending"], ["C1 pending", "J0 pending"]):
            with self.subTest(unverified=unverified):
                report = copy.deepcopy(self.validation)
                report["unverified_criteria"] = unverified
                self.assertFalse(self.ready(report))
        for ids in (("C1", "J0"), ("C1", "J0", "J0"), ("C1", "J0", "unknown"), ("C1", "J0", "G1", "G1")):
            with self.subTest(ids=ids):
                report = copy.deepcopy(self.validation)
                report["criterion_results"] = [
                    {"id": cid, "status": "PASS", "evidence_refs": ["check:1"]} for cid in ids
                ]
                self.assertFalse(self.ready(report))
        self.assertFalse(self.ready(criteria=[*self.criteria, self.criteria[0]]))

    def test_blocking_findings_failed_verdict_and_unrequired_reviews_remain_ineligible(self):
        report = copy.deepcopy(self.validation)
        report["findings"] = [{"blocking": True}]
        self.assertFalse(self.ready(report))
        report["findings"] = []
        report["verdict"] = "FAIL"
        self.assertFalse(self.ready(report))
        self.assertFalse(self.ready(criterion="J0"))
        self.assertFalse(self.ready(criteria=[{**row, "human_review": False} for row in self.criteria]))
