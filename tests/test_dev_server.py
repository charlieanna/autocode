"""A run's development server (#730): `autocode serve` starts the run's saved [serve] command on a free loopback
port, records where it answers, and stops it on request; the status view shows it."""

import contextlib
import io
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import autocode_dev_server as dev_server
import autocode_dev_server_config as config
import autocode_serve_cli as serve_cli
import autocode_util as util

ROOT = Path(__file__).resolve().parents[1]
PYTHON = shlex.quote(sys.executable)
SERVER = PYTHON + " -m http.server {port} --bind 127.0.0.1"
# Serves the workspace unless a file named broken is there, as an app with a syntax error would fail.
APP = """import pathlib, runpy, sys
if pathlib.Path("broken").exists():
    print("SyntaxError: the app is broken", flush=True)
    sys.exit(3)
sys.argv = ["http.server", sys.argv[1], "--bind", "127.0.0.1"]
runpy.run_module("http.server", run_name="__main__")
"""


class Workspace(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        (self.workspace / "index.html").write_text("hello from the app")
        self.run_dir = self.workspace / ".autocode" / "runs" / "r1"
        self.run_dir.mkdir(parents=True)

    def saved(self, command=SERVER, **fields):
        return config.checked({"command": command, **{key.replace("_", "-"): v for key, v in fields.items()}}, "test")

    def start(self, saved):
        """serve() in a thread, as the CLI runs it in the foreground; returns its stop event and results."""
        stop, ready, result = threading.Event(), threading.Event(), {}

        def run():
            try:
                result["record"] = dev_server.serve(
                    self.run_dir,
                    self.workspace,
                    saved,
                    should_stop=stop.is_set,
                    on_ready=lambda record: (
                        result.setdefault("readies", []).append(record) or result.setdefault("ready", record)
                    ),
                    on_wait=lambda record: result.setdefault("waits", []).append(record),
                )
            except BaseException as error:  # noqa: BLE001 - surfaced by the test
                result["error"] = error
            finally:
                ready.set()

        def ready_or_ended(count=1, key="readies"):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and len(result.get(key, ())) < count and not ready.is_set():
                time.sleep(0.05)
            return result

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 30)
        self.addCleanup(stop.set)
        return stop, thread, ready_or_ended


class ServeTests(Workspace):
    def test_a_ready_server_is_recorded_with_its_url_and_stops_when_asked(self):
        saved = self.saved(ready_path="/index.html")
        stop, thread, ready = self.start(saved)
        running = ready()["ready"]
        self.assertEqual("running", running["status"])
        self.assertEqual(f"http://127.0.0.1:{running['port']}/", running["url"])
        with urllib.request.urlopen(running["url"] + "index.html", timeout=5) as page:
            self.assertEqual(b"hello from the app", page.read())
        shown = dev_server.inspect(self.run_dir, {"serve": saved})
        self.assertEqual((True, "running", running["url"]), (shown["configured"], shown["status"], shown["url"]))
        stop.set()
        thread.join(30)
        final = dev_server.inspect(self.run_dir, {"serve": saved})
        self.assertEqual(("stopped", None), (final["status"], final["url"]))
        self.assertIsNotNone(final["ended_at"])
        with self.assertRaises(OSError):
            urllib.request.urlopen(running["url"], timeout=2)

    def test_autocode_serve_stop_from_another_process_ends_the_server(self):
        stop, thread, ready = self.start(self.saved())
        running = ready()["ready"]
        ended = dev_server.request_stop(self.run_dir, timeout=20)
        thread.join(30)
        self.assertEqual("stopped", ended["status"])
        self.assertIn("autocode serve stop", ended["reason"])
        self.assertFalse(thread.is_alive())
        with self.assertRaises(OSError):
            urllib.request.urlopen(running["url"], timeout=2)

    def test_a_server_that_exits_is_recorded_with_its_exit_code_and_output(self):
        command = PYTHON + " -c \"import sys; print('listening on', sys.argv[1]); sys.exit(3)\" {port}"
        record = dev_server.serve(self.run_dir, self.workspace, self.saved(command))
        self.assertEqual(("exited", 3, None), (record["status"], record["exit_code"], record["ready_at"]))
        self.assertIn(f"listening on {record['port']}", record["tail"])
        self.assertIn("before it answered", record["reason"])
        self.assertEqual("exited", dev_server.inspect(self.run_dir, {})["status"])

    def test_a_server_that_never_answers_is_stopped_after_its_wait(self):
        command = PYTHON + ' -c "import time; time.sleep(120)" {port}'
        started = time.monotonic()
        record = dev_server.serve(self.run_dir, self.workspace, self.saved(command, ready_seconds=1))
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual("failed", record["status"])
        self.assertIn("did not answer within 1 s", record["reason"])

    def test_the_server_gets_its_port_and_host_but_no_credentials(self):
        command = (
            PYTHON + " -c \"import os; print(os.environ.get('PORT'), os.environ.get('HOST'), "
            "os.environ.get('GITHUB_TOKEN'))\" {port}"
        )
        record = dev_server.serve(
            self.run_dir,
            self.workspace,
            self.saved(command),
            environ={"PATH": os.environ["PATH"], "GITHUB_TOKEN": "ghp_" + "x" * 36},
        )
        self.assertIn(f"{record['port']} 127.0.0.1 None", record["tail"])

    def test_a_second_server_for_the_same_run_is_refused(self):
        _, _, ready = self.start(self.saved())
        running = ready()["ready"]
        with self.assertRaises(dev_server.Busy) as refused:
            dev_server.serve(self.run_dir, self.workspace, self.saved())
        self.assertIn(running["url"], str(refused.exception))


