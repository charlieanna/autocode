"""Controlled first-validation checks, never model/image qualification.

The fixture uses real Git, original kernel handles, protected journal chronology
and the actual atomic approval sink. Its preceding planning publication is an
explicit controlled fixture, not a native model or independent review receipt.
"""

import hashlib
import json
import os
import select
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, suppress
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import autocode_contract_identity as contracts
import autocode_native_launch as launch
import autocode_native_parent as parent
import autocode_process as processes
import autocode_source_scope as source_scope
import autocode_supervision as supervision
import autocode_util as util
import autocode_verification_copy as copies
import autocode_verify as verify
import autocode_visual_protection as protection

_FAILED_ATTEMPT_FIELDS = {
    "admission.json": (
        "schema",
        "command",
        "cwd",
        "output",
        "timeout_seconds",
        "deadline_monotonic",
        "supervision",
    ),
    "supervision.json": (
        "schema",
        "nonce",
        "owner",
        "keeper",
        "provider",
        "receipt",
        "started_at",
        "phase",
        "processes",
        "cause",
        "cleanup_error",
        "observed_at",
    ),
    "result.json": (
        "exit_code",
        "timed_out",
        "duration_seconds",
        "output_sha256",
        "supervision_sha256",
    ),
}
_FIXED_GIT = "/usr/bin/git" if sys.platform == "linux" else "/Library/Developer/CommandLineTools/usr/bin/git"
_FAILURE_LOG_LIMIT = 4 * 1024 * 1024
_FAILURE_TAIL_BYTES = 512

# Actual readonly checks need their native kernel backend and fixed Git. The
# disposable Linux qualification opts in explicitly; both native CI jobs reject
# skips in their selected classes. Merely importing this fixture launches nothing.
NATIVE_CHECK_HOST = (
    sys.platform == "darwin"
    and Path("/usr/bin/sandbox-exec").is_file()
    or sys.platform == "linux"
    and os.environ.get("AUTOCODE_NATIVE_LINUX_BOUNDARY_TEST") == "1"
    and Path("/usr/bin/bwrap").is_file()
) and Path(_FIXED_GIT).is_file()
requires_native_check_host = unittest.skipUnless(
    NATIVE_CHECK_HOST,
    "requires fixed Git and native Seatbelt or explicitly qualified Linux bubblewrap",
)


