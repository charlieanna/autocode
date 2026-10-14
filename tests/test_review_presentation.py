"""Read-only review evidence preserves provenance and missing-proof distinctions."""

import base64
import copy
import unittest

import autocode_brief_acceptance as brief
import autocode_review_presentation as presentation
import autocode_run_view as run_view


def fixture():
    return {
        "status": "WAITING_FOR_USER",
        "needs": {"kind": "review", "criteria": ["C1"], "token": "review-current", "question": "Accept C1?"},
        "verification": {
            "freshness": "current",
            "reasons": ["The evidence matches the inspected source."],
            "coverage": [
                {
                    "id": "C1",
                    "criterion": "Readable greeting",
                    "verification_method": "Inspect greeting",
                    "human_review": True,
                    "recorded_status": "PASS",
                    "evidence_refs": ["event:greeting"],
                },
                {
                    "id": "C2",
                    "criterion": "Readable usage",
                    "human_review": True,
                    "recorded_status": "PASS",
                    "evidence_refs": ["event:usage"],
                },
                {"id": "C3", "criterion": "Exit zero", "human_review": False},
            ],
        },
        "evidence": {
            "acceptance": [
                {"id": "C1", "human_reviewed": False, "evidence": "Greeting printed"},
                {"id": "C2", "human_reviewed": True},
            ],
            "check_replay": {
                "checks": [
                    {
                        "command": "python -m unittest test_greeting",
                        "evidence_ref": "event:greeting",
                        "exit_code": 0,
                        "output": "/run/greeting.log",
                        "output_sha256": "a" * 64,
                        "tail": "...ok",
                        "results": {
                            "passed": ["GreetingTests::test_name"],
                            "failed": [],
                            "skipped": [],
                            "complete": True,
                        },
                    },
                    {"command": "python greet.py", "evidence_ref": "event:usage", "exit_code": 2},
                ]
            },
            "diff_coverage": {
                "collected": True,
                "tool": "coverage",
                "note": "Run-level proof coverage",
                "files": [
                    {"path": "greet.py", "hunks": [{"start": 1, "end": 5, "covered": False, "uncovered_lines": [4]}]}
                ],
                "unmeasured": ["README.md"],
            },
            "regression_proof": {
                "verdict": "PASS",
                "commands": {"regression": "python -m unittest test_greeting"},
                "fail_to_pass": ["GreetingTests::test_name"],
                "case_tests": {"T1": ["GreetingTests::test_name"]},
            },
            "findings": [
                {"id": "F1", "status": "open", "severity": "low", "finding": "Wording", "scope": {"criteria": ["C1"]}},
                {"id": "F2", "status": "open", "severity": "high", "finding": "Usage", "scope": {"criteria": ["C2"]}},
                {"id": "F3", "status": "open", "severity": "medium", "finding": "Unscoped finding"},
                {"id": "F4", "status": "resolved", "severity": "low", "finding": "Old finding"},
            ],
        },
    }


