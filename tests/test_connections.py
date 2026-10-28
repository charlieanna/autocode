"""Public connection CLI: discovery, safe status, and delegated sign-in."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_connections as connections
import autocode_subcommands as subcommands
from providers import command


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.config = command.user_config_path("company")
        self.config.parent.mkdir(parents=True)
        self.helper = self.root / "company.py"
        self.receipt = self.root / "login receipt"
        self.helper.write_text(
            "import pathlib, sys\n"
            "if sys.argv[1] == 'login':\n"
            " pathlib.Path(sys.argv[2]).write_text('signed in')\n"
            "else:\n"
            " print('SECRET-token-account-info')\n"
            " sys.exit(int(sys.argv[1]))\n"
        )
        self.base = 'name="company"\ncommand=["not-an-agent"]\n'
        self.base += f"version_command={json.dumps([sys.executable, '--version'])}\n"
        self.base += "[roles]\n" + "\n".join(
            f'{role}={{model="company/model",effort="high"}}' for role in command.REQUIRED_ROLES
        )

    def configure(self, exit_code=0):
        self.config.write_text(
            self.base
            + "\n[connection]\n"
            + f"status_command={json.dumps([sys.executable, str(self.helper), str(exit_code)])}\n"
            + f"login_command={json.dumps([sys.executable, str(self.helper), 'login', str(self.receipt)])}\n"
        )

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = subcommands.dispatch(["connections", *args])
        return code, output.getvalue()

    def test_discovery_never_runs_provider_and_does_not_search_workspace(self):
        self.configure()
        with mock.patch.object(connections.subprocess, "run", side_effect=AssertionError("discovery ran a backend")):
            code, output = self.call("list", "--json")
        self.assertEqual(0, code)
        row = next(row for row in json.loads(output)["connections"] if row["id"] == "company")
        self.assertEqual("not_checked", row["connection_status"])
        self.assertIn(
            ["connections", "login", "--provider", "company"], [item["next_command"] for item in row["actions"]]
        )
        self.assertNotIn("SECRET", output)
        self.assertFalse(self.receipt.exists())

    def test_status_exit_contract_and_secret_output_is_never_returned(self):
        for code, expected in ((0, "credentials_present"), (1, "sign_in_required"), (2, "check_failed")):
            with self.subTest(code=code):
                self.configure(code)
                result, output = self.call("check", "--provider", "company", "--json")
                self.assertEqual(0, result)
                self.assertEqual(expected, json.loads(output)["connection_status"])
                self.assertNotIn("SECRET", output)

    def test_login_invokes_configured_backend_with_argument_array(self):
        self.configure()
        self.assertEqual((0, ""), self.call("login", "--provider", "company"))
        self.assertEqual("signed in", self.receipt.read_text())
        self.assertFalse((self.root / ".autocode").exists())

    def test_full_cli_checks_company_login_without_starting_task(self):
        self.configure()
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "tools" / "autocode.py"),
                "connections",
                "check",
                "--provider",
                "company",
                "--json",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("credentials_present", json.loads(result.stdout)["connection_status"])
        self.assertNotIn("SECRET", result.stdout + result.stderr)
        self.assertFalse((self.root / ".autocode").exists())

    def test_uninstalled_or_unconfigured_login_is_not_offered(self):
        self.config.write_text(self.base.replace(sys.executable, "/missing/company"))
        self.assertEqual("unavailable", connections.connection_view("company")["connection_status"])
        self.config.write_text(self.base)
        self.assertEqual([], [a for a in connections.connection_view("company")["actions"] if a["id"] == "login"])
        code, _ = self.call("login", "--provider", "company")
        self.assertEqual(2, code)

    def test_the_list_marks_the_provider_new_runs_use(self):
        with mock.patch.dict(os.environ, {"AUTOCODE_PROVIDER": "kilocode"}):
            rows = connections.view()["connections"]
        self.assertEqual(["kilocode"], [row["id"] for row in rows if row.get("default")])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOCODE_PROVIDER", None)
            self.assertEqual(
                ["opencode"], [row["id"] for row in connections.view()["connections"] if row.get("default")]
            )

    def test_codex_is_listed_as_an_engine_backend_and_checked_through_its_login_status(self):
        """The list had no Codex row, so a client could not offer a signed-in Codex (#2357)."""
        with mock.patch.object(connections.shutil, "which", return_value=None):
            row = next(row for row in connections.view()["connections"] if row["id"] == "codex")
        self.assertEqual(("Codex", "unavailable", []), (row["label"], row["connection_status"], row["actions"]))
        with mock.patch.object(connections.shutil, "which", return_value="/usr/bin/codex"):
            row = connections.connection_view("codex")
            self.assertEqual("not_checked", row["connection_status"])
            self.assertEqual(
                [
                    ["connections", "check", "--provider", "codex", "--json"],
                    ["connections", "login", "--provider", "codex"],
                ],
                [item["next_command"] for item in row["actions"]],
            )
            for code, expected in ((0, "credentials_present"), (1, "sign_in_required"), (2, "check_failed")):
                with (
                    self.subTest(code=code),
                    mock.patch.object(
                        connections.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], code, "Logged in using ChatGPT", ""),
                    ) as run,
                ):
                    checked = connections.connection_view("codex", check=True)
                    self.assertEqual(expected, checked["connection_status"])
                    self.assertEqual(["codex", "login", "status"], run.call_args.args[0])
                    self.assertNotIn("ChatGPT", json.dumps(checked))

    def test_empty_kilo_auth_list_is_not_a_successful_login(self):
        for output, status in (
            ("0 credentials", "sign_in_required"),
            ("● Z.AI Coding Plan api\n", "credentials_present"),
        ):
            with (
                mock.patch.object(connections.shutil, "which", return_value="/kilo"),
                mock.patch.object(
                    connections.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
                ),
            ):
                row = connections.connection_view("kilocode", check=True)
            self.assertEqual(status, row["connection_status"])

    def test_check_timeout_is_explicit_and_does_not_disclose_errors(self):
        self.configure()
        with mock.patch.object(connections.subprocess, "run", side_effect=subprocess.TimeoutExpired("SECRET", 15)):
            _, output = self.call("check", "--provider", "company", "--json")
        self.assertEqual("check_failed", json.loads(output)["connection_status"])
        self.assertNotIn("SECRET", output)

    def test_invalid_connection_commands_are_rejected_without_launch(self):
        for table in ('login_command="sh -c dangerous"', "status_command=[]", 'unexpected=["anything"]'):
            self.config.write_text(self.base + "\n[connection]\n" + table)
            with self.assertRaises(ValueError):
                command.load("company")
            self.assertEqual("unavailable", connections.connection_view("company")["connection_status"])

    def test_path_traversal_provider_is_refused(self):
        row = connections.connection_view("../workspace")
        self.assertEqual("unavailable", row["connection_status"])
        self.assertEqual([], row["actions"])
