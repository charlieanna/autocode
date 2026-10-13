"""Two runs in one checkout: only one run's agents work in it at a time."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_checkout_lock as checkout_lock
import autocode_process as processes

from . import test_subprocess


class ExclusiveTests(unittest.TestCase):
    def test_a_second_holder_is_refused_and_told_who_holds_it(self):
        with tempfile.TemporaryDirectory() as workspace:
            with checkout_lock.exclusive(workspace, "/runs/first"):
                self.assertEqual("/runs/first", checkout_lock.holder(workspace)["run_dir"])
                with self.assertRaises(checkout_lock.CheckoutBusy) as busy:
                    with checkout_lock.exclusive(workspace, "/runs/second"):
                        self.fail("entered a held checkout")
                self.assertEqual("PAUSED_WORKSPACE_BUSY", busy.exception.status)
                self.assertIn("/runs/first", str(busy.exception))
            self.assertEqual({}, checkout_lock.holder(workspace))
            with checkout_lock.exclusive(workspace, "/runs/second"):
                pass

    def test_only_a_held_checkout_can_offer_its_matching_capability(self):
        with tempfile.TemporaryDirectory() as workspace:
            options = {"pass_fds": (1234,)}
            self.assertIsNone(checkout_lock.lease(workspace))
            self.assertIs(options, checkout_lock.child_options(workspace, options))
            with checkout_lock.exclusive(workspace, "/runs/first"):
                lease = checkout_lock.lease(workspace)
                owner = {"pid": os.getpid()}
                self.assertEqual(lease["fd"], checkout_lock.validate_lease(lease, owner))
                self.assertEqual((1234, lease["fd"]), checkout_lock.child_options(workspace, options)["pass_fds"])
                for changed, claimed_owner in (
                    ({"fd": True}, owner),
                    ({"fd": 2}, owner),
                    ({"path": "writer.lock"}, owner),
                    ({"inode": lease["inode"] + 1}, owner),
                    ({}, {"pid": os.getpid() + 1}),
                ):
                    with self.subTest(changed=changed, owner=claimed_owner):
                        with self.assertRaises(ValueError):
                            checkout_lock.validate_lease({**lease, **changed}, claimed_owner)
                with (Path(workspace) / "foreign.lock").open("w") as foreign:
                    with self.assertRaises(ValueError):
                        checkout_lock.validate_lease({**lease, "fd": foreign.fileno()}, owner)
            self.assertIsNone(checkout_lock.lease(workspace))

    def test_uncertain_cleanup_retains_admission_until_owned_birth_identities_are_absent(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            owner = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
            path = root / "worker-receipt.json"
            record = {"schema": 1, "nonce": "a" * 32, "owner": owner, "receipt": str(path)}
            value = {
                **record,
                "keeper": {"pid": 2**30, "birth_identity": 1.0},
                "provider": owner,
                "processes": [owner],
                "phase": "uncertain",
                "cleanup_error": "inspection unavailable",
            }
            with checkout_lock.exclusive(root, root):
                checkout_lock.retain_worker(root, record)
                path.write_text(json.dumps(value))
            with self.assertRaises(checkout_lock.CheckoutBusy):
                with checkout_lock.exclusive(root, "/runs/second"):
                    self.fail("An uncertain keeper exit allowed a surviving worker's checkout to be overwritten")
            retained = checkout_lock.holder(root)
            with patch.object(processes, "live_processes", side_effect=processes.ProcessError("inspection denied")):
                self.assertTrue(checkout_lock.orphaned_workers(retained))
            path.write_text("not json")
            self.assertTrue(checkout_lock.orphaned_workers(retained))
            # PID reuse is not evidence that the original worker survived.
            retired = {**owner, "birth_identity": owner["birth_identity"] + 1}
            path.write_text(json.dumps({**value, "provider": retired, "processes": [retired]}))
            with checkout_lock.exclusive(root, "/runs/second"):
                pass
            self.assertEqual({}, checkout_lock.holder(root))

    def test_a_worker_that_never_armed_does_not_leave_a_permanent_admission_hold(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            owner = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
            record = {"schema": 1, "nonce": "a" * 32, "owner": owner, "receipt": str(root / "never-armed.json")}
            with checkout_lock.exclusive(root, root):
                checkout_lock.retain_worker(root, record)
            with checkout_lock.exclusive(root, "/runs/second"):
                pass
            self.assertEqual({}, checkout_lock.holder(root))


class InPlaceRunTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def test_a_run_waits_while_another_run_works_in_the_checkout(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        other = self.project / ".autocode" / "runs" / "other-run"
        with checkout_lock.exclusive(self.project, other):
            result = self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
            self.assertIn(
                f"Another AutoCode run is working in this checkout ({other}); nothing was changed", result.stderr
            )
            self.assertIn("without --in-place", result.stderr)
            run, state = self.saved()
            self.assertEqual("RUNNING", state["status"], "a busy checkout changes nothing in the waiting run")
            self.assertIsNone(state["base_commit"], "the other run's unfinished files are not where this run starts")
            (self.project / "notes.txt").write_text("the other run's work\n")
            self.assertEqual(
                [],
                [row for row in state["stages"] if not row.get("runner_owned")],
                "no agent may run while another run holds the checkout",
            )
            self.assertFalse((self.project / "greet.py").exists())
            status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
            self.assertEqual("RUNNING", status["status"])
            self.assertEqual("ready", status["view"]["controller"]["kind"], status)
            self.assertEqual(2, status["view"]["controller"]["receipt"]["exit_code"])
        # The same kind of command, once the other run has stopped, simply continues.
        self.launch(["--run-dir", str(run), "--chat"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(
            ["notes.txt"],
            subprocess.run(
                ["git", "ls-tree", "--name-only", state["base_commit"]],
                cwd=self.project,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split(),
            "the run starts from the checkout as it first holds it",
        )
        self.assertEqual({}, checkout_lock.holder(self.project))


if __name__ == "__main__":
    unittest.main()
