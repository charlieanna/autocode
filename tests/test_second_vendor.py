"""``autocode_second_vendor``: which checking stages a new one-vendor run hands to a second signed-in vendor."""

import argparse
import types
import unittest

import autocode_second_vendor as second_vendor

GEMINI_MODELS = {"plan_reviewer": "gemini-2.5-pro", "tester": "gemini-2.5-pro", "completion": "gemini-2.5-pro"}


def facade(label, models=GEMINI_MODELS, refusal=None):
    def login_check(workspace=None):
        if refusal:
            raise RuntimeError(refusal)
        return {}

    return types.SimpleNamespace(LABEL=label, DEFAULT_MODELS=models, login_check=login_check)


def args(**given):
    values = {"role_transport": [], "single_model": None, "replay_recording": None}
    for role in second_vendor.CHECKERS:
        values[f"{role}_model"] = values[f"{role}_provider"] = None
    return argparse.Namespace(**{**values, **given})


class SecondVendorTests(unittest.TestCase):
    def setUp(self):
        self.tools = {"claude": facade("Claude Code"), "gemini": facade("Gemini CLI")}
        self.said = []

    def apply(self, given, run_tool="claude", installed=("gemini",), **extra):
        return second_vendor.apply(
            given,
            run_tool,
            say=self.said.append,
            which=lambda name: f"/bin/{name}" if name in installed else None,
            resolve=self.tools.__getitem__,
            **{"environ": {}, **extra},
        )

    def test_a_claude_code_run_has_its_checks_moved_to_a_signed_in_gemini(self):
        given = args()
        decision = self.apply(given)
        self.assertEqual(["plan_reviewer=gemini", "tester=gemini", "completion=gemini"], given.role_transport)
        self.assertEqual(
            ("gemini-2.5-pro",) * 3, (given.plan_reviewer_model, given.tester_model, given.completion_model)
        )
        self.assertEqual(("gemini", []), (decision["provider"], decision["kept"]))
        self.assertIn("a second vendor checks what Claude Code plans and builds", decision["reason"])
        self.assertEqual(1, len(self.said))
        self.assertIn("Gemini CLI runs the Plan Reviewer (gemini-2.5-pro), Tester", self.said[0])

    def test_a_gemini_run_is_checked_by_claude_code(self):
        self.tools["claude"] = facade("Claude Code", {role: "claude-opus-5-5" for role in second_vendor.CHECKERS})
        given = args()
        self.assertEqual("claude", self.apply(given, run_tool="gemini", installed=("claude",))["provider"])
        self.assertEqual("claude-opus-5-5", given.tester_model)

    def test_a_stage_the_person_chose_keeps_its_route(self):
        given = args(tester_model="claude-sonnet-5-5")
        decision = self.apply(given, stage_choices_taken=("completion",))
        self.assertEqual({"plan_reviewer": "gemini-2.5-pro"}, decision["roles"])
        self.assertEqual(["tester", "completion"], decision["kept"])
        self.assertEqual("claude-sonnet-5-5", given.tester_model)

    def test_nothing_moves_without_a_second_signed_in_vendor_or_against_a_choice(self):
        cases = {
            "not installed": (args(), "claude", ()),
            "no run tool": (args(), None, ("gemini", "claude")),
            "opencode run": (args(), "opencode", ("gemini",)),
            "typed transport": (args(role_transport=["tester=kilocode"]), "claude", ("gemini",)),
            "single model": (args(single_model="claude-opus-5-5"), "claude", ("gemini",)),
            "every checker chosen": (
                args(plan_reviewer_model="a", tester_model="b", completion_provider="kilocode"),
                "claude",
                ("gemini",),
            ),
        }
        for name, (given, run_tool, installed) in cases.items():
            with self.subTest(name):
                before = vars(given).copy()
                self.assertIsNone(self.apply(given, run_tool=run_tool, installed=installed))
                self.assertEqual(before, vars(given))
        self.assertEqual([], self.said)

    def test_an_installed_vendor_that_cannot_sign_in_is_named_and_moves_nothing(self):
        self.tools["gemini"] = facade("Gemini CLI", refusal="Gemini CLI cannot sign in: add GOOGLE_CLOUD_PROJECT\nmore")
        given = args()
        self.assertIsNone(self.apply(given))
        self.assertEqual([], given.role_transport)
        self.assertEqual(
            ["Gemini CLI is installed but cannot check this run: Gemini CLI cannot sign in: add GOOGLE_CLOUD_PROJECT"],
            self.said,
        )

    def test_a_vendor_that_cannot_sign_in_gives_way_to_the_next_signed_in_one(self):
        self.tools["gemini"] = facade("Gemini CLI", refusal="Gemini CLI cannot sign in: expired")
        self.tools["codex"] = config_facade("Codex", CODEX_MODELS)
        given = args()
        decision = self.apply(given, installed=("gemini", "codex"))
        self.assertEqual(("codex", CODEX_MODELS), (decision["provider"], decision["roles"]))
        self.assertEqual(["plan_reviewer=codex", "tester=codex", "completion=codex"], given.role_transport)
        self.assertEqual([[], ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-sol"]], self.tools["codex"].checked)

    def test_a_provider_config_is_signed_in_when_its_version_and_billing_route_check_pass(self):
        self.tools["codex"] = config_facade(
            "Codex", CODEX_MODELS, refusal="codex gpt- models require login mode ChatGPT"
        )
        given = args()
        self.assertIsNone(self.apply(given, installed=("codex",)))
        self.assertEqual(
            ["Codex is installed but cannot check this run: codex gpt- models require login mode ChatGPT"], self.said
        )

    def test_the_host_switch_keeps_every_run_on_its_own_tool(self):
        given = args()
        self.assertIsNone(self.apply(given, environ={second_vendor.OFF_SWITCH: "off"}))
        self.assertEqual([], given.role_transport)

    def test_a_codex_run_without_joint_planning_has_no_plan_reviewer_to_move(self):
        self.tools["claude"] = facade("Claude Code", {role: "claude-opus-5-5" for role in second_vendor.CHECKERS})
        self.tools["codex"] = config_facade("Codex", CODEX_MODELS)
        decision = self.apply(args(), run_tool="codex", installed=("claude",))
        self.assertEqual(["tester", "completion"], list(decision["roles"]))
        decision = self.apply(args(joint_planning=True), run_tool="codex", installed=("claude",))
        self.assertEqual(["plan_reviewer", "tester", "completion"], list(decision["roles"]))

    def test_a_codex_run_is_checked_by_claude_code(self):
        self.tools["claude"] = facade("Claude Code", {role: "claude-opus-5-5" for role in second_vendor.CHECKERS})
        self.tools["codex"] = config_facade("Codex", CODEX_MODELS)
        given = args(joint_planning=True)
        decision = self.apply(given, run_tool="codex", installed=("claude", "gemini"))
        self.assertEqual("claude", decision["provider"])
        self.assertIn("a second vendor checks what Codex plans and builds", decision["reason"])


CODEX_MODELS = {"plan_reviewer": "gpt-6-astra", "tester": "gpt-5.6-sol", "completion": "gpt-5.6-sol"}


def config_facade(label, models, refusal=None):
    """A provider config (providers/command.py): no login_check; its version and billing route are checked."""
    tool = types.SimpleNamespace(LABEL=label, DEFAULT_MODELS=models, checked=[])

    def local_settings(workspace=None):
        tool.checked.append([])
        return {}

    def check_subscription_routes(roles, workspace=None):
        if refusal:
            raise RuntimeError(refusal)
        tool.checked.append([route["model"] for route in roles.values()])

    tool.local_settings, tool.check_subscription_routes = local_settings, check_subscription_routes
    return tool


if __name__ == "__main__":
    unittest.main()
