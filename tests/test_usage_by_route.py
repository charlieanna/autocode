"""`usage.by_role` groups each attempt by the route that ran it (#3903), with reconciling totals."""

import unittest

import autocode_usage as usage


def attempt(name, cost, role=None, route_role=None, identity_role=None):
    record = {
        "stage": name,
        "command": ["x", "--model", "claude-haiku"],
        "exit_code": 0,
        "metrics": {
            "provider_tokens": {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "output_tokens": 10,
                "reasoning_output_tokens": 0,
            },
            "provider_cost_usd": cost,
        },
    }
    if role is not None:
        record["role"] = role
    if route_role is not None:
        record["route_role"] = route_role
    if identity_role is not None:
        record["execution_identity"] = {"role": identity_role}
    return record


def reported_total(by_role):
    return round(sum(entry["reported_usd"] for entry in by_role.values()), 6)


class UsageByRouteTests(unittest.TestCase):
    def test_ac1_by_role_groups_reviewer_attempts_by_recorded_route(self):
        # On a live run every reviewer attempt kept the legacy role task_planner while its route
        # and execution identity named the route that really ran it.
        def reviewer(name, route, cost):
            return attempt(name, cost, role="task_planner", route_role=route, identity_role=route)

        state = {
            "stages": [
                reviewer("review_plan", "plan_reviewer", 0.10),
                reviewer("review_plan", "plan_reviewer", 0.20),
                reviewer("review_completion", "completion", 0.30),
                reviewer("review_completion", "completion", 0.40),
            ]
        }
        totals = usage.summary(state)
        by_role = totals["by_role"]
        self.assertEqual({"plan_reviewer", "completion"}, set(by_role))
        self.assertNotIn("task_planner", by_role)
        self.assertEqual((2, 0.3), (by_role["plan_reviewer"]["stages"], by_role["plan_reviewer"]["reported_usd"]))
        self.assertEqual((2, 0.7), (by_role["completion"]["stages"], by_role["completion"]["reported_usd"]))
        self.assertEqual(4, totals["stages"])
        self.assertEqual(1.0, totals["cost_usd"]["reported"])
        self.assertEqual(totals["cost_usd"]["reported"], reported_total(by_role))

    def test_ac2_by_role_falls_back_to_identity_then_job_named_legacy_role(self):
        state = {
            "stages": [
                # No route_role: the execution identity names the route that ran.
                attempt("review_plan", 0.05, role="task_planner", identity_role="plan_reviewer"),
                # Only an old code name: it is shown by its job name.
                attempt("plan_task", 0.07, role="astra"),
                attempt("builder", 0.11, role="terra"),
            ]
        }
        totals = usage.summary(state)
        by_role = totals["by_role"]
        self.assertEqual({"plan_reviewer", "task_planner", "builder"}, set(by_role))
        self.assertNotIn("astra", by_role)
        self.assertNotIn("terra", by_role)
        self.assertEqual(0.05, by_role["plan_reviewer"]["reported_usd"])
        self.assertEqual(0.07, by_role["task_planner"]["reported_usd"])
        self.assertEqual(0.11, by_role["builder"]["reported_usd"])
        self.assertEqual(0.23, totals["cost_usd"]["reported"])
        self.assertEqual(totals["cost_usd"]["reported"], reported_total(by_role))

    def test_by_role_keeps_raw_accounting_role_fields(self):
        # The grouping changes; the raw fields each accounting attempt row reports stay as recorded.
        state = {"stages": [attempt("review_plan", 0.1, role="task_planner", route_role="plan_reviewer")]}
        rows = usage.summary(state)["accounting"]["attempts"]
        self.assertEqual([("task_planner", "plan_reviewer")], [(row["role"], row["route_role"]) for row in rows])

    def test_an_empty_or_non_text_route_falls_through_to_the_next_answer(self):
        state = {
            "stages": [
                attempt("review_completion", 0.1, role="task_planner", route_role="", identity_role="completion"),
                attempt("review_plan", 0.2, role="glm", route_role=7, identity_role=""),
            ]
        }
        self.assertEqual({"completion", "planner"}, set(usage.summary(state)["by_role"]))

    def test_an_attempt_with_no_role_at_all_is_grouped_as_other(self):
        state = {"stages": [attempt("builder", 0.2)]}
        self.assertEqual({"other"}, set(usage.summary(state)["by_role"]))


if __name__ == "__main__":
    unittest.main()