class ReviewPresentationTests(unittest.TestCase):
    def test_criteria_keep_prior_decisions_and_only_associated_evidence(self):
        view = fixture()
        before = copy.deepcopy(view)
        shown = presentation.review_data(view)
        self.assertEqual("review-current", shown["token"])
        self.assertEqual(["C1", "C2"], [row["id"] for row in shown["criteria"]])
        first, second = shown["criteria"]
        self.assertTrue(first["pending"])
        self.assertFalse(second["pending"])
        self.assertTrue(second["human_reviewed"])
        self.assertEqual(["event:greeting"], [row["evidence_ref"] for row in first["checks"]])
        self.assertEqual(["F1", "F3"], [row["id"] for row in first["open_findings"]])
        first["checks"][0]["results"]["passed"].append("invented")
        first["open_findings"][0]["scope"]["criteria"].append("invented")
        first["diff_coverage"]["files"][0]["path"] = "changed"
        self.assertEqual(before, view)

    def test_rendered_evidence_distinguishes_checks_excerpts_and_aggregate_coverage(self):
        shown = presentation.render_review(presentation.review_data(fixture())["criteria"][0])
        for value in (
            "C1: Readable greeting",
            "Verification method: Inspect greeting",
            "Exit code: 0",
            "passed: GreetingTests::test_name",
            "Exact output: /run/greeting.log",
            "Output SHA-256: " + "a" * 64,
            "Saved output excerpt (not the complete output)",
            "Lines 1-5: uncovered",
            "Uncovered lines: [4]",
            'Unmeasured files: ["README.md"]',
            "Run-level regression proof (not criterion-specific)",
            "Named fail-to-pass tests",
            "F1 [low]: Wording",
            "F3 [medium]: Unscoped finding",
        ):
            self.assertIn(value, shown)
        self.assertNotIn("python greet.py", shown)
        self.assertNotIn("F2", shown)
        self.assertNotIn("F4", shown)

    def test_older_views_keep_missing_criterion_and_do_not_invent_check_association(self):
        shown = presentation.review_data(
            {
                "status": "WAITING_FOR_USER",
                "needs": {"kind": "review", "criteria": ["legacy"], "token": "old"},
                "evidence": {"check_replay": {"checks": [{"command": "pytest", "exit_code": 0}]}},
            }
        )
        row = shown["criteria"][0]
        self.assertEqual("legacy", row["id"])
        self.assertTrue(row["pending"])
        self.assertEqual([], row["checks"])
        self.assertEqual("pytest", row["unassociated_checks"][0]["command"])
        text = presentation.render_review(row)
        self.assertIn("Criterion text is unavailable", text)
        self.assertIn("criterion association unavailable", text)
        self.assertIn("Named tests: not recorded", text)
        self.assertIn("Exact output: not recorded", text)

    def test_stale_evidence_is_never_labeled_current_or_given_review_authority(self):
        view = fixture()
        view["verification"].update(freshness="stale_or_unverified", reasons=["Source changed after checks"])
        view["needs"] = {"kind": "continue"}
        shown = presentation.review_data(view)
        self.assertIsNone(shown["token"])
        self.assertFalse(any(row["pending"] for row in shown["criteria"]))
        text = presentation.render_review(shown["criteria"][0])
        self.assertIn("Recorded automated result: PASS", text)
        self.assertIn("Evidence freshness: stale_or_unverified", text)
        self.assertIn("Source changed after checks", text)

    def test_expected_format_and_observed_bytes_keep_their_criterion_binding(self):
        sources = [
            {
                "id": "task",
                "kind": "task",
                "text": "`todo.py add TEXT` adds a task and exits 0; "
                "`todo.py list` prints tasks as `ID TEXT [open|done]` one per line and exits 0.",
            }
        ]
        manifest = brief.bind(
            sources,
            [
                {
                    "declaration_id": brief.inventory(sources)[0]["id"],
                    "criterion_ids": ["C1"],
                    "steps": [{"argv": ["add", "buy milk"]}, {"argv": ["list"]}],
                    "observe_step": 1,
                    "bindings": [{"placeholder": "TEXT", "step": 0, "argument": 1}],
                    "expect": [{"placeholder": "TEXT", "step": 0, "argument": 1}],
                }
            ],
        )
        view = fixture()
        view["approved_contract"] = {"body": {"brief_acceptance": {"manifest": manifest}}}
        view["evidence"]["check_replay"]["brief_acceptance"] = {
            "checks": [
                {
                    "observation_hash": manifest["observations"][0]["hash"],
                    "command": "runner observation",
                    "exit_code": 0,
                    "observation": {
                        "verdict": "PASS",
                        "steps": [
                            {
                                "argv": ["list"],
                                "exit_code": 0,
                                "stdout_base64": base64.b64encode(b"7 buy milk [open]\n").decode(),
                                "stderr_base64": "",
                            }
                        ],
                    },
                }
            ]
        }
        shown = presentation.review_data(view)
        first, second = shown["criteria"]
        self.assertEqual(1, len(first["exact_observations"]))
        self.assertEqual([], second["exact_observations"])
        text = presentation.render_review(first)
        self.assertIn("Expected output format: ID TEXT [open|done]", text)
        self.assertIn('Expected items (exact): ["buy milk"]', text)
        self.assertIn("Recorded output comparison: PASS", text)
        self.assertIn("Exact stdout bytes: b'7 buy milk [open]\\n'", text)

    def test_terminal_control_bytes_are_displayed_as_literal_data(self):
        view = fixture()
        view["verification"]["coverage"][0]["criterion"] = "Safe\x1b[2J\x07"
        text = presentation.render_review(presentation.review_data(view)["criteria"][0])
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertIn("Safe\\x1b[2J\\x07", text)

    def test_public_view_adds_check_refs_and_copies_finding_scope(self):
        state = {
            "validation": {
                "check_replay": {"checks": [{"command": "pytest", "evidence_ref": "event:check", "exit_code": 0}]}
            },
            "findings_ledger": [{"id": "F1", "status": "open", "scope": {"criteria": ["C1"]}}],
        }
        before = copy.deepcopy(state)
        evidence = run_view.evidence(state)
        self.assertEqual("event:check", evidence["check_replay"]["checks"][0]["evidence_ref"])
        evidence["findings"][0]["scope"]["criteria"].append("changed")
        self.assertEqual(before, state)


if __name__ == "__main__":
    unittest.main()
