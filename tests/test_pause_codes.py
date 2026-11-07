"""Every PAUSED_* status in tools/ is in the one pause-code table, in one of four categories (#2755)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import autocode_pause_authority as authority
import autocode_pause_category as view_category
import autocode_pause_codes as codes
import autocode_provider_refusal as provider_refusal
import autocode_run_status as run_status

from tests.test_stop_explanations import named_pause_states


def raised_pause_codes(root: Path | None = None) -> set[str]:
    named = named_pause_states(root) if root is not None else named_pause_states()
    return {status for status in named if status.startswith("PAUSED_")}


class PauseCodeTableTests(unittest.TestCase):
    def test_every_pause_code_in_tools_is_in_the_table_and_nothing_else_is(self):
        raised = raised_pause_codes()
        self.assertEqual(set(), raised - set(codes.CODES), "PAUSED_* codes missing from autocode_pause_codes")
        self.assertEqual(set(), set(codes.CODES) - raised, "codes in the table that tools/ no longer names")

    def test_each_code_has_exactly_one_of_the_four_categories(self):
        self.assertEqual(("person", "machinery", "budget", "environment"), codes.CATEGORIES)
        self.assertEqual(set(codes.CATEGORIES), set(codes._GROUPS))
        self.assertEqual(sum(len(group) for group in codes._GROUPS.values()), len(codes.CODES))
        for code, category in codes.CODES.items():
            with self.subTest(code=code):
                self.assertIn(category, codes.CATEGORIES)

    def test_the_completeness_check_notices_a_new_pause_code(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "new_stage.py").write_text('raise util.Paused("PAUSED_SOMETHING_NEW", "why")\n')
            self.assertEqual({"PAUSED_SOMETHING_NEW"}, raised_pause_codes(Path(root)) - set(codes.CODES))

    def test_the_table_agrees_with_the_bounds_and_statuses_named_elsewhere(self):
        # The run's own time, cost and iteration bounds are the owner's budget, not AutoCode's machinery.
        for status, bound in authority.PAUSE_BUDGET_KIND.items():
            if bound in ("max_seconds", "max_cost_usd", "iteration_ceiling", "milestone_max_seconds"):
                with self.subTest(status=status):
                    self.assertEqual("budget", codes.CODES[status])
        self.assertEqual("environment", codes.CODES[provider_refusal.STATUS])
        self.assertEqual("machinery", codes.CODES[run_status.PAUSE_PHASE])
        self.assertEqual("person", codes.CODES[authority.INTERVENTION_STATUS])

    def test_a_person_category_is_a_decision_in_the_view_except_the_owners_model_routes(self):
        # Routes are the owner's choice here, while the view still shows them as environment causes.
        routes = {"PAUSED_BILLING_ROUTE", "PAUSED_CROSS_MODEL", "PAUSED_PLANNING_ROUTE"}
        for code, category in codes.CODES.items():
            if category == "person" and code not in routes:
                with self.subTest(code=code):
                    self.assertEqual("your_decision", view_category.category(code))
        for code in routes:
            self.assertEqual("person", codes.CODES[code])
            self.assertEqual("your_environment", view_category.category(code))


if __name__ == "__main__":
    unittest.main()
