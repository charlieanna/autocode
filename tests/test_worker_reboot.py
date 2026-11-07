"""Pure C4 evidence-oracle and disposable-guest admission checks; no VM or provider."""

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest import mock

from tests import worker_reboot_guest as guest
from tests import worker_reboot_vm as vm


def snapshots():
    """One accepted Tester attempt after saved Builder edits, then an interrupted boot."""
    before = {
        "boot_id": "boot-before",
        "launch": {"ready": True, "dispatch_count": 1},
        "files": {"greet.py": "saved-source", "test_greet.py": "saved-test", "README.md": "saved-readme"},
        "provider_calls": "accepted-provider-calls",
        "view": {
            "status": "RUNNING",
            "done": False,
            "delivery": None,
            "needs": {"kind": "continue"},
            "next_command": "autocode --no-chat",
            "liveness": {"kind": "supervised"},
            "controller": {"kind": "running", "checked": True, "alive": True},
            "execution": {
                "boot_id": "boot-before",
                "launch_id": "accepted-launch",
                "controller_epoch": 3,
                "runtime": {"sha256": "same-runtime"},
                "inspection": {"boot_matches": True},
            },
            "routes": {"builder": {"engine": "fixture", "model": "offline-builder"}},
            "approvals": {"plan": "displayed-plan-token"},
            "usage": {
                "accounting": {
                    "attempts": [
                        {
                            "identity": "accepted-attempt",
                            "attempt_id": "tester-1",
                            "worker_attempt": 1,
                            "stage": "sol",
                            "events": 2,
                            "output": "iterations/tester-01.jsonl",
                            "model": "offline-tester",
                            "engine": "fixture",
                            "launch_route": "test-route",
                            "active": True,
                        }
                    ]
                }
            },
        },
    }
    after = deepcopy(before)
    after["boot_id"] = "boot-after"
    after["launch"] = {"ready": False, "reason": "host rebooted; inspect retained attempt", "dispatch_count": 1}
    after["status"] = {"stale": False}
    after["view"].update(
        status="PAUSED_PROCESS_CLEANUP",
        needs={"kind": "resume"},
        next_command="autocode --resume-paused",
        liveness={"kind": "stopped"},
        controller={"kind": "stopped", "checked": True, "alive": False},
    )
    after["view"]["execution"]["inspection"]["boot_matches"] = False
    return before, after


