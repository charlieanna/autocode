"""A coordinated member owns the foreground while anchor polling continues."""

import copy
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_coordination as coordination


class CoordinatedDisplayTests(unittest.TestCase):
    def test_environment_uses_stable_user_catalog_and_preserves_explicit_autocode_home(self):
        with patch.dict(os.environ, {}, clear=True):
            first = coordination.environment(Path("/tmp/project-one"))
            second = coordination.environment(Path("/tmp/project-two"))
        self.assertEqual(first["AUTOCODE_SOURCE_CATALOG_HOME"], second["AUTOCODE_SOURCE_CATALOG_HOME"])
        self.assertEqual(str(Path.home() / ".autocode"), first["AUTOCODE_SOURCE_CATALOG_HOME"])
        self.assertEqual(str(Path("/tmp/project-one/.autocode/registry").resolve()), first["AUTOCODE_HOME"])
        with patch.dict(os.environ, {"AUTOCODE_HOME": "/shared/autocode"}, clear=True):
            explicit = coordination.environment(Path("/tmp/project-one"))
        self.assertEqual("/shared/autocode", explicit["AUTOCODE_SOURCE_CATALOG_HOME"])
        self.assertEqual("/shared/autocode", explicit["AUTOCODE_HOME"])

    def test_anchor_reads_cannot_replace_the_active_member_and_callbacks_are_restored(self):
        for fail in (False, True):
            with self.subTest(child_fails=fail):
                events = []
                observed = lambda view: events.append(("view", view["run_dir"]))
                unavailable = lambda reason: events.append(("unavailable", reason))
                workspace = Path("/tmp/coordinated-display")
                anchor = {
                    "workspace": str(workspace),
                    "run_dir": str(workspace / "anchor"),
                    "status": "WAITING_FOR_USER",
                    "needs": {"kind": "answer"},
                }
                member = {
                    **anchor,
                    "workspace": str(workspace / "member"),
                    "run_dir": str(workspace / "member/run"),
                    "status": "RUNNING",
                    "needs": {"kind": "continue"},
                }
                parent = chat.ChatClient(
                    workspace, Path(anchor["run_dir"]), say=Mock(), observe=observed, unavailable=unavailable
                )
                self.addCleanup(parent.close)

                def respond(child, view):
                    # ChatTUI.wait still pumps the anchor's idle poll while this
                    # child advances. Its data stays fresh but must not steal focus.
                    parent.accept_status(anchor)
                    if parent._unavailable:
                        parent._unavailable("anchor-only outage")
                    child.accept_status(member)
                    if child._unavailable:
                        child._unavailable("member outage")
                    if fail:
                        raise RuntimeError("child refused")
                    return True

                summary = {"members": [], "done": False, "needs": {"kind": "input", "view": member}}
                with (
                    patch.object(coordination, "read", return_value=summary),
                    patch.object(chat.ChatClient, "respond", new=respond),
                ):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "child refused"):
                            coordination.step(parent, anchor, Mock())
                    else:
                        self.assertIsNone(coordination.step(parent, anchor, Mock()))
                self.assertEqual(
                    [("view", member["run_dir"]), ("view", member["run_dir"]), ("unavailable", "member outage")],
                    events,
                )
                parent.accept_status(anchor)
                self.assertEqual(("view", anchor["run_dir"]), events[-1])
                self.assertIs(parent._observe, observed)
                self.assertIs(parent._unavailable, unavailable)


class SharedBudgetDisplayTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/tmp/coordinated-budget-display").resolve()
        self.client = Mock(run_dir=self.root / ".autocode/runs/chat")
        self.need = {
            "kind": "budget",
            "cap_usd": 10,
            "known_spent_usd": 9.5,
            "complete": False,
            "minimum_cap_usd": 10.5,
            "token": "secret-budget-token",
            "effect": "Retain prior spend, routes and all other limits; approve no plan.",
            "next_command": [
                "autocode",
                "coordinate",
                "budget",
                "--run-dir",
                str(self.client.run_dir),
                "--token",
                "secret-budget-token",
                "--max-cost-usd",
                "N",
                "--json",
            ],
        }
        self.summary = {"members": [], "done": False, "needs": self.need}

    def test_explicit_finite_cap_uses_only_fresh_named_action_and_private_token(self):
        self.client.ask.side_effect = ["15.25", "yes"]
        with patch.object(coordination, "read", return_value=self.summary):
            self.assertIsNone(coordination.step(self.client, {}, Mock()))
        self.client.advance.assert_called_once_with(
            [
                "coordinate",
                "budget",
                "--run-dir",
                str(self.client.run_dir),
                "--token",
                "-",
                "--max-cost-usd",
                "15.25",
                "--json",
            ],
            env={"AUTOCODE_COORDINATION_TOKEN": "secret-budget-token"},
        )
        shown = " ".join(str(call.args) for call in self.client.say.call_args_list)
        self.assertIn("incomplete", shown)
        self.assertNotIn("secret-budget-token", shown)

    def test_decline_blank_and_invalid_amounts_never_change_a_cap(self):
        for answers in ([""], ["15", "no"], ["0"], ["nan"], ["inf"], ["9"], ["1e30"]):
            with self.subTest(answers=answers):
                self.client.reset_mock()
                self.client.ask.side_effect = answers
                with patch.object(coordination, "read", return_value=self.summary):
                    coordination.step(self.client, {}, Mock())
                self.client.advance.assert_not_called()

    def test_changed_checkpoint_after_confirmation_is_shown_again_without_dispatch(self):
        self.client.ask.side_effect = ["15", "yes"]
        fresh = copy.deepcopy(self.summary)
        fresh["needs"]["token"] = "new-checkpoint"
        with patch.object(coordination, "read", side_effect=[self.summary, fresh]):
            self.assertIsNone(coordination.step(self.client, {}, Mock()))
        self.client.advance.assert_not_called()

    def test_budget_amount_cannot_be_inserted_into_another_named_operation(self):
        self.need["next_command"][2] = "approve"
        with patch.object(coordination, "read", return_value=self.summary):
            with self.assertRaises(coordination.actions.ClientGap):
                coordination.step(self.client, {}, Mock())
        self.client.ask.assert_not_called()
        self.client.advance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
