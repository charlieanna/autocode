"""#4028: a capture family a stage's tools leave unsettled pauses that run, typed, until its cleanup is proven.

`autocode capture --compare-base` is a separate CLI an agent runs. When it cannot confirm its supervised
command stopped, it keeps its custody journal and exits with error text, and before this fix the run that
launched the agent carried on as if nothing happened. Core now reads the store the provider's own
environment names, before and after the attempt.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import autocode_capture_handoff as handoff
import autocode_metadata_custody as custody
import autocode_operational_block as operational_block
import autocode_taskrun as taskrun

from .test_metadata_custody import BOOT

TOOLS = Path(__file__).resolve().parents[1] / "tools"
OPTIONS = ("--evidence-provenance", "fake", "--engine", "codex")


def journal(boot=BOOT):
    member = uuid.uuid4().hex
    return {
        "schema": 2,
        "group_nonce": uuid.uuid4().hex,
        "boot_id": boot,
        "uid": os.geteuid(),
        "attempts": [{"nonce": member, "result_file": f"result-{member}.json"}],
    }


def leave(store: Path, row: dict) -> None:
    """The files a capture CLI that could not confirm its command stopped leaves in its store."""
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, body in (("admission.lock", ""), ("pending.json", json.dumps(row))):
        descriptor = os.open(store / name, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        os.write(descriptor, body.encode())
        os.close(descriptor)


def settle(store: Path, row: dict) -> None:
    for member in row["attempts"]:
        clean = {"schema": 1, "nonce": member["nonce"], "clean": True, "started": True, "returncode": 0}
        descriptor = os.open(store / member["result_file"], os.O_CREAT | os.O_WRONLY, 0o600)
        os.write(descriptor, json.dumps({**clean, "expired": False}).encode())
        os.close(descriptor)


@unittest.skipUnless(os.name == "posix", "private custody store permissions")
class HandoffTests(unittest.TestCase):
    """The observation itself, over the environment Core handed the provider."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capture-handoff-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        for target, attribute, value in ((custody.sys, "platform", "linux"), (custody, "_boot_id", lambda: BOOT)):
            context = patch.object(target, attribute, value)
            context.start()
            self.addCleanup(context.stop)
        # The provider's environment, not Core's own: a role's isolation can redirect its state directory.
        self.environment = {"HOME": "/nonexistent", "XDG_STATE_HOME": str(self.home / "role-state")}
        self.store = self.home / "role-state/autocode/metadata"

    def test_a_journal_the_attempt_left_is_a_typed_hold_bound_to_that_exact_journal(self):
        start = handoff.before(self.environment)
        row = journal()
        leave(self.store, row)
        hold = handoff.after(start, self.environment)
        self.assertIsInstance(hold, custody.CustodyUncertain)
        self.assertEqual(
            {"schema": 1, "scope": "metadata_check", "root": str(self.store), "pending": row}, hold.binding
        )
        self.assertEqual(
            {"root": str(self.store), "group_nonce": row["group_nonce"], "bound": True}, handoff.describe(hold)
        )
        self.assertFalse(custody.recover(hold.binding), "no cleanup was proven")
        settle(self.store, row)
        self.assertTrue(custody.recover(hold.binding), "the original family's own receipt releases it")

    def test_no_journal_a_settled_one_or_one_held_before_the_attempt_is_not_this_attempt_s(self):
        self.assertIsNone(handoff.after(handoff.before(self.environment), self.environment))
        earlier = journal()
        leave(self.store, earlier)
        start = handoff.before(self.environment)
        self.assertEqual({"readable": True, "group": earlier["group_nonce"]}, start)
        self.assertIsNone(handoff.after(start, self.environment), "an earlier hold already refused every admission")
        settle(self.store, earlier)
        self.assertIsNone(handoff.after(handoff.before({**self.environment}), self.environment))
        later = journal()
        leave(self.store, later)
        self.assertIsNotNone(handoff.after(start, self.environment), "a different journal than the one held before")

    def test_a_store_that_became_unreadable_is_a_hold_no_cleanup_can_release(self):
        start = handoff.before(self.environment)
        leave(self.store, journal())
        (self.store / "pending.json").write_text("{tampered")
        hold = handoff.after(start, self.environment)
        self.assertIsInstance(hold, custody.CustodyUncertain)
        self.assertIsNone(hold.binding)
        self.assertFalse(custody.recover(hold.binding))
        self.assertIsNone(handoff.after({"readable": False, "group": None}, self.environment))

    def test_another_boot_s_journal_or_core_s_own_store_is_read_from_the_given_environment(self):
        leave(self.store, journal())
        other = {"HOME": str(self.home / "elsewhere"), "XDG_STATE_HOME": ""}
        self.assertIsNone(handoff.after(handoff.before(other), other), "a store the provider never named")
        self.assertEqual(self.home / "elsewhere/.local/state/autocode/metadata", custody.store_root(other))

    def test_the_typed_hold_survives_the_stage_s_pause_wrapper(self):
        start = handoff.before(self.environment)
        leave(self.store, journal())
        hold = handoff.after(start, self.environment)
        self.assertIsNotNone(hold)
        try:
            raise operational_block.util.Paused("PAUSED_PROCESS_CLEANUP", str(hold)) from hold
        except operational_block.util.Paused as wrapped:
            self.assertIs(hold, operational_block.error(wrapped))


