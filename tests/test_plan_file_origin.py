"""Which plan file a run's plan was loaded from (tools/autocode_plan_file_origin.py, #720).

What the approval stop says about it is in test_goal_file; what the status view and the evidence report cite,
and how, is in test_evidence_report.
"""

from __future__ import annotations

import copy
import unittest

import autocode_plan_file as plan_file
import autocode_plan_file_origin as origin
from goal_fixtures import body as fixture_body

REVIEW = {"stage": "astra_challenge", "events": "/run/a/plan-challenge-01.jsonl", "events_sha256": "1" * 64}


def loaded_run(current: dict | None = None) -> dict:
    """A run whose revision 3 was loaded from /p.md; ``current`` is revision 4's body, else 3 is current."""
    body = fixture_body()
    body["brief_acceptance"] = {"manifest": {}, "review": REVIEW, "amendments": []}
    loaded = {"revision": 3, "body": body, "approval_status": "draft"}
    loaded[origin.FIELD] = {"path": "/p.md", "goal_hash": plan_file.goal_hash(body)}
    history = [{"revision": 2, "body": fixture_body(), "approval_status": "draft"}]
    if current is None:
        return {"contract_history": history, "goal_contract": loaded}
    return {
        "contract_history": [*history, loaded],
        "goal_contract": {"revision": 4, "body": current, "approval_status": "draft"},
    }


class CitationTests(unittest.TestCase):
    def test_the_loaded_revision_is_cited_by_the_files_path_and_goal_hash(self):
        run = loaded_run()
        goal = plan_file.goal_hash(run["goal_contract"]["body"])
        self.assertEqual({"path": "/p.md", "goal_hash": goal, "revision": 3, "changed": []}, origin.citation(run))

    def test_a_later_revision_names_what_differs_apart_from_the_plan_reviewers_evidence(self):
        changed = copy.deepcopy(loaded_run()["goal_contract"]["body"])
        changed["brief_acceptance"]["review"] = dict(REVIEW, events="/run/a/plan-challenge-02.jsonl")
        self.assertEqual([], origin.citation(loaded_run(changed))["changed"])
        changed["constraints"].append("Keep greet.py short")
        cited = origin.citation(loaded_run(changed))
        self.assertEqual((3, ["constraints"]), (cited["revision"], cited["changed"]))

    def test_an_approval_after_the_load_ends_the_citation(self):
        run = loaded_run(copy.deepcopy(loaded_run()["goal_contract"]["body"]))
        self.assertIsNotNone(origin.citation(run))
        run["contract_history"][-1]["approval_status"] = "approved"
        self.assertIsNone(origin.citation(run))

    def test_a_plan_not_loaded_from_a_file_and_a_malformed_record_are_not_cited(self):
        self.assertIsNone(origin.citation({"goal_contract": {"revision": 1, "body": fixture_body()}}))
        damaged = {
            "empty path": lambda run: run["goal_contract"][origin.FIELD].update(path=""),
            "uppercase goal hash": lambda run: run["goal_contract"][origin.FIELD].update(goal_hash="A" * 64),
            "revision that is not a number": lambda run: run["goal_contract"].update(revision=True),
            "plan that is not a mapping": lambda run: run["goal_contract"].update(body=["not", "a", "plan"]),
            "history that is not a list": lambda run: run.update(contract_history=5),
            "approval whose token is not text": lambda run: run.update(
                user_events=[{"kind": "goal_approval", "token": ["r3"]}]
            ),
        }
        for name, damage in damaged.items():
            with self.subTest(name):
                run = loaded_run()
                damage(run)
                self.assertIsNone(origin.citation(run))


if __name__ == "__main__":
    unittest.main()
