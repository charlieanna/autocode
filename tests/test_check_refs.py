"""A Validator's evidence that cites its own checks as check:<n> (autocode_check_refs)."""

import unittest

import autocode_check_refs as check_refs


class CheckRefsTests(unittest.TestCase):
    def report(self, refs):
        return {
            "checks": [
                {"command": "python -m unittest", "exit_code": 0, "evidence_ref": "event:prt_1"},
                {"command": "python cli.py", "exit_code": 0, "evidence_ref": ".autocode/evidence/cli.json"},
            ],
            "criterion_results": [{"id": "AC1", "status": "PASS", "evidence_refs": refs}],
            "end_to_end_result": {"status": "PASS", "summary": "", "evidence_refs": ["check:2"]},
            "milestone_results": [
                {"milestone_id": "M1", "status": "PASS", "summary": "", "evidence_refs": ["check:1"]}
            ],
        }

    def test_check_references_become_the_checks_evidence(self):
        report = check_refs.resolve(self.report(["check:1", " check:2 ", "README.md", "event:prt_9"]))
        self.assertEqual(
            ["event:prt_1", ".autocode/evidence/cli.json", "README.md", "event:prt_9"],
            report["criterion_results"][0]["evidence_refs"],
        )
        self.assertEqual([".autocode/evidence/cli.json"], report["end_to_end_result"]["evidence_refs"])
        self.assertEqual(["event:prt_1"], report["milestone_results"][0]["evidence_refs"])

    def test_a_reference_to_no_listed_check_is_refused(self):
        for ref in ("check:0", "check:3"):
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, "names no listed check"):
                check_refs.resolve(self.report([ref]))

    def test_technical_flow_proof_uses_authenticated_check_references(self):
        report = self.report(["check:1"])
        report["end_to_end_result"]["technical_result"] = {
            "status": "PASS",
            "summary": "Executed technical flow",
            "evidence_refs": ["check:2"],
        }
        check_refs.resolve(report)
        self.assertEqual(
            [".autocode/evidence/cli.json"], report["end_to_end_result"]["technical_result"]["evidence_refs"]
        )
        report["end_to_end_result"]["technical_result"]["evidence_refs"] = ["check:3"]
        with self.assertRaisesRegex(ValueError, "names no listed check"):
            check_refs.resolve(report)


if __name__ == "__main__":
    unittest.main()
