"""Source-bound changed-code policy; no provider, filesystem proof or language claims."""

import copy
import unittest

import autocode_changed_code_evidence as evidence

SOURCE = "candidate-source"
CONTEXT = "proof-context"
FILE_HASH = "a" * 64
INVENTORY = [{"path": "app.py", "sha256": FILE_HASH, "lines": [3, 4]}]


def envelope(files):
    return {"source_revision": SOURCE, "proof_context": CONTEXT, "files": files}


def coverage(lines=(3, 4)):
    return envelope([{"path": "app.py", "sha256": FILE_HASH, "executed_lines": list(lines)}])


def mutant(line, outcome="killed"):
    return {"line": line, "outcome": outcome, "evidence": [f"receipt:mutant-{line}"], "failed_tests": ["test_boundary"]}


def mutations(rows=None):
    return envelope(
        [{"path": "app.py", "sha256": FILE_HASH, "mutants": rows if rows is not None else [mutant(3), mutant(4)]}]
    )


def assess(cov=None, mut=None, *, inventory=INVENTORY, exceptions=(), source=SOURCE, context=CONTEXT):
    return evidence.assess(inventory, cov, mut, source_revision=source, proof_context=context, exceptions=exceptions)


def exception(obligation):
    return {
        **{key: obligation[key] for key in ("gap_id", "path", "sha256", "line")},
        "source_revision": SOURCE,
        "proof_context": CONTEXT,
        "accepted": True,
        "reason": "Independent review confirms this defensive branch cannot be reached through the public parser.",
        "evidence": ["review:current-parser-invariant"],
    }


