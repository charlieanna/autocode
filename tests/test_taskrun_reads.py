"""One read-only coordination response reuses public observations, never action authority."""

import importlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_coordinate as coordinate
import autocode_taskrun as tasks


def reads():
    return importlib.import_module("autocode_taskrun_reads")


class ReadScopeTests(unittest.TestCase):
    def test_no_scope_does_not_retain_reads(self):
        load = Mock(side_effect=[{"revision": 1}, {"revision": 2}])
        self.assertEqual(1, reads().read("key", load)["revision"])
        self.assertEqual(2, reads().read("key", load)["revision"])

    def test_scope_reuses_identical_reads_and_returns_detached_values(self):
        load = Mock(return_value={"receipts": ["original"]})
        with reads().snapshot():
            first = reads().read("key", load)
            first["receipts"].append("edited")
            self.assertEqual({"receipts": ["original"]}, reads().read("key", load))
            reads().read("other", load)
        self.assertEqual(2, load.call_count)
        self.assertEqual({"receipts": ["original"]}, load.return_value)

    def test_new_and_nested_scopes_obtain_fresh_reads_and_restore_outer_scope(self):
        load = Mock(side_effect=[1, 2, 3, 4])
        with reads().snapshot():
            self.assertEqual(1, reads().read("key", load))
            with reads().snapshot():
                self.assertEqual(2, reads().read("key", load))
            self.assertEqual(1, reads().read("key", load))
        with reads().snapshot():
            self.assertEqual(3, reads().read("key", load))
        self.assertEqual(4, reads().read("key", load))

    def test_failed_read_and_failed_scope_leave_no_retained_result(self):
        load = Mock(side_effect=[ValueError("unavailable"), 2, 3])
        with self.assertRaisesRegex(RuntimeError, "scope failed"):
            with reads().snapshot():
                with self.assertRaisesRegex(ValueError, "unavailable"):
                    reads().read("key", load)
                self.assertEqual(2, reads().read("key", load))
                raise RuntimeError("scope failed")
        self.assertEqual(3, reads().read("key", load))


class TaskRunReadTests(unittest.TestCase):
    def setUp(self):
        self.run = tasks.TaskRun(Path("/workspace"), Path("/workspace/.autocode/runs/task"))
        self.document = {"view": {"status": "RUNNING"}, "interventions": {"pending_ids": ["owned"]}}
        self.reply = subprocess.CompletedProcess([], 0, json.dumps(self.document), "")

    def test_view_and_intervention_evidence_share_one_public_read_only_inside_scope(self):
        with patch.object(self.run, "_invoke", return_value=self.reply) as invoke:
            with reads().snapshot():
                self.assertEqual(self.document["view"], self.run.status())
                self.assertEqual(self.document, self.run.intervention_status())
                self.assertEqual(self.document["view"], self.run.status())
            invoke.assert_called_once_with("status", "--status")
            self.run.status()
            self.assertEqual(2, invoke.call_count)

    def test_evidence_inspection_and_execution_identity_do_not_share_reads(self):
        variants = [
            {"workspace": Path("/other")},
            {"run_dir": Path("/workspace/.autocode/runs/other")},
            {"command": ("other-autocode",)},
            {"env": {"AUTOCODE_HOME": "/other"}},
            {"cwd": Path("/other")},
            {"timeout": 20},
        ]
        with patch.object(tasks.TaskRun, "_invoke", return_value=self.reply) as invoke:
            with reads().snapshot():
                self.run.status()
                self.run.status(inspect_evidence=True)
                for variant in variants:
                    other = tasks.TaskRun(
                        variant.get("workspace", self.run.workspace),
                        variant.get("run_dir", self.run.run_dir),
                        **{k: v for k, v in variant.items() if k not in ("workspace", "run_dir")},
                    )
                    other.status()
                with patch.dict(os.environ, {"AUTOCODE_READ_TEST": "different"}):
                    self.run.status()
            self.assertEqual(9, invoke.call_count)

    def test_identical_new_handles_share_but_rejected_reads_do_not(self):
        other = tasks.TaskRun(self.run.workspace, self.run.run_dir)
        with patch.object(tasks.TaskRun, "_invoke", side_effect=[tasks.TaskRunError("refused"), self.reply]) as invoke:
            with reads().snapshot():
                with self.assertRaisesRegex(tasks.TaskRunError, "refused"):
                    self.run.status()
                self.assertEqual(self.document["view"], other.status())
                self.assertEqual(self.document, self.run.intervention_status())
        self.assertEqual(2, invoke.call_count)


class CoordinatorReadTests(unittest.TestCase):
    def test_status_reuses_reads_then_next_status_observes_changed_hold_and_accounting(self):
        anchor = "/source/.autocode/runs/task"
        member = {"target": "member", "workspace": "/member", "run_dir": "/member/run", "started": True, "base": "base"}
        document = {
            "id": "coordination",
            "anchor": anchor,
            "phase": "fixing",
            "revision": 1,
            "members": [member],
            "joint_attempts": [],
            "budget": {"source_run_dir": anchor},
        }
        current = {"cost": 1, "hold": False}
        calls = []

        def invoke(run, name, *args, **kwargs):
            self.assertEqual(("status", ("--status",), {}), (name, args, kwargs))
            calls.append(str(run.run_dir))
            return subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "view": {
                            "run_dir": str(run.run_dir),
                            "workspace": str(run.workspace),
                            "status": "RUNNING",
                            "cost": {"spent_usd": current["cost"], "complete": True},
                        },
                        "interventions": {"stop_intent": current["hold"]},
                    }
                ),
                "",
            )

        def choose(document, views, **kwargs):
            observed = coordinate.conversation_runtime.observation(coordinate.handle(member))
            return {"kind": "blocked" if observed["interventions"]["stop_intent"] else "watch"}

        with (
            patch.object(coordinate.store, "read", return_value=document),
            patch.object(coordinate.message_inbox, "read", return_value=[]),
            patch.object(coordinate, "next_step", side_effect=choose),
            patch.object(
                coordinate,
                "anchor_view",
                side_effect=lambda *a, **kw: tasks.TaskRun(Path("/source"), Path(anchor)).status(),
            ),
            patch.object(tasks.TaskRun, "_invoke", autospec=True, side_effect=invoke),
        ):
            first = coordinate.status(anchor)
            self.assertEqual("watch", first["needs"]["kind"])
            self.assertEqual([member["run_dir"], anchor], calls)
            current.update(cost=2, hold=True)
            second = coordinate.status(anchor)
            self.assertEqual("blocked", second["needs"]["kind"])
            self.assertEqual([member["run_dir"], anchor] * 2, calls)
            self.assertEqual(4, sum(row["cost"]["spent_usd"] for row in second["accounting"]["runs"]))
            # Outside the read-only coordinator response, even the same handle reads afresh.
            coordinate.handle(member).intervention_status()
            self.assertEqual(5, len(calls))