def _failed_check_attempt(run, attempt):
    """Bounded facts from one original fixture attempt; never a verdict or credential dump."""
    metadata = {}
    for name, keys in _FAILED_ATTEMPT_FIELDS.items():
        path = attempt / name
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            raw = stream.read(_FAILURE_LOG_LIMIT + 1)
        if len(raw) > _FAILURE_LOG_LIMIT:
            raise ValueError("Owned command metadata exceeds its existing receipt limit")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Owned command metadata is not an object")
        metadata[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "fields": {key: value[key] for key in keys if key in value},
        }
    admission = metadata.get("admission.json", {}).get("fields", {})
    command = admission.get("command")
    if isinstance(command, str) and command in {shlex.join([_FIXED_GIT, *args]) for args in copies.GIT_SETUP_ARGUMENTS}:
        output = admission.get("output")
        if not isinstance(output, str):
            raise ValueError("Fixed setup output is missing")
        path = Path(output)
        if (
            path.parent != attempt.parent.parent
            or not path.name.startswith("setup-")
            or path.suffix != ".log"
            or path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(run)
        ):
            raise ValueError("Fixed setup output is outside its original owned attempt")
        with path.open("rb") as stream:
            raw = stream.read(_FAILURE_LOG_LIMIT + 1)
        if len(raw) > _FAILURE_LOG_LIMIT:
            raise ValueError("Fixed setup output exceeds its diagnostic bound")
        result = metadata.get("result.json", {}).get("fields", {})
        if result and result.get("output_sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError("Fixed setup output differs from its original result")
        metadata["fixed_setup_output"] = {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "tail": raw[-_FAILURE_TAIL_BYTES:].decode("utf-8", "replace"),
        }
    return metadata


def _visible_pid(pid):
    """Fixture-only namespace translation from the kernel, never a run grant."""
    if sys.platform != "linux":
        return pid
    fields = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
    ids = [int(value) for value in fields["NSpid"].split()]
    if not ids or ids[0] != pid:
        raise AssertionError("Kernel namespace PID does not name the observed original process")
    return ids[-1]


def _ready_target(tree, ready):
    if sys.platform != "linux":
        return tree.known.get(ready["pid"])
    matches = []
    for row in tuple(tree.known.values()):
        try:
            if _visible_pid(row["pid"]) == ready["pid"] and processes.live_processes([row]):
                matches.append(row)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return matches[0] if len(matches) == 1 else None


def _fault_ready_command():
    script = (
        "from pathlib import Path; import json,os,signal; "
        "marker=Path(os.environ['TMPDIR'], 'actual-ready.tmp'); "
        "marker.write_text(json.dumps({'pid':os.getpid()})); "
        "marker.replace(marker.with_name('actual-ready.json')); "
        "signal.pause()"
    )
    return "python3 -c " + shlex.quote(script)


class _ControlledNativeCheckFixture:
    """Disposable original owner construction shared by tests and its child fixture.

    This is fixture setup only. No test method is called by another test.
    """

    def prepare(self, workspace=None):
        if workspace is None:
            temporary = tempfile.TemporaryDirectory(prefix="native-owned-check-unit-")
            self.addCleanup(temporary.cleanup)
            workspace = temporary.name
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".gitignore").write_text(".autocode/\nignored.txt\nfuture.txt\nunrelated.txt\n")
        (self.root / "tracked.txt").write_text("original source\n")
        (self.root / "tests.py").write_text(
            'import unittest\nfrom pathlib import Path\nclass SourceTest(unittest.TestCase):\n def test_selected_source(self):\n  self.assertEqual(Path("ignored.txt").read_text(), "selected ignored source\\n")\n'
        )
        (self.root / "empty_tests.py").write_text("import unittest\n")
        for args in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"],
        ):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        (self.root / "ignored.txt").write_text("selected ignored source\n")
        self.run = self.root / ".autocode" / "runs" / "owned"
        self.run.mkdir(parents=True)
        self.run_handle = (self.run / "writer.lock").open("a+")
        self.checkout_handle = (self.root / ".autocode" / "writer.lock").open("a+")
        self.addCleanup(self.run_handle.close)
        self.addCleanup(self.checkout_handle.close)
        self.raw = util.snapshot(self.root)
        self.context = launch.NativeRunContext(
            self.root,
            self.run,
            run_handle=self.run_handle,
            checkout_handle=self.checkout_handle,
            start_state={"goal_contract": None, "settings": {}, "stages": []},
            snapshot=self.raw,
        )
        self.factory, self.owner = self.context.factory, self.context.factory.protection
        self.original_origin_path = next(self.owner.root.iterdir())
        self.original_origin = self.original_origin_path.read_bytes()
        self.command = (
            "test \"$(cat ignored.txt)\" = 'selected ignored source' && test ! -e .autocode && printf '%s\\n' checked"
        )
        self.command = "sh -c " + shlex.quote(self.command)
        paths = ["ignored.txt", "future.txt"]
        initial = {
            "kind": "validate",
            "milestone_id": "M1",
            "objective": "Validate approved source",
            "requirements": ["Preserve scope"],
            "acceptance_criteria": ["C1"],
            "affected_paths": paths,
            "validation_plan": [self.command],
            "findings": [],
        }
        body = {
            "open_blocking_questions": [],
            "acceptance_criteria": [{"id": "C1"}],
            "constraints": ["VISUAL_SLICE_GATES=complete-cases-v1", 'VISUAL_CASE_CRITERIA={"only":["C1"]}'],
            "milestones": [{"id": "M1", "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": paths}],
            "initial_task": initial,
        }
        goal = {"task_id": "fixture", "revision": 1, "body": body}
        goal["hash"] = util.digest(goal)
        goal.update(origin="user_cli_edit", approval_status="draft", approval_event=None)
        self.token = contracts.token(goal)
        self.state = {
            "workspace": str(self.root),
            "goal_contract": goal,
            "settings": {
                "joint_planning": True,
                "milestone_checkpoints": {"enabled": True},
                "design_manifest": {"body": {"version": 1, "cases": [{"id": "only", "implementation_paths": paths}]}},
            },
            "current_task": None,
            "status": "AWAITING_GOAL_APPROVAL",
            "displayed_goal": self.token,
            "planning": {"final_token": self.token},
            "user_events": [],
            "findings_ledger": [],
        }
        self.anchor = self.run / "controlled-planning.txt"
        self.anchor.write_text("Controlled planning fixture; no native model receipt.\n")
        self.owner._append(
            "planning-committed",
            {
                "contract": deepcopy(goal),
                "settings": deepcopy(self.state["settings"]),
                "source": deepcopy(self.raw),
                "token": self.token,
                "pins": {str(self.anchor): util.file_hash(self.anchor)},
            },
        )
        self.sink = parent.ParentSink(
            self.run,
            factory=self.factory,
            ordinary_write=util.atomic_json,
            snapshot=lambda value: source_scope.snapshot(self.root, value),
        )

        if isinstance(self, unittest.TestCase):
            self.addCleanup(self._retain_failed_check_metadata)

    def _retain_failed_check_metadata(self):
        """Keep bounded original failure facts before disposable fixture cleanup.

        This is diagnostic only: no state, arbitrary check-log bodies, process
        queries or new authority. Every failure here leaves the original test
        failure unchanged, including failures while reporting diagnostics.
        """
        try:
            result = getattr(getattr(self, "_outcome", None), "result", None)
            if result is None or not any(
                test is self or getattr(test, "test_case", None) is self
                for test, _traceback in (*result.errors, *result.failures)
            ):
                return
            attempts = sorted(
                {
                    path
                    for pattern in (
                        "checks/*/command-supervision/*",
                        "check-replay/*/check-*/command-supervision/*",
                    )
                    for path in self.run.glob(pattern)
                    if path.is_dir() and not path.is_symlink() and path.resolve().is_relative_to(self.run)
                }
            )
            for attempt in attempts:
                metadata = _failed_check_attempt(self.run, attempt)
                print(
                    json.dumps(
                        {
                            "kind": "native-check-fixture-failure-metadata",
                            "test": self.id(),
                            "attempt": str(attempt.relative_to(self.run)),
                            "metadata": metadata,
                            "diagnostic_only": True,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
        except BaseException as error:
            # Reporting cannot replace the original failed assertion/exception.
            with suppress(BaseException):
                print(
                    json.dumps(
                        {
                            "kind": "native-check-fixture-failure-metadata-unavailable",
                            "test": self.id(),
                            "error_type": type(error).__name__,
                            "diagnostic_only": True,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

    def approve(self):
        handle = self.factory.prepare_initial_approval(self.state, self.token, snapshot=self.raw)
        candidate = deepcopy(self.state)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": self.token, "at": util.now()}
        candidate["user_events"].append(event)
        candidate["goal_contract"].update(approval_status="approved", approval_event=event)
        current = source_scope.snapshot(self.root, candidate)
        self.factory.initial_assignment_proof(handle, candidate, snapshot=current)
        decision = protection._json(handle.body)["decision"]
        candidate["current_task"] = {
            **deepcopy(decision["next_task"]),
            "id": "task-0123456789ab",
            "assigned_at": util.now(),
            "objective": decision["next_objective"],
            "affected_paths": decision["affected_paths"],
            "source_revision": current["revision"],
            "contract_hash": candidate["goal_contract"]["hash"],
            "contract_revision": 1,
            "decision": "CONTINUE",
        }
        with self.sink.publication(approval=handle):
            self.sink.write(self.run / "state.json", candidate)
        self.sink.publish(candidate, approval=handle)
        self.state = candidate
        return current

    def declare(self, methods):
        goal = self.state["goal_contract"]
        goal["body"]["initial_task"]["validation_plan"] = list(methods)
        goal["hash"] = util.digest({key: goal[key] for key in ("task_id", "revision", "body")})
        self.token = contracts.token(goal)
        self.state["displayed_goal"] = self.state["planning"]["final_token"] = self.token
        self.owner._append(
            "planning-committed",
            {
                "contract": deepcopy(goal),
                "settings": deepcopy(self.state["settings"]),
                "source": deepcopy(self.raw),
                "token": self.token,
                "pins": {str(self.anchor): util.file_hash(self.anchor)},
            },
        )

    def execution(self):
        import autocode_native_check_execution as checks

        return checks.NativeCheckExecution(self.context, self.state, persist=self.sink.write)

    def run_check(self, command, name="actual"):
        execution = self.execution()
        receipt = verify.scratch_run(
            self.root, self.run / "checks" / name, command=command, timeout=30, native_execution=execution
        )
        return execution, receipt


class NativeCheckFailureDiagnosticsTests(unittest.TestCase):
    """Failure-only projection is bounded; this does not launch an original check."""

    def test_fixed_git_setup_keeps_distinct_original_causes_and_bounded_result(self):
        with tempfile.TemporaryDirectory(prefix="native-check-diagnostic-unit-") as temporary:
            run = Path(temporary).resolve()
            causes = ("provider_stopped", "stage_deadline")
            for number, cause in enumerate(causes, 1):
                parent = run / "checks" / str(number)
                attempt = parent / "command-supervision" / "original"
                attempt.mkdir(parents=True)
                output = parent / f"setup-{number}.log"
                raw = b"prior private bytes\n" + b"x" * 600 + cause.encode()
                output.write_bytes(raw)
                command = shlex.join([_FIXED_GIT, *copies.GIT_SETUP_ARGUMENTS[0]])
                (attempt / "admission.json").write_text(json.dumps({"command": command, "output": str(output)}))
                (attempt / "supervision.json").write_text(json.dumps({"phase": "stopped", "cause": cause}))
                (attempt / "result.json").write_text(
                    json.dumps(
                        {
                            "exit_code": None,
                            "timed_out": cause == "stage_deadline",
                            "duration_seconds": 15.0,
                            "output_sha256": hashlib.sha256(raw).hexdigest(),
                            "supervision_sha256": "pinned original receipt",
                            "tail": "SECRET_FROM_UNTRUSTED_RESULT_TAIL",
                        }
                    )
                )
                facts = _failed_check_attempt(run, attempt)
                self.assertEqual(facts["supervision.json"]["fields"]["cause"], cause)
                self.assertEqual(facts["result.json"]["fields"]["timed_out"], cause == "stage_deadline")
                self.assertEqual(facts["fixed_setup_output"]["bytes"], len(raw))
                self.assertEqual(facts["fixed_setup_output"]["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertLessEqual(len(facts["fixed_setup_output"]["tail"].encode()), _FAILURE_TAIL_BYTES)
                self.assertNotIn("prior private bytes", json.dumps(facts))
                self.assertNotIn("SECRET_FROM_UNTRUSTED_RESULT_TAIL", json.dumps(facts))

    def test_arbitrary_check_output_and_result_tail_are_not_retained(self):
        with tempfile.TemporaryDirectory(prefix="native-check-diagnostic-unit-") as temporary:
            run = Path(temporary)
            parent = run / "checks" / "actual"
            attempt = parent / "command-supervision" / "original"
            attempt.mkdir(parents=True)
            output = parent / "setup-1.log"
            output.write_text("SECRET_FROM_ARBITRARY_CHECK")
            (attempt / "admission.json").write_text(
                json.dumps({"command": "python3 -c 'print(1)'", "output": str(output)})
            )
            (attempt / "result.json").write_text(
                json.dumps({"exit_code": None, "tail": "SECRET_FROM_RESULT", "environment": "SECRET_FROM_ENV"})
            )
            facts = _failed_check_attempt(run, attempt)
            self.assertNotIn("fixed_setup_output", facts)
            self.assertEqual(facts["result.json"]["fields"], {"exit_code": None})
            self.assertNotIn("SECRET_FROM_", json.dumps(facts))
            # Malformed admission fields and a broken diagnostic emitter cannot
            # replace a failure already recorded by unittest.
            (attempt / "admission.json").write_text(json.dumps({"command": ["unhashable"], "output": str(output)}))
            self.assertNotIn("fixed_setup_output", _failed_check_attempt(run, attempt))
            from types import SimpleNamespace

            probe = SimpleNamespace(run=run, id=lambda: "original_failed_test")
            probe._outcome = SimpleNamespace(result=SimpleNamespace(errors=[(probe, "original failure")], failures=[]))
            with patch("builtins.print", side_effect=OSError("diagnostic output unavailable")):
                _ControlledNativeCheckFixture._retain_failed_check_metadata(probe)


class NativeCheckExecutionTests(_ControlledNativeCheckFixture, unittest.TestCase):
    def setUp(self):
        self.prepare()

    @requires_native_check_host
    def test_original_initial_validate_runs_clean_check_without_builder(self):
        current = self.approve()
        import autocode_native_check_execution as checks

        execution = checks.NativeCheckExecution(self.context, self.state, persist=self.sink.write)
        receipt = verify.scratch_run(
            self.root, self.run / "checks" / "first", command=self.command, timeout=30, native_execution=execution
        )
        self.assertEqual(receipt["exit_code"], 0, receipt)
        self.assertFalse(receipt["timed_out"])
        self.assertIn("checked", receipt["tail"])
        self.assertEqual(execution.authenticate(receipt)["source"], current)
        rows = self.owner._rows()
        self.assertEqual(sum(row["kind"] == "check-finished" for row in rows), 6)
        prepared = [row["body"] for row in rows if row["kind"] == "check-prepared"]
        self.assertEqual(len(prepared), 6)
        native = copies.native_git_setup(prepared[0]["boundary"]["scratch"])
        for row, command in zip(prepared[:5], native["commands"], strict=False):
            self.assertTrue(row["boundary"]["setup"])
            self.assertEqual(row["command"][-1], shlex.join(command))
            self.assertEqual(row["environment_sha256"], util.digest(native["environment"]))
            self.assertEqual(row["boundary"]["executables"][native["executable"]], native["sha256"])
            self.assertIn(native["executable"], row["boundary"]["runtime_roots"])
        self.assertFalse(prepared[-1]["boundary"]["setup"])
        self.assertNotIn(native["executable"], prepared[-1]["boundary"]["runtime_roots"])
        self.assertNotIn(native["executable"], prepared[-1]["boundary"]["executables"])
        self.assertFalse(any(row["kind"] == "prepared" for row in rows))
        self.assertEqual(source_scope.snapshot(self.root, self.state), current)
        self.assertEqual(self.original_origin_path.read_bytes(), self.original_origin)
        self.assertNotIn("active_runner_check", self.state)

    def test_missing_original_approval_and_foreign_context_or_sink_refuse(self):
        from types import SimpleNamespace

        import autocode_native_check_execution as checks

        with self.assertRaises(util.Paused):
            self.execution()
        self.approve()
        with self.assertRaises(util.Paused):
            checks.NativeCheckExecution(SimpleNamespace(factory=self.factory), self.state, persist=self.sink.write)
        with self.assertRaises(util.Paused):
            checks.NativeCheckExecution(self.context, self.state, persist=util.atomic_json)

    def test_task_contract_event_criteria_settings_and_head_changes_refuse(self):
        self.approve()
        execution = self.execution()
        original = deepcopy(self.state)
        changes = ["task", "contract", "event", "criteria", "settings", "kind", "owner"]
        for change in changes:
            self.state.clear()
            self.state.update(deepcopy(original))
            if change == "task":
                self.state["current_task"]["objective"] = "foreign"
            elif change == "contract":
                self.state["goal_contract"]["body"]["constraints"].append("foreign")
            elif change == "event":
                self.state["user_events"] = []
            elif change == "criteria":
                self.state["current_task"]["criterion_ids"] = ["FOREIGN"]
            elif change == "settings":
                self.state["settings"]["new_route"] = "foreign"
            elif change == "kind":
                self.state["current_task"]["kind"] = "implement"
            else:
                self.state["current_task"]["milestone_id"] = "M2"
            with self.subTest(change=change), self.assertRaises(util.Paused):
                execution.checkpoint()
        self.state.clear()
        self.state.update(original)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "new HEAD",
            ],
            check=True,
            capture_output=True,
        )
        with self.assertRaises(util.Paused):
            execution.checkpoint()

    def test_selected_ignored_create_edit_delete_hold_unrelated_ignored_is_excluded(self):
        self.approve()
        execution = self.execution()
        (self.root / "unrelated.txt").write_text("excluded")
        self.assertEqual(execution.checkpoint(), 0)
        for name, data in [("future.txt", b"created"), ("ignored.txt", b"changed"), ("ignored.txt", None)]:
            path = self.root / name
            original = path.read_bytes() if path.exists() else None
            try:
                if data is None:
                    path.unlink()
                else:
                    path.write_bytes(data)
                with self.subTest(name=name, data=data), self.assertRaises(util.Paused):
                    execution.checkpoint()
            finally:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)

    def test_runtime_planning_bytes_retained_outcome_and_missing_ack_hold(self):
        self.approve()
        execution = self.execution()
        original = self.anchor.read_bytes()
        self.anchor.write_text("changed")
        with self.assertRaises(util.Paused):
            execution.checkpoint()
        self.anchor.write_bytes(original)
        with patch.object(protection.history, "runtime_files", return_value={"foreign": "f" * 64}):
            with self.assertRaises(util.Paused):
                execution.checkpoint()
        row = next(row for row in self.owner._rows() if row["kind"] == "parent-commit-acknowledged")
        path = self.owner.root / (f"{row['sequence']:08d}-" + row["id"] + ".json")
        moved = self.run / "controlled-unavailable-ack.json"
        path.rename(moved)
        try:
            with self.assertRaises(util.Paused):
                execution.checkpoint()
        finally:
            moved.rename(path)
        outcomes = self.run / "native-parent-outcomes"
        outcomes.mkdir()
        (outcomes / "controlled.json").write_text('{"native_authority":"HELD"}')
        with self.assertRaises(util.Paused):
            execution.checkpoint()

    def test_unsupported_overlays_dependencies_and_identity_probe_refuse_before_setup(self):
        self.approve()
        execution = self.execution()
        import autocode_check_replay as replay
        import autocode_verification_copy as copies

        with patch.object(copies, "create") as create, patch.object(verify, "make_tree") as old_tree:
            for options in (
                {"patch": "x.patch"},
                {"files": {"x": "y"}},
                {"command": None},
                {"generated_unrecorded": True},
                {"dependencies_from": self.root.parent},
                {"source_paths": ["tracked.txt"]},
            ):
                with self.subTest(options=options), self.assertRaises(util.Paused):
                    verify.scratch_run(
                        self.root,
                        self.run / "checks" / "never",
                        **{"command": self.command, "native_execution": execution, **options},
                    )
            create.assert_not_called()
            old_tree.assert_not_called()
        probe = unittest.mock.Mock()
        with self.assertRaises(util.Paused):
            replay.replay(
                [],
                self.root,
                self.run,
                {"source_revision": source_scope.snapshot(self.root, self.state)["revision"]},
                verify.scratch_run,
                approved_state=self.state,
                native_execution=execution,
                execution_identity=probe,
            )
        probe.assert_not_called()

    @requires_native_check_host
    def test_real_per_test_results_remain_complete_and_source_bound(self):
        command = "python3 -m unittest -v tests"
        self.declare([command])
        current = self.approve()
        execution, receipt = self.run_check(command)
        self.assertEqual(receipt["exit_code"], 0, receipt)
        self.assertFalse(receipt["error"], receipt)
        self.assertTrue(receipt["results_expected"], receipt)
        self.assertTrue(verify.schedule.complete_results(receipt), receipt)
        results = receipt["results"]
        self.assertEqual(results["total"], 1)
        self.assertEqual(
            {name.replace("::", ".") for name in results["passed"]}, {"tests.SourceTest.test_selected_source"}
        )
        self.assertEqual(results["failed"], [])
        self.assertEqual(results["skipped"], [])
        self.assertIn("test_selected_source", Path(receipt["output"]).read_text())
        self.assertEqual(util.file_hash(Path(receipt["output"])), receipt["output_sha256"])
        self.assertEqual(execution.authenticate(receipt)["source"], current)
        self.assertEqual(source_scope.snapshot(self.root, self.state), current)

    @requires_native_check_host
    def test_approved_go_copy_instrumentation_matches_runner_and_preserves_refusals(self):
        # Original approval, native copy setup and result slots; no Go target is launched.
        cases = (
            ("/tools/go test .", ["/tools/go", "test", "-json", "."], False),
            (
                "'/a directory/go' test -run 'Test(A|B)[12]$' .",
                ["/a directory/go", "test", "-json", "-run", "Test(A|B)[12]$", "."],
                False,
            ),
            ("/tools/go test -json=false .", ["/tools/go", "test", "-json=false", "."], True),
            # #2503: literal env assignments are an approved form; the copy instruments the Go command after them.
            ("env GOWORK=off /tools/go test .", ["env", "GOWORK=off", "/tools/go", "test", "-json", "."], False),
        )
        env_command = "env -i GOWORK=off /tools/go test ."
        unknown_command = "/tools/not-go test ."
        self.declare([command for command, _, _ in cases] + [env_command, unknown_command])
        self.approve()
        execution = self.execution()
        for index, (command, expected_argv, disabled) in enumerate(cases):
            output = self.run / "checks" / f"go-instrument-{index}"
            with self.subTest(command=command), execution.copy(self.root, output, command=command) as tree:
                xml = execution.result_path(tree, output / "suite.xml")
                framework = verify.command_framework(command)
                self.assertEqual("go", framework.name)
                actual = execution.instrument(framework.name, command, xml, tree)
                self.assertEqual(expected_argv, shlex.split(actual))
                self.assertEqual(verify._with_results(framework, command, xml, tree), actual)
                if disabled:
                    printed = output / "not-a-go-execution.log"
                    printed.write_text(
                        "\n".join(
                            json.dumps(event)
                            for event in (
                                {"Action": "run", "Package": "fixture", "Test": "TestProbe"},
                                {"Action": "pass", "Package": "fixture", "Test": "TestProbe"},
                            )
                        )
                    )
                    receipt = {"command": actual, "output": str(printed), "exit_code": 0, "timed_out": False}
                    receipt["results"] = verify.per_test_results(framework, receipt, xml, tree=tree)
                    self.assertIsNone(receipt["results"])
                    self.assertFalse(verify.schedule.complete_results(receipt))
                for wrong_command, wrong_xml, wrong_tree in (
                    (actual + " --foreign", xml, tree),
                    (actual, xml.with_name("foreign.xml"), tree),
                    (actual, xml, tree.parent / "foreign-tree"),
                ):
                    with self.assertRaisesRegex(util.Paused, "foreign suite instrumentation"):
                        execution.instrument(framework.name, wrong_command, wrong_xml, wrong_tree)
        # An env option and an executable without a Go collection claim are refused before copy setup.
        self.assertIsNone(verify.command_framework(env_command))
        self.assertIsNone(verify.command_framework(unknown_command))
        with patch.object(copies, "create") as create:
            for command in (env_command, unknown_command):
                with (
                    self.subTest(unapproved=command),
                    self.assertRaisesRegex(util.Paused, "approved executable command"),
                ):
                    with execution.copy(self.root, self.run / "checks" / "never-go", command=command):
                        self.fail("Unapproved command entered an original copy")
            create.assert_not_called()
        self.assertEqual(0, execution.checkpoint())

    @requires_native_check_host
    def test_empty_suite_actual_exit_never_establishes_native_coverage(self):
        command = "python3 -m unittest -v empty_tests"
        self.declare([command])
        current = self.approve()
        execution, receipt = self.run_check(command)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertIn(receipt["exit_code"], (0, 5), receipt)
        self.assertTrue(receipt["results_expected"], receipt)
        self.assertEqual(receipt["results"]["total"], 0)
        self.assertEqual(receipt["results"]["passed"], [])
        self.assertFalse(verify.schedule.complete_results(receipt), receipt)
        self.assertEqual(verify.suite_health(receipt), "broken", receipt)
        output = Path(receipt["output"]).read_text()
        self.assertIn("Ran 0 tests", output)
        if receipt["exit_code"] == 0:
            self.assertIn("zero tests or incomplete", receipt["error"])
        else:
            # Python 3.14 reports real exit 5 for an empty unittest suite.
            # This branch proves nonacceptance, not an actual zero-exit rejection.
            self.assertEqual(receipt["exit_code"], 5)
            self.assertIn("NO TESTS RAN", output)
        print(
            f"Empty suite actual exit: {receipt['exit_code']}; "
            f"zero-exit rejection exercised: {receipt['exit_code'] == 0}"
        )
        self.assertEqual(execution.authenticate(receipt)["source"], current)
        self.assertEqual(source_scope.snapshot(self.root, self.state), current)

    @requires_native_check_host
    def test_native_replay_runs_each_approved_repetition_and_refuses_old_or_bare_receipt(self):
        import autocode_check_replay as replay

        self.declare(["Run `" + self.command + "` twice"])
        current = self.approve()
        execution = self.execution()
        result = replay.replay(
            [],
            self.root,
            self.run,
            {"source_revision": current["revision"]},
            verify.scratch_run,
            approved_state=self.state,
            timeout=30,
            native_execution=execution,
        )
        self.assertEqual(result["verdict"], replay.PASS)
        self.assertEqual(len(result["checks"]), 2)
        self.assertEqual(len({row["native_check"]["finished_id"] for row in result["checks"]}), 2)
        self.assertEqual(execution.checkpoint(), 2)
        old = deepcopy(result["checks"][0])
        with self.assertRaises(util.Paused):
            replay.replay(
                [],
                self.root,
                self.run,
                {"source_revision": current["revision"]},
                lambda *_args, **_kwargs: old,
                approved_state=self.state,
                timeout=30,
                native_execution=execution,
            )
        with self.assertRaises(util.Paused):
            execution.authenticate({"exit_code": 0, "timed_out": False})
        copied = self.execution()
        with self.assertRaises(util.Paused):
            copied.authenticate(old)

    @requires_native_check_host
    def test_original_source_private_git_and_copy_test_writes_are_kernel_denied(self):
        import json  # noqa: F401 - preserve existing import behavior

        (self.run / "private.txt").write_text("private authority")
        original = "Path(" + repr(str(self.root.parent)) + ") / " + repr(self.root.name)
        script = (
            "from pathlib import Path\nimport os\nroot=" + original + "\n"
            "reads=[root/'tracked.txt',root/'.git/config',root/'.autocode/runs/owned/state.json',root/'.autocode/runs/owned/private.txt',Path('/Library/Developer/CommandLineTools/usr/bin/git')]\n"
            "for path in reads:\n try: path.read_bytes()\n except OSError as e:\n  assert e.errno in (1,2,13),e\n else: raise AssertionError('private read: '+str(path))\n"
            "writes=[root/'tracked.txt',root/'.git/config',root/'.autocode/runs/owned/private.txt',Path('tracked.txt'),Path('tests.py'),Path('new-source.py')]\n"
            "for path in writes:\n try: path.write_text('unauthorized')\n except OSError as e:\n  assert e.errno in (1,2,13,30),e\n else: raise AssertionError('write: '+str(path))\n"
            "out=Path(os.environ['TMPDIR']);(out/'allowed').write_text('allowed')\n"
            "link=out/'escape';link.symlink_to(root/'tracked.txt')\n"
            "try: link.write_text('escaped')\nexcept OSError as e:\n assert e.errno in (1,2,13,30),e\nelse: raise AssertionError('symlink escape')\n"
            "print('kernel-denials-observed')\n"
        )
        command = "python3 -c " + shlex.quote(script)
        self.declare([command])
        current = self.approve()
        execution, receipt = self.run_check(command)
        self.assertEqual(receipt["exit_code"], 0, receipt)
        self.assertIn("kernel-denials-observed", receipt["tail"])
        self.assertEqual(execution.authenticate(receipt)["source"], current)

    @requires_native_check_host
    def test_durable_before_exec_failure_never_releases_setup_target(self):
        self.approve()
        execution = self.execution()
        with patch.object(self.sink, "ordinary_write", side_effect=OSError("controlled original sink failure")):
            with self.assertRaises((OSError, util.Paused)):
                verify.scratch_run(
                    self.root,
                    self.run / "checks" / "failed-sink",
                    command=self.command,
                    timeout=30,
                    native_execution=execution,
                )
        rows = self.owner._rows()
        self.assertEqual(sum(row["kind"] == "check-prepared" for row in rows), 1)
        self.assertFalse(any(row["kind"] in ("check-sealed", "check-released", "check-finished") for row in rows))
        self.assertFalse(
            any(
                any(path.iterdir())
                for path in (self.root / ".autocode").glob("tool-containment-*/scratch/verification/.git")
            )
        )

    @requires_native_check_host
    def test_actual_checkpoint_task_change_refuses_same_blocked_child(self):
        self.approve()
        execution = self.execution()
        ordinary = self.sink.ordinary_write

        def changed(path, value, **hooks):
            if value.get("active_runner_check"):
                value["current_task"]["objective"] = "changed during original admission"
            return ordinary(path, value, **hooks)

        with patch.object(self.sink, "ordinary_write", side_effect=changed):
            with self.assertRaises(util.Paused):
                verify.scratch_run(
                    self.root,
                    self.run / "checks" / "changed-admission",
                    command=self.command,
                    timeout=30,
                    native_execution=execution,
                )
        self.assertFalse(any(row["kind"] == "check-released" for row in self.owner._rows()))

    def test_provider_builder_tester_capture_and_completion_routes_remain_held(self):
        from types import SimpleNamespace

        import autocode_native_calls as calls

        self.approve()
        native = calls.PlanningCalls(
            SimpleNamespace(factory=self.factory),
            {
                "version": 1,
                "mode": "isolated-builtin-openai",
                "opencode_version": "1.18.33",
                "model": "openai/gpt-5.6",
                "reasoning_effort": "high",
                "max_requests": 1,
                "image_limits": {"width": 480, "height": 640, "pixels": 480 * 640, "bytes": 1024 * 1024},
                "sdk_path": ".autocode/sdk",
                "sdk_manifest_sha256": "a" * 64,
                "transport_identity_sha256": "b" * 64,
                "executable_sha256": "c" * 64,
            },
            initial_task="controlled",
            run_dir=self.run,
            workspace=self.root,
            write=util.atomic_json,
            snapshot=lambda value: source_scope.snapshot(self.root, value),
            planning_stages=("requirements_gather",),
            recognizer="recognize_workflow",
        )
        for stage in ("terra", "sol", "astra_checkpoint", "plan_finalize"):
            with self.subTest(stage=stage), self.assertRaises(util.Paused):
                native.guard(self.state, stage, allow_write=stage == "terra")
        with self.assertRaises(util.Paused):
            native.require_supported_action(SimpleNamespace(run_dir=self.run), self.state)

    @requires_native_check_host
    def test_pending_handle_foreign_replay_policy_drift_and_overlap_refuse_before_target(self):
        import autocode_command_supervision as command_supervision

        self.approve()
        execution = self.execution()
        handles = []
        prepare = self.owner.prepare_check

        def capture(*args, **kwargs):
            handle = prepare(*args, **kwargs)
            handles.append((handle, kwargs))
            return handle

        class ControlledBeforeTransport(Exception):
            pass

        with (
            patch.object(self.owner, "prepare_check", side_effect=capture),
            patch.object(command_supervision, "run", side_effect=ControlledBeforeTransport),
        ):
            with self.assertRaises(ControlledBeforeTransport):
                verify.scratch_run(
                    self.root,
                    self.run / "checks" / "pending",
                    command=self.command,
                    timeout=30,
                    native_execution=execution,
                )
        self.assertEqual(len(handles), 1)
        handle, arguments = handles[0]
        foreign = protection.PreparedCheck(handle.owner, handle.nonce, handle.row_id, handle.body)
        with self.assertRaises(util.Paused):
            self.owner.require_prepared_check(foreign)
        with self.assertRaises(util.Paused):
            self.owner.initial_validate_authority(self.state, snapshot=source_scope.snapshot(self.root, self.state))
        with self.assertRaises(util.Paused):
            self.owner.release_check(handle, object(), object(), object(), lambda *_: None)
        with self.assertRaises(util.Paused):
            self.owner.finish_check(
                handle, {"exit_code": 0, "timed_out": False}, after=source_scope.snapshot(self.root, self.state)
            )
        options = {"cwd": arguments["cwd"], "env": {**arguments["environment"], "FOREIGN": "1"}}
        with patch.object(launch.supervision, "launch") as transport:
            with self.assertRaises(util.Paused):
                with self.context.held_check(
                    handle,
                    command=arguments["command"],
                    environment=options["env"],
                    options=options,
                    receipt_path=self.run / "never.json",
                    timeout=30,
                ):
                    pass
            transport.assert_not_called()
        body = protection._json(handle.body)
        profile = Path(body["boundary"]["profile"])
        profile.chmod(0o600)
        profile.write_text(profile.read_text() + "\n(allow default)\n")
        with self.assertRaises(util.Paused):
            self.owner.require_prepared_check(handle)

    @requires_native_check_host
    def test_fixed_setup_rejects_alternate_argv_environment_and_installation_grants(self):
        self.approve()
        execution = self.execution()
        captured = []

        class ControlledBeforePreparation(Exception):
            pass

        def capture(*args, **kwargs):
            captured.append((args, deepcopy(kwargs)))
            raise ControlledBeforePreparation

        with patch.object(self.owner, "prepare_check", side_effect=capture):
            with self.assertRaises(ControlledBeforePreparation):
                verify.scratch_run(
                    self.root,
                    self.run / "checks" / "setup-inputs",
                    command=self.command,
                    timeout=30,
                    native_execution=execution,
                )
        self.assertEqual(len(captured), 1)
        arguments, original = captured[0]
        executable = copies.native_git_setup(Path(original["boundary"]["scratch"]))["executable"]
        mutations = ("argv", "environment", "template", "helper", "root", "executable")
        for change in mutations:
            options = deepcopy(original)
            if change == "argv":
                options["command"][-1] += " --bare"
            elif change == "environment":
                options["environment"]["DEVELOPER_DIR"] = "/Library/Developer"
            elif change == "template":
                options["environment"]["GIT_TEMPLATE_DIR"] = (
                    "/Library/Developer/CommandLineTools/usr/share/git-core/templates"
                )
            elif change == "helper":
                options["environment"]["GIT_EXEC_PATH"] = "/usr/bin"
            elif change == "root":
                options["boundary"]["runtime_roots"].append("/Library/Developer/CommandLineTools")
            else:
                options["boundary"]["executables"][executable] = "0" * 64
            # Invalid facts must fail in the original owner before a prepared
            # journal row, hence before any held process can be admitted.
            with (
                self.subTest(change=change),
                patch.object(self.owner, "_append", side_effect=AssertionError("invalid setup reached the journal")),
            ):
                with self.assertRaises(util.Paused):
                    self.owner.prepare_check(*arguments, **options)
        handle = self.owner.prepare_check(*arguments, **original)
        self.owner.require_prepared_check(handle)

    @requires_native_check_host
    def test_fixed_setup_rechecks_executable_and_empty_resources_before_transport(self):
        import autocode_command_supervision as command_supervision

        self.approve()
        execution = self.execution()
        captured = []
        original_prepare = self.owner.prepare_check

        class ControlledBeforeTransport(Exception):
            pass

        def capture(*args, **kwargs):
            handle = original_prepare(*args, **kwargs)
            captured.append((handle, deepcopy(kwargs)))
            return handle

        with (
            patch.object(self.owner, "prepare_check", side_effect=capture),
            patch.object(command_supervision, "run", side_effect=ControlledBeforeTransport),
        ):
            with self.assertRaises(ControlledBeforeTransport):
                verify.scratch_run(
                    self.root,
                    self.run / "checks" / "setup-freshness",
                    command=self.command,
                    timeout=30,
                    native_execution=execution,
                )
        handle, arguments = captured[0]
        empty = Path(arguments["boundary"]["scratch"]) / "native-git-empty"
        executable = Path(copies.native_git_setup(Path(arguments["boundary"]["scratch"]))["executable"])
        original_hash = util.file_hash

        def changed_hash(path):
            return "0" * 64 if Path(path) == executable else original_hash(path)

        def refused_before_transport():
            with patch.object(
                launch.supervision, "launch", side_effect=AssertionError("invalid setup reached transport")
            ) as transport:
                with self.assertRaises(util.Paused):
                    with self.context.held_check(
                        handle,
                        command=arguments["command"],
                        environment=arguments["environment"],
                        options={"cwd": arguments["cwd"], "env": arguments["environment"]},
                        receipt_path=self.run / "never-setup.json",
                        timeout=30,
                    ):
                        pass
                transport.assert_not_called()

        # Controlled changed-file hash, not a modification of installed Git.
        with patch.object(util, "file_hash", side_effect=changed_hash):
            refused_before_transport()
        empty.chmod(0o700)
        try:
            refused_before_transport()
        finally:
            empty.chmod(0o500)
        empty.chmod(0o700)
        (empty / "unexpected-hook").write_text("not empty")
        empty.chmod(0o500)
        try:
            refused_before_transport()
        finally:
            empty.chmod(0o700)
            (empty / "unexpected-hook").unlink()
            empty.chmod(0o500)
        original_empty = empty.with_name("retained-empty")
        # macOS renames a directory only when it is writable; the symlink
        # below must be the only difference, so the target keeps 0500.
        empty.chmod(0o700)
        empty.rename(original_empty)
        original_empty.chmod(0o500)
        empty.symlink_to(original_empty, target_is_directory=True)
        try:
            refused_before_transport()
        finally:
            empty.unlink()
            original_empty.chmod(0o700)
            original_empty.rename(empty)
            empty.chmod(0o500)
        self.owner.require_prepared_check(handle)
        self.assertFalse(any(row["kind"] == "check-released" for row in self.owner._rows()))

    @requires_native_check_host
    def test_failing_actual_approved_check_cannot_supply_replay_pass(self):
        import autocode_check_replay as replay

        command = "sh -c 'exit 7'"
        self.declare([command])
        current = self.approve()
        execution = self.execution()
        with self.assertRaisesRegex(ValueError, "exited 7"):
            replay.replay(
                [{"command": command, "exit_code": 0, "evidence_ref": "controlled"}],
                self.root,
                self.run,
                {"source_revision": current["revision"]},
                verify.scratch_run,
                approved_state=self.state,
                timeout=30,
                native_execution=execution,
            )
        finished = [row for row in self.owner._rows() if row["kind"] == "check-finished"]
        self.assertEqual(finished[-1]["body"]["result"]["exit_code"], 7)
        self.assertEqual(finished[-1]["body"]["after"], current)

    @requires_native_check_host
    def test_timeout_disposition_after_actual_owned_exit_never_becomes_native_finish(self):
        import autocode_command_supervision as command_supervision

        self.approve()
        execution = self.execution()
        actual = command_supervision.run

        def deadline_disposition(*args, **kwargs):
            result = actual(*args, **kwargs)
            return {**result, "exit_code": None, "timed_out": True}

        with patch.object(command_supervision, "run", side_effect=deadline_disposition):
            with self.assertRaises(util.Paused):
                verify.scratch_run(
                    self.root,
                    self.run / "checks" / "deadline",
                    command=self.command,
                    timeout=30,
                    native_execution=execution,
                )
        rows = self.owner._rows()
        self.assertTrue(any(row["kind"] == "check-released" for row in rows))
        self.assertFalse(any(row["kind"] == "check-finished" for row in rows))

    def test_replay_summary_must_name_exact_protected_selected_revision_before_setup(self):
        import autocode_check_replay as replay

        current = self.approve()
        execution = self.execution()
        self.assertNotEqual(self.raw["revision"], current["revision"])
        before = {path.name: path.read_bytes() for path in self.owner.root.iterdir()}
        target = unittest.mock.Mock()
        for value in (None, "", "f" * 64, self.raw["revision"]):
            with self.subTest(revision=value), self.assertRaisesRegex(util.Paused, "summary differs"):
                replay.replay(
                    [],
                    self.root,
                    self.run,
                    {"source_revision": value},
                    target,
                    approved_state=self.state,
                    timeout=30,
                    native_execution=execution,
                )
        target.assert_not_called()
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.owner.root.iterdir()})
        self.assertFalse((self.run / "check-replay").exists())

    def test_ordinary_collected_success_is_not_relabelled_by_later_cleanup_clock(self):
        from contextlib import contextmanager
        from types import SimpleNamespace

        import autocode_command_supervision as command_supervision

        real_launch = command_supervision.supervision.launch
        now = [0.0]

        @contextmanager
        def finish_after_caller_deadline(*args, **kwargs):
            with real_launch(*args, **kwargs) as child:
                yield child
            # Only the caller's test clock moves, after real owned cleanup.
            # The actual supervisor/keeper used their unchanged real deadline.
            now[0] = 100.0

        with (
            patch.object(command_supervision, "time", SimpleNamespace(monotonic=lambda: now[0])),
            patch.object(command_supervision.supervision, "launch", side_effect=finish_after_caller_deadline),
        ):
            receipt = command_supervision.run(
                "printf '%s\\n' ordinary-collected",
                self.root,
                self.run / "ordinary.log",
                timeout=30,
                env={"PATH": "/usr/bin:/bin"},
            )
        self.assertEqual(receipt["exit_code"], 0, receipt)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertIn("ordinary-collected", receipt["tail"])
        self.assertTrue(command_supervision.receipts.completed(receipt))


@requires_native_check_host
class FixedNativeGitSetupFactsTests(unittest.TestCase):
    def test_untrusted_installed_executable_metadata_refuses_without_execution(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory(prefix="native-git-facts-") as temporary:
            scratch = Path(temporary).resolve()
            (scratch / "native-git-empty").mkdir(mode=0o500)
            facts = copies.native_git_setup(scratch)
            executable = Path(facts["executable"])
            actual_stat = Path.stat
            for uid, mode in ((os.getuid(), 0o755), (0, 0o775), (0, 0o777), (0, 0o644)):
                if uid == 0 and mode == 0o755:
                    uid = 1

                def changed(path, *args, **kwargs):
                    if path == executable:
                        return SimpleNamespace(st_uid=uid, st_mode=stat.S_IFREG | mode)
                    return actual_stat(path, *args, **kwargs)

                with self.subTest(uid=uid, mode=oct(mode)), patch.object(Path, "stat", changed):
                    with self.assertRaisesRegex(ValueError, "root-owned nonwritable"):
                        copies.native_git_setup(scratch)


@requires_native_check_host
class NativeCheckDeadlineTests(_ControlledNativeCheckFixture, unittest.TestCase):
    def setUp(self):
        self.prepare()

    def test_actual_released_check_is_terminated_at_controlled_waiter_deadline(self):
        command = _fault_ready_command()
        self.declare([command])
        before = self.approve()
        execution = self.execution()
        approval_pins = {path.name: util.file_hash(path) for path in self.owner.root.glob("*.json")}
        original_wait = processes.wait_for_stage
        original_time, original_threading = processes.time, processes.threading
        observed = {}
        errors = []
        workers = []

        class Clock:
            offset = 0.0

            def monotonic(clock):
                return time.monotonic() + clock.offset

            def __getattr__(clock, name):
                return getattr(time, name)

        clock = Clock()

        def wait(child, timeout, checkpoint, **kwargs):
            if execution.scope["copy"] is None:
                return original_wait(child, timeout, checkpoint, **kwargs)
            marker = execution.scope["scratch"] / "writable" / "actual-ready.json"

            class DeadlineTimer:
                def __init__(timer, interval, callback, args=(), kwargs=None):
                    self.assertEqual(interval, timeout)
                    self.assertGreater(interval, 0)
                    self.assertEqual(callback.__name__, "stop_at_deadline")
                    self.assertEqual(
                        args,
                        ({"kind": "stage", "reason": f"Stage exceeded its {timeout:g}-second hard runtime limit"},),
                    )
                    self.assertFalse(kwargs)
                    observed["registered_interval"] = interval
                    observed["registered_callback"] = callback.__name__
                    timer.cancelled = threading.Event()

                    def fire_after_actual_ready():
                        try:
                            while not timer.cancelled.is_set():
                                active = [
                                    item for item in tuple(execution.owner._active_checks.values()) if item[1] is child
                                ]
                                if marker.is_file() and active:
                                    handle, actual_child, tree = active[0]
                                    ready = json.loads(marker.read_text())
                                    target = _ready_target(tree, ready)
                                    if target is None:
                                        timer.cancelled.wait(0.02)
                                        continue
                                    self.assertTrue(processes.live_processes([target]))
                                    self.assertIs(actual_child, child)
                                    self.assertIsNone(child.poll())
                                    observed.update(
                                        marker=ready,
                                        target=deepcopy(target),
                                        handle=handle,
                                        tree=tree,
                                        child=child,
                                        metadata=deepcopy(child.supervision),
                                    )
                                    # Cross only the module-local waiter clock,
                                    # then call the real registered deadline callback.
                                    registered = clock.monotonic() + interval
                                    clock.offset += interval + 1
                                    self.assertGreater(clock.monotonic(), registered)
                                    callback(*args)
                                    observed["deadline_callback_returned"] = True
                                    return
                                timer.cancelled.wait(0.02)
                        except BaseException as error:
                            errors.append(error)

                    timer.worker = threading.Thread(target=fire_after_actual_ready, daemon=True)
                    workers.append(timer.worker)

                def start(timer):
                    timer.worker.start()

                def cancel(timer):
                    timer.cancelled.set()

            class Threads:
                def Timer(proxy, interval, callback, args=(), kwargs=None):
                    if callback.__name__ == "stop_at_deadline":
                        return DeadlineTimer(interval, callback, args, kwargs)
                    return threading.Timer(interval, callback, args=args, kwargs=kwargs)

                def __getattr__(proxy, name):
                    return getattr(threading, name)

            with patch.object(processes, "time", clock), patch.object(processes, "threading", Threads()):
                result = original_wait(child, timeout, checkpoint, **kwargs)
                observed["wait_result"] = result
                observed["collected_target_exit"] = child.returncode
                return result

        with patch.object(processes, "wait_for_stage", side_effect=wait):
            with self.assertRaises(util.Paused):
                verify.scratch_run(
                    self.root,
                    self.run / "checks" / "real-deadline",
                    command=command,
                    timeout=30,
                    native_execution=execution,
                )
        for worker in workers:
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
        self.assertFalse(errors, errors)
        self.assertIs(processes.time, original_time)
        self.assertIs(processes.threading, original_threading)
        self.assertTrue(observed["deadline_callback_returned"])
        self.assertGreater(observed["marker"]["pid"], 0)
        self.assertLess(observed["collected_target_exit"], 0)
        self.assertEqual(observed["wait_result"], (observed["collected_target_exit"], True))
        final = supervision.receipt(observed["metadata"])
        self.assertEqual(final["phase"], "stopped", final)
        self.assertIn(final["cause"], ("provider_stopped", "controller_finished"))
        self.assertFalse(final.get("cleanup_error"), final)
        result = util.read_object(Path(observed["metadata"]["receipt"]).with_name("result.json"))
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])
        rows = self.owner._rows()
        prepared_id = observed["handle"].row_id
        self.assertEqual(sum(row["kind"] == "check-finished" for row in rows), 5)
        self.assertTrue(
            any(row["kind"] == "check-released" and row["body"]["prepared_id"] == prepared_id for row in rows)
        )
        self.assertFalse(
            any(row["kind"] == "check-finished" and row["body"]["prepared_id"] == prepared_id for row in rows)
        )
        self.assertEqual(
            [],
            processes.live_processes(
                [
                    observed["target"],
                    observed["metadata"]["provider"],
                    observed["metadata"]["keeper"],
                    *observed["tree"].known.values(),
                    *final["processes"],
                ]
            ),
        )
        self.assertEqual(source_scope.snapshot(self.root, self.state), before)
        self.assertEqual(self.original_origin_path.read_bytes(), self.original_origin)
        self.assertEqual(approval_pins, {name: util.file_hash(self.owner.root / name) for name in approval_pins})
        with self.assertRaises(util.Paused):
            execution.checkpoint()


def _owner_loss_fixture(workspace):
    """An original disposable owner, not a nested invocation of a test method."""
    with ExitStack() as lifetime:
        fixture = _ControlledNativeCheckFixture()
        fixture.addCleanup = lifetime.callback
        fixture.prepare(workspace)
        command = _fault_ready_command()
        fixture.declare([command])
        before = fixture.approve()
        execution = fixture.execution()
        approval_pins = {path.name: util.file_hash(path) for path in fixture.owner.root.glob("*.json")}
        stopped = threading.Event()

        def announce_actual_ready():
            try:
                while not stopped.is_set():
                    for handle, child, tree in tuple(fixture.owner._active_checks.values()):
                        body = protection._json(handle.body)
                        if body["boundary"]["setup"]:
                            continue
                        marker = Path(body["boundary"]["scratch"]) / "writable" / "actual-ready.json"
                        if not marker.is_file():
                            continue
                        ready = json.loads(marker.read_text())
                        target = _ready_target(tree, ready)
                        if target is None:
                            continue
                        if not processes.live_processes([target]):
                            raise AssertionError("Released target ended before owner-loss readiness")
                        event = {
                            "kind": "actual-released-check-ready",
                            "metadata": deepcopy(child.supervision),
                            "target_marker": ready,
                            "target": deepcopy(target),
                            "prepared_id": handle.row_id,
                            "journal": str(fixture.owner.root),
                            "approval_pins": approval_pins,
                            "source": before,
                            "origin_path": str(fixture.original_origin_path),
                            "origin_sha256": util.file_hash(fixture.original_origin_path),
                            "processes": list(tree.known.values()),
                        }
                        print(json.dumps(event), flush=True)
                        return
                    stopped.wait(0.02)
            except BaseException as error:
                print(json.dumps({"kind": "fixture-observer-error", "error": repr(error)}), flush=True)

        observer = threading.Thread(target=announce_actual_ready, daemon=True)
        observer.start()
        try:
            verify.scratch_run(
                fixture.root,
                fixture.run / "checks" / "owner-loss",
                command=command,
                timeout=30,
                native_execution=execution,
            )
            raise AssertionError("Owner-loss fixture unexpectedly completed")
        finally:
            stopped.set()
            observer.join(timeout=1)


@requires_native_check_host
class NativeCheckOwnerLossTests(unittest.TestCase):
    def test_actual_owner_loss_stops_released_check_without_final_receipt(self):
        import autocode_source_snapshot as snapshots

        with tempfile.TemporaryDirectory(prefix="native-check-owner-loss-") as temporary:
            root = Path(temporary).resolve()
            workspace = root / "workspace"
            script = (
                "import sys; sys.path[:0]="
                + repr([str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / "tools")])
                + "; "
                "from tests.test_native_check_execution import _owner_loss_fixture; "
                "_owner_loss_fixture(" + repr(str(workspace)) + ")"
            )
            with (root / "owner.stderr").open("wb") as stderr:
                owner = subprocess.Popen(
                    [sys.executable, "-u", "-B", "-c", script],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    start_new_session=True,
                    close_fds=True,
                )
                tree = processes.ProcessTree(owner.pid, lambda _rows: None)
                tree.capture_root()
                try:
                    pending = bytearray()
                    deadline = time.monotonic() + 300
                    while b"\n" not in pending:
                        remaining = deadline - time.monotonic()
                        self.assertGreater(remaining, 0, "Original check never reached readiness")
                        self.assertTrue(select.select([owner.stdout], [], [], remaining)[0])
                        chunk = os.read(owner.stdout.fileno(), 65536)
                        self.assertTrue(chunk, (root / "owner.stderr").read_text())
                        pending.extend(chunk)
                    event = json.loads(bytes(pending).split(b"\n", 1)[0])
                    self.assertEqual(event["kind"], "actual-released-check-ready", event)
                    metadata = event["metadata"]
                    self.assertEqual(metadata["owner"]["pid"], owner.pid)
                    self.assertTrue(processes.matches(metadata["owner"], tree.capture_root()))
                    self.assertGreater(event["target_marker"]["pid"], 0)
                    self.assertEqual(event["target_marker"]["pid"], _visible_pid(event["target"]["pid"]))
                    retained = [
                        metadata["owner"],
                        metadata["keeper"],
                        metadata["provider"],
                        event["target"],
                        *event["processes"],
                    ]
                    for row in retained:
                        tree.known[row["pid"]] = row
                    self.assertTrue(processes.live_processes([metadata["owner"]]))
                    self.assertTrue(processes.live_processes([metadata["provider"]]))
                    self.assertTrue(processes.live_processes([metadata["keeper"]]))
                    self.assertTrue(processes.live_processes([event["target"]]))
                    # Kill only our actual owner Popen; never the target/keeper.
                    owner.kill()
                    self.assertEqual(owner.wait(timeout=10), -signal.SIGKILL)
                    deadline = time.monotonic() + 20
                    final = None
                    while time.monotonic() < deadline:
                        final = supervision.receipt(metadata)
                        if final and final.get("phase") in ("stopped", "uncertain", "discharged"):
                            for row in final["processes"]:
                                tree.known[row["pid"]] = row
                            if not processes.live_processes(list(tree.known.values())):
                                break
                        threading.Event().wait(0.02)
                    self.assertIsNotNone(final)
                    self.assertEqual(final["cause"], "owner_lost", final)
                    self.assertEqual(final["phase"], "stopped", final)
                    self.assertFalse(final.get("cleanup_error"), final)
                    self.assertEqual([], processes.live_processes(list(tree.known.values())))
                    rows = [json.loads(path.read_text()) for path in sorted(Path(event["journal"]).glob("*.json"))]
                    self.assertEqual(sum(row["kind"] == "check-finished" for row in rows), 5)
                    self.assertTrue(any(row["kind"] == "approval-committed" for row in rows))
                    self.assertTrue(
                        any(
                            row["kind"] == "check-released" and row["body"]["prepared_id"] == event["prepared_id"]
                            for row in rows
                        )
                    )
                    self.assertFalse(
                        any(
                            row["kind"] == "check-finished" and row["body"]["prepared_id"] == event["prepared_id"]
                            for row in rows
                        )
                    )
                    self.assertFalse(Path(metadata["receipt"]).with_name("result.json").exists())
                    self.assertEqual(
                        snapshots.snapshot(workspace, paths=event["source"]["source_paths"]), event["source"]
                    )
                    self.assertEqual(util.file_hash(Path(event["origin_path"])), event["origin_sha256"])
                    self.assertEqual(
                        event["approval_pins"],
                        {name: util.file_hash(Path(event["journal"]) / name) for name in event["approval_pins"]},
                    )
                finally:
                    # Fixture failure still retains/stops exactly this owned tree.
                    if owner.poll() is None:
                        tree.sample(notify=False)
                    tree.stop(owner)
                    owner.wait(timeout=10)
                    owner.stdout.close()


if __name__ == "__main__":
    unittest.main()