class RebootOracleTests(unittest.TestCase):
    def test_an_interrupted_recovery_retains_the_accepted_attempt_and_edits(self):
        before, after = snapshots()
        guest.check_reboot(before, after)

    def test_a_stale_retained_attempt_can_handoff_without_claiming_completion(self):
        before, after = snapshots()
        after["status"]["stale"] = True
        after["view"].update(status="RUNNING", needs={"kind": "continue"}, next_command=None)
        guest.check_reboot(before, after)

    def test_reboot_evidence_rejects_replay_or_lost_work(self):
        changes = (
            ("same boot", ("boot_id",), "boot-before"),
            ("wrong original boot", ("view", "execution", "boot_id"), "unrelated-boot"),
            ("ready old launch", ("launch", "ready"), True),
            ("missing reboot diagnosis", ("launch", "reason"), "worker is absent"),
            ("repeated dispatch", ("launch", "dispatch_count"), 2),
            ("old boot matches", ("view", "execution", "inspection", "boot_matches"), True),
            ("different launch", ("view", "execution", "launch_id"), "replacement-launch"),
            ("different controller epoch", ("view", "execution", "controller_epoch"), 4),
            ("uninspected owner", ("view", "controller", "checked"), False),
            ("live owner", ("view", "controller", "alive"), True),
            ("running owner", ("view", "controller", "kind"), "running"),
            ("busy owner", ("view", "controller", "kind"), "busy"),
            ("unknown owner", ("view", "controller", "kind"), "unknown"),
            ("claimed provider liveness", ("view", "liveness", "kind"), "supervised"),
            ("false completion", ("view", "done"), True),
            ("false delivery", ("view", "delivery"), {"verified_complete": True}),
            ("changed source edit", ("files", "greet.py"), "changed-source"),
            ("changed test edit", ("files", "test_greet.py"), "changed-test"),
            ("changed route", ("view", "routes", "builder", "model"), "different-builder"),
            ("changed approval", ("view", "approvals", "plan"), "different-plan-token"),
            ("changed runtime", ("view", "execution", "runtime", "sha256"), "different-runtime"),
            ("extra provider call", ("provider_calls",), "replayed-provider-calls"),
        )
        for label, path, value in changes:
            with self.subTest(label=label):
                before, after = snapshots()
                target = after
                # The baseline launch boot binding is checked against before, not after.
                if label == "wrong original boot":
                    target = before
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(AssertionError):
                    guest.check_reboot(before, after)

    def test_a_second_attempt_or_changed_accepted_attempt_is_not_retention(self):
        for replay in (True, False):
            with self.subTest(replay=replay):
                before, after = snapshots()
                attempts = after["view"]["usage"]["accounting"]["attempts"]
                if replay:
                    attempts.append({**attempts[0], "attempt_id": "tester-2"})
                else:
                    attempts[0]["events"] += 1
                with self.assertRaisesRegex(AssertionError, "retained attempt"):
                    guest.check_reboot(before, after)

    def test_read_only_inspection_needs_an_honest_recovery_or_retained_handoff(self):
        for status, need in (("RUNNING", "continue"), ("PAUSED_PROCESS_CLEANUP", "continue")):
            with self.subTest(status=status, need=need):
                before, after = snapshots()
                after["view"].update(status=status, needs={"kind": need})
                with self.assertRaisesRegex(AssertionError, "recovery boundary"):
                    guest.check_reboot(before, after)

    def test_a_paused_label_without_a_recovery_offer_is_not_a_pass(self):
        for needs in ({}, None):
            with self.subTest(needs=needs):
                before, after = snapshots()
                after["view"].update(needs=needs, next_command=None)
                with self.assertRaisesRegex(AssertionError, "recovery boundary"):
                    guest.check_reboot(before, after)

    def usage_snapshots(self):
        before, after = snapshots()
        completed = {
            **before["view"]["usage"]["accounting"]["attempts"][0],
            "identity": "finished-planner",
            "attempt_id": "planner-1",
            "stage": "astra_plan",
            "active": False,
            "finished_at": "2026-10-25T00:00:00Z",
            "tokens": {"input_tokens": 120, "output_tokens": 30, "cached_input_tokens": None},
            "requests": [
                {
                    "native_id": "fixture-request",
                    "tokens": {"input_tokens": 120, "output_tokens": 30},
                    "reported_cost_usd": 0.02,
                }
            ],
            "reported_cost_usd": 0.02,
            "historical_estimated_cost_usd": None,
            "duration_seconds": 0.4,
        }
        for sample in (before, after):
            sample["view"]["usage"]["accounting"]["attempts"].insert(0, deepcopy(completed))
        return before, after

    def test_completed_usage_is_retained_while_the_active_attempt_can_finalize(self):
        before, after = self.usage_snapshots()
        active = after["view"]["usage"]["accounting"]["attempts"][1]
        active.update(
            active=False,
            finished_at="2026-10-25T00:00:01Z",
            tokens={"input_tokens": 42},
            requests=[{"native_id": "active-finished-request"}],
            reported_cost_usd=0.01,
        )
        guest.check_reboot(before, after)

    def test_recorded_completed_usage_requests_and_costs_cannot_be_lost_or_changed(self):
        changes = (
            ("tokens", {"input_tokens": 0, "output_tokens": 30}),
            ("requests", []),
            (
                "requests",
                [
                    {
                        "native_id": "another-request",
                        "tokens": {"input_tokens": 120, "output_tokens": 30},
                        "reported_cost_usd": 0.02,
                    }
                ],
            ),
            ("reported_cost_usd", 0.0),
            ("duration_seconds", None),
        )
        for field, value in changes:
            with self.subTest(field=field):
                before, after = self.usage_snapshots()
                after["view"]["usage"]["accounting"]["attempts"][0][field] = value
                with self.assertRaisesRegex(AssertionError, "completed-attempt usage"):
                    guest.check_reboot(before, after)

    def test_attempt_identity_ignores_only_display_liveness(self):
        before, after = snapshots()
        after["view"]["usage"]["accounting"]["attempts"][0]["active"] = False
        self.assertEqual(guest.attempt_ids(before["view"]), guest.attempt_ids(after["view"]))
        guest.check_reboot(before, after)


