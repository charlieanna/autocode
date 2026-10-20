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

    def test_citations_outside_the_checks_own_evidence_are_unbound(self):
        allowed = {"event:prt_1", ".autocode/evidence/cli.json"}
        bound = check_refs.resolve(self.report(["check:1", "check:2"]))
        self.assertEqual([], check_refs.unbound_refs(bound, allowed))
        free_text = check_refs.resolve(self.report(["README.md", "reviewed the output myself"]))
        self.assertEqual(
            [("AC1", "README.md"), ("AC1", "reviewed the output myself")],
            check_refs.unbound_refs(free_text, allowed),
        )


if __name__ == "__main__":
    unittest.main()


class DropRedundantTests(unittest.TestCase):
    def report(self, *refs):
        return {"criterion_results": [{"id": "AC1", "status": "PASS", "evidence_refs": list(refs)}]}

    def test_an_unverified_name_beside_a_verified_check_is_dropped(self):
        report = self.report("event:e1", "hello.txt")
        self.assertEqual([("AC1", "hello.txt")], check_refs.drop_redundant_unbound(report, {"event:e1"}))
        self.assertEqual(["event:e1"], report["criterion_results"][0]["evidence_refs"])
        self.assertEqual([], check_refs.unbound_refs(report, {"event:e1"}))

    def test_a_criterion_with_nothing_verified_keeps_its_citations_and_is_still_refused(self):
        report = self.report("hello.txt")
        self.assertEqual([], check_refs.drop_redundant_unbound(report, {"event:e1"}))
        self.assertEqual([("AC1", "hello.txt")], check_refs.unbound_refs(report, {"event:e1"}))

    def test_a_citation_that_may_not_go_quietly_keeps_the_refusal(self):
        report = self.report("event:e1", ".autocode/scratch/x.png")
        kept = check_refs.drop_redundant_unbound(
            report, {"event:e1"}, droppable=lambda ref: not ref.startswith(".autocode")
        )
        self.assertEqual([], kept)
        self.assertEqual([("AC1", ".autocode/scratch/x.png")], check_refs.unbound_refs(report, {"event:e1"}))
