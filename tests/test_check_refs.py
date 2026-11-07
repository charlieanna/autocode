"""A Validator's evidence that cites its own checks as check:<n> (autocode_check_refs)."""

import tempfile
import unittest
from pathlib import Path

import autocode_check_refs as check_refs


class CheckRefsTests(unittest.TestCase):
    def workspace(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

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

    def test_pass_without_evidence_is_refused_without_mutating_the_report(self):
        for refs in ([], [""], [" "]):
            with self.subTest(refs=refs):
                report = self.report(refs)
                before = repr(report)
                with self.assertRaisesRegex(ValueError, "PASS criterion results lack evidence references: AC1"):
                    check_refs.require_passing_evidence(report)
                self.assertEqual(before, repr(report))
        check_refs.require_passing_evidence(self.report(["check:1"]))

    def test_nonpassing_criteria_do_not_need_fabricated_evidence(self):
        for status in ("FAIL", "NOT_VERIFIED", "BLOCKED"):
            report = self.report([])
            report["criterion_results"][0]["status"] = status
            check_refs.require_passing_evidence(report)

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

    def test_an_extra_unverified_citation_beside_a_verified_one_is_dropped(self):
        workspace = self.workspace()
        report = self.report(["check:1", "hello.txt", "check:2", "I read the file"])
        dropped = check_refs.drop_unverified_extras(report, workspace)
        self.assertEqual(["check:1", "check:2"], report["criterion_results"][0]["evidence_refs"])
        self.assertEqual(
            [{"criterion": "AC1", "ref": "hello.txt"}, {"criterion": "AC1", "ref": "I read the file"}], dropped
        )
        # A check's own evidence reference and a runner receipt are verified too.
        receipt = str(workspace / ".autocode" / "runs" / "r" / "activity.jsonl")
        report = self.report(["event:prt_1", receipt, "README.md"])
        check_refs.drop_unverified_extras(report, workspace)
        self.assertEqual(["event:prt_1", receipt], report["criterion_results"][0]["evidence_refs"])

    def test_a_criterion_with_no_verified_citation_keeps_them_for_the_refusal(self):
        workspace = self.workspace()
        scratch = str(workspace / ".autocode" / "scratch" / "shot.png")
        for refs in (["hello.txt"], ["I read the file", scratch], ["check:3", "hello.txt"]):
            with self.subTest(refs=refs):
                report = self.report(list(refs))
                self.assertEqual([], check_refs.drop_unverified_extras(report, workspace))
                self.assertEqual(refs, report["criterion_results"][0]["evidence_refs"])
        # check:3 names no listed check: it is never dropped, so resolve still refuses it.
        report = self.report(["check:1", "check:3"])
        self.assertEqual([], check_refs.drop_unverified_extras(report, workspace))
        with self.assertRaisesRegex(ValueError, "names no listed check"):
            check_refs.resolve(report)
        # An event the runner did not attach claims an execution that did not happen: refused, never dropped.
        report = check_refs.resolve(self.report(["check:1", "event:invented"]))
        self.assertEqual([], check_refs.drop_unverified_extras(report, workspace))
        self.assertEqual(
            [("AC1", "event:invented")], check_refs.unbound_refs(report, {"event:prt_1", ".autocode/evidence/cli.json"})
        )

    def test_end_to_end_citations_are_left_alone(self):
        report = self.report(["check:1"])
        report["end_to_end_result"]["evidence_refs"] = ["check:2", "README.md"]
        check_refs.drop_unverified_extras(report, self.workspace())
        self.assertEqual(["check:2", "README.md"], report["end_to_end_result"]["evidence_refs"])

    def test_only_a_tester_report_without_design_evidence_is_tidied(self):
        workspace = self.workspace()
        for record, settings, tidied in (
            ({"stage": "sol"}, {}, True),
            ({"stage": "sol_report_repair", "original_stage": "sol"}, None, True),
            ({"stage": "astra_checkpoint"}, {}, False),
            ({"stage": "sol"}, {"design_manifest": {"manifest_hash": "h"}}, False),
            ({"stage": "sol"}, {"figma_file": "https://figma.example/file"}, False),
        ):
            with self.subTest(record=record, settings=settings):
                report = self.report(["check:1", "hello.txt"])
                check_refs.tidy_tester_citations(record, report, workspace, settings)
                kept = report["criterion_results"][0]["evidence_refs"]
                self.assertEqual(["check:1"] if tidied else ["check:1", "hello.txt"], kept)
                self.assertEqual(
                    [{"criterion": "AC1", "ref": "hello.txt"}] if tidied else None,
                    record.get("dropped_evidence_refs"),
                )

    def test_runner_receipts_exclude_model_writable_areas(self):
        workspace = self.workspace()
        self.assertTrue(check_refs.runner_receipt(workspace, ".autocode/runs/r/activity.jsonl"))
        for ref in (
            ".autocode/scratch/review/shot.png",
            ".autocode/investigation/notes.md",
            f".autocode/tool-containment-{'a' * 32}/out.txt",
            "hello.txt",
            "../elsewhere/.autocode/runs/r/activity.jsonl",
        ):
            with self.subTest(ref=ref):
                self.assertFalse(check_refs.runner_receipt(workspace, ref))


if __name__ == "__main__":
    unittest.main()