class PreparationBudgetTests(unittest.TestCase):
    def test_actual_remaining_time_reserves_every_later_phase(self):
        assigned = vm.preparation_allowance(1320, 485)
        self.assertEqual(
            {"reboot_request": 45, "reboot": 300, "inspection": 135, "diagnostics": 45, "cleanup": 15},
            assigned["reserves"],
        )
        self.assertEqual(295, assigned["ssh_seconds"])
        self.assertEqual(280, assigned["guest_seconds"])
        self.assertEqual(835, assigned["ssh_seconds"] + sum(assigned["reserves"].values()))
        self.assertLess(vm.preparation_allowance(1320, 600)["guest_seconds"], assigned["guest_seconds"])

    def test_exhausted_budget_grants_no_preparation_call(self):
        for now in (820, 825, 1320, 1500):
            with self.subTest(now=now):
                assigned = vm.preparation_allowance(1320, now)
                self.assertEqual(0, assigned["guest_seconds"])
                self.assertEqual(0, assigned["ssh_seconds"])

    def test_diagnostic_window_is_bounded_and_strips_nested_authority(self):
        machine = mock.Mock()
        machine.phase.return_value = ["fixture", "diagnose"]
        machine.ssh.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "status": {"status": "RUNNING", "token": "secret-token"},
                    "launch": {"ready": False, "private_env": "secret-env"},
                    "secret": "secret-top",
                }
            ),
            stderr='C4_PROGRESS {"phase":"diagnose","token":"secret-trace"}\n',
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(vm.time, "monotonic", return_value=100):
            evidence = Path(directory)
            vm.public_diagnostics(machine, 200, evidence)
            report = (evidence / "prepare-diagnostics.json").read_text()
        self.assertNotIn("secret-", report)
        self.assertEqual(45, machine.ssh.call_args.kwargs["timeout"])
        machine.phase.assert_called_once_with("diagnose", budget_seconds=30)
        self.assertTrue(machine.ssh.call_args.args[0][-1].startswith("cd /opt/autocode && exec "))

    def test_diagnostics_never_uses_reserved_cleanup_or_retries_a_timeout(self):
        for deadline, attempted in ((130, False), (200, True)):
            with self.subTest(deadline=deadline):
                machine = mock.Mock()
                machine.phase.return_value = ["fixture", "diagnose"]
                machine.ssh.side_effect = subprocess.TimeoutExpired(
                    "fixture diagnose", 45, stderr=b'C4_PROGRESS {"phase":"diagnose","result":"timeout"}\n'
                )
                with (
                    tempfile.TemporaryDirectory() as directory,
                    mock.patch.object(vm.time, "monotonic", return_value=100),
                ):
                    vm.public_diagnostics(machine, deadline, Path(directory))
                    report = json.loads((Path(directory) / "prepare-diagnostics.json").read_text())
                self.assertEqual(int(attempted), machine.ssh.call_count)
                self.assertIn("unavailable", report["result"])

    def test_trace_retains_only_bounded_progress_not_prose_or_tokens(self):
        stream = "raw secret-prose\n" + "\n".join(
            "C4_PROGRESS " + json.dumps({"phase": str(n), "token": "secret-token", "operation": "x" * 300})
            for n in range(100)
        )
        rows = vm.trace_records(stream.encode())
        self.assertEqual(64, len(rows))
        self.assertEqual("99", rows[-1]["phase"])
        self.assertEqual(256, len(rows[-1]["operation"]))
        self.assertNotIn("secret-", json.dumps(rows))

    def test_vm_wait_caps_each_read_at_remaining_time(self):
        clock = {"now": 0.0}
        machine = vm.VM(Path("/fixture"), 22222, "instance", "outer")
        machine.child = mock.Mock(poll=mock.Mock(return_value=None))

        def read(args, **kwargs):
            self.assertLessEqual(kwargs["timeout"], 2 - clock["now"])
            clock["now"] += kwargs["timeout"]
            return subprocess.CompletedProcess(args, 255, stdout="", stderr="")

        with (
            mock.patch.object(vm.time, "monotonic", side_effect=lambda: clock["now"]),
            mock.patch.object(vm.time, "sleep", side_effect=lambda seconds: clock.update(now=clock["now"] + seconds)),
            mock.patch.object(machine, "ssh", side_effect=read) as ssh,
        ):
            with self.assertRaises(RuntimeError):
                machine.wait(["true"], seconds=2)
        self.assertEqual(1, ssh.call_count)


