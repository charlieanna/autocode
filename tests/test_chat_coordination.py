"""A coordinated member owns the foreground while anchor polling continues."""

import copy
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_coordination as coordination


class CoordinatedDisplayTests(unittest.TestCase):
    def test_missing_repository_asks_for_catalog_setup_without_bug_specific_repos_command(self):
        run_dir = Path("/tmp/coordinated-missing/.autocode/runs/chat").resolve()
        client = Mock(run_dir=run_dir, said=set())
        summary = {
            "version": 1,
            "anchor": str(run_dir),
            "done": False,
            "members": [],
            "needs": {
                "kind": "repository",
                "reason": "No matching repository was found",
                "target": "fulfillment history",
                "candidates": [],
            },
        }
        with patch.object(coordination, "read", return_value=summary):
            result = coordination.step(client, {}, Mock())
        self.assertEqual(2, result)
        client.ask.assert_not_called()
        shown = "\n".join(call.args[0] for call in client.say.call_args_list)
        self.assertIn("autocode registry roots add PATH", shown)
        self.assertIn("autocode registry repositories add PATH", shown)
        self.assertNotIn("/repos PATH", shown)

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

    def test_repositories_command_reads_the_shared_catalog_without_registering_a_bug_specific_path(self):
        history = Mock()
        history.read.side_effect = [
            {
                "catalog": "/home/user/.autocode/source-catalog.json",
                "roots": [{"path": "/work/services"}],
                "repositories": [{"path": "/work/standalone/history"}],
            },
            {
                "complete": True,
                "diagnostics": [],
                "repositories": [
                    {
                        "path": "/work/services/api",
                        "candidates": [{"path": "/work/services/api", "head": "abc123"}],
                    }
                ],
            },
            {"github": {"configured": True}},
        ]
        text = coordination.repositories(history, "/tmp/one-off-repository")
        self.assertEqual(
            [
                ["registry", "roots", "list", "--json"],
                ["registry", "roots", "discover", "--json"],
                ["coordinate", "access", "--json"],
            ],
            [call.args[0] for call in history.read.call_args_list],
        )
        self.assertIn("/work/services/api", text)
        self.assertIn("/work/standalone/history", text)
        self.assertIn("configure", text.lower())

    def test_approval_display_includes_each_member_location_revision_role_and_grounding(self):
        root = Path("/work/history")
        run_dir = root / ".autocode/runs/fix"
        client = Mock(run_dir=run_dir)
        client.said = set()
        client.ask.return_value = "n"
        member = {
            "target": "history",
            "location": str(root),
            "base": "a" * 40,
            "role": "member",
            "status": "WAITING_FOR_APPROVAL",
            "supporting_evidence": {
                "source_quote": "keep its history in fulfillment-rg-epp-history",
                "evidence": "client.py:4",
                "evidence_repository": "/work/api",
                "evidence_revision": "b" * 40,
            },
        }
        summary = {
            "version": 1,
            "anchor": str(run_dir),
            "done": False,
            "members": [member],
            "needs": {
                "kind": "approval",
                "summary": {"runs": [{"target": "history", "plan": {"body": "Plan body"}}]},
                "joint_plan": {"body": "Combined regression plan body"},
            },
            "approval": {"token": "approval-token", "publication": "No automatic merge."},
        }
        with patch.object(coordination, "read", return_value=summary):
            coordination.step(client, {}, lambda plan: plan["body"])
        shown = "\n".join(call.args[0] for call in client.say.call_args_list)
        for value in (
            str(root),
            "a" * 40,
            "member",
            "keep its history",
            "/work/api",
            "client.py:4",
            "b" * 40,
            "Combined regression plan body",
        ):
            self.assertIn(value, shown)

    def test_a_carried_approval_is_announced_once_and_taken_as_the_named_advance(self):
        run_dir = Path("/tmp/coordinated-carry/.autocode/runs/chat").resolve()
        client = Mock(run_dir=run_dir, said=set())
        summary = {
            "version": 1,
            "anchor": str(run_dir),
            "done": False,
            "members": [],
            "needs": {
                "kind": "carry_approval",
                "carried": {"revision": 2, "token": "first", "unchanged": ["api", "history"]},
            },
            "next_command": ["autocode", "coordinate", "advance", "--run-dir", str(run_dir), "--json"],
        }
        with patch.object(coordination, "read", return_value=summary):
            self.assertIsNone(coordination.step(client, {}, Mock()))
            self.assertIsNone(coordination.step(client, {}, Mock()))
        client.advance.assert_called_with(["coordinate", "advance", "--run-dir", str(run_dir), "--json"])
        self.assertEqual(2, client.advance.call_count)
        said = [call.args[0] for call in client.say.call_args_list]
        self.assertEqual(
            ["The revised plans keep what you approved (api, history); continuing under that approval."], said
        )
        client.ask.assert_not_called()

    def test_candidate_binding_takes_the_named_core_action(self):
        run_dir = Path("/tmp/coordinated-bind/.autocode/runs/chat").resolve()
        client = Mock(run_dir=run_dir, said=set())
        summary = {
            "version": 1,
            "anchor": str(run_dir),
            "done": False,
            "members": [],
            "needs": {"kind": "bind_candidates"},
            "next_command": [
                "autocode",
                "coordinate",
                "bind",
                "--run-dir",
                str(run_dir),
                "--json",
            ],
        }
        with patch.object(coordination, "read", return_value=summary):
            self.assertIsNone(coordination.step(client, {}, Mock()))
        client.advance.assert_called_once_with(["coordinate", "bind", "--run-dir", str(run_dir), "--json"])

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