class RunWorkspace(Workspace):
    def write_run(self, status, *checkpoints):
        util.atomic_json(
            self.run_dir / "state.json",
            {"status": status, "code_checkpoints": [{"id": ident, "recorded_check": None} for ident in checkpoints]},
        )


class RestartTests(RunWorkspace):
    def test_the_code_version_is_the_latest_code_checkpoint(self):
        self.assertIsNone(dev_server.code_version({}))
        self.assertIsNone(dev_server.code_version({"code_checkpoints": []}))
        rows = [{"id": "code-a"}, {"id": "code-b", "recorded_check": None}]
        self.assertEqual("code-b", dev_server.code_version({"code_checkpoints": rows}))
        rows[1]["recorded_check"] = {"verdict": "PASS"}
        self.assertEqual("code-b", dev_server.code_version({"code_checkpoints": rows}))

    def test_each_code_change_restarts_the_server_on_the_same_port_even_while_the_run_pauses(self):
        self.write_run("RUNNING", "code-a")
        _, thread, ready = self.start(self.saved(ready_path="/index.html"))
        first = ready()["ready"]
        self.assertEqual(("code-a", 0), (first["code_checkpoint"], first["restarts"]))
        self.write_run("PAUSED_FOR_REVIEW", "code-a", "code-b")
        second = ready(2)["readies"][-1]
        self.assertEqual(
            ("code-b", 1, first["url"], first["session"]),
            (second["code_checkpoint"], second["restarts"], second["url"], second["session"]),
        )
        self.assertNotEqual(first["id"], second["id"])
        with urllib.request.urlopen(second["url"] + "index.html", timeout=5) as page:
            self.assertEqual(b"hello from the app", page.read())
        shown = dev_server.inspect(self.run_dir, {})
        self.assertEqual(("running", 1, "code-b"), (shown["status"], shown["restarts"], shown["code_checkpoint"]))
        ended = dev_server.request_stop(self.run_dir, timeout=20)
        thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(("stopped", second["id"]), (ended["status"], ended["id"]))
        self.assertTrue((self.run_dir / dev_server.ATTEMPTS / first["id"] / "output.log").is_file())


class CrashTests(RunWorkspace):
    def broken_app(self):
        (self.workspace / "app.py").write_text(APP)
        (self.workspace / "broken").write_text("")
        return self.saved(PYTHON + " app.py {port}")

    def test_only_a_run_that_has_not_completed_can_still_change_its_code(self):
        for status, expected in (
            (None, False),
            ("RUNNING", True),
            ("PAUSED_FOR_REVIEW", True),
            ("BLOCKED", True),
            ("TASK_COMPLETE", False),
            ("COMPLETE", False),
        ):
            with self.subTest(status=status):
                state = {} if status is None else {"status": status}
                self.assertIs(expected, dev_server.can_change_code(state))

    def test_a_crashed_server_waits_for_the_next_code_change_and_starts_again(self):
        saved = self.broken_app()
        self.write_run("RUNNING", "code-a")
        _, thread, ready = self.start(saved)
        waiting = ready(key="waits")["waits"][0]
        self.assertEqual(("exited", 3, True), (waiting["status"], waiting["exit_code"], waiting["waiting"]))
        self.assertIn("the app is broken", waiting["tail"])
        self.assertTrue(thread.is_alive())
        shown = dev_server.inspect(self.run_dir, {})
        self.assertEqual(("exited", True, None), (shown["status"], shown["waiting"], shown["url"]))
        (self.workspace / "broken").unlink()
        self.write_run("RUNNING", "code-a", "code-b")
        running = ready()["ready"]
        self.assertEqual(
            ("running", "code-b", 1, False),
            (running["status"], running["code_checkpoint"], running["restarts"], running["waiting"]),
        )
        with urllib.request.urlopen(running["url"] + "index.html", timeout=5) as page:
            self.assertEqual(b"hello from the app", page.read())
        dev_server.request_stop(self.run_dir, timeout=20)
        thread.join(30)
        self.assertFalse(thread.is_alive())

    def test_autocode_serve_stop_ends_a_serve_that_is_waiting(self):
        saved = self.broken_app()
        self.write_run("PAUSED_FOR_REVIEW", "code-a")
        _, thread, ready = self.start(saved)
        ready(key="waits")
        ended = dev_server.request_stop(self.run_dir, timeout=20)
        thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(("stopped", 3, False), (ended["status"], ended["exit_code"], ended["waiting"]))
        self.assertIn("autocode serve stop", ended["reason"])
        self.assertIn("the app is broken", ended["tail"])

    def test_a_run_that_completes_while_the_server_is_down_ends_serve(self):
        saved = self.broken_app()
        self.write_run("RUNNING", "code-a")
        _, thread, ready = self.start(saved)
        ready(key="waits")
        self.write_run("COMPLETE", "code-a")
        thread.join(30)
        self.assertFalse(thread.is_alive())
        record = ready()["record"]
        self.assertEqual(("exited", False), (record["status"], record["waiting"]))
        self.assertFalse(dev_server.inspect(self.run_dir, {})["waiting"])


