import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_permission_recovery as recovery
import autocode_recovery_limits as limits


class PermissionRecovery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()

    def prepare(self, stage, denied, recoveries, stages, run="first"):
        return recovery.prepare(self.workspace, stage, denied, recoveries, stages,
                                run_dir=self.workspace / ".autocode/runs" / run)

    def test_temporary_path_changes_do_not_create_a_new_incident(self):
        first = self.prepare("terra", recovery.operation(
            "permission requested: external_directory (/tmp/first/*); auto-rejecting"), [], [])
        first["events"] = "first.jsonl"
        second = self.prepare("terra", recovery.operation(
            "permission requested: external_directory (/private/tmp/second/*); auto-rejecting"),
            [first], [{"stage": "terra", "events": "first.jsonl", "abandoned": True}])
        self.assertEqual(first["incident_id"], second["incident_id"])
        self.assertEqual(2, second["repeat_count"])
        self.assertTrue(Path(second["diagnostic_directory"]).is_relative_to(self.workspace))
        (Path(second["diagnostic_directory"]) / "probe.txt").write_text("usable")

    def test_successful_owner_ends_incident_but_another_role_does_not(self):
        denied = recovery.operation("permission requested: external_directory (/external/project/*)")
        first = self.prepare("terra", denied, [], [])
        first["events"] = "first.jsonl"
        archived = {"stage": "terra", "events": "first.jsonl", "abandoned": True}
        other = self.prepare("terra", denied, [first], [archived, {"stage": "sol"}])
        self.assertEqual(2, other["repeat_count"])
        resolved = self.prepare("terra", denied, [first], [archived, {"stage": "terra"}])
        self.assertEqual(1, resolved["repeat_count"])

    def test_symlink_cannot_redirect_scratch_outside_workspace(self):
        outside = self.workspace / "outside"
        outside.mkdir()
        (self.workspace / ".autocode").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.prepare("terra", recovery.operation("external_directory"), [], [])
        self.assertEqual([], list(outside.iterdir()))

    def test_unchanged_resume_does_not_erase_permission_hold_or_allowance(self):
        denied = recovery.operation("permission requested: external_directory (/tmp/probe/*)")
        context = self.prepare("terra", denied, [], [])
        context.update(repeat_count=2, source_revision="retained")
        state = {"workspace": str(self.workspace), "recovery_context": context}
        with patch.object(limits, "snapshot", return_value={"revision": "retained"}):
            reason = limits.stop_reason(state, 2, 3)
            self.assertEqual("PAUSED_REPEATED_FAILURE", reason[0])
            self.assertIn("/tmp/probe/*", reason[1])
        with patch.object(limits, "snapshot", return_value={"revision": "changed"}):
            self.assertIsNone(limits.stop_reason(state, 2, 3))
            self.assertEqual("PAUSED_TIMEOUT_RECOVERY", limits.stop_reason(state, 3, 3)[0])

    def held(self, **fields):
        context = self.prepare("terra", recovery.operation(
            "permission requested: external_directory (/tmp/probe/*)"), [], [])
        context.update(stage="terra", events="builder-02.jsonl", source_revision="retained", **fields)
        stages = [{"stage": "terra", "events": "builder-02.jsonl", "abandoned": True}]
        return {"workspace": str(self.workspace), "recovery_context": context, "stages": stages}

    def test_the_hold_ends_once_an_attempt_of_its_stage_is_accepted_but_not_after_a_diagnosis(self):
        # #301: an authorized attempt that succeeds without changing the source must not leave the
        # next stage (the Tester) held by the Builder's old denial.
        state = self.held(repeat_count=2)
        with patch.object(limits, "snapshot", return_value={"revision": "retained"}):
            self.assertEqual("PAUSED_REPEATED_FAILURE", limits.stop_reason(state, 0, 3)[0])
            for row, held in (({"stage": "investigate_stuck", "events": "investigator.jsonl"}, True),
                              ({"stage": "terra", "events": "builder-03.jsonl", "abandoned": True}, True),
                              ({"stage": "terra", "events": "builder-03.jsonl"}, False)):
                with self.subTest(row=row):
                    reason = limits.stop_reason({**state, "stages": state["stages"] + [row]}, 0, 3)
                    self.assertEqual(held, reason is not None and reason[0] == "PAUSED_REPEATED_FAILURE")

    def test_the_denial_ceiling_holds_every_launch_too(self):
        with patch.object(limits, "snapshot", return_value={"revision": "retained"}):
            self.assertIsNone(limits.stop_reason(self.held(repeat_count=1, denied_since_accepted=2), 0, 3))
            reason = limits.stop_reason(self.held(repeat_count=1, denied_since_accepted=3), 0, 3)
            self.assertEqual("PAUSED_REPEATED_FAILURE", reason[0])
            self.assertIn("ceiling", reason[1])

    def test_a_diagnosis_between_denials_does_not_restart_the_ceiling(self):
        denied = recovery.operation("permission requested: external_directory (/srv/other/*)")
        denials = [{"stage": "sol", "events": f"validator-0{n}.jsonl", "abandoned": True,
                    "rejection_reason": recovery.DENIAL_MARKER + " before a terminal turn"} for n in range(1, 5)]
        for between, expected in (({"stage": "investigate_stuck"}, 4), ({"stage": "astra_diagnose"}, 4),
                                  ({"stage": "sol"}, 0)):
            with self.subTest(between=between):
                after = self.prepare("sol", denied, [], denials + [between])
                self.assertEqual(expected, after["denied_since_accepted"])

    def test_two_runs_do_not_share_scratch_even_for_the_same_denial(self):
        denied = recovery.operation("permission requested: external_directory (/tmp/probe/*)")
        first = self.prepare("terra", denied, [], [], "first")
        second = self.prepare("terra", denied, [], [], "second")
        self.assertNotEqual(first['diagnostic_directory'], second['diagnostic_directory'])
        receipt = Path(first['diagnostic_directory']) / 'retained.txt'
        receipt.write_text('first run evidence')
        self.prepare("terra", denied, [], [], "second")
        self.assertEqual('first run evidence', receipt.read_text())
