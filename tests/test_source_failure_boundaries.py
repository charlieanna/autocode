"""Source observation failures keep their type across operational boundaries."""

import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_command_receipt as receipts
import autocode_preparation_source_failure as source_failure
import autocode_provider_launch as launch
import autocode_result_application as application
import autocode_tool_admission as admission
import autocode_util as util
import autocode_verification_copy as copies
import autocode_verification_preparation as preparation
import autocode_verification_prepare_worker as worker
from providers.kilocode import KiloProvider

from .test_verification_preparation import PublicationFixture

PUBLICATION = "autocode-source-refusal-sha256="


class SourceFailureBoundaryTests(unittest.TestCase):
    def error(self):
        return util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Required source read failed")

    def test_source_failure_is_never_a_rejected_model_report(self):
        error = self.error()
        with self.assertRaises(util.Paused) as raised:
            application.raise_if_not_rejected(error)
        self.assertIs(error, raised.exception)
        application.raise_if_not_rejected(util.Paused("PAUSED_INVALID_OUTPUT", "Invalid model report"))

    def test_source_failure_is_never_a_broken_backend_boundary(self):
        for options in ({}, {"kilo_readonly": True}, {"isolation": object()}):
            with self.subTest(options=options):
                error = self.error()
                self.assertIs(error, admission.failure(error, **options))

    def test_native_copy_source_query_retains_its_actual_failure(self):
        original = subprocess.CalledProcessError(128, ["/usr/bin/git"], stderr=b"source read denied")
        with tempfile.TemporaryDirectory() as folder, patch.object(subprocess, "run", side_effect=original):
            try:
                copies._retain_history(Path(folder), Path(folder), "a" * 40, {}, deadline=time.monotonic() + 30)
            except Exception as error:
                self.assertIsInstance(error, util.Paused)
                self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", error.status)
                self.assertIn("source read denied", str(error))
            else:
                self.fail("A failed source query cannot establish the native copy")

    def test_readiness_and_provider_launch_keep_the_source_failure(self):
        from unittest.mock import Mock

        error = self.error()
        kilo = object.__new__(KiloProvider)
        kilo.NAME = "kilocode"
        kilo.check_tools = Mock(side_effect=error)
        kilo.launch = Mock(side_effect=error)
        routes = {"tester": {"engine": "opencode", "provider": "kilocode", "model": "fixture/tester"}}
        with patch.object(admission.role_transport, "groups", return_value=[(kilo, routes, None)]):
            with self.assertRaises(util.Paused) as raised:
                admission.selected_tools(SimpleNamespace(NAME="fixture"), {}, Path("/project"), Path("/run"))
        self.assertIs(error, raised.exception)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(util.Paused) as raised:
                launch.prepare(
                    engine="opencode",
                    adapter=kilo,
                    role="tester",
                    route_role="tester",
                    workspace=root,
                    run_dir=root / "run",
                    session=None,
                    model="fixture/tester",
                    effort="low",
                    allow_write=False,
                    planning=False,
                    report=root / "output.json",
                    schema=root / "schema.json",
                    prompt_file=root / "prompt.txt",
                    sandbox="read-only",
                    transport_args=[],
                    chatgpt=False,
                    provider="kilocode",
                    enforce_tool_boundary=False,
                )
        self.assertIs(error, raised.exception)

    def test_native_copy_cannot_offer_source_recovery_without_confirmed_cleanup(self):
        for cause in (subprocess.CalledProcessError(128, ["/usr/bin/git"]), OSError("cannot read source")):
            for cleanup in (True, False):
                with self.subTest(cause=type(cause).__name__, cleanup=cleanup), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder).resolve()
                    run = root / ".autocode/runs/test"
                    run.mkdir(parents=True)
                    error = self.error()
                    error.__cause__ = cause
                    with patch.object(copies, "create", side_effect=error):
                        if cleanup:
                            with self.assertRaises(util.Paused) as raised:
                                copies.allocate(root, run)
                            self.assertIs(error, raised.exception)
                            self.assertFalse(list(run.iterdir()))
                        else:
                            with patch.object(copies.shutil, "rmtree", side_effect=OSError("cleanup unconfirmed")):
                                try:
                                    copies.allocate(root, run)
                                except Exception as retained:
                                    self.assertFalse(util.source_unavailable(retained))
                                    if isinstance(cause, subprocess.CalledProcessError):
                                        self.assertIsInstance(retained, subprocess.CalledProcessError)
                                        self.assertFalse(retained.cleanup_confirmed)
                                        self.assertIn("cleanup unconfirmed", retained.cleanup_error)
                                    else:
                                        self.assertIsInstance(retained, receipts.OwnershipUncertain)
                                else:
                                    self.fail("Unconfirmed copy cleanup must retain the hold")
                            self.assertEqual(1, len(list(run.iterdir())))