class GuestPreparationTests(unittest.TestCase):
    def test_one_guest_deadline_covers_every_command_without_resetting(self):
        clock = {"now": 100.0}
        budget = guest.Budget(20, clock=lambda: clock["now"])

        def command(args, **kwargs):
            clock["now"] += 8
            return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

        with mock.patch.object(guest.subprocess, "run", side_effect=command) as run:
            guest.run_bounded(["fixture", "one"], budget, env={}, operation="one")
            guest.run_bounded(["fixture", "two"], budget, env={}, operation="two")
            clock["now"] = 120
            with self.assertRaises(guest.PublicFailure):
                guest.run_bounded(["fixture", "three"], budget, env={}, operation="three")
        self.assertEqual([15, 12], [row.kwargs["timeout"] for row in run.call_args_list])
        self.assertEqual(2, run.call_count)

    def test_invalid_guest_budgets_cannot_authorize_commands(self):
        for seconds in (0, -1, float("inf"), float("nan")):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                guest.Budget(seconds)

    def test_progress_is_bounded_stderr_and_omits_task_authority_and_prose(self):
        budget = guest.Budget(30, clock=lambda: 0)
        view = {
            "status": "RUNNING",
            "next_stage": "terra",
            "needs": {"kind": "continue", "token": "secret-token"},
            "chat": {"messages": [{"text": "secret-prose"}]},
        }
        output, error = io.StringIO(), io.StringIO()
        progress = guest.Progress(budget)
        with redirect_stdout(output), redirect_stderr(error):
            progress.emit("status", view=view)
            progress.emit("status", view=view)
            for index in range(100):
                progress.emit("stage_" + str(index), view=view)
            progress.emit("failed", operation="prepare", result="TimeoutError", terminal=True)
        rows = error.getvalue().splitlines()
        self.assertEqual("", output.getvalue())
        self.assertEqual(64, len(rows))
        self.assertNotIn("secret-", error.getvalue())
        self.assertEqual(64, len(vm.trace_records(error.getvalue())))

    def test_lost_launch_reply_does_not_redispatch(self):
        from autocode_worker_view import PREFIX

        budget = guest.Budget(30, clock=lambda: 0)
        driver = guest.PublicDriver(budget, env={}, progress=mock.Mock())
        execution = {"id": "durable_local", "available": True, "next_command": list(PREFIX)}
        replies = [
            subprocess.CompletedProcess([], 0, stdout=json.dumps({"execution": {"modes": [execution]}}), stderr=""),
            subprocess.TimeoutExpired("worker launch", 15),
        ]
        with mock.patch.object(guest.subprocess, "run", side_effect=replies) as run:
            with self.assertRaises(guest.PublicFailure):
                driver.launch("fixed fixture brief", ())
        self.assertEqual(2, run.call_count)
        self.assertEqual(1, sum("launch" in call.args[0] for call in run.call_args_list))

    def test_failed_prepare_emits_exception_class_without_raw_message(self):
        output, error = io.StringIO(), io.StringIO()
        argv = ["guest", "prepare", "--instance", "fixture", "--outer-boot", "outer", "--budget-seconds", "30"]
        with (
            mock.patch.object(guest.sys, "argv", argv),
            mock.patch.object(guest, "require_guest"),
            mock.patch.object(guest, "prepare", side_effect=RuntimeError("secret-error-body")),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            with self.assertRaises(RuntimeError):
                guest.main()
        rows = vm.trace_records(error.getvalue())
        self.assertEqual("failed", rows[-1]["phase"])
        self.assertEqual("RuntimeError", rows[-1]["result"])
        self.assertNotIn("secret-error-body", error.getvalue())
        self.assertEqual("", output.getvalue())

    def test_dispatch_rejects_a_view_from_another_run_before_any_call(self):
        driver = guest.PublicDriver(guest.Budget(30, clock=lambda: 0), env={}, progress=mock.Mock())
        with mock.patch.object(driver, "call") as call:
            with self.assertRaises(guest.PublicFailure):
                driver.dispatch({"workspace": "/another/project", "run_dir": "/another/run"})
        call.assert_not_called()

    def test_diagnosis_uses_public_current_launch_without_a_saved_handle(self):
        launch_id = "a" * 32
        driver = mock.Mock()
        driver.status.return_value = {
            "status": "RUNNING",
            "execution": {"launch_id": launch_id},
            "needs": {"token": "secret-token"},
        }
        driver.call.return_value = {"launch_id": launch_id, "ready": False, "private_env": "secret-env"}
        with (
            mock.patch.object(guest, "PublicDriver", return_value=driver),
            mock.patch.object(guest, "handle", side_effect=AssertionError("private handle must not be required")),
        ):
            result = guest.diagnose(guest.Budget(30, clock=lambda: 0), progress=mock.Mock())
        driver.call.assert_called_once_with(["worker", "inspect", launch_id], "diagnose_inspect")
        self.assertNotIn("secret-", json.dumps(result))

    def test_unavailable_status_discovers_only_public_launches(self):
        driver = mock.Mock(
            workspace=Path("/fixture/project"), run_dir=Path("/fixture/project/.autocode/runs/literal-reboot")
        )
        driver.status.side_effect = guest.PublicFailure("status", "unavailable")
        driver.call.return_value = {"launches": [{"launch_id": "a" * 32, "ready": False, "token": "secret"}] * 7}
        with mock.patch.object(guest, "PublicDriver", return_value=driver):
            result = guest.diagnose(guest.Budget(30, clock=lambda: 0), progress=mock.Mock())
        self.assertEqual(4, len(result["launches"]))
        self.assertEqual("diagnose_launches", driver.call.call_args.args[1])
        self.assertEqual(["worker", "launches"], driver.call.call_args.args[0][:2])
        self.assertNotIn("secret", json.dumps(result))


class GuestAdmissionTests(unittest.TestCase):
    def admit(self, *, marker=None, product="guest-instance", boot="guest-boot", uid=1000, platform="linux"):
        if marker is None:
            marker = {"instance": "guest-instance", "purpose": "autocode-c4-disposable-guest"}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(guest, "MARKER", mock.Mock(read_text=lambda: json.dumps(marker))))
            stack.enter_context(mock.patch.object(guest, "PRODUCT", mock.Mock(read_text=lambda: product)))
            stack.enter_context(mock.patch.object(guest, "BOOT", mock.Mock(read_text=lambda: boot)))
            stack.enter_context(mock.patch.object(guest.os, "getuid", return_value=uid))
            stack.enter_context(mock.patch.object(guest.sys, "platform", platform))
            guest.require_guest("guest-instance", "outer-boot")

    def test_the_exact_marked_nonroot_guest_can_be_admitted(self):
        self.admit()

    def test_outer_host_and_mismatched_guest_identity_are_refused(self):
        changes = (
            {"boot": "outer-boot"},
            {"marker": {"instance": "another-instance", "purpose": "autocode-c4-disposable-guest"}},
            {"marker": {"instance": "guest-instance", "purpose": "ordinary-host"}},
            {"marker": {"instance": "guest-instance", "purpose": "autocode-c4-disposable-guest", "extra": True}},
            {"product": "another-instance"},
            {"uid": 0},
            {"platform": "darwin"},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "exact disposable QEMU guest"):
                self.admit(**change)