@unittest.skipUnless(sys.platform == "linux", "metadata custody is held only on Linux")
class CaptureHoldCliTests(unittest.TestCase):
    """Through the real CLI and TaskRun: the scripted stage leaves a capture journal, then finishes normally."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capture-hold-cli-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.workspace = root / "project"
        self.workspace.mkdir()
        git = ["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(TOOLS / "fake_codex.py", bindir / "codex")
        shutil.copy2(TOOLS / "goal_fixtures.py", bindir / "goal_fixtures.py")
        (bindir / "codex").chmod(0o755)
        self.state_home = root / "state"
        self.store = self.state_home / "autocode/metadata"
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(self.state_home),
            "CODEX_HOME": str(root / "codex-home"),
            "AUTOCODE_FIXTURE_MODE": "milestones",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def test_the_run_pauses_typed_and_resumes_only_after_the_original_family_s_cleanup_is_proven(self):
        held = {**self.env, "AUTOCODE_FIXTURE_CAPTURE_HOLD": "draft_plan"}
        run = taskrun.TaskRun.start(
            self.workspace, "Build greeting and goodbye", options=OPTIONS, env=held, timeout=300
        )
        row = json.loads((self.store / "pending.json").read_text())
        view = run.status()
        self.assertEqual("PAUSED_PROCESS_CLEANUP", view["status"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertIsNone(view["next_command"], "a person is never handed AutoCode's own cleanup")
        state = json.loads((run.run_dir / "state.json").read_text())
        receipts = state["resolver"]["operational_receipts"]
        (receipt,) = receipts.values()
        self.assertEqual("blocked", receipt["decision"]["action"])
        self.assertEqual(row, receipt["receipt"]["evidence"]["custody_binding"]["pending"])
        attempt = next(r for r in state["stages"] + [state.get("active_stage") or {}] if r.get("capture_custody"))
        self.assertEqual(row["group_nonce"], attempt["capture_custody"]["group_nonce"])

        run.env = self.env  # the stage that left the journal does not run again: nothing is retried
        self.assertEqual("PAUSED_PROCESS_CLEANUP", run.advance()["status"], "no cleanup proof, no progress")
        settle(self.store, row)
        advanced = run.advance()
        self.assertEqual("WAITING_FOR_USER", advanced["status"], "the plan is ready for review again")
        state = json.loads((run.run_dir / "state.json").read_text())
        actions = [row["decision"]["action"] for row in state["resolver"]["operational_receipts"].values()]
        self.assertEqual(["blocked", "recovered"], actions)


if __name__ == "__main__":
    unittest.main()
