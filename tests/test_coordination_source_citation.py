"""Coordinated members cite whole-task sentences in local ACs for provenance, not fulfillment."""

import unittest

import autocode_source_trace as source_trace


class CoordinationSourceCitationTests(unittest.TestCase):
    def test_source_trace_accepts_whole_task_in_local_acs_with_verbatim_required_behaviors(self):
        """Local ACs cite whole-task text; verification_methods are LOCAL tests/guards."""
        task = {
            "id": "task:0",
            "kind": "user_goal",
            "text": "Fix total for subtotal 100. Follow documented contracts. Publish PRs; do not merge. Prove fix on combined code. Preserve zero outputs exactly: 0, 0.0, -0.0 and $0.00 superseded form.",
        }
        contract = {
            "acceptance_criteria": [
                {
                    "id": "AC_RECEIPT",
                    "criterion": "HistoryClient follows documented service contracts",
                    "sources": [{"source_id": "task:0", "quote": task["text"]}],
                    "verification_method": "test: test_receipt_contract",
                },
                {
                    "id": "AC_ZERO",
                    "criterion": "Preserve zero outputs: 0, 0.0, -0.0",
                    "sources": [
                        {
                            "source_id": "task:0",
                            "quote": "Preserve zero outputs exactly: 0, 0.0, -0.0 and $0.00 superseded form.",
                        }
                    ],
                    "verification_method": "guard: test_zero_preservation",
                },
            ],
            "required_behaviors": [task["text"]],
            "constraints": [],
        }
        source_trace.check_citations(contract, [task])
        source_trace.check_ready(contract, [task], {"out_of_scope": [], "negation_waivers": []})

    def test_source_trace_rejects_forged_source_id(self):
        """Validator rejects citation with unknown source_id."""
        task = {"id": "task:0", "kind": "user_goal", "text": "Fix bug."}
        contract = {
            "acceptance_criteria": [
                {
                    "id": "AC1",
                    "criterion": "Fix",
                    "sources": [{"source_id": "forged:0", "quote": "Fix bug."}],
                    "verification_method": "test",
                }
            ],
            "required_behaviors": [],
            "constraints": [],
        }
        with self.assertRaises(ValueError) as cm:
            source_trace.check_citations(contract, [task])
        self.assertIn("without quoting", str(cm.exception))

    def test_source_trace_rejects_uncited_sentence(self):
        """Validator rejects when whole-task sentence is not cited."""
        task = {"id": "task:0", "kind": "user_goal", "text": "Fix bug. Follow contracts."}
        contract = {
            "acceptance_criteria": [
                {
                    "id": "AC1",
                    "criterion": "Fix",
                    "sources": [{"source_id": "task:0", "quote": "Fix bug."}],
                    "verification_method": "test",
                }
            ],
            "required_behaviors": [],
            "constraints": [],
        }
        with self.assertRaises(ValueError) as cm:
            source_trace.check_ready(contract, [task], {"out_of_scope": [], "negation_waivers": []})
        self.assertIn("neither cited nor out of scope", str(cm.exception))

    def test_source_trace_rejects_dropped_negation(self):
        """Validator rejects when AC drops negation from source."""
        task = {"id": "task:0", "kind": "user_goal", "text": "Publish PRs; do not merge."}
        contract = {
            "acceptance_criteria": [
                {
                    "id": "AC1",
                    "criterion": "Publish PRs",
                    "sources": [{"source_id": "task:0", "quote": "Publish PRs; do not merge."}],
                    "verification_method": "test",
                }
            ],
            "required_behaviors": [],
            "constraints": [],
        }
        with self.assertRaises(ValueError) as cm:
            source_trace.check_ready(contract, [task], {"out_of_scope": [], "negation_waivers": []})
        self.assertIn("dropped the source's negation", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