class InspectTests(Workspace):
    def test_a_run_with_no_server_reads_off_and_says_whether_one_is_configured(self):
        self.assertEqual(
            {"configured": False, "status": "off", "url": None, "command": None},
            {key: dev_server.inspect(self.run_dir, {})[key] for key in ("configured", "status", "url", "command")},
        )
        shown = dev_server.inspect(self.run_dir, {"serve": self.saved()})
        self.assertEqual((True, "off", SERVER), (shown["configured"], shown["status"], shown["command"]))

    def test_a_running_record_whose_owner_died_reads_lost(self):
        util.atomic_json(
            self.run_dir / dev_server.RECORD,
            {
                "schema": 1,
                "id": "a",
                "status": "running",
                "url": "http://127.0.0.1:4100/",
                "port": 4100,
                "command": "serve 4100",
                "supervision": {"owner": {"pid": 1}},
            },
        )
        for alive, status, url in ((True, "running", "http://127.0.0.1:4100/"), (False, "lost", None)):
            with (
                self.subTest(alive=alive),
                patch.object(
                    dev_server.supervision, "observe", return_value={"owner": {"checked": True, "alive": alive}}
                ),
            ):
                shown = dev_server.inspect(self.run_dir, {})
                self.assertEqual((status, url), (shown["status"], shown["url"]))

    def test_stop_with_nothing_running_changes_nothing(self):
        self.assertIsNone(dev_server.request_stop(self.run_dir, timeout=1))
        self.assertFalse((self.run_dir / dev_server.RECORD).exists())


