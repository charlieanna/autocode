"""A failed runner lifecycle observation is a finding the repair loop acts on (#451).

Pure ledger logic over runner-verified replay results; the public CLI path is in test_risk_cli.
"""
from __future__ import annotations

import copy
import unittest

import autocode_completion as completion
import autocode_findings as findings_ledger
import autocode_risk_findings as risk_findings
from tests.test_risk_acceptance import QUEUE

OBSERVATION = {"hash": "a" * 64, "protocol": "lease_queue_lifecycle_v1", "criterion_ids": ["AC-queue"],
               "target": {"module": "leasequeue", "class_name": "LeaseQueue", "methods": {}},
               "declaration": {"source_quote": "Build a durable SQLite LeaseQueue(path) ..."}}
REASON = ("Mandatory lifecycle observation failed or timed out: "
          "Restart reused a lease token or accepted a stale acknowledgment/release")


def replay(error, revision="rev-one", summary="/run/check-replay/x/risk-acceptance/1/summary.json"):
    return {"verdict": "FAIL" if error else "PASS", "risk_acceptance": {
        "verdict": "FAIL" if error else "PASS", "source_revision": revision, "summary": summary,
        "checks": [{"observation_hash": OBSERVATION["hash"], "error": error,
                    "output": "/run/check-replay/x/risk-acceptance/1/case-01/output.txt"}]}}


def state():
    return {"goal_contract": {"hash": "c" * 64, "body": {"acceptance_criteria": [{"id": "AC-queue"}],
                                                       "risk_acceptance": {"manifest": {"observations": [OBSERVATION]}}}},
            "findings_ledger": [], "unresolved_findings": [{"id": "", "finding": "Tester note", "blocking": False}]}


