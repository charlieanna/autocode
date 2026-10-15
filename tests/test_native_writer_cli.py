"""Direct run_role boundary controls, not native/provider qualification.

Original approval, journals, normalized ParentSink and writer preparation are
real. Provider preparation/kernel conformance is controlled. The actual final
provider_launch.verify_containment refuses the nonconcrete isolation owner;
no held target or model request is permitted. Ordinary/read-only call order
also reaches that original CLI join.
"""

import unittest
from contextlib import ExitStack
from copy import deepcopy  # noqa: F401 - preserve existing import behavior
from pathlib import Path  # noqa: F401 - preserve existing import behavior
from types import SimpleNamespace  # noqa: F401 - preserve existing import behavior
from unittest.mock import Mock, patch

import autocode as runner
import autocode_native_calls as calls
import autocode_util as util

import tests.test_native_writer_execution as fixtures


class DirectWriterCliControls(unittest.TestCase):
    setUp = fixtures.OriginalWriterTransportControls.setUp
    prepare = fixtures.OriginalWriterTransportControls.prepare
    acknowledge = fixtures.OriginalWriterTransportControls.acknowledge
    commit = fixtures.OriginalWriterTransportControls.commit

    def caller(self):
        self.state.update(iteration=1, next_stage="terra", version=3, task="complete original controlled input")
        native = object.__new__(calls.PlanningCalls)
        native.context, native.sink = self.context, self.sink
        native.workspace, native.run_dir = self.root, self.run
        native.initial_task = self.state["task"]
        native.snapshot = lambda state: self.before
        native.role_isolation = Mock(return_value=self.isolation)
        native._captures, native._captures_source = None, None
        schema = self.run / "controlled-schema.json"
        util.atomic_json(schema, {"type": "object"})
        return native, schema

    def invoke(self, native, schema, worker, order, *, allow_write=True):
        original_verify = runner.provider_launch.verify_containment

        def verify(*args, **kwargs):
            order.append("verify")
            return original_verify(*args, **kwargs)

        with ExitStack() as stack:
            # These unrelated setup seams do not authenticate native authority.
            for module, name in (
                (runner, "timeout_recovery_guard"),
                (runner.dispatch, "enforce_cross_model_verification"),
                (runner.goals, "execution_guard"),
                (runner.processes, "preflight"),
                (runner.task_preflight, "guard"),
                (runner.readonly_events, "prepare_opencode_snapshots"),
                (runner.job_source, "capture"),
            ):
                stack.enter_context(patch.object(module, name))
            stack.enter_context(
                patch.object(
                    runner.provider_launch, "prepare", return_value=(self.command, self.environment, {}, worker)
                )
            )
            stack.enter_context(patch.object(runner.provider_launch, "stage_record", return_value={}))
            stack.enter_context(
                patch.object(runner.opencode, "prompt_for_schema", side_effect=lambda prompt, *args: prompt)
            )
            stack.enter_context(
                patch.object(
                    runner.provider_launch, "containment_prompt", side_effect=lambda prompt, *args, **kw: prompt
                )
            )
            stack.enter_context(patch.object(runner.provider_launch, "verify_containment", side_effect=verify))
            held = stack.enter_context(
                patch.object(self.context, "held_launch", side_effect=AssertionError("target forbidden"))
            )
            regular = stack.enter_context(
                patch.object(runner.supervision, "launch", side_effect=AssertionError("target forbidden"))
            )
            stack.enter_context(
                patch.object(
                    runner.resolver_recovery,
                    "admit_dispatch",
                    side_effect=lambda *args, **kwargs: (
                        order.append("ordinary-admission")
                        or (_ for _ in ()).throw(util.Paused("CONTROLLED", "stop before target admission"))
                    ),
                )
            )
            try:
                runner.run_role(
                    role="terra",
                    prompt="Controlled setup",
                    sandbox="workspace-write" if allow_write else "read-only",
                    workspace=self.root,
                    run_dir=self.run,
                    state=self.state,
                    schema=schema,
                    model="controlled/model",
                    allow_write=allow_write,
                    dry_run=False,
                    persist=self.sink.write,
                    native=native,
                )
            finally:
                held.assert_not_called()
                regular.assert_not_called()

    def test_actual_final_provider_verifier_refuses_after_real_writer_preparation(self):
        native, schema = self.caller()
        order = []
        worker = {
            "tool_containment": self.boundary,
            "native_role_isolation": self.descriptor,
            "command": self.command,
            "environment": self.environment,
        }
        actual_prepare = self.factory.prepare_launch

        def prepare(*args, **kwargs):
            order.append("prepare")
            return actual_prepare(*args, **kwargs)

        with (
            patch.object(self.factory, "prepare_launch", side_effect=prepare),
            self.assertRaisesRegex(util.Paused, "Missing exact finalized native role isolation owner"),
        ):
            self.invoke(native, schema, worker, order)
        self.assertEqual(order, ["prepare", "verify"])
        rows = self.owner._rows()
        prepared = [row for row in rows if row["kind"] == "prepared"]
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["body"]["record_identity"]["stage"], "terra")
        self.assertFalse(any(row["kind"] in ("sealed", "released", "finished") for row in rows))
        self.assertNotIn("active_stage", self.state)
        self.assertEqual((self.root / "ignored.txt").read_text(), "selected ignored source\n")
        self.assertIsNone(next(iter(self.owner._writer_executions.values())).admission)

    def test_readonly_native_keeps_final_verify_before_preparation(self):
        native, schema = self.caller()
        order = []
        native.guard = Mock()
        native.dispatch_guard = Mock()
        worker = {
            "tool_containment": {**self.boundary, "allow_write": False, "write_paths": [], "writer_path_kinds": {}},
            "native_role_isolation": self.descriptor,
            "command": self.command,
            "environment": self.environment,
        }
        with (
            patch.object(
                self.factory, "prepare_launch", side_effect=AssertionError("prepare before verify")
            ) as prepare,
            self.assertRaisesRegex(util.Paused, "Missing exact finalized native role isolation owner"),
        ):
            self.invoke(native, schema, worker, order, allow_write=False)
        self.assertEqual(order, ["verify"])
        prepare.assert_not_called()
        self.assertFalse(any(row["kind"] == "prepared" for row in self.owner._rows()))

    def test_ordinary_route_keeps_final_verify_before_ordinary_admission(self):
        native, schema = self.caller()
        order = []
        self.state["version"] = 2
        worker = {"tool_containment": None, "command": self.command, "environment": self.environment}
        with self.assertRaisesRegex(util.Paused, "stop before target admission"):
            self.invoke(None, schema, worker, order)
        self.assertEqual(order, ["verify", "ordinary-admission"])
        self.assertFalse(any(row["kind"] == "prepared" for row in self.owner._rows()))
        self.assertNotIn("active_stage", self.state)


if __name__ == "__main__":
    unittest.main()
