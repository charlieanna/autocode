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
    def test_a_declared_lifecycle_api_with_its_stated_contention_promise_leaves_nothing_unverified(self):
        # The runner hard-kills a worker for the durability promise and races three interpreters for
        # the contention one (autocode_risk_protocols).
        for scenario in ("ladder-18-durable-lease-queue", "ladder-19-transactional-outbox"):
            with self.subTest(scenario=scenario):
                self.assertEqual([], claims((CATALOG / scenario / "brief.md").read_text()))

    def test_a_concurrency_promise_the_protocol_does_not_race_stays_disclosed(self):
        brief = (CATALOG / "ladder-18-durable-lease-queue" / "brief.md").read_text()
        extra = brief + " pending() is consistent while other processes call nack concurrently."
        rows = claims(extra)
        self.assertEqual(["concurrency"], [row["kind"] for row in rows])
        self.assertIn("nack concurrently", rows[0]["quote"])
        self.assertIn("No runner protocol races concurrent callers", rows[0]["reason"])
        # Without a supported declaration, the stated contention promise is not raced either.
        unsupported = QUEUE.replace("token is fresh and opaque on every claim", "tokens identify claims")
        self.assertIn("concurrency", {row["kind"] for row in claims(unsupported)})

    def test_durability_without_a_supported_family_is_disclosed_not_required(self):
        rows = claims((CATALOG / "ladder-21-event-replay-snapshots" / "brief.md").read_text())
        self.assertIn("durability", {row["kind"] for row in rows})
        self.assertEqual([], acceptance.inventory([{"id": "task:0", "kind": "task", "text": (
            CATALOG / "ladder-21-event-replay-snapshots" / "brief.md").read_text()}], []))

    def test_a_storage_class_outside_both_families_is_disclosed_not_required(self):
        text = "Write TodoList(filename) with add(item) and claim(item). The list must survive restart."
        self.assertEqual([], acceptance.inventory([{"id": "task:0", "kind": "task", "text": text}], []))
        self.assertEqual(["durability"], [row["kind"] for row in claims(text)])

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