class ServeCliTests(Workspace):
    def save_state(self, settings):
        util.atomic_json(
            self.run_dir / "state.json",
            {
                "workspace": str(self.workspace),
                "run_dir": str(self.run_dir),
                "status": "PAUSED_FOR_REVIEW",
                "task": "Build the app",
                "iteration": 1,
                "sessions": {},
                "settings": settings,
            },
        )

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = serve_cli.cli([*argv, "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)])
        return code, out.getvalue(), err.getvalue()

    def autocode(self, *argv, **options):
        env = {**os.environ, "CODEX_HOME": str(self.workspace / ".codex-home")}
        return subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "autocode.py"), *argv], env=env, text=True, **options
        )

    # A line from `autocode serve start`, read with a bound: readline blocks until the child writes one, so a
    # server that never starts (the native Windows advisory job, #1681) would stall the module until
    # run_suite ends it after 600 s and hides every later verdict. The reader thread dies with the child,
    # which the cleanup kills.
    def line(self, process, timeout=30):
        lines = []
        reader = threading.Thread(target=lambda: lines.append(process.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout)
        if not lines:
            self.fail(f"autocode serve start printed no line within {timeout} s")
        return lines[0]

    def test_status_shows_a_configured_server_that_is_not_running(self):
        self.save_state({"serve": self.saved()})
        code, out, _ = self.cli("status", "--json")
        self.assertEqual(0, code)
        shown = json.loads(out)
        self.assertEqual((True, "off", None), (shown["configured"], shown["status"], shown["url"]))

    def test_a_run_without_a_serve_command_cannot_start_a_server(self):
        self.save_state({})
        code, _, err = self.cli("start")
        self.assertEqual(2, code)
        self.assertIn("serve", err)
        self.assertIn(".autocode/config.toml", err)
        self.assertFalse((self.run_dir / dev_server.RECORD).exists())

    def test_a_missing_run_directory_or_workspace_is_named_without_an_errno(self):
        self.save_state({"serve": self.saved()})
        missing = self.workspace / ".autocode" / "runs" / "missing"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = serve_cli.cli(["status", "--workspace", str(self.workspace), "--run-dir", str(missing)])
        self.assertEqual((2, f"autocode serve: No such run directory: {missing}\n"), (code, err.getvalue()))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = serve_cli.cli(
                ["status", "--workspace", str(self.workspace / "nope"), "--run-dir", str(self.run_dir)]
            )
        self.assertEqual(2, code)
        self.assertEqual(f"autocode serve: No such workspace directory: {self.workspace / 'nope'}\n", err.getvalue())

    def test_a_run_outside_the_workspace_is_refused(self):
        self.save_state({"serve": self.saved()})
        with tempfile.TemporaryDirectory() as other:
            code = serve_cli.cli(["status", "--workspace", other, "--run-dir", str(self.run_dir)])
        self.assertEqual(2, code)

    def test_start_serves_in_the_foreground_until_autocode_serve_stop(self):
        self.save_state({"serve": self.saved()})
        start = ["serve", "start", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)]
        process = self.autocode(*start, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        line = self.line(process)
        self.assertIn("Serving http://127.0.0.1:", line)
        code, out, _ = self.cli("stop")
        self.assertEqual(0, code)
        self.assertIn("stopped", out)
        self.assertEqual(0, process.wait(30))
        self.assertEqual("stopped", dev_server.read(self.run_dir)["status"])

    @unittest.skipIf(sys.platform == "win32", "the test ends the server with SIGINT, which Windows cannot send")
    def test_start_says_when_a_code_change_restarted_the_server(self):
        self.save_state({"serve": self.saved()})
        start = ["serve", "start", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)]
        process = self.autocode(*start, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        url = self.line(process).split()[1]
        self.line(process)
        state = util.read(self.run_dir / "state.json")
        util.atomic_json(self.run_dir / "state.json", {**state, "code_checkpoints": [{"id": "code-b"}]})
        self.assertEqual(f"Serving {url} again after a code change (code-b).\n", self.line(process))
        process.send_signal(signal.SIGINT)
        self.assertEqual(0, process.wait(30))

    def test_start_waits_after_a_crash_until_autocode_serve_stop(self):
        (self.workspace / "app.py").write_text(APP)
        (self.workspace / "broken").write_text("")
        self.save_state({"serve": self.saved(PYTHON + " app.py {port}")})
        start = ["serve", "start", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)]
        process = self.autocode(*start, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 30
        while process.poll() is None and not (dev_server.read(self.run_dir) or {}).get("waiting"):
            self.assertLess(time.monotonic(), deadline, "serve start never waited")
            time.sleep(0.05)
        code, out, _ = self.cli("stop")
        _, err = process.communicate(timeout=30)
        self.assertEqual((0, 0), (code, process.returncode))
        self.assertIn("stopped", out)
        shown = err.splitlines()
        self.assertIn("Development server exited: the server exited before it answered.", shown)
        self.assertIn("  SyntaxError: the app is broken", shown)
        self.assertIn("Waiting for the run's next code change; stop it with Ctrl-C or: autocode serve stop", shown)

    # The test stops the server the way a terminal's Ctrl-C does, with SIGINT to the one child. On Windows,
    # Popen.send_signal takes only SIGTERM, CTRL_C_EVENT and CTRL_BREAK_EVENT (a console event reaches the
    # whole process group), so the case cannot run there; the native advisory job stalled in it, waiting for
    # the server's first line, until run_suite ended the module after 600 s (#1681).
    @unittest.skipIf(sys.platform == "win32", "a foreground Ctrl-C is sent as SIGINT, which Windows cannot send")
    def test_ctrl_c_stops_a_foreground_server(self):
        self.save_state({"serve": self.saved()})
        start = ["serve", "start", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)]
        process = self.autocode(*start, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        url = self.line(process).split()[1]
        process.send_signal(signal.SIGINT)
        self.assertEqual(0, process.wait(30))
        self.assertEqual("stopped", dev_server.read(self.run_dir)["status"])
        with self.assertRaises((OSError, urllib.error.URLError)):
            urllib.request.urlopen(url, timeout=2)

    def test_the_status_view_shows_the_dev_server(self):
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        self.save_state({"serve": self.saved()})
        status = self.autocode(
            "--workspace", str(self.workspace), "--run-dir", str(self.run_dir), "--status", stdout=subprocess.PIPE
        )
        out, _ = status.communicate(timeout=120)
        self.assertEqual(0, status.returncode)
        shown = json.loads(out)["view"]["dev_server"]
        self.assertEqual((True, "off", SERVER), (shown["configured"], shown["status"], shown["command"]))


if __name__ == "__main__":
    unittest.main()
