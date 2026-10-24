"""Public durable commands through the real core and offline supervised provider.

The service transport is synchronous here for deterministic checkpoint/authority
faults. Real service/terminal lifetime is separately required by worker-host CI.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode
import autocode_client_actions as actions
import autocode_worker_records as records
import autocode_worker_view as worker_view

from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised offline providers require supported Linux")
class DurableCoreFlow(unittest.TestCase):
    def setUp(self):
        self.workspace, fixture = fixture_workspace(self)
        self.env = {**os.environ, **fixture, "AUTOCODE_WORKER_HOME": str(self.workspace.parent / "launches")}
        self.run_dir = self.workspace / ".autocode" / "runs" / "durable-flow"
        self.starts = 0
        self.launch_id = None

    def capability(self):
        return {"available": True, "adapter": "deterministic-service-transport"}

    def inspect(self, launch_id):
        return {
            "checked": True,
            "present": True,
            "pid": os.getpid(),
            "invocation_id": launch_id,
            "active_state": "active",
            "sub_state": "running",
        }

    def start(self, launch_id, argv, **kwargs):
        self.starts += 1
        record = records.read(Path(self.env["AUTOCODE_WORKER_HOME"]), launch_id)
        path = str(Path(self.env["AUTOCODE_WORKER_HOME"]) / (launch_id + ".json"))
        # Mirrors only the transport's exec, with actual entry validation in the core.
        with patch.dict(os.environ, {"INVOCATION_ID": launch_id}):
            code, output, error = self.invoke([*record["request"]["argv"], "--worker-receipt", path])
        self.assertIn(code, (0, 2), (output, error))
        self.assertNotIn("error:", error, (output, error))

    def invoke(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["autocode", *argv]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code = autocode.main()
            except SystemExit as exit:
                code = exit.code
        return code, out.getvalue(), err.getvalue()

    def public(self, argv):
        code, text, error = self.invoke(argv)
        self.assertEqual(0, code, (text, error))
        return json.loads(text)

    def status(self):
        return self.public(["--status", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)])["view"]

    def test_launch_binding_named_continuation_and_duplicate_receipt_preserve_one_delivery(self):
        with patch.dict(os.environ, self.env), patch("autocode_worker_service.UserServices", return_value=self):
            mode = self.public(["modes", "list", "--json"])["execution"]["modes"][1]
            initial = actions.worker_transport(
                mode,
                ["--brief", BRIEF, "--in-place", *FIXTURE_OPTIONS, "--max-parallel-builders", "1"],
                self.workspace,
                self.run_dir,
                fresh=True,
            )
            receipt = self.public(initial)
            self.assertTrue(receipt["ready"], receipt)
            self.assertEqual(1, self.starts)
            view = self.status()
            self.assertTrue(view["execution"]["detach_supported"])
            self.assertEqual(1, view["observation"]["version"])
            self.assertEqual("durable_local", view["execution"]["mode"])
            # Retrying an accepted start after its run exists returns its original receipt.
            repeated = self.public(initial)
            self.assertEqual(receipt["launch_id"], repeated["launch_id"])
            self.assertEqual(1, self.starts)
            for _ in range(12):
                if view.get("done"):
                    break
                command = actions.named(view, self.workspace, self.run_dir)
                if view["needs"]["kind"] == "answer":
                    decisions = actions.decisions(view, command)
                    self.assertTrue(decisions)
                    self.assertTrue(all(row.key.startswith("requirements-review-") for row in decisions), view["needs"])
                    command = actions.fill(view, command, {row.key: "plan" for row in decisions})
                else:
                    self.assertIn(view["needs"]["kind"], ("continue", "approve_plan"), view["needs"])
                argv = actions.worker_transport(view["execution"], command.argv, self.workspace, self.run_dir)
                with patch.dict(os.environ, command.env):
                    response = self.public(argv)
                self.assertTrue(response["ready"], response)
                # Same inspected action and private authority cannot produce another invocation.
                count = self.starts
                with patch.dict(os.environ, command.env):
                    self.assertEqual(response["launch_id"], self.public(argv)["launch_id"])
                self.assertEqual(count, self.starts)
                view = self.status()
            self.assertTrue(view["done"], view)
            self.assertTrue(view["delivery"]["verified_complete"])
            self.assertEqual(1, sum(row.get("stage") == "terra" for row in view["usage"]["accounting"]["attempts"]))
            self.assertEqual("owned", worker_view.execution(None)["mode"])