class WorkerSourceFailureTests(unittest.TestCase):
    def test_capture_never_suppresses_unknown_custody_or_a_removal_failure(self):
        cases = [
            ("execute", receipts.OwnershipUncertain("unconfirmed")),
            ("execute", util.Paused("PAUSED_INVALID_OUTPUT", "report error")),
            ("remove", util.Paused("PAUSED_SOURCE_UNAVAILABLE", "remove failure")),
        ]
        for operation, error in cases:
            with (
                self.subTest(operation=operation, error=type(error).__name__),
                self.assertRaises(type(error)) as raised,
            ):
                with source_failure.capture({"operation": operation}):
                    raise error
            self.assertIs(error, raised.exception)

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return PublicationFixture(Path(temporary.name).resolve() / "obligations")

    def test_worker_publishes_a_bound_source_failure_instead_of_losing_it_in_a_traceback(self):
        fixture = self.fixture()
        fixture.prep.update(worker=str(Path(worker.__file__).resolve()), worker_sha256=util.file_hash(worker.__file__))
        util.atomic_json(fixture.prep_path, fixture.prep)
        fixture.request["preparation_admission"]["sha256"] = util.file_hash(fixture.prep_path)
        result = Path(fixture.request["result"])
        result.unlink()
        util.atomic_json(fixture.request_path, fixture.request)
        request_sha = util.file_hash(fixture.request_path)
        output = StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [str(worker.__file__), "--request", str(fixture.request_path), "--request-sha256", request_sha],
            ),
            patch.object(
                worker.verify, "scratch_run", side_effect=util.Paused("PAUSED_SOURCE_UNAVAILABLE", "source read denied")
            ),
            redirect_stdout(output),
        ):
            try:
                worker.main()
            except util.Paused as error:
                self.fail(f"A collected worker must transport the source failure: {error}")
        answer = util.read(result)
        self.assertEqual(request_sha, answer["request_sha256"])
        self.assertEqual(fixture.prep["nonce"], answer["nonce"])
        self.assertEqual(
            {"status": "PAUSED_SOURCE_UNAVAILABLE", "reason": "source read denied"}, answer["source_refusal"]
        )
        self.assertNotIn("receipt", answer, "No test PASS was produced")
        self.assertEqual(PUBLICATION + util.file_hash(result), output.getvalue().strip())

    def launch(self, fault=None):
        fixture = self.fixture()

        def outer(command, cwd, output, *, timeout, env):
            request_path = Path(shlex.split(command)[3])
            request = util.read(request_path)
            answer = {
                "operation": "execute",
                "nonce": fixture.prep["nonce"],
                "request_sha256": util.file_hash(request_path),
                "source_refusal": {"status": "PAUSED_SOURCE_UNAVAILABLE", "reason": "source read denied"},
            }
            if fault == "nonce":
                answer["nonce"] = "unbound"
            elif fault == "status":
                answer["source_refusal"]["status"] = "COMPLETE"
            elif fault == "worker":
                fixture.worker.write_text("changed worker")
            elif fault == "cleanup":
                fixture.terminal["cleanup_error"] = "unknown cleanup"
            elif fault == "inner":
                fixture.result["supervision_sha256"] = "0" * 64
            util.atomic_json(request["result"], answer)
            digest = util.file_hash(request["result"])
            Path(output).write_text(PUBLICATION + ("0" * 64 if fault == "marker" else digest) + "\n")
            fixture.admission.update(command=command, cwd=str(cwd), output=str(output))
            util.atomic_json(fixture.path.with_name("admission.json"), fixture.admission)
            util.atomic_json(fixture.path, fixture.terminal)
            util.atomic_json(fixture.inner_path.with_name("result.json"), fixture.result)
            return {
                "command": command,
                "output": str(output),
                "output_sha256": util.file_hash(output),
                "exit_code": 0,
                "timed_out": False,
                "error": "",
                "supervision_errors": [],
                "supervision": fixture.metadata,
                "supervision_sha256": util.file_hash(fixture.path),
            }

        with (
            preparation.admitted(fixture.active["preparation_admission"]),
            patch.object(preparation.commands, "run", side_effect=outer),
        ):
            return preparation.launch("execute", fixture.root.parent, fixture.out, {}, timeout=30, env={})

    def test_parent_preserves_the_source_failure_after_authenticating_both_command_owners(self):
        try:
            self.launch()
        except Exception as error:
            self.assertIsInstance(error, util.Paused)
            self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", error.status)
            self.assertIn("source read denied", str(error))
        else:
            self.fail("The worker's negative result cannot grant test evidence")

    def test_unknown_or_changed_worker_evidence_stays_held(self):
        for fault in ("nonce", "status", "worker", "cleanup", "inner", "marker"):
            with self.subTest(fault=fault), self.assertRaises(receipts.OwnershipUncertain):
                self.launch(fault)
