"""A project's [serve] setting: the values it takes, how a new run takes it from the project file, and where a
run saves it (#730)."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import autocode_args
import autocode_dev_server_config as config
import autocode_doctor as doctor
from providers import opencode

SOURCE = "/w/.autocode/config.toml"


class CheckedTests(unittest.TestCase):
    def test_a_command_string_takes_the_defaults(self):
        self.assertEqual(
            {"command": "npm run dev -- --port {port}", "ready_path": None, "ready_seconds": config.READY_SECONDS},
            config.checked("npm run dev -- --port {port}", SOURCE),
        )

    def test_a_table_names_the_ready_path_and_its_wait(self):
        self.assertEqual(
            {"command": "python3 -m http.server {port}", "ready_path": "/health", "ready_seconds": 20},
            config.checked(
                {"command": " python3 -m http.server {port} ", "ready-path": "/health", "ready-seconds": 20}, SOURCE
            ),
        )

    def test_values_a_server_cannot_be_started_from_are_refused_naming_the_file(self):
        refused = (
            "npm run dev",
            "",
            "   ",
            "serve {port}\x00",
            42,
            ["npm", "{port}"],
            {"ready-path": "/"},
            {"command": "x {port}", "port": 3000},
            {"command": "x {port}", "ready-path": "health"},
            {"command": "x {port}", "ready-path": "//evil.example/"},
            {"command": "x {port}", "ready-path": "/a b"},
            {"command": "x {port}", "ready-path": "/\x1b[2J"},
            {"command": "x {port}", "ready-seconds": 0},
            {"command": "x {port}", "ready-seconds": config.MAX_READY_SECONDS + 1},
            {"command": "x {port}", "ready-seconds": True},
            {"command": "x {port}", "ready-seconds": 1.5},
        )
        for value in refused:
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                config.checked(value, SOURCE)
            self.assertIn(f"{SOURCE}: serve", str(error.exception))

    def test_command_for_fills_every_port_placeholder_and_nothing_else(self):
        saved = config.checked("serve --port {port} --url http://127.0.0.1:{port}/ ${HOME} {other}", SOURCE)
        self.assertEqual(
            "serve --port 8123 --url http://127.0.0.1:8123/ ${HOME} {other}", config.command_for(saved, 8123)
        )


class ProjectFileTests(unittest.TestCase):
    """A new run takes [serve] from .autocode/config.toml; a saved run reads no file."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / ".autocode").mkdir()
        self.config = self.root / ".autocode" / "config.toml"

    def parse(self, *argv):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            args, _ = autocode_args.parse(None, [*argv, "--workspace", str(self.root)], opencode.DEFAULT_MODELS)
        return args, stderr.getvalue()

    def test_a_new_run_takes_the_table_and_a_saved_run_reads_no_file(self):
        self.config.write_text(
            'engine = "codex"\n\n[serve]\ncommand = "npm run dev -- --port {port}"\nready-path = "/"\n'
        )
        args, stderr = self.parse("Build it")
        self.assertEqual(
            {"command": "npm run dev -- --port {port}", "ready_path": "/", "ready_seconds": config.READY_SECONDS},
            args.serve,
        )
        self.assertIn("engine, serve", stderr)
        run = self.root / ".autocode" / "runs" / "r1"
        run.mkdir(parents=True)
        args, _ = self.parse("--run-dir", str(run), "--resume-paused")
        self.assertIsNone(getattr(args, "serve", None))

    def test_a_command_without_a_port_stops_a_new_run(self):
        self.config.write_text('serve = "npm run dev"\n')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
            autocode_args.parse(None, ["Build it", "--workspace", str(self.root)], opencode.DEFAULT_MODELS)
        self.assertEqual(2, stop.exception.code)
        self.assertIn(f"{self.config}: serve", stderr.getvalue())
        self.assertIn("{port}", stderr.getvalue())

    def test_doctor_shows_the_serve_command_and_where_it_was_set(self):
        rows = {row["key"]: (row["value"], row["source"]) for row in doctor.project_settings(self.root)[1]}
        self.assertEqual((None, "built-in default"), rows["serve"])
        self.config.write_text('serve = "npm start -- --port {port}"\n')
        check, found = doctor.project_settings(self.root)
        rows = {row["key"]: (row["value"], row["source"]) for row in found}
        self.assertEqual(str(self.config), rows["serve"][1])
        self.assertEqual("npm start -- --port {port}", rows["serve"][0]["command"])
        self.assertEqual(doctor.OK, check.status)


if __name__ == "__main__":
    unittest.main()