class CoordinatorSafetyTests(unittest.TestCase):
    def test_explicit_tcg_admission_does_not_require_kvm(self):
        expected = {
            "GITHUB_ACTIONS": "true",
            "RUNNER_OS": "Linux",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_TEMP": "/ci/temp",
            "GITHUB_WORKSPACE": "/ci/source",
        }
        with (
            mock.patch.dict(vm.os.environ, expected, clear=True),
            mock.patch.object(vm.sys, "platform", "linux"),
            mock.patch.object(vm.platform, "machine", return_value="x86_64"),
            mock.patch.object(vm.os, "getuid", return_value=1000),
            mock.patch.object(vm.os, "access", return_value=False),
        ):
            vm.require_ci()

    def test_admission_names_every_failed_predicate_and_does_not_dump_secrets(self):
        with (
            mock.patch.dict(vm.os.environ, {"UNRELATED_SECRET": "do-not-log-this"}, clear=True),
            mock.patch.object(vm.sys, "platform", "darwin"),
            mock.patch.object(vm.platform, "machine", return_value="arm64"),
            mock.patch.object(vm.os, "getuid", return_value=0),
        ):
            report = vm.admission_metadata()
        self.assertEqual(8, len(report["errors"]))
        self.assertIn("RUNNER_ENVIRONMENT must be github-hosted", report["errors"])
        self.assertIn("machine must be x86_64", report["errors"])
        self.assertIn("uid must be a nonroot Linux user", report["errors"])
        self.assertNotIn("do-not-log-this", json.dumps(report))
        self.assertEqual("tcg", report["accelerator"])
        self.assertIn("readable", report["kvm"])
        with self.assertRaisesRegex(RuntimeError, "RUNNER_ENVIRONMENT must be github-hosted"):
            vm.require_ci(report)

    def test_admission_only_refusal_cannot_start_commands_or_create_evidence(self):
        output = io.StringIO()
        with (
            mock.patch.dict(vm.os.environ, {}, clear=True),
            mock.patch.object(vm.sys, "argv", ["worker_reboot_vm.py", "--admission-only"]),
            mock.patch.object(vm.subprocess, "run") as run,
            mock.patch.object(vm.subprocess, "Popen") as start,
            mock.patch.object(vm.Path, "mkdir") as mkdir,
            redirect_stdout(output),
        ):
            with self.assertRaisesRegex(RuntimeError, "GITHUB_ACTIONS must be true"):
                vm.main()
        self.assertIn("GITHUB_ACTIONS must be true", json.loads(output.getvalue())["errors"])
        run.assert_not_called()
        start.assert_not_called()
        mkdir.assert_not_called()

    def test_standalone_admission_runs_without_site_packages_before_setup(self):
        source = Path(vm.__file__).resolve()
        reply = subprocess.run(
            [sys.executable, "-I", "-S", str(source), "--admission-only"],
            env={"GITHUB_ACTIONS": "false"},
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(1, reply.returncode)
        self.assertIn("GITHUB_ACTIONS must be true", json.loads(reply.stdout)["errors"])
        self.assertNotIn("ModuleNotFoundError", reply.stderr)

    def test_tcg_command_keeps_the_same_owned_guest_and_resource_bounds(self):
        argv = vm.qemu_command("qemu", "disk", "seed", "serial", 22222, "guest-instance")
        self.assertEqual("tcg,thread=multi", argv[argv.index("-accel") + 1])
        self.assertEqual("max", argv[argv.index("-cpu") + 1])
        self.assertEqual("2", argv[argv.index("-smp") + 1])
        self.assertEqual("2048", argv[argv.index("-m") + 1])
        self.assertIn("type=1,uuid=guest-instance", argv)
        self.assertIn("user,id=c4,hostfwd=tcp:127.0.0.1:22222-:22", argv)
        self.assertNotIn("-no-reboot", argv)

    def test_admission_artifact_does_not_collide_or_overwrite_a_prior_coordinator_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "image.qcow2"
            image.write_bytes(b"reviewed-test-image")
            early = root / ".scenario-runs/worker-reboot-admission"
            early.mkdir(parents=True)
            (early / "admission.json").write_text("retained early report")
            env = {"GITHUB_WORKSPACE": str(root), "RUNNER_TEMP": str(root)}
            clock = {"now": 0.0}

            def fail_command(*args, **kwargs):
                clock["now"] = 1.0
                raise OSError("stop before guest setup")

            with (
                mock.patch.dict(vm.os.environ, env, clear=True),
                mock.patch.object(vm.sys, "argv", ["coordinator", "--image", str(image)]),
                mock.patch.object(vm, "admission_metadata", return_value={"accelerator": "tcg", "errors": []}),
                mock.patch.object(vm, "IMAGE_SHA256", hashlib.sha256(image.read_bytes()).hexdigest()),
                mock.patch.object(vm.signal, "signal"),
                mock.patch.object(vm, "BOOT", mock.Mock(read_text=lambda: "outer-boot")),
                mock.patch.object(vm.socket, "socket") as socket,
                mock.patch.object(vm.subprocess, "run", side_effect=fail_command),
                mock.patch.object(vm.VM, "stop", side_effect=lambda: clock.update(now=2.0)),
                mock.patch.object(vm.subprocess, "Popen") as start,
            ):
                socket.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 22222)
                with mock.patch.object(vm.time, "monotonic", side_effect=lambda: clock["now"]):
                    with self.assertRaisesRegex(OSError, "stop before guest setup"):
                        vm.main()
                evidence = root / ".scenario-runs/worker-reboot"
                recorded = (evidence / "failure.json").read_bytes()
                phases = json.loads((evidence / "phase-durations.json").read_text())
                self.assertEqual({"coordinator_setup": 1.0, "cleanup": 1.0}, phases["phase_seconds"])
                self.assertEqual("failed", phases["outcome"])
                self.assertEqual("tcg", phases["accelerator"])
                self.assertEqual(2.0, phases["total_seconds"])
                with self.assertRaises(FileExistsError):
                    vm.main()
                self.assertEqual(recorded, (evidence / "failure.json").read_bytes())
                self.assertEqual("retained early report", (early / "admission.json").read_text())
                start.assert_not_called()

    def completed_fixture_with_cleanup(
        self, *, fails, prepare_error=False, elapsed_before_prepare=None, no_budget=False, invalid_baseline=False
    ):
        before, after = snapshots()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            private.mkdir()
            for name in ("host", "host.pub", "client", "client.pub"):
                (private / name).write_text("fixture SSH material")
            image = root / "image"
            image.write_bytes(b"reviewed fixture image")
            evidence = root / ".scenario-runs/worker-reboot"
            machine = mock.Mock()
            machine.ssh_options = []
            machine.phase.side_effect = lambda phase, **kwargs: ["fixture", phase]
            machine.child = None
            events = []
            clock = {"now": 0.0}

            def ssh(args, **kwargs):
                text, error = "", ""
                if args == ["cat", "/etc/autocode-reboot-product-uuid"]:
                    text = "guest-instance"
                elif args[0] == "cat":
                    text = "boot-before"
                elif args[-1].endswith("fixture prepare"):
                    events.append("prepare")
                    if prepare_error:
                        raise subprocess.TimeoutExpired(
                            "fixture prepare",
                            kwargs["timeout"],
                            stderr=b'C4_PROGRESS {"phase":"planning","token":"do-not-save"}\n',
                        )
                    text = "invalid JSON" if invalid_baseline else json.dumps(before)
                    error = 'C4_PROGRESS {"phase":"baseline","result":"ready"}\n'
                elif args[-1].endswith("fixture diagnose"):
                    events.append("diagnose")
                    text = json.dumps({"status": {"status": "RUNNING", "token": "do-not-save"}, "errors": []})
                elif args[-1].endswith("fixture reboot"):
                    events.append("reboot_request")
                    self.assertEqual(45.0, kwargs["timeout"])
                    clock["now"] += kwargs["timeout"]
                elif args[-1].endswith("fixture inspect"):
                    text = json.dumps(after)
                if elapsed_before_prepare is not None and args[0].endswith("/pip") and "--no-deps" in args:
                    clock["now"] = elapsed_before_prepare
                return subprocess.CompletedProcess(args, 0, stdout=text, stderr=error)

            def cleanup():
                events.append("cleanup")
                if prepare_error or no_budget or invalid_baseline:
                    self.assertTrue((evidence / "prepare-diagnostics.json").is_file())
                else:
                    self.assertTrue(
                        (evidence / "after-2.json").is_file(), "The control must reach all proof observations"
                    )
                self.assertFalse((evidence / "result.json").exists(), "PASS must wait for cleanup")
                if fails:
                    raise OSError("fixture cleanup failed")

            machine.ssh.side_effect = ssh
            machine.stop.side_effect = cleanup
            with (
                mock.patch.dict(vm.os.environ, {"GITHUB_WORKSPACE": str(root), "RUNNER_TEMP": str(root)}, clear=True),
                mock.patch.object(vm.sys, "argv", ["coordinator", "--image", str(image)]),
                mock.patch.object(vm, "admission_metadata", return_value={"accelerator": "tcg", "errors": []}),
                mock.patch.object(vm, "IMAGE_SHA256", hashlib.sha256(image.read_bytes()).hexdigest()),
                mock.patch.object(vm.signal, "signal"),
                mock.patch.object(vm, "BOOT", mock.Mock(read_text=lambda: "outer-boot")),
                mock.patch.object(vm.uuid, "uuid4", return_value="guest-instance"),
                mock.patch.object(vm.tempfile, "mkdtemp", return_value=str(private)),
                mock.patch.object(vm, "VM", return_value=machine),
                mock.patch.object(vm.socket, "socket") as socket,
                mock.patch.object(vm, "source_archive", side_effect=lambda source, path: path.write_bytes(b"source")),
                mock.patch.object(
                    vm.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout="source-pin", stderr=""),
                ),
                mock.patch.object(
                    vm.subprocess, "Popen", return_value=mock.Mock(pid=1234, poll=mock.Mock(return_value=None))
                ),
                mock.patch.object(
                    vm.time,
                    "monotonic",
                    side_effect=lambda: clock["now"],
                ),
            ):
                socket.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 22222)
                if prepare_error:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        vm.main()
                elif invalid_baseline:
                    with self.assertRaises(json.JSONDecodeError):
                        vm.main()
                elif no_budget:
                    with self.assertRaisesRegex(TimeoutError, "No preparation budget"):
                        vm.main()
                elif fails:
                    with self.assertRaisesRegex(OSError, "fixture cleanup failed"):
                        vm.main()
                else:
                    vm.main()
            machine.stop.assert_called_once()
            if not prepare_error and not no_budget and not invalid_baseline:
                reconnect = machine.wait.call_args_list[-1]
                self.assertEqual(300, reconnect.kwargs["seconds"])
                self.assertEqual("boot-before", reconnect.kwargs["changed_boot"])
            phases = json.loads((evidence / "phase-durations.json").read_text())
            if prepare_error or no_budget or invalid_baseline:
                self.assertEqual(
                    (["prepare"] if prepare_error or invalid_baseline else []) + ["diagnose", "cleanup"], events
                )
                self.assertFalse((evidence / "result.json").exists())
                self.assertEqual("fixture_prepare", json.loads((evidence / "failure.json").read_text())["phase"])
                progress = json.loads((evidence / "prepare-progress.json").read_text())
                expected_progress = [{"phase": "planning"}] if prepare_error else []
                if invalid_baseline:
                    expected_progress = [{"phase": "baseline", "result": "ready"}]
                self.assertEqual(expected_progress, progress)
                self.assertNotIn("do-not-save", (evidence / "prepare-diagnostics.json").read_text())
            elif fails:
                self.assertFalse((evidence / "result.json").exists())
                self.assertEqual("cleanup", json.loads((evidence / "failure.json").read_text())["phase"])
                self.assertEqual("failed", phases["outcome"])
            else:
                self.assertEqual("PASS", json.loads((evidence / "result.json").read_text())["result"])
                self.assertEqual("passed", phases["outcome"])
            if elapsed_before_prepare is not None:
                assigned = json.loads((evidence / "preparation-budget.json").read_text())
                self.assertEqual(0.0 if no_budget else 295.0, assigned["ssh_seconds"])
                self.assertEqual(0.0 if no_budget else 280.0, assigned["guest_seconds"])

    def test_invalid_baseline_preserves_captured_preparation_progress(self):
        self.completed_fixture_with_cleanup(fails=False, invalid_baseline=True)

    def test_exhausted_preparation_budget_never_starts_prepare(self):
        self.completed_fixture_with_cleanup(fails=False, elapsed_before_prepare=900.0, no_budget=True)

    def test_preparation_uses_actual_remaining_budget(self):
        self.completed_fixture_with_cleanup(fails=False, elapsed_before_prepare=485.0)

    def test_preparation_timeout_retains_trace_and_reads_diagnostics_before_cleanup(self):
        self.completed_fixture_with_cleanup(fails=False, prepare_error=True)

    def test_failed_guest_cleanup_never_leaves_a_pass_result(self):
        self.completed_fixture_with_cleanup(fails=True)

    def test_pass_result_is_published_after_successful_guest_cleanup(self):
        self.completed_fixture_with_cleanup(fails=False)

    def test_ci_admission_refuses_local_or_unprepared_hosts(self):
        expected = {
            "GITHUB_ACTIONS": "true",
            "RUNNER_OS": "Linux",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_TEMP": "/ci/temp",
            "GITHUB_WORKSPACE": "/ci/source",
        }
        for changes in ({"GITHUB_ACTIONS": "false"}, {"RUNNER_ENVIRONMENT": "self-hosted"}, {"RUNNER_TEMP": ""}):
            with self.subTest(changes=changes), mock.patch.dict(vm.os.environ, {**expected, **changes}, clear=True):
                with (
                    mock.patch.object(vm.sys, "platform", "linux"),
                    mock.patch.object(vm.platform, "machine", return_value="x86_64"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "disposable GitHub-hosted"):
                        vm.require_ci()

    def test_ssh_has_a_fixed_loopback_target_and_quotes_remote_arguments(self):
        import shlex
        from pathlib import Path

        machine = vm.VM(Path("/ci/private"), 22222, "guest-instance", "outer-boot")
        supplied = ["echo", "literal; reboot"]
        with mock.patch.object(vm.subprocess, "run") as run:
            machine.ssh(supplied)
        argv = run.call_args.args[0]
        self.assertEqual(["autocode@127.0.0.1", shlex.join(supplied)], argv[-2:])
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertNotIn("shell", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
