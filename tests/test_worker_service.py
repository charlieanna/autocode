"""Host admission and literal, noninteractive service dispatch without a task loop."""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock

import autocode_worker_service as services

ID = "a" * 32


def reply(text="", code=0):
    return subprocess.CompletedProcess([], code, text, "private diagnostic")


def manager(*, version="systemd 255 (255.4)", linger="yes", env=None):
    run = Mock(side_effect=[reply(version), reply("255.4"), reply(linger), reply()])
    return services.UserServices(platform="linux", uid=1000, env=env or {"PATH": "/usr/bin"}, run=run), run


class HostAdmission(unittest.TestCase):
    def test_non_linux_never_launches_or_administers_a_manager(self):
        run = Mock()
        adapter = services.UserServices(platform="darwin", uid=1000, env={}, run=run)
        self.assertFalse(adapter.capability()["available"])
        run.assert_not_called()

    def test_unqualified_expansion_support_or_logout_lifetime_refuses_before_dispatch(self):
        for version, linger in (("systemd 253", "yes"), ("unparseable", "yes"), ("systemd 255", "no")):
            with self.subTest(version=version, linger=linger):
                adapter, run = manager(version=version, linger=linger)
                with self.assertRaises(services.ServiceUnavailable):
                    adapter.start(ID, ["/runtime/python"], workspace=Path("/task"), log=Path("/control/output"))
                self.assertFalse(any("--unit=" in str(call) for call in run.call_args_list))
                self.assertFalse(any("enable-linger" in str(call) for call in run.call_args_list))

    def test_unavailable_or_ambiguous_capability_stays_unavailable(self):
        for failure in (OSError("offline"), subprocess.TimeoutExpired("systemctl", 5)):
            run = Mock(side_effect=failure)
            adapter = services.UserServices(platform="linux", uid=1000, env={}, run=run)
            self.assertFalse(adapter.capability()["available"])


class ServiceDispatch(unittest.TestCase):
    def test_literal_task_arguments_and_environment_values_are_not_service_expansion_or_argv_secrets(self):
        secret = "fixture-credential-value"
        env = {"PATH": "/usr/bin", "API_KEY": secret, "INVOCATION_ID": "old-owner", "NOTIFY_SOCKET": "/old/socket"}
        adapter, run = manager(env=env)
        argv = ["/runtime/python", "/runtime/core.py", "--brief", "Keep $HOME ${NAME} %n 你好 literally"]
        adapter.start(ID, argv, workspace=Path("/task/100% work"), log=Path("/control/100% log"))
        args, kwargs = run.call_args
        command = args[0]
        self.assertEqual(argv, command[command.index("--") + 1 :])
        self.assertIn("--expand-environment=no", command)
        self.assertIn("--working-directory=/task/100%% work", command)
        self.assertIn("--property=StandardOutput=append:/control/100%% log", command)
        self.assertIn("--setenv=API_KEY", command)
        self.assertNotIn(secret, " ".join(command))
        self.assertFalse(any("--setenv=" + key in command for key in ("INVOCATION_ID", "NOTIFY_SOCKET")))
        self.assertEqual(secret, kwargs["env"]["API_KEY"])
        self.assertNotIn("--scope", command)
        self.assertNotIn("--pty", command)
        self.assertNotIn("--pipe", command)
        self.assertIn("--service-type=exec", command)

    def test_lost_or_failed_launch_receipt_never_retries_dispatch(self):
        for result in (subprocess.TimeoutExpired("systemd-run", 5), reply(code=1)):
            with self.subTest(result=type(result).__name__):
                adapter, run = manager()
                run.side_effect = [reply("systemd 255"), reply("255"), reply("yes"), result]
                with self.assertRaises(services.ServiceOutcomeUnknown):
                    adapter.start(ID, ["/runtime/python"], workspace=Path("/task"), log=Path("/control/output"))
                self.assertEqual(1, sum("--unit=" in str(call) for call in run.call_args_list))

    def test_unknown_service_observation_is_not_a_completed_or_absent_worker(self):
        for result in (reply(code=1), reply("LoadState=loaded\nMainPID=bad"), OSError("denied")):
            with self.subTest(result=type(result).__name__):
                run = Mock(side_effect=result if isinstance(result, Exception) else [result])
                adapter = services.UserServices(env={}, run=run)
                observed = adapter.inspect(ID)
                self.assertFalse(observed["checked"])
                self.assertIsNone(observed["present"])
                self.assertNotIn("complete", observed)

    def test_loaded_service_metadata_does_not_grant_task_completion(self):
        text = (
            "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=123\nInvocationID="
            + "b" * 32
            + "\nExecMainStatus=0\n"
        )
        run = Mock(return_value=reply(text))
        observed = services.UserServices(env={}, run=run).inspect(ID)
        self.assertTrue(observed["checked"])
        self.assertEqual(123, observed["pid"])
        self.assertEqual("b" * 32, observed["invocation_id"])
        self.assertNotIn("complete", observed)
        self.assertNotIn("verified", observed)