class ChangedCodeEvidenceTests(unittest.TestCase):
    def test_parent_mutant_survivor_is_visible_but_parent_kill_gives_no_child_credit(self):
        for outcome in ("survived", "killed"):
            with self.subTest(outcome=outcome):
                parent = {**mutant(1, outcome), "end_line": 4, "operator": "none_assignment"}
                result = assess(coverage(), mutations([parent]))
                self.assertEqual("UNVERIFIED", result["status"])
                for row in result["obligations"]:
                    self.assertFalse(row["mutation_demonstrated"])
                    if outcome == "survived":
                        overlap = row.get("overlapping_mutations") or []
                        self.assertTrue(overlap)
                        self.assertEqual("survived", overlap[0]["outcome"])
                        self.assertEqual(1, overlap[0]["line"])

        parent = {**mutant(1, "survived"), "end_line": 4, "operator": "none_assignment"}
        direct = assess(coverage(), mutations([parent, mutant(3), mutant(4)]))
        self.assertEqual("PASS", direct["status"])
        self.assertTrue(all(row["mutation_demonstrated"] for row in direct["obligations"]))
        self.assertTrue(all(row.get("overlapping_mutations") for row in direct["obligations"]))

    def test_parent_overlap_context_is_bounded_and_discloses_omissions(self):
        parents = [{**mutant(1, "survived"), "end_line": 4, "operator": f"site-{n}"} for n in range(7)]
        result = assess(coverage(), mutations(parents))
        for row in result["obligations"]:
            self.assertEqual(3, len(row["overlapping_mutations"]))
            self.assertEqual(4, row["overlapping_mutations_omitted"])
            self.assertFalse(row["mutation_demonstrated"])

    def test_each_executable_line_needs_current_behavioral_evidence(self):
        result = assess(coverage(), mutations())
        self.assertEqual("PASS", result["status"])
        self.assertEqual(["tested", "tested"], [row["status"] for row in result["obligations"]])
        self.assertEqual([], result["fallback"])
        self.assertEqual([3, 4], [row["line"] for row in result["obligations"]])

    def test_execution_without_assertion_evidence_does_not_pass(self):
        result = assess(coverage(), mutations([mutant(3), mutant(4, "survived")]))
        self.assertEqual("UNVERIFIED", result["status"])
        self.assertTrue(result["obligations"][1]["executed"])
        self.assertIn("app.py:4", result["reason"])
        self.assertEqual("reason_required", result["obligations"][1]["status"])

    def test_the_reason_names_a_bounded_number_of_gaps_and_counts_the_rest(self):
        # #3164: the reason is copied into the Builder's next action and every review handoff; listing
        # 274,546 lines made it 16 MB. Every gap stays in obligations.
        lines = list(range(1, evidence.LISTED_LINES + 6))
        result = assess(inventory=[{"path": "app.py", "sha256": FILE_HASH, "lines": lines}])
        self.assertEqual(len(lines), sum(row["status"] == "reason_required" for row in result["obligations"]))
        self.assertIn(f"app.py:{evidence.LISTED_LINES},", result["reason"])
        self.assertNotIn(f"app.py:{evidence.LISTED_LINES + 1}", result["reason"])
        self.assertTrue(result["reason"].endswith(", and 5 more in obligations"), result["reason"])
        exact = assess(inventory=[{"path": "app.py", "sha256": FILE_HASH, "lines": lines[: evidence.LISTED_LINES]}])
        self.assertTrue(exact["reason"].endswith(f"app.py:{evidence.LISTED_LINES}"), exact["reason"])

    def test_missing_coverage_uses_only_demonstrated_line_specific_mutation_fallback(self):
        result = assess({"collected": False, "note": "no project coverage tool"}, mutations())
        self.assertEqual("PASS", result["status"])
        self.assertEqual(["mutation_only"], result["fallback"])
        self.assertTrue(all(not row["executed"] for row in result["obligations"]))
        partial = assess(None, mutations([mutant(3)]))
        self.assertEqual("UNVERIFIED", partial["status"])
        self.assertEqual("reason_required", partial["obligations"][1]["status"])

    def test_unsuccessful_or_unsampled_mutant_never_hides_behind_a_killed_one(self):
        for outcome in ("survived", "not_run", "timeout", "unsupported", "error", "unknown"):
            with self.subTest(outcome=outcome):
                result = assess(coverage(), mutations([mutant(3), mutant(4), mutant(4, outcome)]))
                self.assertEqual("UNVERIFIED", result["status"])
        for rows in ([], [mutant(3)]):
            with self.subTest(rows=rows):
                self.assertEqual("UNVERIFIED", assess(coverage(), mutations(rows))["status"])

    def test_killed_label_without_observed_failed_tests_and_receipts_is_insufficient(self):
        for key, value in (("failed_tests", []), ("failed_tests", [""]), ("evidence", []), ("evidence", "claim")):
            with self.subTest(key=key, value=value):
                row = {**mutant(4), key: value}
                self.assertEqual("UNVERIFIED", assess(coverage(), mutations([mutant(3), row]))["status"])

    def test_changed_source_context_or_file_bytes_invalidate_observations(self):
        for key, value in (("source_revision", "older-source"), ("proof_context", "older-context")):
            with self.subTest(key=key):
                cov, mut = coverage(), mutations()
                cov[key] = mut[key] = value
                self.assertEqual("UNVERIFIED", assess(cov, mut)["status"])
        cov, mut = coverage(), mutations()
        cov["files"][0]["sha256"] = mut["files"][0]["sha256"] = "b" * 64
        self.assertEqual("UNVERIFIED", assess(cov, mut)["status"])

    def test_exact_independently_checked_reason_can_resolve_the_named_line_only(self):
        initial = assess(coverage(), mutations([mutant(3)]))
        reason = exception(initial["obligations"][1])
        result = assess(coverage(), mutations([mutant(3)]), exceptions=[reason])
        self.assertEqual("PASS", result["status"])
        self.assertEqual("excepted", result["obligations"][1]["status"])
        self.assertEqual(reason["reason"], result["obligations"][1]["reason"])
        self.assertEqual(["reviewer_reason"], result["fallback"])
        self.assertEqual("UNVERIFIED", assess(exceptions=[reason])["status"])

    def test_prose_unaccepted_duplicate_stale_or_misbound_reasons_do_not_authorize(self):
        reason = exception(assess()["obligations"][1])
        changes = {
            "accepted": 1,
            "reason": " ",
            "evidence": [],
            "gap_id": "unknown",
            "source_revision": "old",
            "proof_context": "old",
            "path": "other.py",
            "sha256": "b" * 64,
            "line": 3,
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                result = assess(coverage(), mutations([mutant(3)]), exceptions=[{**reason, key: value}])
                self.assertEqual("UNVERIFIED", result["status"])
        for reasons in ([reason, reason], ["Reviewer said okay"], {"approved": True}):
            with self.subTest(reasons=reasons):
                self.assertEqual("UNVERIFIED", assess(coverage(), mutations([mutant(3)]), exceptions=reasons)["status"])

    def test_authenticated_reason_provenance_is_retained_without_changing_authority(self):
        reason = exception(assess()["obligations"][1])
        reason.update(
            reason_kind="defensive_unreachable",
            reviewer={"stage": "tester", "role": "tester", "output": "owned/tester.json"},
        )
        result = assess(coverage(), mutations([mutant(3)]), exceptions=[reason])
        accepted = result["obligations"][1]["exception"]
        self.assertEqual("PASS", result["status"])
        self.assertEqual(reason["reason_kind"], accepted["reason_kind"])
        self.assertEqual(reason["reviewer"], accepted["reviewer"])
        reason["reviewer"]["output"] = "changed"
        self.assertEqual("owned/tester.json", accepted["reviewer"]["output"])
        reason["accepted"] = False
        self.assertEqual("UNVERIFIED", assess(coverage(), mutations([mutant(3)]), exceptions=[reason])["status"])

    def test_boolean_line_is_not_an_exact_independent_review_binding(self):
        inventory = [{"path": "app.py", "sha256": FILE_HASH, "lines": [1]}]
        row = exception(assess(inventory=inventory)["obligations"][0])
        row["line"] = True
        self.assertEqual("UNVERIFIED", assess(inventory=inventory, exceptions=[row])["status"])

    def test_unknown_language_and_missing_inventory_cannot_quietly_pass(self):
        inventory = [{"path": "src/app.rs", "sha256": FILE_HASH, "lines": [9]}]
        result = assess(inventory=inventory)
        self.assertEqual("UNVERIFIED", result["status"])
        self.assertEqual("reviewer_reason_required", result["obligations"][0]["fallback"])
        self.assertIn("src/app.rs:9", result["reason"])
        for value in (None, {}, [{"path": "app.py", "lines": []}], INVENTORY * 2):
            with self.subTest(value=value):
                self.assertEqual("UNVERIFIED", assess(inventory=value)["status"])

    def test_classified_nonexecutable_files_have_no_line_obligations_but_still_need_hashes(self):
        inventory = [{"path": "types.pyi", "sha256": FILE_HASH, "lines": []}]
        self.assertEqual("PASS", assess(inventory=inventory)["status"])
        self.assertEqual([], assess(inventory=inventory)["obligations"])
        self.assertEqual("PASS", assess(inventory=[])["status"])

    def test_duplicate_or_malformed_observation_files_fail_closed(self):
        for change in (lambda rows: rows.extend(copy.deepcopy(rows)), lambda rows: rows.append(None)):
            cov, mut = coverage(), mutations()
            change(cov["files"])
            change(mut["files"])
            self.assertEqual("UNVERIFIED", assess(cov, mut)["status"])
        mut = mutations([mutant(3), mutant(4), {"line": True, "outcome": "killed"}])
        self.assertEqual("UNVERIFIED", assess(coverage(), mut)["status"])

    def test_gap_identity_and_output_are_deterministic_and_inputs_are_unchanged(self):
        inventory = [{**INVENTORY[0], "lines": [4, 3]}]
        cov, mut = coverage(), mutations()
        before = copy.deepcopy((inventory, cov, mut))
        expected = assess(cov, mut)
        self.assertEqual(expected, assess(cov, mut, inventory=inventory))
        self.assertEqual(before, (inventory, cov, mut))
        gap = expected["obligations"][0]["gap_id"]
        self.assertNotEqual(gap, assess(source="next-source")["obligations"][0]["gap_id"])
        self.assertNotEqual(gap, assess(context="next-context")["obligations"][0]["gap_id"])
        changed = [{**INVENTORY[0], "sha256": "b" * 64}]
        self.assertNotEqual(gap, assess(inventory=changed)["obligations"][0]["gap_id"])


class NoSiteSettlementTests(unittest.TestCase):
    """The runner settles a line the mutator gave no site; the Tester keeps an explicit veto (#4218)."""

    def settle(self, mut, *, exceptions=(), cov=None, inventory=INVENTORY):
        return evidence.assess(
            inventory,
            coverage() if cov is None else cov,
            mut,
            source_revision=SOURCE,
            proof_context=CONTEXT,
            exceptions=exceptions,
            settle_no_site=True,
        )

    def test_a_processed_line_with_no_mutation_site_settles_without_a_model_decision(self):
        # app.py:3 is killed; app.py:4 has no mutant starting on it (an import, a decorator, __future__).
        result = self.settle(mutations([mutant(3)]))
        self.assertEqual("PASS", result["status"])
        no_site = result["obligations"][1]
        self.assertEqual("excepted", no_site["status"])
        self.assertEqual([], no_site["mutation_outcomes"])
        self.assertEqual(evidence.RUNNER_NO_SITE, no_site["fallback"])
        self.assertEqual(evidence.RUNNER_NO_SITE, no_site["exception"]["reason_kind"])
        self.assertEqual(evidence.NO_SITE_REASON, no_site["reason"])
        self.assertEqual([evidence.NO_SITE_EVIDENCE], no_site["exception"]["evidence"])
        self.assertIn(evidence.RUNNER_NO_SITE, result["fallback"])
        # The exception is bound to the exact gap, file bytes and line.
        self.assertEqual(no_site["gap_id"], evidence.gap_id(SOURCE, CONTEXT, "app.py", FILE_HASH, 4))

    def test_a_whole_file_the_mutator_gave_no_sites_settles_its_lines(self):
        result = self.settle(mutations([]))
        self.assertEqual("PASS", result["status"])
        self.assertEqual({"excepted"}, {row["status"] for row in result["obligations"]})
        self.assertEqual([evidence.RUNNER_NO_SITE], result["fallback"])

    def test_not_run_budget_and_survived_lines_are_never_auto_settled(self):
        for outcome in ("not_run", "survived", "timeout", "unsupported", "error", "unknown"):
            with self.subTest(outcome=outcome):
                result = self.settle(mutations([mutant(3), mutant(4, outcome)]))
                self.assertEqual("UNVERIFIED", result["status"])
                self.assertEqual("reason_required", result["obligations"][1]["status"])
                self.assertTrue(result["obligations"][1]["mutation_outcomes"])

    def test_an_unprocessed_empty_or_malformed_mutation_file_is_not_settled(self):
        malformed = mutations([{"line": True, "outcome": "killed"}])
        for mut in (None, envelope([]), malformed):
            with self.subTest(mut=mut):
                result = self.settle(mut)
                self.assertEqual("UNVERIFIED", result["status"])
                self.assertEqual("reason_required", result["obligations"][0]["status"])
        # Bytes other than the inventoried file are not a processed observation.
        other = mutations([mutant(3)])
        other["files"][0]["sha256"] = "b" * 64
        self.assertEqual("UNVERIFIED", self.settle(other)["status"])

    def test_a_tester_reject_vetoes_the_machine_reason_and_keeps_the_line_open(self):
        no_site = self.settle(mutations([mutant(3)]))["obligations"][1]
        reject = {**exception(no_site), "accepted": False}
        result = self.settle(mutations([mutant(3)]), exceptions=[reject])
        self.assertEqual("UNVERIFIED", result["status"])
        self.assertEqual("reason_required", result["obligations"][1]["status"])
        self.assertEqual("reviewer_reason_required", result["obligations"][1]["fallback"])

    def test_a_tester_accept_takes_precedence_over_the_machine_reason(self):
        no_site = self.settle(mutations([mutant(3)]))["obligations"][1]
        accept = exception(no_site)
        result = self.settle(mutations([mutant(3)]), exceptions=[accept])
        self.assertEqual("PASS", result["status"])
        self.assertEqual("reviewer_reason", result["obligations"][1]["fallback"])
        self.assertEqual(accept["reason"], result["obligations"][1]["reason"])

    def test_without_the_runner_admission_a_no_site_line_still_needs_a_reason(self):
        # The pure policy default is unchanged: only the runner (collect/reviewed) admits no-site reasons.
        result = evidence.assess(
            INVENTORY, coverage(), mutations([mutant(3)]), source_revision=SOURCE, proof_context=CONTEXT
        )
        self.assertEqual("UNVERIFIED", result["status"])
        self.assertEqual("reason_required", result["obligations"][1]["status"])


if __name__ == "__main__":
    unittest.main()
