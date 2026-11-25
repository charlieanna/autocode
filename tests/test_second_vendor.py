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
            **extra,
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
            "codex run": (args(), None, ("gemini", "claude")),
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


if __name__ == "__main__":
    unittest.main()
