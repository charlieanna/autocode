"""Validation-only rounds that change nothing stop before another Validator (issue #300)."""

import unittest

import autocode_validation_rounds as rounds


class ValidationRounds(unittest.TestCase):
    def state(self, task="task-1", output="validator-1.json", passed=("C1",)):
        return {
            "goal_contract": {"hash": "contract"},
            "current_task": {"id": task, "kind": "validate"},
            "validation": {
                "contract_hash": "contract",
                "source_revision": "rev",
                "output": output,
                "criterion_results": [{"id": cid, "status": "PASS"} for cid in passed],
            },
            "stages": [],
        }

    def blocking(self, *ids, **extra):
        return [{"id": ident, "source": "tester", "status": "open", "blocking": True, **extra} for ident in ids]

    def rounds(self, state, *attempts, blocking=None):
        blocking = blocking or self.blocking("F-1", not_rechecked_in="validator.json")
        results = []
        for task, output in attempts:
            state["current_task"]["id"], state["validation"]["output"] = task, output
            results.append(rounds.admit(state, blocking, "rev"))
        return results

    def test_third_unchanged_round_stops_and_names_each_finding_and_why_it_is_open(self):
        state = self.state()
        state["stages"] = [
            {
                "stage": "tester",
                "task_id": "task-2",
                "rejected": True,
                "rejection_reason": "No independently executed Validator tool event matches receipt",
            }
        ]
        blocking = self.blocking("F-1", not_rechecked_in="validator.json") + [
            {
                "id": "F-2",
                "source": "task_planner",
                "status": "open",
                "blocking": True,
                "pending_resolution": {
                    "reason": "Finding scope lacks fully passing verification",
                    "unverified_criteria": ["C2"],
                },
            }
        ]
        first, second, third = self.rounds(
            state, ("task-1", "v0"), ("task-2", "v1"), ("task-3", "v2"), blocking=blocking
        )
        self.assertEqual((None, None), (first, second))
        self.assertIn("F-1, F-2", third["reason"])
        why = third["reason"]
        self.assertIn("F-1 (Tester): the Tester's latest accepted report did not recheck it", why)
        self.assertIn(
            "F-2 (Completion Reviewer): its resolution was not accepted "
            "(Finding scope lacks fully passing verification; unverified C2)",
            why,
        )
        self.assertIn("No independently executed Validator tool event matches receipt", why)
        self.assertNotIn("request", third)
        self.assertEqual(["F-1", "F-2"], third["evidence"]["finding_ids"])
        self.assertEqual("rev", third["evidence"]["source_revision"])
        self.assertEqual("contract", third["evidence"]["contract_hash"])
        self.assertEqual(2, third["evidence"]["rounds_without_progress"])
        self.assertEqual(2, len(third["evidence"]["rounds"]))
        self.assertEqual("v2", third["evidence"]["validation_output"])
        self.assertEqual("task-3", third["evidence"]["task_id"])
        self.assertEqual(state[rounds.KEY], third["evidence"]["rounds"])
        state[rounds.KEY][0]["frontier"]["findings"]["F-1"][0] = "later state"
        self.assertEqual("not_rechecked", third["evidence"]["rounds"][0]["frontier"]["findings"]["F-1"][0])
        for machinery in ("--close-finding", "--feedback", "What must change"):
            self.assertNotIn(machinery, str(third))

    def test_another_attempt_at_an_admitted_round_is_not_a_new_round(self):
        state = self.state()
        self.assertEqual(
            [None] * 4, self.rounds(state, ("task-1", "v0"), ("task-1", "v0"), ("task-2", "v1"), ("task-2", "v1"))
        )
        self.assertIsNotNone(self.rounds(state, ("task-3", "v2"))[0])

    def test_a_round_that_changed_findings_passes_or_acceptance_restarts_the_count(self):
        changes = {
            "closed finding": lambda state: {"blocking": self.blocking("F-2")},
            "new passing criterion": lambda state: state["validation"]["criterion_results"].append(
                {"id": "C2", "status": "PASS"}
            ),
            "accepted milestone": lambda state: state.update(
                milestone_progress={"contract:M1": {"accepted": True, "contract_hash": "contract"}}
            ),
        }
        for name, change in changes.items():
            with self.subTest(name):
                state = self.state()
                self.assertEqual([None, None], self.rounds(state, ("task-1", "v0"), ("task-2", "v1")))
                state["current_task"]["id"], state["validation"]["output"] = "task-3", "v2"
                override = change(state) or {}
                blocking = override.get("blocking", self.blocking("F-1"))
                self.assertIsNone(rounds.admit(state, blocking, "rev"))
                state["current_task"]["id"], state["validation"]["output"] = "task-4", "v3"
                self.assertIsNone(rounds.admit(state, blocking, "rev"))
                state["current_task"]["id"], state["validation"]["output"] = "task-5", "v4"
                self.assertIsNotNone(rounds.admit(state, blocking, "rev"))

    def round(self, state, number, blocking, passed=None):
        state["current_task"]["id"], state["validation"]["output"] = f"task-{number}", f"v{number}"
        if passed is not None:
            state["validation"]["criterion_results"] = [{"id": cid, "status": "PASS"} for cid in passed]
        return rounds.admit(state, blocking, "rev")

    def test_passes_that_come_and_go_do_not_keep_a_finding_going(self):
        # #300 review: any change of the passing set restarted the count, so alternating results never stopped.
        for name, sequence, stops_at in (
            ("alternating", [("C1",), ("C2",), ("C1",), ("C2",), ("C1",)], 3),
            ("a criterion lost", [("C1", "C2"), ("C1",), ("C1",)], 2),
        ):
            with self.subTest(name):
                state, blocking = self.state(), self.blocking("F-1")
                results = [self.round(state, number, blocking, passed) for number, passed in enumerate(sequence)]
                self.assertEqual([None] * stops_at, results[:stops_at])
                self.assertIsNotNone(results[stops_at])
                self.assertIn("F-1", results[stops_at]["reason"])

    def test_findings_opened_meanwhile_do_not_restart_another_findings_count(self):
        # #300 review: the count was keyed on the whole open set, so a new finding each round reset it.
        state = self.state()
        results = [
            self.round(state, number, self.blocking(*[f"F-{n}" for n in range(1, number + 2)])) for number in range(3)
        ]
        self.assertEqual([None, None], results[:2])
        # F-1 went two rounds without progress; F-2 and F-3 have not, so they are not named.
        self.assertIn("finding(s) F-1;", results[2]["reason"])
        self.assertNotIn("F-2", results[2]["reason"])

    def test_a_finding_that_only_switches_between_states_it_had_still_stops(self):
        not_rechecked = self.blocking("F-1", not_rechecked_in="validator.json")
        pending = self.blocking(
            "F-1",
            pending_resolution={
                "reason": "Finding scope lacks fully passing verification",
                "unverified_criteria": ["C2"],
            },
        )
        state = self.state()
        results = [
            self.round(state, number, rows)
            for number, rows in enumerate([not_rechecked, pending, not_rechecked, pending])
        ]
        self.assertEqual([None, None, None], results[:3])  # pending was new once
        self.assertIsNotNone(results[3])

    def test_fewer_unverified_criteria_on_a_pending_resolution_is_progress(self):
        def pending(*unverified):
            return self.blocking(
                "F-1",
                pending_resolution={
                    "reason": "Finding scope lacks fully passing verification",
                    "unverified_criteria": list(unverified),
                },
            )

        state = self.state()
        results = [
            self.round(state, number, rows)
            for number, rows in enumerate(
                [pending("C1", "C2"), pending("C1", "C2"), pending("C2"), pending("C2"), pending("C1", "C2")]
            )
        ]
        self.assertEqual([None] * 4, results[:4])
        self.assertIsNotNone(results[4])

    def test_the_stop_names_its_findings_and_a_settled_duplicate_but_closes_nothing(self):
        state = self.state()
        state["findings_ledger"] = [
            {
                "id": "F-0",
                "source": "task_planner",
                "status": "resolved",
                "finding": "Regression proof has no fail-to-pass test",
                "evidence": "proof-01",
            }
        ]
        blocking = self.blocking(
            "F-1", not_rechecked_in="v.json", finding="Regression proof has no fail-to-pass test", evidence="proof-01"
        )
        stop = self.rounds(state, ("task-1", "v0"), ("task-2", "v1"), ("task-3", "v2"), blocking=blocking)[2]
        self.assertEqual(["F-1"], stop["evidence"]["finding_ids"])
        self.assertIn(
            "F-1 has the same finding or evidence as resolved F-0; that match does not settle F-1",
            stop["reason"],
        )
        self.assertNotIn("--close-finding", stop["reason"])
        self.assertEqual("open", blocking[0]["status"])

    def test_the_stop_says_a_resolved_part_of_the_same_split_finding_does_not_settle_it(self):
        # #447: F-1 is the part of F-0 an approved revision moved to another milestone; it is not a duplicate.
        state = self.state()
        state["findings_ledger"] = [
            {
                "id": "F-0",
                "source": "tester",
                "status": "resolved",
                "finding": "Stop leaves the worker running",
                "evidence": "event:check",
            }
        ]
        blocking = self.blocking(
            "F-1",
            not_rechecked_in="v.json",
            finding="Stop leaves the worker running",
            evidence="event:check",
            split_from="F-0",
        )
        stop = self.rounds(state, ("task-1", "v0"), ("task-2", "v1"), ("task-3", "v2"), blocking=blocking)[2]
        self.assertEqual(["F-1"], stop["evidence"]["finding_ids"])
        self.assertNotIn("as resolved F-0", stop["reason"])
        self.assertIn(
            "F-1 and F-0 are parts of finding F-0, split across milestones by an approved revision; "
            "resolving F-0 covered other criteria and does not settle F-1",
            stop["reason"],
        )

    def test_first_validation_of_new_source_or_without_blockers_is_not_validation_only(self):
        state = self.state()
        self.assertIsNone(rounds.admit(state, [], "rev"))
        self.assertIsNone(rounds.admit(state, self.blocking("F-1"), "new-rev"))
        state["validation"]["contract_hash"] = "old-contract"
        self.assertIsNone(rounds.admit(state, self.blocking("F-1"), "rev"))
        self.assertNotIn(rounds.KEY, state)


if __name__ == "__main__":
    unittest.main()
