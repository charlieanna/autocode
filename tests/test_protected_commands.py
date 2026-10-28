"""Command-only recovery never replaces the sealed original test inventory."""

import copy
import tempfile
import unittest
from pathlib import Path

import autocode_client_actions as actions
import autocode_pause_command as pause_command
import autocode_protected_commands as commands
import autocode_run_view as run_view
import autocode_util as util


class CommandRecoveryTests(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        record = {
            "binding_hash": "old",
            "command": "python3 -m unittest discover -v",
            "files": {
                "tests/__init__.py": {"size": 0},
                "repos/a/tests/test_original.py": {"size": 100, "sha256": "original"},
            },
        }
        command = "python3 -m pytest repos/a/tests tests -q"
        body = {
            "open_blocking_questions": [],
            "acceptance_criteria": [{"id": "AC1", "verification_method": "Run `" + command + "` and assert exit 0"}],
        }
        contract = {"task_id": "goal", "revision": 1, "body": body, "origin": "agent_plan"}
        contract["hash"] = util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        event = {"actor": "user_cli", "token": "r1:" + contract["hash"]}
        contract.update(approval_status="approved", approval_event=event)
        return (
            {
                "workspace": str(root),
                "status": "PAUSED_OPERATOR_REQUEST",
                "next_stage": "sol",
                "goal_contract": contract,
                "user_events": [event],
                "settings": {"protected_tests": record},
            },
            record,
            command,
        )

    def test_names_exact_command_and_keeps_original_inventory(self):
        state, record, expected = self.fixture()
        before = copy.deepcopy(state)
        proposed = commands.proposal(state, record)
        self.assertEqual(expected, proposed["command"])
        self.assertEqual(record["files"], proposed["files"])
        self.assertEqual(before, state)
        offered = run_view.needs(state)
        self.assertEqual("--resume-paused --revise-protected-tests @approved-suite", offered["action"])
        self.assertIn(expected, offered["reason"])
        workspace, run_dir = Path(state["workspace"]), Path(state["workspace"]) / ".autocode/runs/repair"
        displayed = pause_command.command(offered, run_dir=run_dir, workspace=workspace)
        named = actions.named({"next_command": displayed}, workspace, run_dir)
        self.assertEqual(["--resume-paused", "--revise-protected-tests", "@approved-suite"], named.argv[-3:])

    def test_nonapproved_active_and_uncertain_calls_cannot_revise(self):
        for change in (
            {"status": "RUNNING"},
            {"next_stage": "terra"},
            {"active_stage": {"stage": "sol"}},
            {"pending_report_repair": {"stage": "sol"}},
            {"uncertain_artifacts": ["call"]},
        ):
            state, record, _ = self.fixture()
            state.update(change)
            self.assertIsNone(commands.proposal(state, record))
        state, record, _ = self.fixture()
        state["goal_contract"]["approval_status"] = "draft"
        self.assertIsNone(commands.proposal(state, record))

    def test_filtered_or_incomplete_or_ambiguous_commands_are_not_offered(self):
        for command in (
            "python3 -m pytest tests -q",
            "python3 -m pytest repos/a/tests tests -k new",
            "python3 -m pytest repos/a/tests tests --ignore=old",
            "python3 -m pytest /tmp/tests tests -q",
        ):
            state, record, _ = self.fixture()
            # Re-seal the changed plan through the same explicit fixture approval.
            contract = state["goal_contract"]
            contract["body"]["acceptance_criteria"][0]["verification_method"] = command
            contract["hash"] = util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
            contract["approval_event"]["token"] = "r1:" + contract["hash"]
            self.assertIsNone(commands.proposal(state, record), command)
        state, record, _ = self.fixture()
        state["current_task"] = {"validation_plan": ["python3 -m pytest repos/a/tests tests -v"]}
        self.assertIsNone(commands.proposal(state, record))

    def test_an_existing_or_executable_marker_cannot_trigger_correction(self):
        state, record, _ = self.fixture()
        record["files"]["tests/__init__.py"]["size"] = 1
        self.assertIsNone(commands.proposal(state, record))
        record["files"]["tests/__init__.py"]["size"] = 0
        marker = Path(state["workspace"]) / "tests/__init__.py"
        marker.parent.mkdir()
        marker.touch()
        self.assertIsNone(commands.proposal(state, record))
