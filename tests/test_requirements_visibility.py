"""Requirements approval exposes the complete constraints to clients."""
import unittest

import autocode_run_view as run_view


class RequirementsVisibilityTests(unittest.TestCase):
    def test_current_handoff_exposes_every_constraint_in_order(self):
        constraints = [f"Constraint {number}" for number in range(1, 10)]
        state = {"requirements_handoff": {"report": {"constraints": constraints}}}
        self.assertEqual(constraints, run_view.requirements_view(state)["constraints"])

    def test_older_handoff_without_constraints_is_readable(self):
        state = {"requirements_handoff": {"report": {"intended_outcome": "A task"}}}
        self.assertEqual([], run_view.requirements_view(state)["constraints"])


if __name__ == "__main__":
    unittest.main()
