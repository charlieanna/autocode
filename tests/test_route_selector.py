"""The automatic route selector (#1426 step 7): the deterministic choice, and its stops."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_route_selector as selector
import autocode_role_route as role_route

GLM = "zai-coding-plan/glm-5.3"
GLM_KILO = "kilo/z-ai/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
SOL = "openai/gpt-6-sol"
SOL_KILO = "kilo/openai/gpt-6-sol"
ASTRA = "openai/gpt-6-astra"
ASTRA_KILO = "kilo/openai/gpt-6-astra"
DEEPSEEK = "kilo/deepseek/deepseek-v4.1-flash"
ALL_ROLES = ("requirements", "glm", "terra", "plan_reviewer", "sol", "completion", "astra")


def candidate(model, *, tool="kilocode", account="Kilo Gateway", billing="pay per token", levels=None, probe=None):
    return {
        "model": model,
        "tool": tool,
        "account": account,
        "billing": billing,
        "levels": levels,
        "probe": probe,
    }


def coding_plans():
    """The two subscription logins the live glm53-mimo profile runs on (opencode)."""
    return [
        candidate(GLM, tool="opencode", account="Z.AI Coding Plan", billing="subscription"),
        candidate(MIMO, tool="opencode", account="Xiaomi Token Plan (Singapore)", billing="subscription"),
        candidate(SOL, tool="opencode", account="ChatGPT login", billing="subscription"),
        candidate(ASTRA, tool="opencode", account="ChatGPT login", billing="subscription"),
    ]


def gateway_only():
    """A Kilo login with only the Gateway connected: the first case selection must handle."""
    return [candidate(GLM_KILO), candidate(DEEPSEEK), candidate(SOL_KILO), candidate(ASTRA_KILO)]


class QualificationTableTests(unittest.TestCase):
    def test_the_shipped_table_parses_and_covers_every_pipeline_role(self):
        table = selector.table()
        self.assertEqual("2026-10-17", table["as_of"])
        for role in selector.PIPELINE_ROLES:
            self.assertTrue(selector.qualified(role), f"{role} has no qualified model")

    def test_every_entry_names_its_evidence_and_provenance(self):
        for role in selector.PIPELINE_ROLES:
            for entry in selector.qualified(role):
                self.assertIn(entry["provenance"], selector.PROVENANCE)
                self.assertTrue(entry["evidence"].strip())
                self.assertIn(entry["model"], entry["routes"])

    def test_bad_tables_are_refused_with_the_field_named(self):
        with self.assertRaisesRegex(ValueError, "as_of"):
            selector.parse({"roles": {}})
        with self.assertRaisesRegex(ValueError, "evidence"):
            selector.parse({"as_of": "x", "roles": {"terra": [{"model": GLM, "provenance": "qualified"}]}})
        with self.assertRaisesRegex(ValueError, "provenance"):
            selector.parse(
                {"as_of": "x", "roles": {"terra": [{"model": GLM, "evidence": "run", "provenance": "guess"}]}}
            )
        wrong_routes = {"model": GLM, "evidence": "run", "provenance": "qualified", "routes": [MIMO]}
        with self.assertRaisesRegex(ValueError, "including the model"):
            selector.parse({"as_of": "x", "roles": {"terra": [wrong_routes]}})

    def test_a_model_the_table_does_not_list_is_never_chosen(self):
        result = selector.select(ALL_ROLES, [candidate("kilo/unbiased/pareto-latest")])
        self.assertIsNone(result["routes"])
        self.assertIn("no qualified model is available", result["stop"])


class RankingTests(unittest.TestCase):
    def test_a_subscription_route_beats_a_pricier_pay_per_token_one(self):
        cheaper = dict(candidate(SOL_KILO, billing="pay per token"), model=SOL_KILO)
        entry = selector.qualified("plan_reviewer")[0]
        sub = candidate(SOL, billing="subscription")
        key_sub, key_ppp = selector._rank(sub, entry), selector._rank(cheaper, entry)
        self.assertLess(key_sub, key_ppp)

    def test_a_known_route_beats_an_unknown_one_at_the_same_price(self):
        entry = selector.qualified("plan_reviewer")[0]
        known = candidate(SOL_KILO, probe="known")
        unknown = candidate(SOL_KILO)
        self.assertLess(selector._rank(known, entry), selector._rank(unknown, entry))

    def test_the_price_order_is_the_input_plus_output_sum(self):
        entry = selector.qualified("terra")[0]
        glm = candidate(GLM_KILO)
        deepseek = candidate(DEEPSEEK)
        self.assertLess(selector._rank(deepseek, entry), selector._rank(glm, entry))


class SelectionTests(unittest.TestCase):
    def test_two_subscription_logins_reproduce_the_glm53_mimo_split(self):
        allowed = {"Z.AI Coding Plan", "Xiaomi Token Plan (Singapore)", "ChatGPT login"}
        result = selector.select(ALL_ROLES, coding_plans(), permissions=allowed)
        routes = result["routes"]
        self.assertIsNotNone(routes)
        # The same family split the live glm53-mimo profile uses: MiMo builds and
        # reviews the plan, GLM requires, plans, validates and completes.
        self.assertEqual(GLM, routes["requirements"]["model"])
        self.assertEqual(GLM, routes["glm"]["model"])
        self.assertEqual(MIMO, routes["terra"]["model"])
        self.assertEqual(MIMO, routes["plan_reviewer"]["model"])
        self.assertEqual(GLM, routes["sol"]["model"])
        self.assertEqual(GLM, routes["completion"]["model"])
        self.assertEqual(ASTRA, routes["astra"]["model"])
        self.assertEqual({role_route.AUTO_SELECTED}, {r["origin"] for r in routes.values()})

    def test_a_gateway_only_login_gets_the_owners_starting_table(self):
        result = selector.select(ALL_ROLES, gateway_only())
        routes = result["routes"]
        self.assertIsNotNone(routes)
        # DeepSeek V4.1 Flash builds, GLM 5.3 requires and plans, and the checkers
        # that GLM cannot take (Plan Reviewer) or that cost more than GLM stay on
        # GPT-6 Sol; every route bills Kilo Gateway credits.
        self.assertEqual(GLM_KILO, routes["requirements"]["model"])
        self.assertEqual(GLM_KILO, routes["glm"]["model"])
        self.assertEqual(DEEPSEEK, routes["terra"]["model"])
        self.assertEqual(SOL_KILO, routes["plan_reviewer"]["model"])
        self.assertEqual(GLM_KILO, routes["sol"]["model"])
        self.assertEqual(GLM_KILO, routes["completion"]["model"])
        self.assertEqual(ASTRA_KILO, routes["astra"]["model"])
        self.assertEqual("Kilo Gateway", routes["terra"]["account"])

    def test_one_family_connected_stops_in_account_terms_without_single_model(self):
        result = selector.select(
            ALL_ROLES, [candidate(GLM, tool="opencode", account="Z.AI Coding Plan", billing="subscription")]
        )
        self.assertIsNone(result["routes"])
        stop = result["stop"]
        self.assertIn("Connect a second model family", stop)
        self.assertIn("ChatGPT login", stop)
        self.assertIn("Xiaomi Token Plan", stop)
        self.assertNotIn("--single-model", stop)

    def test_permissions_remove_a_connected_model_and_the_stop_names_the_account(self):
        result = selector.select(ALL_ROLES, coding_plans(), permissions={"Z.AI Coding Plan"})
        self.assertIsNone(result["routes"])
        self.assertIn("which you have not allowed", result["stop"])
        self.assertIn("ChatGPT login", result["stop"])

    def test_mixed_tools_are_refused_rather_than_mixed(self):
        result = selector.select(ALL_ROLES, gateway_only() + coding_plans()[:1])
        self.assertIsNone(result["routes"])
        self.assertIn("one tool per run", result["stop"])

    def test_an_unknown_role_is_refused(self):
        result = selector.select(("terra", "review"), gateway_only())
        self.assertIsNone(result["routes"])
        self.assertIn("review", result["stop"])

    def test_a_pinned_route_is_kept_and_still_checked_for_independence(self):
        result = selector.select(ALL_ROLES, coding_plans(), pinned={"terra": GLM})
        routes = result["routes"]
        self.assertIsNotNone(routes)
        self.assertEqual(GLM, routes["terra"]["model"])
        self.assertEqual(role_route.USER_EXPLICIT, routes["terra"]["origin"])
        # The Tester may no longer be GLM: the pinned Builder is, so it takes GPT-6 Sol.
        self.assertNotEqual(GLM, routes["sol"]["model"])
        self.assertEqual(SOL, routes["sol"]["model"])

    def test_unknown_access_is_listed_for_the_probe_and_known_access_is_not(self):
        result = selector.select(ALL_ROLES, gateway_only())
        self.assertEqual(sorted({GLM_KILO, DEEPSEEK, SOL_KILO, ASTRA_KILO}), result["probe"])
        known = [dict(c, probe="known") for c in gateway_only()]
        self.assertEqual([], selector.select(ALL_ROLES, known)["probe"])

    def test_a_level_the_model_does_not_advertise_is_never_invented(self):
        advertised = [dict(c, levels=["low", "medium", "high"]) for c in coding_plans()]
        routes = selector.select(ALL_ROLES, advertised)["routes"]
        self.assertEqual("medium", routes["requirements"]["reasoning_effort"])
        self.assertEqual("high", routes["plan_reviewer"]["reasoning_effort"])
        silent = coding_plans()  # discovery could not read any level list
        routes = selector.select(ALL_ROLES, silent)["routes"]
        self.assertIsNone(routes["requirements"]["reasoning_effort"])
        partial = [dict(c, levels=["low"]) for c in coding_plans()]  # advertises only low
        routes = selector.select(ALL_ROLES, partial)["routes"]
        self.assertIsNone(routes["requirements"]["reasoning_effort"])


class RouteRecordContractTests(unittest.TestCase):
    def test_the_selector_writes_the_origin_the_record_reads_back(self):
        saved = {"route_origins": {"terra": role_route.AUTO_SELECTED}}
        self.assertEqual(
            role_route.AUTO_SELECTED,
            role_route.origin(saved, "terra", {"model": GLM, "reasoning_effort": None}),
        )


if __name__ == "__main__":
    unittest.main()
