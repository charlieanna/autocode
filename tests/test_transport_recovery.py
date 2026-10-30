"""A connection-change pause names only the acceptance its CLI can admit."""

import copy
import os
import subprocess
import sys
import unittest

import autocode_run_view as run_view
import autocode_taskrun as taskrun

from tests import test_taskrun as fixtures
from tests.terminal_screen import TerminalScreen, require_tmux
from tests.test_chat_client import DONE, RUN, WORKSPACE, ScriptedChat


def paused(**fields):
    return {
        "status": "PAUSED_TRANSPORT_CHANGED",
        "stop_reason": "Local model/auth/provider settings differ from checkpoint",
        "settings": {"engine": "opencode", "roles": {}},
        **fields,
    }


class TransportOfferTests(unittest.TestCase):
    def test_reconciled_transport_pause_names_explicit_acceptance_and_its_purpose(self):
        state = paused()
        before = copy.deepcopy(state)
        shown = run_view.view(state, workspace=WORKSPACE, run_dir=RUN)
        self.assertIn("--resume-paused --accept-transport-change", shown["next_command"])
        self.assertIn("accept", shown["needs"]["recovery_hint"].lower())
        self.assertIn("connection", shown["needs"]["recovery_hint"].lower())
        self.assertIsNot(shown["needs"].get("confirmation_required"), False)
        self.assertEqual(state, before)

    def test_unreconciled_frontiers_and_other_engines_never_offer_acceptance(self):
        for extra in (
            {"settings": {"engine": "codex"}},
            {"settings": {"engine": "retired"}},
            {"settings": {}},
            {"settings": {"engine": "opencode", "roles": {}, "transport_identities": {"role:sol": {}}}},
            {"status": "PAUSED_REQUESTED"},
            {"active_stage": {"iteration": 1, "output": "/runs/r1/iterations/001/builder-01.json"}},
            {"active_stage": {}},
            {"active_runner_check": {}},
            {"pending_report_repair": {"original": {"stage": "sol"}}},
            {"uncertain_artifacts": ["retained.json"]},
            {"applied_interventions": [{"kind": "stop"}]},
        ):
            with self.subTest(extra=extra):
                shown = run_view.view(paused(**extra), workspace=WORKSPACE, run_dir=RUN)
                self.assertNotIn("--accept-transport-change", shown["next_command"] or "")

    def test_existing_chat_requires_yes_and_uses_only_the_current_core_action(self):
        shown = run_view.view(paused(), workspace=WORKSPACE, run_dir=RUN)
        for answer, expected in (("n", []), ("y", [["--resume-paused", "--accept-transport-change"]])):
            with self.subTest(answer=answer):
                client = ScriptedChat(shown, DONE, replies=[answer])
                self.addCleanup(client.close)
                client.respond(shown)
                self.assertEqual(expected, client.taken)
                self.assertIn("connection", client.text().lower())
                self.assertIn("Continue?", client.text())


@unittest.skipUnless(os.name == "posix", "the TUI uses a POSIX curses terminal")
class TransportTerminalTests(unittest.TestCase):
    def test_real_tui_displays_explicit_acceptance_and_requires_the_persons_answer(self):
        tmux = require_tmux(self)
        program = """import json,sys
sys.path.insert(0,sys.argv[1])
from tests.test_transport_recovery import paused
from tests.test_chat_client import DONE,RUN,WORKSPACE,ScriptedChat
from autocode_chat_tui import ChatTUI
import autocode_run_view
view=autocode_run_view.view(paused(),workspace=WORKSPACE,run_dir=RUN)
client=ScriptedChat(view,DONE)
try:
 with ChatTUI() as ui:
  ui.observe(view)
  ui.say('Offline connection-recovery fixture; no provider.')
  client._say,client._ask=ui.say,ui.ask
  client.respond(view)
 expected=[['--resume-paused','--accept-transport-change']] if sys.argv[2]=='y' else []
 assert client.taken==expected, client.taken
finally:
 client.close()
"""
        root = fixtures.HERE.parent
        for answer in ("n", "y"):
            with (
                self.subTest(answer=answer),
                TerminalScreen(
                    [sys.executable, "-c", program, str(root), answer],
                    cwd=root,
                    env=os.environ,
                    cols=120,
                    rows=34,
                    tmux=tmux,
                ) as screen,
            ):
                shown = screen.wait_for("Continue? [y/N]")
                self.assertIn("explicitly accepts the changed connection", shown)
                # The person reads the plain-words effect; the chat never shows the internal flag it takes.
                self.assertNotIn("--accept-transport-change", shown)
                screen.type(answer)
                screen.keys("Enter")
                self.assertEqual(0, screen.exit_status())


