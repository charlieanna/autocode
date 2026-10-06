"""The status view discloses durability/concurrency promises no runner protocol proves (#451)."""
from __future__ import annotations

import unittest
from pathlib import Path

import autocode_risk_acceptance as acceptance
import autocode_risk_disclosure as disclosure
import autocode_run_view as run_view
from tests.test_risk_acceptance import QUEUE

CATALOG = Path(__file__).resolve().parents[1] / "scenarios" / "catalog"


def claims(text, kind="task"):
    sources = [{"id": "task:0", "kind": kind, "text": text}]
    return disclosure.claims(sources, acceptance.inventory(sources, []))


class RiskDisclosureTests(unittest.TestCase):
    def test_a_declared_lifecycle_api_leaves_only_its_unproven_contention_promise(self):
        rows = claims((CATALOG / "ladder-18-durable-lease-queue" / "brief.md").read_text())
        self.assertEqual(["concurrency"], [row["kind"] for row in rows])
        self.assertIn("atomic under contention", rows[0]["quote"])
        self.assertIn("one worker at a time", rows[0]["reason"])

    def test_durability_without_a_supported_family_is_disclosed_not_required(self):
        rows = claims((CATALOG / "ladder-21-event-replay-snapshots" / "brief.md").read_text())
        self.assertIn("durability", {row["kind"] for row in rows})
        self.assertEqual([], acceptance.inventory([{"id": "task:0", "kind": "task", "text": (
            CATALOG / "ladder-21-event-replay-snapshots" / "brief.md").read_text()}], []))

    def test_an_unsupported_declaration_does_not_count_as_proven(self):
        reworded = QUEUE.replace("token is fresh and opaque on every claim", "tokens identify claims")
        self.assertIn("durability", {row["kind"] for row in claims(reworded)})
        self.assertNotIn("durability", {row["kind"] for row in claims(QUEUE)})

    def test_ordinary_excluded_and_nonhuman_text_disclose_nothing(self):
        for text in ("Build a helpful to-do CLI with tests.",
                     "Concurrent builders and real compiler processes are out of scope."):
            self.assertEqual([], claims(text), text)
        self.assertEqual([], disclosure.view({"task": ""}))
        self.assertEqual([], disclosure.view({"task": None, "user_events": "malformed"}))

    def test_the_status_view_carries_the_disclosure(self):
        evidence = run_view.evidence({"task": "Build a durable notes store that survives restart."})
        self.assertEqual(["durability"], [row["kind"] for row in evidence["unverified_risk_claims"]])
        self.assertEqual([], run_view.evidence({"task": "Build a to-do CLI."})["unverified_risk_claims"])


if __name__ == "__main__":
    unittest.main()
