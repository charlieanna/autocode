"""Committed facts survive every kind of unavailable or conflicting refresh."""

import copy
import unittest

import autocode_mutation_checkpoint as checkpoint


def study():
    return {
        "collected": True,
        "selected_tests": ["tests.test_value.ValueTests.test_value"],
        "execution_policy": "stop_after_selected_failure_v1",
        "reference_output": "reference-one.log",
        "files": [
            {
                "path": "packages/value.py",
                "sha256": "1" * 64,
                "mutants": [
                    {"site_id": "one", "source_sha256": "1" * 64, "outcome": "killed", "evidence": ["one.log"]},
                    {"site_id": "two", "source_sha256": "1" * 64, "outcome": "timeout", "evidence": ["two.log"]},
                    {"site_id": "three", "source_sha256": "1" * 64, "outcome": "not_run", "evidence": []},
                ],
            }
        ],
    }


class MutationCheckpointTests(unittest.TestCase):
    def test_only_untouched_sites_can_change_in_a_replacement(self):
        previous = study()
        candidate = copy.deepcopy(previous)
        candidate["reference_output"] = "reference-two.log"
        candidate["files"][0]["mutants"][2].update(outcome="survived", evidence=["three.log"])
        self.assertIsNone(checkpoint.replacement_error(previous, candidate))
        self.assertEqual("not_run", previous["files"][0]["mutants"][2]["outcome"])

    def test_incomplete_foreign_or_rewritten_measurements_cannot_replace_prior_facts(self):
        previous = study()
        edits = {
            "missing": lambda c: c.clear(),
            "uncollected": lambda c: c.update(collected=False, note="reference failed"),
            "inventory": lambda c: c.update(files=[]),
            "malformed_inventory": lambda c: c.update(files=None),
            "tests": lambda c: c.update(selected_tests=["another test"]),
            "policy": lambda c: c.update(execution_policy="another policy"),
            "reference": lambda c: c.pop("reference_output"),
            "source": lambda c: c["files"][0].update(sha256="2" * 64),
            "site": lambda c: c["files"][0]["mutants"][2].update(site_id="another site"),
            "unbound_site": lambda c: c["files"][0]["mutants"][2].update(site_id=None),
            "duplicate": lambda c: c["files"][0]["mutants"].append(copy.deepcopy(c["files"][0]["mutants"][0])),
            "kill": lambda c: c["files"][0]["mutants"][0].update(evidence=["rerun.log"]),
            "timeout": lambda c: c["files"][0]["mutants"][1].update(outcome="killed"),
            "malformed_file": lambda c: c["files"].append(None),
            "malformed_site": lambda c: c["files"][0]["mutants"].append(None),
        }
        for kind, edit in edits.items():
            with self.subTest(kind=kind):
                candidate = copy.deepcopy(previous)
                edit(candidate)
                self.assertTrue(checkpoint.replacement_error(previous, candidate))
        for candidate in (None, [], "invalid", {"collected": True, "files": None}):
            with self.subTest(candidate=candidate):
                self.assertTrue(checkpoint.replacement_error(previous, candidate))
        self.assertIsInstance(checkpoint.replacement_error(previous, {"collected": False, "note": 12}), str)
        for previous in (None, []):
            self.assertTrue(checkpoint.replacement_error(previous, study()))

    def test_retention_preserves_receipts_and_records_a_limited_reference_timeout(self):
        previous = {
            "mutation_check": study(),
            "changed_code_receipts": [{"output": "one.log", "timed_out": False}],
            "changed_code_inputs": {"inventory": [{"path": "packages/value.py"}]},
        }
        before = copy.deepcopy(previous)
        attempted = {
            "changed_code_receipts": [
                *previous["changed_code_receipts"],
                {"output": "ref-failed.log", "timed_out": True},
            ]
        }
        report = {"collected": False, "reference_attempt": {"timed_out": True, "budget_limited": True}}
        retained, receipts, inputs, failure = checkpoint.retain(
            previous, attempted, report, "unavailable", deadline_expired=True
        )
        self.assertEqual(previous["mutation_check"], retained)
        self.assertEqual(attempted["changed_code_receipts"], receipts)
        self.assertEqual(["one.log", "ref-failed.log"], inputs["command_outputs"])
        self.assertEqual(failure, inputs[checkpoint.FAILURE])
        self.assertEqual("window_budget_exhausted", failure["kind"])
        self.assertEqual(["ref-failed.log"], failure["command_outputs"])
        retained["files"].clear()
        receipts[0]["timed_out"] = True
        inputs["inventory"].clear()
        self.assertEqual(before, previous)
        _, missing_prefix, _, _ = checkpoint.retain(
            previous,
            {"changed_code_receipts": [{"output": "ref-only.log"}]},
            report,
            "unavailable",
            deadline_expired=True,
        )
        self.assertEqual(previous["changed_code_receipts"], missing_prefix[:1])

    def test_only_an_exhausted_truncated_reference_is_a_budget_deferral(self):
        previous = {"mutation_check": study()}
        for expired, timed_out, limited in ((False, True, True), (True, False, True), (True, True, False)):
            with self.subTest(expired=expired, timed_out=timed_out, limited=limited):
                report = {"reference_attempt": {"timed_out": timed_out, "budget_limited": limited}}
                *_, failure = checkpoint.retain(previous, {}, report, "reference unavailable", deadline_expired=expired)
                self.assertEqual("reference_unavailable", failure["kind"])

        *_, failure = checkpoint.retain(
            previous,
            {},
            {"reference_attempt": {"not_started": True, "budget_limited": True}},
            "preparation exhausted the window",
            deadline_expired=True,
        )
        self.assertEqual("window_budget_exhausted", failure["kind"])

    def test_conflicting_or_malformed_receipts_never_replace_the_owned_prefix(self):
        previous = {"mutation_check": study(), "changed_code_receipts": [{"output": "one.log", "tag": "original"}]}
        for receipt in ({"output": "one.log", "tag": "changed"}, None, {"output": None}):
            with self.subTest(receipt=receipt):
                attempted = {"changed_code_receipts": [receipt]}
                report = {"reference_attempt": {"timed_out": True, "budget_limited": True}}
                _, retained, _, failure = checkpoint.retain(
                    previous, attempted, report, "bad refresh", deadline_expired=True
                )
                self.assertEqual(previous["changed_code_receipts"], retained)
                self.assertEqual([receipt], failure["receipt_conflicts"])
                self.assertEqual("reference_unavailable", failure["kind"])