class TransportAcceptanceCLITests(unittest.TestCase):
    setUp = fixtures.MixedRoleTransportTaskRunTests.setUp
    start = fixtures.MixedRoleTransportTaskRunTests.start
    operations = fixtures.MixedRoleTransportTaskRunTests.operations

    def test_real_connection_drift_offers_the_action_that_releases_the_pause(self):
        # Reuse the offline tools with one shared transport. Separately selected
        # per-role tools deliberately have immutable identities and cannot accept.
        self.start_options = ()
        options = list(self.options)
        for role, model in (
            ("astra", "zai-coding-plan/glm-5.3"),
            ("sol", "zai-coding-plan/glm-5.3"),
            ("completion", "zai-coding-plan/glm-5.3"),
            ("terra", "xiaomi-token-plan-sgp/mimo-v2.6-pro"),
        ):
            options[options.index(f"--{role}-model") + 1] = model
        self.options = tuple(options)
        run = self.start()
        attached = taskrun.TaskRun(self.workspace, run.run_dir, options=self.options, env=self.env, timeout=300)
        shown = attached.status()
        attached.approve_plan(shown["needs"]["token"])
        approved = attached.status()
        launches = len(self.operations("launch"))
        # Only this disposable adapter configuration changes; no checkpoint edit.
        self.env["OPENCODE_PERMISSION"] = "{}"
        held = attached.advance()
        self.assertEqual("PAUSED_TRANSPORT_CHANGED", held["status"])
        self.assertEqual(launches, len(self.operations("launch")))
        self.assertEqual("PAUSED_TRANSPORT_CHANGED", attached.resume_paused()["status"])
        self.assertEqual(launches, len(self.operations("launch")))
        self.assertIn("--accept-transport-change", held["next_command"])
        # A thin client executes the current view's command, without inventing a flag.
        from autocode_client_actions import named

        command = named(held, self.workspace, run.run_dir)
        result = subprocess.run(
            [*attached.command, *command.argv, "--workspace", str(self.workspace), "--run-dir", str(run.run_dir)],
            env={**self.env, **command.env},
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertIn(result.returncode, (0, 2), result.stdout + result.stderr)
        after = attached.status()
        self.assertNotEqual(
            "PAUSED_TRANSPORT_CHANGED", after["status"], str(after.get("stop_reason")) + "\n" + result.stderr[-2500:]
        )
        self.assertEqual(approved["approved_contract"], after["approved_contract"])
        self.assertEqual(approved["routes"], after["routes"])
        self.assertEqual(approved["cost"]["cap_usd"], after["cost"]["cap_usd"])

    def test_mixed_role_tools_keep_their_existing_immutable_identity_refusal(self):
        run = self.start()
        attached = taskrun.TaskRun(self.workspace, run.run_dir, options=self.options, env=self.env, timeout=300)
        attached.approve_plan(attached.status()["needs"]["token"])
        self.env["OPENCODE_PERMISSION"] = "{}"
        launches = len(self.operations("launch"))
        held = attached.advance()
        self.assertEqual("PAUSED_TRANSPORT_CHANGED", held["status"])
        self.assertNotIn("--accept-transport-change", held["next_command"])
        with self.assertRaisesRegex(taskrun.TaskRunError, "Mixed-role tool identities are immutable"):
            attached.accept_transport_change()
        self.assertEqual(launches, len(self.operations("launch")))
