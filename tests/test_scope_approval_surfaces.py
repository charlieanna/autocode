"""Every approval surface discloses work excluded from the token-bound plan (#2146)."""

import contextlib
import io
import unittest
from unittest.mock import patch

import autocode_bridge_relay as relay
import autocode_changeset as changeset
import autocode_chat_changeset as chat_changeset
import autocode_chat_client as chat
import autocode_client_actions as actions


def plan(excluded):
    return {
        "token": "r1:shown",
        "acceptance_criteria": [
            {"id": "C1", "criterion": "Local fix", "verification_method": "Run contract test", "human_review": True}
        ],
        "constraints": ["Preserve callers"],
        "permission_boundaries": ["Only this checkout"],
        "scope_exclusions": [excluded],
        "git_delivery": {"kind": "atomic_commit", "message": "Fix local contract"},
    }


class ScopeApprovalSurfaceTests(unittest.TestCase):
    def test_terminal_and_bridge_show_all_exclusions(self):
        shown = plan("billing-api: changing the server response is not covered")
        shown["scope_exclusions"].append("web-client: end-to-end checkout is not verified")
        for render in (chat.render_plan, relay.plan_markdown):
            with self.subTest(renderer=render.__name__):
                text = render(shown)
                self.assertIn("Not covered", text)
                self.assertIn("Create one local atomic commit: Fix local contract", text)
                for excluded in shown["scope_exclusions"]:
                    self.assertIn(excluded, text)

    def test_set_approval_discloses_each_members_exclusions(self):
        summary = {
            "changeset": "set-1",
            "runs": [
                {
                    "target": target,
                    "role": role,
                    "run_dir": f"/{target}",
                    "status": "AWAITING_GOAL_APPROVAL",
                    "plan": plan(excluded),
                }
                for target, role, excluded in (
                    ("api", "source", "web-client: UI changes are excluded"),
                    ("worker", "member", "billing-db: migration is excluded"),
                )
            ],
            "approval": {"next_command": ["autocode", "changeset", "approve"]},
        }
        output = io.StringIO()
        with patch.object(changeset, "status", return_value=summary), contextlib.redirect_stdout(output):
            self.assertEqual(0, changeset.cli(["status", "--run-dir", "/api"]))
        terminal = chat_changeset.text(summary, chat.render_plan)
        for text in (output.getvalue(), terminal):
            for row in summary["runs"]:
                self.assertIn(row["plan"]["scope_exclusions"][0], text)
            self.assertIn("Run contract test", text)
            self.assertIn("Create one local atomic commit: Fix local contract", text)
        bridge = relay.changeset_markdown(summary, {"run_dir": "/api"})
        self.assertIn("billing-db: migration is excluded", bridge)

    def test_malformed_exclusions_cannot_be_silently_approved(self):
        shown = plan("Other repo is excluded")
        view = {"needs": {"kind": "approve_plan", "token": shown["token"]}, "displayed_plan": shown}
        self.assertIs(actions.plan_for_approval(view), shown)
        for invalid in (None, "not a list", [None], [{"repository": "hidden"}]):
            with self.subTest(invalid=invalid):
                shown["scope_exclusions"] = invalid
                self.assertIsNone(actions.plan_for_approval(view))
        del shown["scope_exclusions"]
        self.assertIs(actions.plan_for_approval(view), shown, "legacy plans remain readable")