class RiskFindingTests(unittest.TestCase):
    def test_a_failed_observation_opens_one_runner_blocking_finding_the_builder_sees(self):
        current = state()
        opened = risk_findings.reconcile(current, replay(REASON), {"source_revision": "rev-one"})
        self.assertEqual(1, len(opened))
        row = opened[0]
        self.assertEqual(("runner", "open", True, "high"), (row["source"], row["status"], row["blocking"], row["severity"]))
        self.assertIn("Restart reused a lease token", row["finding"])
        self.assertIn("leasequeue.LeaseQueue", row["finding"])
        self.assertIn("killed with SIGKILL", row["finding"])  # what the fixed protocol does, to reproduce it
        self.assertIn("summary.json", row["evidence"])
        self.assertIn("case-01/output.txt", row["evidence"])
        self.assertEqual([row], findings_ledger.blocking_entries(current))
        self.assertEqual([row["id"]], [item["id"] for item in current["unresolved_findings"] if item.get("source") == "runner"])
        self.assertEqual("Tester note", current["unresolved_findings"][0]["finding"])
        self.assertIn(row["id"], {item["id"] for item in findings_ledger.handoff(current)})
        self.assertEqual(1, findings_ledger.summary(current)["by_source"]["runner"])
        message = risk_findings.blocking_summary(current)
        self.assertIn(row["id"] + " (runner)", message)
        self.assertIn("request REWORK", message)

    def test_a_failed_contention_observation_says_how_the_runner_raced_it(self):
        current = state()
        observation = current["goal_contract"]["body"]["risk_acceptance"]["manifest"]["observations"][0]
        observation["declaration"] = {**observation["declaration"], "promises": ["durable_restart", "contention_atomicity"]}
        row = risk_findings.reconcile(current, replay("Concurrent claims leased one job twice and left another unclaimed"),
                                      {})[0]
        self.assertIn("Concurrent claims leased one job twice", row["finding"])
        self.assertIn("Released together", row["finding"])  # what the contention phase does, to reproduce it
        self.assertIn("killed with SIGKILL", row["finding"])

    def test_a_repeated_failure_refreshes_the_same_finding(self):
        current = state()
        first = risk_findings.reconcile(current, replay(REASON), {})[0]
        again = risk_findings.reconcile(current, replay(REASON, revision="rev-two"), {})[0]
        self.assertIs(first, again)
        self.assertEqual(2, again["times_reported"])
        self.assertIn("rev-two", again["finding"])
        self.assertEqual(1, len(current["findings_ledger"]))
        self.assertEqual(1, sum(item.get("source") == "runner" for item in current["unresolved_findings"]))

    def test_only_the_runner_passing_the_observation_closes_it(self):
        current = state()
        row = risk_findings.reconcile(current, replay(REASON), {})[0]
        # A Tester cannot dispose of or cite the runner's finding.
        findings_ledger.record_validation(current, {"verdict": "PASS", "findings": [], "finding_dispositions": [
            {"id": row["id"], "disposition": "resolved", "evidence": "event:check"}]}, {"output": "sol-2.json"})
        self.assertEqual("open", row["status"])
        with self.assertRaisesRegex(ValueError, "not one open finding of this reviewer"):
            findings_ledger.record_validation(current, {"verdict": "FAIL", "findings": [
                {"id": row["id"], "finding": "still failing", "severity": "high"}]}, {"output": "sol-3.json"})
        # A replay that did not run this observation leaves it open; one that passed it closes it.
        self.assertEqual([], risk_findings.reconcile(current, None, {}))
        self.assertEqual([], risk_findings.reconcile(current, {"verdict": "PASS", "risk_acceptance": None}, {}))
        self.assertEqual("open", row["status"])
        risk_findings.reconcile(current, replay("", revision="rev-three"), {})
        self.assertEqual("resolved", row["status"])
        self.assertIn("PASS on source rev-three", row["resolution_evidence"])
        self.assertEqual([], findings_ledger.blocking_entries(current))

    def test_an_amended_observation_retires_its_finding_while_the_replacement_is_checked(self):
        current = state()
        row = risk_findings.reconcile(current, replay(REASON), {})[0]
        replacement = {**OBSERVATION, "hash": "d" * 64}
        current["goal_contract"]["body"]["risk_acceptance"]["manifest"]["observations"] = [replacement]
        failing = copy.deepcopy(replay(REASON))
        failing["risk_acceptance"]["checks"][0]["observation_hash"] = replacement["hash"]
        new = risk_findings.reconcile(current, failing, {})[0]
        self.assertEqual("retracted", row["status"])
        self.assertEqual(("open", replacement["hash"]), (new["status"], new["observation_hash"]))
        self.assertEqual([new], findings_ledger.blocking_entries(current))

    def test_unknown_observations_and_unsaved_validations_do_not_reach_the_builder(self):
        current = state()
        unknown = copy.deepcopy(replay(REASON))
        unknown["risk_acceptance"]["checks"][0]["observation_hash"] = "b" * 64
        self.assertEqual([], risk_findings.reconcile(current, unknown, {}))
        archived = risk_findings.reconcile(current, replay(REASON), {}, saved=False)
        self.assertEqual(1, len(archived))
        self.assertFalse(any(item.get("source") == "runner" for item in current["unresolved_findings"]))


class CompletionRefusalTests(unittest.TestCase):
    """The refusal names what actually blocks completion (#451, gap 7)."""

    def refused(self, validation):
        return completion.rejection({"task": QUEUE, "acceptance_criteria": [{"id": "AC1"}], "validation": validation})

    def test_a_failed_validation_names_its_failed_criteria_not_the_missing_lifecycle_proof(self):
        message = self.refused({"verdict": "FAIL", "criterion_results": [{"id": "AC1", "status": "FAIL"}]})
        self.assertIn("no passing result for AC1", message)
        self.assertNotIn("lifecycle", message)

    def test_a_passing_report_without_runner_proof_still_names_the_lifecycle_gap(self):
        message = self.refused({"verdict": "PASS", "criterion_results": [{"id": "AC1", "status": "PASS"}]})
        self.assertIn("Source-declared lifecycle promises need fresh, intact runner process-recovery proof", message)

    def test_a_failed_runner_observation_is_named_with_its_receipt(self):
        message = self.refused({"verdict": "PASS", "criterion_results": [{"id": "AC1", "status": "PASS"}],
                                "check_replay": replay(REASON)})
        self.assertIn("observation failed on this source: " + REASON, message)
        self.assertIn("summary.json", message)
        self.assertIn("REWORK", message)


if __name__ == "__main__":
    unittest.main()
