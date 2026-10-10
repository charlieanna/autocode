"""Only a completed original review can authorize retained dispositions."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_findings as findings
from autocode_report_findings import (
    MALFORMED_ELSEWHERE,
    REPAIR_INSTRUCTION,
    UNAUTHORIZED,
    preserved_dispositions,
    refusal,
    retained,
)
from autocode_report_source import original_report_for_repair


def disposition(fid="F-1", *, action="resolved", evidence="event:check"):
    return {"id": fid, "disposition": action, "evidence": evidence}


def report(*rows, source="sol", outcome=None):
    field, default = ("verdict", "PASS") if source == "sol" else ("status", "COMPLETE")
    return {
        field: default if outcome is None else outcome,
        "finding_dispositions": list(rows),
        "unrelated_evidence": "missing.json",
    }


class PreservedDispositionsTests(unittest.TestCase):
    def test_citation_repair_retains_exact_dispositions_without_mutating_inputs(self):
        for stage, source in (("sol", "sol"), ("astra_review", "astra"), ("astra_plan", "astra")):
            with self.subTest(stage=stage):
                record = {"stage": stage, "exit_code": 0, "rejected": True}
                original = report(disposition(), disposition("F-2", action="retracted"), source=source)
                repaired = {**copy.deepcopy(original), "unrelated_evidence": "existing.json"}
                before = copy.deepcopy((record, original, repaired))
                kept = preserved_dispositions(record, original, repaired)
                self.assertEqual({source: original["finding_dispositions"]}, kept)
                self.assertEqual(before, (record, original, repaired))
                kept[source][0]["evidence"] = "changed by caller"
                self.assertEqual(before, (record, original, repaired))

    def test_repair_never_whitelists_a_new_id_action_or_evidence(self):
        original = report(disposition())
        for change in (
            {"id": "F-new"},
            {"disposition": "retracted"},
            {"evidence": "event:new"},
            {"evidence": " event:check "},
        ):
            with self.subTest(change=change):
                changed = {**disposition(), **change}
                self.assertEqual(
                    {}, preserved_dispositions({"stage": "sol", "exit_code": 0}, original, report(changed))
                )
        kept = preserved_dispositions(
            {"stage": "sol", "exit_code": 0}, original, report(disposition(), disposition("F-new"))
        )
        self.assertEqual({"sol": [disposition()]}, kept)

    def test_checkpoint_preserves_each_reviewers_rows_without_crossing_sources(self):
        original = {
            "validation": report(disposition("F-sol")),
            "decision": report(disposition("F-astra", action="retracted"), source="astra"),
        }
        record = {"stage": "astra_checkpoint", "exit_code": 0}
        expected = {"sol": [disposition("F-sol")], "astra": [disposition("F-astra", action="retracted")]}
        self.assertEqual(expected, preserved_dispositions(record, original, copy.deepcopy(original)))
        swapped = {"validation": original["decision"], "decision": original["validation"]}
        self.assertEqual({}, preserved_dispositions(record, original, swapped))
        duplicate = {"validation": report(disposition()), "decision": report(disposition(), source="astra")}
        self.assertEqual({}, preserved_dispositions(record, duplicate, duplicate))

    def test_blocked_missing_or_unknown_original_outcome_cannot_gain_authority(self):
        for stage, source, field in (
            ("sol", "sol", "verdict"),
            ("astra_review", "astra", "status"),
            ("astra_plan", "astra", "status"),
        ):
            record = {"stage": stage, "exit_code": 0}
            repaired = report(disposition(), source=source)
            for outcome in ("BLOCKED", "UNKNOWN", "NOT_VERIFIED", None, "", False):
                with self.subTest(stage=stage, outcome=outcome):
                    original = copy.deepcopy(repaired)
                    if outcome is None:
                        original.pop(field)
                    else:
                        original[field] = outcome
                    self.assertEqual({}, preserved_dispositions(record, original, repaired))

    def test_recognized_original_outcomes_retain_existing_dispositions(self):
        for source, stage, outcomes in (
            ("sol", "sol", ("PASS", "FAIL")),
            ("astra", "astra_review", ("CONTINUE", "REWORK", "COMPLETE", "TASK_COMPLETE")),
        ):
            for outcome in outcomes:
                with self.subTest(source=source, outcome=outcome):
                    original = report(disposition(), source=source, outcome=outcome)
                    self.assertEqual(
                        {source: [disposition()]},
                        preserved_dispositions({"stage": stage, "exit_code": 0}, original, copy.deepcopy(original)),
                    )

    def test_checkpoint_outcome_authority_is_separate_for_each_reviewer(self):
        record = {"stage": "astra_checkpoint", "exit_code": 0}
        valid = {"validation": report(disposition("F-sol")), "decision": report(disposition("F-astra"), source="astra")}
        for blocked_source, section, field, retained_source, retained_id in (
            ("sol", "validation", "verdict", "astra", "F-astra"),
            ("astra", "decision", "status", "sol", "F-sol"),
        ):
            for outcome in ("BLOCKED", "UNKNOWN", None):
                with self.subTest(source=blocked_source, outcome=outcome):
                    original = copy.deepcopy(valid)
                    if outcome is None:
                        original[section].pop(field)
                    else:
                        original[section][field] = outcome
                    self.assertEqual(
                        {retained_source: [disposition(retained_id)]}, preserved_dispositions(record, original, valid)
                    )

    def test_incomplete_stopped_or_already_repaired_original_cannot_authorize(self):
        original = report(disposition())
        record = {"stage": "sol", "exit_code": 0}
        for change in (
            {"stage": "terra"},
            {"stage": "sol_report_repair"},
            {"exit_code": 1},
            {"exit_code": None},
            {"exit_code": False},
            *(
                {flag: True}
                for flag in (
                    "report_only",
                    "report_repaired",
                    "truncated_output",
                    "timed_out",
                    "interrupted",
                    "abandoned",
                )
            ),
        ):
            with self.subTest(change=change):
                self.assertEqual({}, preserved_dispositions({**record, **change}, original, original))
        for extracted in (
            None,
            [],
            "invalid JSON",
            {"report": original, "extraction_error": "incomplete"},
            {"report": original, "extraction_error": None},
        ):
            with self.subTest(extracted=extracted):
                self.assertEqual({}, preserved_dispositions(record, extracted, original))

    def test_malformed_or_duplicate_ids_fail_closed_for_original_and_repair(self):
        record = {"stage": "sol", "exit_code": 0}
        good = report(disposition())
        bad_reports = [
            report(disposition(), disposition()),
            {"verdict": "PASS", "finding_dispositions": {}},
            report({"id": "F-1"}),
            report({**disposition(), "extra": "ignored?"}),
            report(disposition("")),
            report(disposition(" F-1")),
            report(disposition(action="fixed")),
            report(disposition(evidence=" ")),
        ]
        for bad in bad_reports:
            with self.subTest(bad=bad):
                self.assertEqual({}, preserved_dispositions(record, bad, good))
                self.assertEqual({}, preserved_dispositions(record, good, bad))

    def test_absent_dispositions_do_not_gain_authority_from_a_repair(self):
        record = {"stage": "sol", "exit_code": 0}
        self.assertEqual({}, preserved_dispositions(record, {"verdict": "PASS"}, report(disposition())))
        self.assertEqual({}, preserved_dispositions(record, report(disposition()), {"verdict": "PASS"}))


class UnretainedDispositionTests(unittest.TestCase):
    """#459: a refused repair names the row and what differs, but is still refused."""

    SOL = {"stage": "sol", "exit_code": 0}

    def test_retained_grants_exactly_the_preserved_rows(self):
        original = report(disposition(), disposition("F-2", action="retracted"))
        checkpoint = {
            "validation": report(disposition("F-sol")),
            "decision": report(disposition("F-astra"), source="astra"),
        }
        cases = [
            (self.SOL, original, copy.deepcopy(original)),
            (self.SOL, original, report(disposition(evidence="receipt.json"), disposition("F-2"))),
            (self.SOL, original, report(disposition(), disposition("F-new"))),
            (self.SOL, report(disposition(), outcome="BLOCKED"), report(disposition())),
            (self.SOL, None, original),
            ({**self.SOL, "report_repaired": True}, original, original),
            (self.SOL, report(disposition(), disposition()), report(disposition())),
            ({"stage": "astra_checkpoint", "exit_code": 0}, checkpoint, copy.deepcopy(checkpoint)),
            ({"stage": "terra", "exit_code": 0}, original, original),
        ]
        for record, before, after in cases:
            with self.subTest(record=record, before=before, after=after):
                fields = retained(record, before, after)
                self.assertEqual({"preserved_finding_dispositions", "unretained_finding_dispositions"}, set(fields))
                self.assertEqual(
                    preserved_dispositions(record, before, after), fields["preserved_finding_dispositions"]
                )
                kept = {
                    (source, row["id"])
                    for source, rows in fields["preserved_finding_dispositions"].items()
                    for row in rows
                }
                explained = {
                    (source, fid) for source, rows in fields["unretained_finding_dispositions"].items() for fid in rows
                }
                self.assertFalse(kept & explained)

    def test_each_unretained_row_says_what_differs(self):
        original = report(disposition())
        changed = "from the original review's row"
        for repaired_row, why in (
            (disposition(evidence=".autocode/evidence/receipt.json"), f"changed its evidence {changed}"),
            (disposition(action="retracted"), f"changed its disposition {changed}"),
            (disposition(action="retracted", evidence="event:new"), f"changed its disposition and evidence {changed}"),
            ({**disposition(), "extra": "x"}, f"changed its extra {changed}"),
            (disposition("F-new"), "is not in the original review"),
        ):
            with self.subTest(row=repaired_row):
                fields = retained(self.SOL, original, report(repaired_row))
                self.assertEqual({"sol": {repaired_row["id"]: why}}, fields["unretained_finding_dispositions"])
        # A kept row is not explained; only the new one is.
        fields = retained(self.SOL, original, report(disposition(), disposition("F-new")))
        self.assertEqual({"sol": [disposition()]}, fields["preserved_finding_dispositions"])
        self.assertEqual({"sol": {"F-new": "is not in the original review"}}, fields["unretained_finding_dispositions"])
        # An exact row lost only because another repaired row is malformed is not told it cannot be kept.
        fields = retained(self.SOL, original, report(disposition(), {"id": "F-2", "disposition": "resolved"}))
        self.assertEqual({}, fields["preserved_finding_dispositions"])
        self.assertEqual(
            {"F-1": MALFORMED_ELSEWHERE, "F-2": "is not in the original review"},
            fields["unretained_finding_dispositions"]["sol"],
        )

    def test_a_row_without_authority_says_it_cannot_be_kept_even_when_edited(self):
        # Naming a difference would imply an exact copy is kept, and following that would waste a repair.
        exact = report(disposition())
        repairs = (
            exact,
            report(disposition(evidence=".autocode/evidence/receipt.json")),
            report(disposition(action="retracted")),
            report(disposition("F-new")),
        )
        for record, before in (
            (self.SOL, report(disposition(), outcome="BLOCKED")),
            (self.SOL, None),
            ({**self.SOL, "report_repaired": True}, exact),
            ({**self.SOL, "exit_code": 1}, exact),
            ({**self.SOL, "timed_out": True}, exact),
            ({**self.SOL, "truncated_output": True}, exact),
            ({**self.SOL, "interrupted": True}, exact),
            (self.SOL, report(disposition(), {"id": "F-2", "disposition": "resolved"})),
        ):
            for repaired in repairs:
                with self.subTest(record=record, before=before, repaired=repaired):
                    fid = repaired["finding_dispositions"][0]["id"]
                    self.assertEqual(
                        {"sol": {fid: UNAUTHORIZED}},
                        retained(record, before, repaired)["unretained_finding_dispositions"],
                    )
        self.assertEqual(
            {}, retained({"stage": "terra", "exit_code": 0}, exact, exact)["unretained_finding_dispositions"]
        )

    def test_checkpoint_explains_each_reviewer_separately(self):
        original = {
            "validation": report(disposition("F-sol")),
            "decision": report(disposition("F-astra"), source="astra"),
        }
        repaired = copy.deepcopy(original)
        repaired["validation"]["finding_dispositions"][0]["evidence"] = "receipt.json"
        fields = retained({"stage": "astra_checkpoint", "exit_code": 0}, original, repaired)
        self.assertEqual({"astra": [disposition("F-astra")]}, fields["preserved_finding_dispositions"])
        self.assertEqual(
            {"sol": {"F-sol": "changed its evidence from the original review's row"}},
            fields["unretained_finding_dispositions"],
        )
        # A blocked reviewer authorizes nothing; the other reviewer's difference is still named.
        original["decision"]["status"] = "BLOCKED"
        repaired["decision"]["finding_dispositions"][0]["evidence"] = "receipt.json"
        fields = retained({"stage": "astra_checkpoint", "exit_code": 0}, original, repaired)
        self.assertEqual({}, fields["preserved_finding_dispositions"])
        self.assertEqual(
            {
                "sol": {"F-sol": "changed its evidence from the original review's row"},
                "astra": {"F-astra": UNAUTHORIZED},
            },
            fields["unretained_finding_dispositions"],
        )

    def test_refusal_names_each_row_and_falls_back_without_an_explanation(self):
        record = {
            "unretained_finding_dispositions": {
                "sol": {"F-1": "changed its evidence", "F-2": "is not in the original review"}
            }
        }
        message = refusal("sol", ["F-1"], record)
        self.assertTrue(
            message.startswith(
                "A report-only repair cannot close findings: sol finding_dispositions row F-1 changed its evidence. "
            ),
            message,
        )
        self.assertIn("byte-for-byte copy, evidence included", message)
        self.assertIn(
            "A report-only repair cannot close findings: sol finding_dispositions row F-1 changed its "
            "evidence; sol finding_dispositions row F-2 is not in the original review. ",
            refusal("sol", ["F-1", "F-2", "F-1"], record),
        )
        self.assertIn(
            "astra finding_dispositions row F-1 is not an exact row of the original completed review",
            refusal("astra", ["F-1"], record),
        )
        self.assertIn("row F-2 is not an exact row", refusal("sol", ["F-2"], {}))

    def test_the_ledger_still_refuses_a_changed_row_and_names_it(self):
        state = {}
        findings.record_validation(
            state,
            {
                "findings": [
                    {
                        "severity": "high",
                        "finding": "Empty names are accepted",
                        "evidence": "event:check",
                        "blocking": True,
                    }
                ]
            },
            {"output": "sol-01.json"},
        )
        fid = findings.open_entries(state)[0]["id"]
        original = report(disposition(fid))
        for change, why in (
            ({"evidence": ".autocode/evidence/receipt.json"}, "changed its evidence"),
            ({"disposition": "retracted"}, "changed its disposition"),
            ({"evidence": " event:check "}, "changed its evidence"),
        ):
            with self.subTest(change=change):
                repaired = report({**disposition(fid), **change})
                record = {"output": "repair.json", "report_repaired": True, **retained(self.SOL, original, repaired)}
                with self.assertRaises(ValueError) as caught:
                    findings.record_validation(state, copy.deepcopy(repaired), record)
                self.assertIn(
                    f"report-only repair cannot close findings: sol finding_dispositions row {fid} {why}",
                    str(caught.exception),
                )
                self.assertEqual([fid], [row["id"] for row in findings.open_entries(state)])
        # The fresh review's exact row is the only one a repair can carry through.
        record = {"output": "repair.json", "report_repaired": True, **retained(self.SOL, original, original)}
        findings.record_validation(state, copy.deepcopy(original), record)
        self.assertEqual([], findings.open_entries(state))

    def test_the_ledger_names_every_changed_row_of_open_findings_at_once(self):
        state = {}
        findings.record_validation(
            state,
            {
                "findings": [
                    {"severity": "high", "finding": defect, "evidence": "event:check", "blocking": True}
                    for defect in ("Empty names are accepted", "Long names are cut", "Tabs are kept")
                ]
            },
            {"output": "sol-01.json"},
        )
        first, second, third = (row["id"] for row in findings.open_entries(state))
        original = report(disposition(first), disposition(second), disposition(third), disposition("F-closed"))
        # The first and third rows were rewritten; the second is exact; F-closed names no open finding.
        repaired = report(
            disposition(first, evidence="receipt.json"),
            disposition(second),
            disposition(third, evidence="receipt.json"),
            disposition("F-closed", evidence="x"),
        )
        record = {"output": "repair.json", "report_repaired": True, **retained(self.SOL, original, repaired)}
        with self.assertRaises(ValueError) as caught:
            findings.record_validation(state, copy.deepcopy(repaired), record)
        message = str(caught.exception)
        for fid in (first, third):
            self.assertIn(
                f"sol finding_dispositions row {fid} changed its evidence from the original review's row", message
            )
        self.assertNotIn(second, message)
        self.assertNotIn("F-closed", message)
        self.assertEqual(3, len(findings.open_entries(state)))

    def test_the_repair_prompt_rule_forbids_editing_a_closure_row(self):
        for phrase in (
            "byte-for-byte",
            "evidence text",
            "omit a row rather than edit it",
            "even where you correct a citation elsewhere",
            "cites a check (check:N) that you corrected",
        ):
            self.assertIn(phrase, REPAIR_INSTRUCTION)


class OriginalReportExtractionTests(unittest.TestCase):
    def test_saved_json_is_extracted_without_acceptance_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            value = report(disposition())
            path.write_text(json.dumps(value))
            before = path.read_bytes()
            self.assertEqual(
                {"report": value, "extraction_error": None},
                original_report_for_repair({"stage": "sol", "output": str(path)}),
            )
            self.assertEqual(before, path.read_bytes())
            path.write_text("{invalid")
            result = original_report_for_repair({"output": str(path)})
            self.assertIsNone(result["report"])
            self.assertTrue(result["extraction_error"])

    def test_opencode_uses_terminal_events_and_retains_extraction_failure(self):
        original = {"engine": "opencode", "events": "original.jsonl", "output": "report.json"}
        with patch("autocode_report_source.opencode.final_report", return_value=report(disposition())) as extract:
            result = original_report_for_repair(original)
        extract.assert_called_once_with("original.jsonl")
        self.assertEqual({"report": report(disposition()), "extraction_error": None}, result)
        with patch("autocode_report_source.opencode.final_report", side_effect=RuntimeError("no terminal report")):
            self.assertEqual(
                {"report": None, "extraction_error": "no terminal report"}, original_report_for_repair(original)
            )
