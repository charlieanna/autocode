"""Moving one job to another connected tool at a quota stop: the answer's form and the saved binding."""

import copy
import unittest

import autocode_role_transport as role_transport
import autocode_route_tool as route_tool


def settings(engine="opencode"):
    return {
        "engine": engine,
        "provider": "kilo",
        "roles": {"tester": {"engine": engine, "model": "anthropic/claude-x", "provider": "kilo"}},
    }


IDENTITY = {"engine": "claude_code", "identity_version": 1, "executable": "/usr/bin/claude", "config_sha256": "a" * 64}


class RouteToolTests(unittest.TestCase):
    def test_only_a_bare_tool_name_before_a_colon_names_a_tool(self):
        cases = {
            "claude_code:anthropic/claude-x": ("claude_code", "anthropic/claude-x"),
            "kilo:openrouter/x:free": ("kilo", "openrouter/x:free"),
            "openrouter/x:free": (None, "openrouter/x:free"),
            "ollama/llama3:8b": (None, "ollama/llama3:8b"),
            "gpt-6-luna": (None, "gpt-6-luna"),
            "Kilo:x/y": (None, "Kilo:x/y"),
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.assertEqual(expected, role_transport.split_answer(answer))

    def test_a_move_and_a_move_back_leave_every_other_role_alone(self):
        saved = settings()
        saved["roles"]["builder"] = {"engine": "opencode", "model": "openai/gpt-x", "provider": "kilo"}
        original = copy.deepcopy(saved)
        role_transport.rebind(saved, "tester", "claude_code", IDENTITY)
        self.assertEqual("claude_code", saved["roles"]["tester"]["provider"])
        self.assertEqual(
            {"provider": "claude_code", "identity": IDENTITY},
            saved["transport_identities"]["role:tester"],
        )
        self.assertEqual(original["roles"]["builder"], saved["roles"]["builder"])
        role_transport.rebind(saved, "tester", "kilo", None)
        self.assertEqual("kilo", saved["roles"]["tester"]["provider"])
        self.assertNotIn("role:tester", saved["transport_identities"])

    def test_only_a_quota_stop_on_a_connected_tool_moves_a_job(self):
        quota = {"pause_status": "PAUSED_BUDGET"}
        self.assertIsNone(route_tool.prepare(settings(), "tester", "kilo", "x/y", {}, None, None))
        for saved, origin, refusal in (
            (settings(), {"pause_status": "PAUSED_CONTENT_FILTER"}, "Only a quota stop"),
            (settings(), {"pause_status": "PAUSED_TIMEOUT_RECOVERY"}, "Only a quota stop"),
            (settings("codex"), quota, "Codex keeps its own"),
        ):
            with self.subTest(origin=origin, engine=saved["engine"]):
                with self.assertRaisesRegex(ValueError, refusal):
                    route_tool.prepare(saved, "tester", "claude_code", "x/y", origin, None, None)


if __name__ == "__main__":
    unittest.main()
