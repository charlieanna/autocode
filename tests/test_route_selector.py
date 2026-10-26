"""The automatic route selector (#1426 step 7): the deterministic choice, and its stops."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_role_route as role_route
import autocode_route_selector as selector

GLM = "zai-coding-plan/glm-5.3"
GLM_KILO = "kilo/z-ai/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
SOL = "openai/gpt-6-sol"
SOL_KILO = "kilo/openai/gpt-6-sol"
ASTRA = "openai/gpt-6-astra"
ASTRA_KILO = "kilo/openai/gpt-6-astra"
DEEPSEEK = "kilo/deepseek/deepseek-v4.1-flash"
OPUS = "anthropic/claude-opus-5-5"
LUNA = "openai/gpt-6-luna"
FLASH = "xiaomi-token-plan-sgp/mimo-v2.6-flash"
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
        self.assertEqual("2026-10-25", table["as_of"])
        for role in selector.PIPELINE_ROLES:
            self.assertTrue(selector.qualified(role), f"{role} has no qualified model")

    def test_the_table_excludes_mimo_from_plan_review_with_the_owner_decision(self):
        # #2121: a stock profile pins MiMo as reviewer, so a live run reaches it; the exclusion
        # keeps that run from qualifying it and selection from ever choosing it.
        excluded = selector.table()["excluded"]["plan_reviewer"]
        self.assertEqual([MIMO], [entry["model"] for entry in excluded])
        self.assertIn("Owner decision 2026-10-20", excluded[0]["evidence"])

    def test_an_excluded_model_needs_evidence_and_may_not_also_be_qualified(self):
        listed = {**selector.qualified("plan_reviewer")[0], "model": MIMO, "routes": [MIMO]}
        table = {"as_of": "x", "roles": {"plan_reviewer": [listed]}, "excluded": {"plan_reviewer": [{"model": MIMO}]}}
        with self.assertRaisesRegex(ValueError, "evidence is required"):
            selector.parse(table)
        table["excluded"]["plan_reviewer"][0]["evidence"] = "Owner decision"
        with self.assertRaisesRegex(ValueError, "excluded and also listed"):
            selector.parse(table)

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
    def test_three_subscription_logins_put_gpt6_sol_on_building_and_plan_review(self):
        allowed = {"Z.AI Coding Plan", "Xiaomi Token Plan (Singapore)", "ChatGPT login"}
        result = selector.select(ALL_ROLES, coding_plans(), permissions=allowed)
        routes = result["routes"]
        self.assertIsNotNone(routes)
        # Owner decisions 2026-10-20: GPT-6 Sol builds and reviews the plan; GLM requires, plans, and checks
        # Sol's work as Tester and Completion Reviewer (a checker never grades its own family).
        self.assertEqual(GLM, routes["requirements"]["model"])
        self.assertEqual(GLM, routes["glm"]["model"])
        self.assertEqual(SOL, routes["terra"]["model"])
        self.assertEqual(SOL, routes["plan_reviewer"]["model"])
        self.assertEqual(GLM, routes["sol"]["model"])
        self.assertEqual(GLM, routes["completion"]["model"])
        self.assertEqual(ASTRA, routes["astra"]["model"])
        self.assertEqual({role_route.AUTO_SELECTED}, {r["origin"] for r in routes.values()})

    def test_mimo_never_reviews_a_plan_and_claude_is_the_second_choice(self):
        # Owner decision 2026-10-20: MiMo reasoned 32-45k tokens per plan review and failed twice.
        opus = candidate(OPUS, tool="opencode", account="Anthropic", billing="pay per token")
        plans = [row for row in coding_plans() if row["model"] not in (SOL, ASTRA)]
        result = selector.select(("requirements", "glm", "terra", "plan_reviewer"), [*plans, opus])
        self.assertEqual(OPUS, result["routes"]["plan_reviewer"]["model"])
        both = selector.select(("requirements", "glm", "terra", "plan_reviewer"), [*coding_plans(), opus])
        self.assertEqual(SOL, both["routes"]["plan_reviewer"]["model"], "GPT-6 Sol first, even beside Claude")

    def test_gpt6_luna_builds_at_medium_and_no_gpt_model_checks_it(self):
        luna = candidate(LUNA, tool="opencode", account="ChatGPT login", billing="subscription")
        routes = selector.select(ALL_ROLES, [*coding_plans(), luna])["routes"]
        self.assertEqual(LUNA, routes["terra"]["model"])
        self.assertEqual("medium", routes["terra"]["reasoning_effort"])
        # All GPT models are one family (owner, 2026-10-20): GLM checks Luna's build, never GPT-6 Sol.
        self.assertEqual(GLM, routes["sol"]["model"])
        self.assertEqual("medium", routes["sol"]["reasoning_effort"])  # the GLM Tester at medium (2026-10-21)
        self.assertEqual(GLM, routes["completion"]["model"])

    def test_mimo_flash_builds_when_only_a_gpt_model_could_check_an_openai_build(self):
        account = {"tool": "opencode", "account": "ChatGPT login", "billing": "subscription"}
        flash = candidate(FLASH, tool="opencode", account="Xiaomi Token Plan (Singapore)", billing="subscription")
        rows = [candidate(LUNA, **account), candidate(SOL, **account), flash]
        routes = selector.select(("terra", "sol", "completion"), rows)["routes"]
        self.assertEqual(FLASH, routes["terra"]["model"])
        self.assertEqual(SOL, routes["sol"]["model"])
        self.assertEqual(SOL, routes["completion"]["model"])

    def test_claude_builds_when_gpt6_sol_is_not_connected(self):
        opus = candidate(OPUS, tool="opencode", account="Anthropic", billing="pay per token")
        plans = [row for row in coding_plans() if row["model"] not in (SOL, ASTRA)]
        routes = selector.select(("requirements", "glm", "terra", "sol", "completion"), [*plans, opus])["routes"]
        self.assertEqual(OPUS, routes["terra"]["model"])
        self.assertNotEqual(OPUS, routes["completion"]["model"], "Claude never checks its own build")

    def test_without_sol_or_claude_the_person_is_offered_those_and_never_mimo(self):
        plans = [row for row in coding_plans() if row["model"] not in (SOL, ASTRA)]
        result = selector.select(("requirements", "glm", "terra", "plan_reviewer"), plans)
        self.assertIsNone(result["routes"])
        stop = result["stop"]
        self.assertIn("Plan Reviewer: no qualified model is available.", stop)
        self.assertIn(f"{SOL} — connect the ChatGPT login", stop)
        self.assertIn(f"{OPUS} — connect the Anthropic (Claude) login", stop)
        self.assertNotIn(MIMO + " —", stop)

    def test_a_preference_must_be_a_nonnegative_integer(self):
        table = {
            "as_of": "x",
            "roles": {"plan_reviewer": [{**selector.qualified("plan_reviewer")[0], "preference": -1}]},
        }
        with self.assertRaisesRegex(ValueError, "preference"):
            selector.parse(table)

    def test_a_gateway_only_login_gets_the_owners_starting_table(self):
        result = selector.select(ALL_ROLES, gateway_only())
        routes = result["routes"]
        self.assertIsNotNone(routes)
        # GPT-6 Sol builds and reviews the plan, GLM 5.3 requires, plans and checks the build;
        # every route bills Kilo Gateway credits.
        self.assertEqual(GLM_KILO, routes["requirements"]["model"])
        self.assertEqual(GLM_KILO, routes["glm"]["model"])
        self.assertEqual(SOL_KILO, routes["terra"]["model"])
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
        self.assertIn("Anthropic (Claude) login", stop)  # plan review offers GPT-6 Sol or Claude, never MiMo
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
        self.assertEqual(sorted({GLM_KILO, SOL_KILO, ASTRA_KILO}), result["probe"])
        known = [dict(c, probe="known") for c in gateway_only()]
        self.assertEqual([], selector.select(ALL_ROLES, known)["probe"])

    def test_the_tables_level_is_used_unless_discovery_reports_the_model_lacks_it(self):
        advertised = [dict(c, levels=["low", "medium", "high"]) for c in coding_plans()]
        routes = selector.select(ALL_ROLES, advertised)["routes"]
        self.assertEqual("medium", routes["requirements"]["reasoning_effort"])
        self.assertEqual("high", routes["plan_reviewer"]["reasoning_effort"])
        # Discovery could not read any level list: the table's level stands, because
        # it is the level AutoCode's qualifying runs used on that model.
        silent = coding_plans()
        routes = selector.select(ALL_ROLES, silent)["routes"]
        self.assertEqual("medium", routes["requirements"]["reasoning_effort"])
        # Discovery reports the model lacks the table's level: the model's own default.
        partial = [dict(c, levels=["low"]) for c in coding_plans()]
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


class NextRouteTests(unittest.TestCase):
    """Step 9's decision: the route a stopped model's role switches to on its own (#1426)."""

    def test_a_dead_tester_switches_to_the_next_qualified_independent_model(self):
        result = selector.select(ALL_ROLES, coding_plans(), pinned={"terra": MIMO})
        result["candidates"] = selector.permitted_set(coding_plans())
        switched = selector.next_route(result, "sol", GLM)
        self.assertIsNotNone(switched)
        self.assertEqual(SOL, switched["model"])
        self.assertEqual("ChatGPT login", switched["account"])

    def test_no_candidate_left_means_ask_not_guess(self):
        # A pinned MiMo Builder stops. GPT-6 Sol is preferred, but it already checks the Builder as Completion
        # Reviewer, and GLM is the Tester's family: switching would break independence, so the person is asked.
        result = selector.select(ALL_ROLES, coding_plans(), pinned={"terra": MIMO})
        result["candidates"] = selector.permitted_set(coding_plans())
        self.assertEqual(SOL, result["routes"]["completion"]["model"])
        self.assertIsNone(selector.next_route(result, "terra", MIMO))

    def test_other_roles_keep_their_routes_through_the_switch(self):
        result = selector.select(ALL_ROLES, coding_plans(), pinned={"terra": MIMO})
        result["candidates"] = selector.permitted_set(coding_plans())
        before = {role: route["model"] for role, route in result["routes"].items()}
        switched = selector.next_route(result, "sol", GLM)
        self.assertIsNotNone(switched)
        # The saved selection itself is not mutated by asking for a next route.
        self.assertEqual(before, {role: route["model"] for role, route in result["routes"].items()})

    def test_a_selection_without_a_saved_permitted_set_has_no_next_route(self):
        result = selector.select(ALL_ROLES, coding_plans())
        self.assertIsNone(selector.next_route(result, "sol", GLM))

    def test_permitted_set_lists_each_roles_qualified_connected_candidates(self):
        permitted = selector.permitted_set(coding_plans())
        self.assertEqual({SOL, GLM, MIMO}, {c["model"] for c in permitted["terra"]})
        self.assertEqual({SOL, GLM}, {c["model"] for c in permitted["sol"]})
        self.assertEqual({ASTRA}, {c["model"] for c in permitted["astra"]})
