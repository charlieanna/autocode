"""A novelty hold states the run's active time against its time limit and the current milestone's time against
its budget, so a person deciding on the one scoped retry sees what is left of each (#611)."""

import unittest

import autocode_milestone_scope as milestone_scope
import autocode_time_left as time_left


def run(**extra):
    """A run on an approved contract, its Builder on milestone M1, with 1,250 s of active time and M1 at 300 s."""
    state = {
        "active_seconds": 1250,
        "settings": {
            "limits": {"max_seconds": 43200},
            "milestone_checkpoints": {"enabled": True, "max_seconds": 5400},
        },
        "goal_contract": {"hash": "h", "body": {"milestones": [{"id": "M1"}]}},
        "current_task": {"id": "T1", "milestone_id": "M1", "contract_hash": "h"},
        "milestone_progress": {"h:M1": {"id": "M1", "seconds": 300}},
    }
    state.update(extra)
    return state


class RunTimeTests(unittest.TestCase):
    def test_the_active_time_is_stated_against_the_limit_the_stage_boundary_enforces(self):
        self.assertEqual(["Active time: 1,250 s of the 43,200 s limit."], time_left.lines(run(milestone_progress={})))

    def test_no_limit_states_no_active_time(self):
        for limits in ({"max_seconds": 0}, {"max_seconds": None}, {}):
            with self.subTest(limits=limits):
                state = run(milestone_progress={})
                state["settings"]["limits"] = limits
                self.assertEqual([], time_left.lines(state))

    def test_a_run_without_counted_time_has_used_none_and_a_fraction_keeps_one_place(self):
        state = run(milestone_progress={})
        del state["active_seconds"]
        self.assertEqual(["Active time: 0 s of the 43,200 s limit."], time_left.lines(state))
        state["active_seconds"] = 12.34
        self.assertEqual(["Active time: 12.3 s of the 43,200 s limit."], time_left.lines(state))


class MilestoneTimeTests(unittest.TestCase):
    def test_the_current_milestone_is_stated_against_its_budget(self):
        self.assertEqual(
            ["Active time: 1,250 s of the 43,200 s limit.", "Milestone M1: 300 s of its 5,400 s budget."],
            time_left.lines(run()),
        )

    def test_a_batch_names_its_milestones(self):
        state = run(
            current_task={"id": "T1", "milestone_ids": ["M2", "M1"], "contract_hash": "h"},
            milestone_progress={},
        )
        state["milestone_progress"][milestone_scope.key(state)] = {"milestone_ids": ["M2", "M1"], "seconds": 60}
        self.assertEqual("Milestones M1, M2: 60 s of their 5,400 s budget.", time_left.lines(state)[-1])

    def test_no_milestone_line_when_its_budget_does_not_govern_the_task(self):
        cases = {
            "checkpoints off": lambda s: s["settings"]["milestone_checkpoints"].update(enabled=False),
            "no budget": lambda s: s["settings"]["milestone_checkpoints"].update(max_seconds=0),
            "budget not saved": lambda s: s["settings"]["milestone_checkpoints"].pop("max_seconds"),
            "slice budget governs": lambda s: s.update(progressive={"budget": {"pools": {}}}),
            "task off the contract": lambda s: s["current_task"].update(contract_hash="old"),
            "no task": lambda s: s.update(current_task={}),
            "no row yet": lambda s: s.update(milestone_progress={}),
        }
        for name, change in cases.items():
            with self.subTest(name):
                state = run()
                change(state)
                self.assertEqual(["Active time: 1,250 s of the 43,200 s limit."], time_left.lines(state))

    def test_stating_never_changes_the_run(self):
        state = run(milestone_progress={})
        before = repr(state)
        time_left.lines(state)
        self.assertEqual(before, repr(state))


if __name__ == "__main__":
    unittest.main()
