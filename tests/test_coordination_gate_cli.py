"""A conversational set approval reaches real isolated joint proof and deduplicated publication."""

import json
import sys
import unittest

from tests import test_coordination_tui_e2e as fixture


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised provider requires supported Linux")
class CoordinatedGateCliTests(unittest.TestCase):
    setUp = fixture.CoordinationTuiEndToEnd.setUp
    tearDown = fixture.CoordinationTuiEndToEnd.tearDown
    run_flow = fixture.CoordinationTuiEndToEnd.run_flow

    def answer(self, question):
        fixture.CoordinationTuiEndToEnd.answer(self, question)
        return "yes coplete"

    def test_typo_approval_is_interpreted_once_before_build_and_joint_publication(self):
        self.env["COORDINATION_FIXTURE_MEMBER_CONTEXT"] = "1"
        self.run_flow()
        calls = [json.loads(line) for line in (self.intake / ".autocode/provider-calls.jsonl").read_text().splitlines()]
        replies = [row for row in calls if row.get("name") == "conversation" and row["stage"] == "astra_finalize"]
        self.assertEqual(1, len(replies), "one interpretation, no duplicate reviewer dispatch")
        self.assertEqual("confirm", replies[0]["intent"])
        self.assertTrue(any("Plan Reviewer:" in line for line in self.lines))


if __name__ == "__main__":
    unittest.main()
