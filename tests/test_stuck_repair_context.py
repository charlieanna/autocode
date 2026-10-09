"""Investigator report repairs retain the file evidence needed for their probes."""
import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path

import autocode_stuck_job as stuck

from . import test_report_repair as repairs


class InvestigatorRepairHandoffTests(unittest.TestCase):
    setUp = repairs.RepairTests.setUp
    stage_record = repairs.RepairTests.stage_record
    queue = repairs.RepairTests.queue
    repair_request = repairs.RepairTests.repair_request

    def investigation(self):
        archived = self.run / "archived-plan-finalize-report-repair-02"
        archived.mkdir()
        output = archived / "plan-finalize-report-repair-02.json"
        output.write_text(json.dumps({"unexpected": "field"}))
        self.state.update(next_stage=stuck.STAGE, stuck_investigation={
            "identity": "finalize:invalid", "stage": "astra_finalize", "status": "PAUSED_INVALID_OUTPUT",
            "reason": "$.decisions[0]: unexpected fields"}, stages=[{
                "stage": "astra_finalize_report_repair", "iteration": 9, "output": str(output),
                "rejected": True, "rejection_reason": "$.decisions[0]: unexpected fields"}],
            failure_history={"finalize": {"identity": {"stage": "astra_finalize"},
                                         "count": 3, "last_error": "unexpected fields"}})
        self.queue(role="astra", stage=stuck.STAGE, error=ValueError("diagnosed causes' probes did not exit 0"))
        return output

    def test_repair_gets_the_active_investigation_and_exact_archived_probe_path(self):
        output = self.investigation()
        data = json.loads(self.repair_request()["prompt"].split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertIn("investigation_context", data)
        context = data["investigation_context"]
        self.assertEqual(self.state["stuck_investigation"]["reason"], context["stuck"]["reason"])
        self.assertEqual(str(output), context["recent_stages"][0]["output"])
        self.assertEqual(3, context["failure_history"]["finalize"]["count"])
        self.assertIn({"evidence_ref": str(output), "probe_path": "run/plan-finalize-report-repair-02.json"},
                      context["evidence_files"])

    def test_investigator_repair_does_not_permit_shell_event_citations(self):
        self.investigation()
        prompt = self.repair_request()["prompt"].split("CURRENT HANDOFF DATA\n", 1)[0]
        self.assertNotIn("Evidence references must be bare event: IDs or exact file paths", prompt)
        self.assertIn("event: IDs are invalid for investigate_stuck", prompt)


class InvestigatorRepairContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="stuck-repair-context-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.run = self.root / "run"
        self.run.mkdir()
        self.saved = self.run / "archived-repair-02" / "repair-02.json"
        self.saved.parent.mkdir()
        self.saved.write_text("{}")
        self.repository = self.workspace / "diagnosis.txt"
        self.repository.write_text("saved diagnosis")
        self.schema = self.run / "schemas" / "v3-astra_finalize.json"
        self.schema.parent.mkdir()
        self.schema.write_text("{}")
        self.outside = self.root / "outside.txt"
        self.outside.write_text("not this run")
        self.state = {"task": "Repair the blank-name behavior", "workspace": str(self.workspace),
                      "stuck_investigation": {"identity": "finalize:invalid", "stage": "astra_finalize",
                                              "status": "PAUSED_INVALID_OUTPUT", "reason": "unexpected fields"},
                      "stages": [{"stage": "astra_finalize", "output": str(self.saved), "rejected": True}],
                      "failure_history": {}}

    def helper(self):
        return importlib.import_module("autocode_stuck_repair_context")

    def test_context_maps_existing_cited_files_and_omits_invalid_refs_without_mutating_state(self):
        before = copy.deepcopy(self.state)
        source = {"path": str(self.saved), "content": {"evidence_refs": [
            "diagnosis.txt:1", str(self.saved), str(self.schema), str(self.saved.parent),
            "event:read", str(self.outside), "missing.json"]}}
        context = self.helper().context(self.state, stuck.STAGE, self.run / "state.json", self.workspace, (source,))
        self.assertEqual([
            {"evidence_ref": str(self.saved), "probe_path": "run/repair-02.json"},
            {"evidence_ref": "diagnosis.txt:1", "probe_path": "diagnosis.txt"},
            {"evidence_ref": str(self.schema), "probe_path": "run/v3-astra_finalize.json"},
        ], context["evidence_files"])
        context["stuck"]["reason"] = "changed"
        self.assertEqual(before, self.state)

    def test_evidence_files_describes_scratch_names_for_archived_reports_and_schema_files(self):
        refs = [str(self.saved), str(self.schema), "diagnosis.txt:1", "event:read", str(self.saved.parent)]
        self.assertEqual([
            {"evidence_ref": str(self.saved), "probe_path": "run/repair-02.json"},
            {"evidence_ref": str(self.schema), "probe_path": "run/v3-astra_finalize.json"},
            {"evidence_ref": "diagnosis.txt:1", "probe_path": "diagnosis.txt"},
        ], self.helper().evidence_files(refs, self.workspace, self.run))

    def test_other_stages_and_a_missing_active_request_have_no_investigation_context(self):
        self.assertEqual({}, self.helper().context(self.state, "sol", self.run / "state.json", self.workspace))
        self.state.pop("stuck_investigation")
        self.assertEqual({}, self.helper().context(self.state, stuck.STAGE, self.run / "state.json", self.workspace))

    def test_evidence_policy_keeps_validator_and_builder_event_permissions(self):
        for stage in ("sol", "terra"):
            with self.subTest(stage=stage):
                instruction = self.helper().evidence_instruction(stage)
                self.assertIn("Evidence references must be bare event: IDs or exact file paths", instruction)
                self.assertIn("independently executed Validator tool event", instruction)
                self.assertNotIn("event: IDs are invalid", instruction)
        strict = self.helper().evidence_instruction(stuck.STAGE)
        self.assertIn("event: IDs are invalid for investigate_stuck", strict)
        self.assertIn("investigation_context.evidence_files", strict)
        self.assertIn("never open an absolute live run path", strict)

    def test_event_citations_remain_rejected_by_real_file_validation(self):
        with self.assertRaisesRegex(ValueError, "not found there.*event:read"):
            stuck.cited_files({"evidence_refs": ["event:read"]}, self.workspace, self.run)


if __name__ == "__main__":
    unittest.main()
