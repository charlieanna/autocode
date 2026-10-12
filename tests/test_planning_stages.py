"""Every planning-stage table is the canonical list in autocode_roles (#1124)."""

from __future__ import annotations

import unittest

import autocode_planning_metadata
import autocode_recovery_progress
import autocode_report_repair_context
import autocode_roles as roles
import autocode_run_records
import autocode_stuck_job
from units import autoplanner

# A table that is a subset of ALL_PLANNING_STAGES on purpose, with its reason.
# Anything not listed here must equal the canonical list exactly.
DOCUMENTED_SUBSETS = {
    "units.autoplanner.STAGES": (set(roles.PLANNING_STAGES), "the legacy planning flow's stages"),
    "units.autoplanner.V2_STAGES": (set(roles.PLANNING_V2_STAGES), "the planning-v2 flow's stages"),
}

TABLES = {
    "units.autoplanner.STAGES": autoplanner.STAGES,
    "units.autoplanner.V2_STAGES": autoplanner.V2_STAGES,
    "autocode_recovery_progress.PLANNING": autocode_recovery_progress.PLANNING,
    "autocode_stuck_job.PLANNING": autocode_stuck_job.PLANNING,
    "autocode_run_records.PLANNING_STAGES": autocode_run_records.PLANNING_STAGES,
    "autocode_report_repair_context.PLANNING_STAGES": autocode_report_repair_context.PLANNING_STAGES,
    "autocode_planning_metadata.PLANNING_STAGES": autocode_planning_metadata.PLANNING_STAGES,
}


class PlanningStageListTests(unittest.TestCase):
    def test_every_table_is_canonical_or_a_documented_subset(self):
        canonical = set(roles.ALL_PLANNING_STAGES)
        for name, table in TABLES.items():
            with self.subTest(table=name):
                actual = set(table)
                if name in DOCUMENTED_SUBSETS:
                    self.assertEqual(DOCUMENTED_SUBSETS[name][0], actual, DOCUMENTED_SUBSETS[name][1])
                else:
                    self.assertEqual(
                        canonical,
                        actual,
                        f"{name} disagrees with autocode_roles.ALL_PLANNING_STAGES; derive it from there",
                    )

    def test_the_documented_subsets_are_still_subsets(self):
        canonical = set(roles.ALL_PLANNING_STAGES)
        for name, (subset, reason) in DOCUMENTED_SUBSETS.items():
            with self.subTest(table=name):
                self.assertLessEqual(subset, canonical, f"{name} ({reason}) is not a subset any more")

    def test_planning_flow_reads_a_state_or_a_settings_dict(self):
        self.assertEqual("v2", roles.planning_flow({"settings": {"planning_flow": "v2"}}))
        self.assertEqual("legacy", roles.planning_flow({"settings": {}}))
        self.assertEqual("legacy", roles.planning_flow({}))
        self.assertEqual("v2", roles.planning_flow({"planning_flow": "v2"}))
        self.assertEqual("legacy", roles.planning_flow({"planning_flow": "something-else"}))


if __name__ == "__main__":
    unittest.main()
