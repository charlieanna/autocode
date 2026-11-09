"""Executable status offers keep exact bindings; terminal text never prints them."""

import copy
import shlex
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_chat_view as chat
import autocode_checkpoint_reconciliation as reconciliation
import autocode_client_actions as actions
import autocode_pause_command as pause
import autocode_run_view as run_view


class NamedActionTokenTests(unittest.TestCase):
    workspace = Path("/tmp/project with spaces")
    run_dir = Path("/tmp/run with spaces")

    def test_a_separate_numeric_offer_keeps_its_own_recovery_binding(self):
        state = {"status": "PAUSED_TIME_LIMIT", "active_seconds": 5, "settings": {"limits": {"max_seconds": 3}}}
        with patch.object(run_view, "needs", return_value={"kind": "resume", "action": "--resume-paused"}):
            view = run_view.view(state, workspace=self.workspace, run_dir=self.run_dir)
        row, command = actions.bound_offer(view, self.workspace, self.run_dir)
        filled = actions.fill_offer(view, row, command, "6")
        self.assertEqual({"AUTOCODE_EXPECTED_RECOVERY_TOKEN": view["recovery"]["token"]}, filled.env)
        self.assertEqual(["--resume-paused", "--max-seconds", "6", "--expected-recovery-token", "-"], filled.argv)
        self.assertEqual(["--resume-paused"], actions.named(view, self.workspace, self.run_dir).argv)

    def test_held_change_set_approval_names_the_current_member_plan(self):
        state = {"goal_contract": {"revision": 7, "hash": "current-plan"}}
        with (
            patch("autocode_changeset_barrier.held_approval", return_value=True),
            patch("autocode_changeset_barrier.refusal", return_value="Review the linked plans"),
        ):
            view = run_view.action(state, workspace=self.workspace, run_dir=self.run_dir)
        command = actions.named(view, self.workspace, self.run_dir)
        self.assertEqual({"AUTOCODE_APPROVE_GOAL_TOKEN": "r7:current-plan"}, command.env)
        self.assertEqual(["--approve-goal", "-"], command.argv)

    def test_reconciliation_offer_binds_to_the_retained_checkpoint(self):
        from tests.test_checkpoint_reconciliation import checkpoint, inspection

        state = checkpoint()
        fresh = inspection(state["active_stage"]["supervision"], owner=False, keeper=False, provider=False)
        view = run_view.view(state, liveness=fresh)
        command = actions.named(view, Path("/w"), Path("/r"))
        token = command.env["AUTOCODE_RECONCILE_ONLY_TOKEN"]
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", reconciliation.decision(state, token, fresh)["status"])
        state["active_stage"]["iteration"] += 1
        with self.assertRaisesRegex(ValueError, "checkpoint changed"):
            reconciliation.decision(state, token, fresh)

    def test_public_actions_deliver_exact_tokens_only_in_the_private_launch_map(self):
        token = "current token 'quoted' = value"
        cases = (
            ({"kind": "approve_plan", "token": token}, "approve_goal"),
            ({"kind": "review", "token": token, "criteria": ["AC1", "AC2"]}, "review_token"),
            ({"kind": "answer", "resolver_token": token, "questions": [{"id": "Q1"}]}, "resolver_token"),
            (
                {
                    "kind": "answer",
                    "resolver_token": token,
                    "resolver_request_id": "req-1",
                    "resolver_scope": "operational_exhaustion",
                },
                "resolver_token",
            ),
            (
                {
                    "kind": "retry_job",
                    "job_retry_token": token,
                    "action": "--resume-paused --retry-failed-stage --job-retry-token -",
                    "route": {"question_id": "route-analyst"},
                },
                "job_retry_token",
            ),
        )
        for need, field in cases:
            with self.subTest(kind=need["kind"]):
                original = copy.deepcopy(need)
                with patch.object(run_view, "needs", return_value=need):
                    view = run_view.action(
                        {"status": "WAITING_FOR_USER", "settings": {}},
                        workspace=self.workspace,
                        run_dir=self.run_dir,
                    )
                command = actions.named(view, self.workspace, self.run_dir)
                self.assertEqual({pause.TOKEN_ENV_VARS[field]: token}, command.env)
                self.assertNotIn(token, command.argv)
                self.assertEqual("-", command.argv[command.argv.index("--" + field.replace("_", "-")) + 1])
                if need.get("route"):
                    routed = actions.parse(view["needs"]["route"]["command"], self.workspace, self.run_dir)
                    self.assertEqual(command.env, routed.env)
                    self.assertEqual(["--answer", "route-analyst=MODEL", "--job-retry-token", "-"], routed.argv)
                self.assertEqual(original, need)

    def test_chat_reply_preserves_quoted_token_without_exposing_it_in_messages_or_argv(self):
        token = "reply token 'quoted' = value"
        need = {"kind": "answer", "resolver_scope": "blocker", "resolver_request_id": "req-1", "resolver_token": token}
        view = {"status": "WAITING_FOR_USER", "needs": need}
        projected = chat.project({"status": view["status"]}, view, workspace=self.workspace, run_dir=self.run_dir)
        view["chat"] = projected
        command = actions.message(view, "The dependency is ready", projected["context"], self.workspace, self.run_dir)
        self.assertEqual({"AUTOCODE_RESOLVER_TOKEN": token}, command.env)
        self.assertNotIn(token, command.argv)
        self.assertEqual("The dependency is ready", command.argv[-1])
        self.assertNotIn(token, str(projected["messages"]))
        view["needs"] = {**need, "resolver_token": "newer-token"}
        with self.assertRaisesRegex(actions.ClientGap, "Unsupported message action"):
            actions.message(view, "The dependency is ready", projected["context"], self.workspace, self.run_dir)

    def test_terminal_header_and_trailer_mask_every_transport_token(self):
        for variable in pause.TOKEN_ENV_VARS.values():
            with self.subTest(variable=variable):
                token = "private token 'quoted' = value"
                command = f"{variable}={shlex.quote(token)} autocode --run-dir '/tmp/run with spaces' --no-chat"
                view = {
                    "pause_category": "your_decision",
                    "pause_category_label": "Your decision",
                    "status": "WAITING_FOR_USER",
                    "next_command": command,
                }
                shown = f"{variable}=- autocode --run-dir '/tmp/run with spaces' --no-chat"
                self.assertEqual("Your decision\nState: WAITING_FOR_USER\nNext command: " + shown, pause.summary(view))
                self.assertEqual("Next command: " + shown, pause.trailer(view, "line\n" * 21))
                self.assertEqual("", pause.trailer(view, "line\n" * 21 + "Next command: " + shown))
                self.assertEqual(command, view["next_command"])
