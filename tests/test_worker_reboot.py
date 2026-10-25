"""Pure C4 evidence-oracle and disposable-guest admission checks; no VM or provider."""

import json
import unittest
from contextlib import ExitStack
from copy import deepcopy
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
