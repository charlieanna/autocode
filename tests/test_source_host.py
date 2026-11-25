"""The source host answers snapshots and read-only CLI queries from warm memory (#1707, #3618)."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import autocode_source_host as host
import autocode_util as util

REAL_SOCKET_PATH = host.socket_path
ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=F", "-c", "user.email=f@example.test", *args],
        check=True,
        capture_output=True,
    )


class SourceHostTests(unittest.TestCase):
    def setUp(self):
        # A Unix socket path must stay short; the default temporary folder on macOS is not.
        self.temp = Path(tempfile.mkdtemp(dir="/tmp" if os.path.isdir("/tmp") else None))
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.root = self.temp / "project"
        self.root.mkdir()
        git(self.root, "init", "-q")
        (self.root / "app.py").write_text("VALUE = 1\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "seed")
        self.socket = self.temp / "h.sock"
        patcher = mock.patch.object(host, "socket_path", return_value=self.socket)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(host._state.update, {"failed": False, "started": False})
        self.addCleanup(util.use_snapshot_service, None)

    def serve(self, cli_runner=None):
        threading.Thread(target=host.serve, args=(self.socket,), kwargs={"cli_runner": cli_runner}, daemon=True).start()
        self.addCleanup(host.stop_host, self.socket, timeout=2.0)
        deadline = time.monotonic() + 10
        while not self.socket.exists():
            self.assertLess(time.monotonic(), deadline, "the host did not start")
            time.sleep(0.02)

    def local(self, root=None):
        util.use_snapshot_service(None)
        return util.snapshot(root or self.root)

    def test_the_host_answers_exactly_what_a_local_snapshot_does_including_after_an_edit(self):
        self.serve()
        self.assertEqual(self.local(), host.snapshot(self.root, False))
        (self.root / "app.py").write_text("VALUE = 2\n")
        (self.root / "new.py").write_text("NEW = True\n")
        edited = host.snapshot(self.root, False)
        self.assertEqual(self.local(), edited)
        self.assertIn("new.py", edited["files"])

    def test_a_snapshot_the_host_cannot_take_stops_the_caller_the_same_way(self):
        self.serve()
        outside = self.temp / "not-a-repository"
        outside.mkdir()
        with self.assertRaises(util.Paused) as local:
            self.local(outside)
        with self.assertRaises(util.Paused) as hosted:
            host.snapshot(outside, False)
        self.assertEqual(
            (local.exception.status, str(local.exception)), (hosted.exception.status, str(hosted.exception))
        )

    def test_without_a_host_the_caller_starts_one_once_and_takes_its_own_snapshot(self):
        with mock.patch.object(host, "subprocess") as spawn:  # this module's reference only
            self.assertIsNone(host.snapshot(self.root, False))
            self.assertIsNone(host.snapshot(self.root, False))
            self.assertEqual(1, spawn.Popen.call_count)
            self.assertEqual(
                [sys.executable, "-B", str(Path(host.__file__).resolve()), str(self.socket)],
                spawn.Popen.call_args.args[0],
            )
            util.use_snapshot_service(host.snapshot)
            served = util.snapshot(self.root)
        self.assertEqual(self.local(), served)

    def test_first_read_uses_the_starting_host_without_importing_a_second_controller(self):
        # An initial read and a subsequent read both get the host's distinctive exit code.
        runner = mock.Mock(return_value=37)
        with (
            mock.patch.dict(os.environ, {host.SETTING: "1"}),
            mock.patch.object(host, "_needs_capability_reexec", return_value=False),
            mock.patch.object(host, "_start", side_effect=lambda path: self.serve(cli_runner=runner)) as launch,
        ):
            self.assertEqual(37, host.cli_fast_path(["modes", "list", "--json"]))
            self.assertEqual(37, host.cli_fast_path(["--status"]))
        launch.assert_called_once_with(self.socket)
        self.assertEqual(runner.call_count, 2)

    def test_busy_host_leaves_other_reads_and_snapshots_to_the_caller(self):
        entered = threading.Event()
        release = threading.Event()
        held_answer = []

        def run_cli(request):
            if request["argv"] == ["modes", "list", "--json"]:
                entered.set()
                release.wait(10)
            return {"exit_code": 37}

        # Use the real socket and request lock. Avoid changing the test process's
        # cwd, environment and streams while the hosted invocation is blocked.
        runner = mock.Mock()
        self.serve(cli_runner=runner)
        with (
            mock.patch.object(host, "_execute_cli", side_effect=lambda _, request, __: run_cli(request)),
            mock.patch.dict(os.environ, {host.SETTING: "1"}),
            mock.patch.object(host, "_needs_capability_reexec", return_value=False),
            mock.patch.object(host, "ANSWER_SECONDS", 2),
        ):
            held = threading.Thread(
                target=lambda: held_answer.append(host.cli_fast_path(["modes", "list", "--json"])),
                daemon=True,
            )
            held.start()
            try:
                self.assertTrue(entered.wait(5), "the held request never entered the host")
                for args in (["--status"], ["registry", "list", "--json"]):
                    self.assertIsNone(host.cli_fast_path(args))
                    self.assertTrue(held.is_alive(), "an independent read waited for the held request to time out")
                    self.assertFalse(host._state["failed"], "the host should explicitly decline a busy request")
                self.assertIsNone(host.snapshot(self.root, False))
                self.assertFalse(host._state["failed"], "a cold read's snapshot must also avoid the busy host")
            finally:
                release.set()
                held.join(5)
            self.assertEqual([37], held_answer)
            self.assertEqual(37, host.cli_fast_path(["--status"]))
            self.assertEqual(self.local(), host.snapshot(self.root, False))

    def test_unavailable_starting_host_falls_back_without_sending_a_read(self):
        with (
            mock.patch.dict(os.environ, {host.SETTING: "1"}),
            mock.patch.object(host, "_needs_capability_reexec", return_value=False),
            mock.patch.object(host, "_start") as launch,
            mock.patch.object(host, "STARTUP_SECONDS", 0.001),
        ):
            self.assertIsNone(host.cli_fast_path(["--status"]))
        launch.assert_called_once_with(self.socket)

    def test_only_the_setting_routes_snapshots_through_the_host(self):
        with mock.patch.dict(os.environ, {host.SETTING: ""}):
            host.install()
        self.assertIsNone(util._SNAPSHOT_SERVICE)
        with mock.patch.dict(os.environ, {host.SETTING: "1"}):
            host.install()
        self.assertIs(host.snapshot, util._SNAPSHOT_SERVICE)

    def test_a_host_for_another_git_environment_is_never_asked(self):
        base = {"HOME": "/home/a", "PATH": "/bin", "TERM": "xterm"}
        same = REAL_SOCKET_PATH(base)
        self.assertEqual(same, REAL_SOCKET_PATH({**base, "TERM": "dumb"}), "unrelated settings share a host")
        for changed in ({"GIT_DIR": "/elsewhere"}, {"HOME": "/home/b"}, {"PATH": "/opt/bin"}):
            with self.subTest(changed=changed):
                self.assertNotEqual(same, REAL_SOCKET_PATH({**base, **changed}))
        self.assertEqual(0o700, same.parent.stat().st_mode & 0o777 if same.parent.exists() else 0o700)

    def test_linux_capability_reexec_only_defers_when_privileges_are_held_or_droppable(self):
        unprivileged_ubuntu = (
            "Name:\tpython\nCapBnd:\t000001ffffffffff\nCapPrm:\t0000000000000000\nCapEff:\t0000000000000000\n"
        )
        privileged_root = (
            "Name:\tpython\nCapBnd:\t000001ffffffffff\nCapPrm:\t000001ffffffffff\nCapEff:\t000001ffffffffff\n"
        )
        setpcap_only = (
            "Name:\tpython\nCapBnd:\t000001ffffffffff\nCapPrm:\t0000000000000100\nCapEff:\t0000000000000100\n"
        )
        permitted_ptrace = (
            "Name:\tpython\nCapBnd:\t0000000000000000\nCapPrm:\t0000000000080000\nCapEff:\t0000000000000000\n"
        )
        with (
            mock.patch.object(host.sys, "platform", "linux"),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            with mock.patch("builtins.open", mock.mock_open(read_data=unprivileged_ubuntu)):
                self.assertFalse(host._needs_capability_reexec())
            with mock.patch("builtins.open", mock.mock_open(read_data=privileged_root)):
                self.assertTrue(host._needs_capability_reexec())
            with mock.patch("builtins.open", mock.mock_open(read_data=setpcap_only)):
                self.assertTrue(host._needs_capability_reexec())
            with mock.patch("builtins.open", mock.mock_open(read_data=permitted_ptrace)):
                self.assertTrue(host._needs_capability_reexec())
            with mock.patch("builtins.open", mock.mock_open(read_data="CapBnd:\t0\n")):
                self.assertFalse(host._needs_capability_reexec())
            with mock.patch("builtins.open", side_effect=OSError("no proc")):
                self.assertFalse(host._needs_capability_reexec())
            with mock.patch.dict(os.environ, {"_AUTOCODE_CAPABILITY_REEXEC": "1"}):
                self.assertTrue(host._needs_capability_reexec())
            with (
                mock.patch.dict(os.environ, {"AUTOCODE_NO_CAPABILITY_DROP": "1"}),
                mock.patch("builtins.open", mock.mock_open(read_data=privileged_root)),
            ):
                self.assertFalse(host._needs_capability_reexec())
        with mock.patch.object(host.sys, "platform", "darwin"):
            self.assertFalse(host._needs_capability_reexec())


@unittest.skipIf(os.name == "nt", "warm CLI host uses POSIX Unix-domain sockets")
class WarmCliHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(dir="/tmp" if os.path.isdir("/tmp") else None)).resolve()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.tmpdir = self.temp / "t"
        self.tmpdir.mkdir(mode=0o700)
        self.home = self.temp / "home"
        self.home.mkdir()
        self.xdg = self.temp / "xdg"
        self.xdg.mkdir()
        self.autocode_home = self.temp / "autocode-home"
        self.autocode_home.mkdir()
        self._tracked_sockets: set[Path] = set()
        self._tracked_procs: list[subprocess.Popen] = []
        self.addCleanup(self._cleanup_hosts)

        self.workspace, self.run_dir = self._make_project("project-main", task="Inspect warm status")

    def _cleanup_hosts(self):
        for proc in self._tracked_procs:
            if proc.poll() is None:
                with mock.patch.dict(os.environ, {}, clear=False):
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
        uid_dir = self.tmpdir / f"autocode-{os.getuid()}"
        if uid_dir.is_dir():
            for sock in uid_dir.glob("*.sock"):
                self._tracked_sockets.add(sock)
        stop_fn = getattr(host, "stop_host", None)
        if callable(stop_fn):
            for sock in list(self._tracked_sockets):
                stop_fn(sock, timeout=5.0)

    def _make_project(self, name: str, *, task: str = "Build the feature") -> tuple[Path, Path]:
        workspace = self.temp / name
        workspace.mkdir()
        git(workspace, "init", "-q")
        (workspace / "app.py").write_text("VALUE = 1\n")
        git(workspace, "add", ".")
        git(workspace, "commit", "-qm", "seed")
        run_dir = workspace / ".autocode" / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        util.atomic_json(
            run_dir / "state.json",
            {
                "workspace": str(workspace),
                "run_dir": str(run_dir),
                "status": "PAUSED_FOR_REVIEW",
                "task": task,
                "iteration": 1,
                "sessions": {},
                "settings": {"engine": "codex"},
            },
        )
        return workspace, run_dir

    def test_script_entry_shares_the_request_context_with_runtime_imports(self):
        # The background host is launched as a script. Its handler and the
        # runtime's normal import must see the same thread-local request guard.
        script = """
import runpy, sys, types
sys.path.insert(0, sys.argv[1])
entry = types.ModuleType('autocode_entry')
def call():
    import autocode_source_host as runtime_host
    return 0 if runtime_host._in_request() else 3
entry.run_in_host = call
entry._autocode_module = lambda: None
sys.modules['autocode_entry'] = entry
sys.argv = [sys.argv[2], sys.argv[3]]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        path = self.tmpdir / "host.sock"
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(TOOLS), str(TOOLS / "autocode_source_host.py"), str(path)],
            cwd=self.workspace,
            env=self._env(warm=True),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._tracked_procs.append(proc)
        deadline = time.monotonic() + 15
        while not path.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(path.exists(), "script host did not start")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(10)
            conn.connect(str(path))
            request = {"kind": "cli", "argv": ["--version"], "cwd": str(self.workspace), "env": self._env(warm=True)}
            conn.sendall(json.dumps(request).encode() + b"\n")
            with conn.makefile("rb") as response:
                answer = json.loads(response.readline())
        self.assertEqual(0, answer["exit_code"], "the runtime saw a different request context")

    def _env(self, *, warm: bool, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            **os.environ,
            "TMPDIR": str(self.tmpdir),
            "TEMP": str(self.tmpdir),
            "TMP": str(self.tmpdir),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "AUTOCODE_HOME": str(self.autocode_home),
            "AUTOCODE_PROJECT_INDEX_HOME": str(self.home / ".autocode"),
            host.SETTING: "1" if warm else "0",
        }
        if extra:
            env.update(extra)
        return env

    def _run_cli(
        self,
        *argv: str,
        warm: bool,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
        script: Path | None = None,
        entry_module: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = self._env(warm=warm, extra=extra_env)
        target = script or (TOOLS / "autocode.py")
        if entry_module:
            env["PYTHONPATH"] = str(target.parent)
            cmd = [sys.executable, "-m", "autocode_entry", *argv]
        else:
            cmd = [sys.executable, str(target), *argv]
        return subprocess.run(
            cmd,
            cwd=str(cwd or self.workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _wait_for_warm_host(self, env: dict[str, str], *, tools_dir: Path | None = None) -> dict:
        sock = REAL_SOCKET_PATH(env, tools_dir=tools_dir) if tools_dir else REAL_SOCKET_PATH(env)
        self._tracked_sockets.add(sock)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            info = host.host_status(sock)
            if info.get("running"):
                return info
            time.sleep(0.05)
        self.fail(f"warm host did not become ready on {sock}")

    @classmethod
    def _strip_live_timestamps(cls, value):
        if isinstance(value, dict):
            return {key: cls._strip_live_timestamps(item) for key, item in value.items() if key != "sampled_at"}
        if isinstance(value, list):
            return [cls._strip_live_timestamps(item) for item in value]
        return value

    @classmethod
    def _normalize_json(cls, text: str):
        return cls._strip_live_timestamps(json.loads(text))

    def test_warm_cli_matches_cold_output_and_reuses_host_pid_across_read_queries(self):
        env = self._env(warm=True)
        # First eligible call falls through transparently while starting the warm host in background.
        first = self._run_cli("modes", "list", "--json", warm=True)
        self.assertEqual(0, first.returncode, first.stderr)
        started = self._wait_for_warm_host(env)
        warm_pid = started["pid"]
        self.assertIsInstance(warm_pid, int)

        queries = (
            (["--version"], False),
            (["--workspace", str(self.workspace), "--run-dir", str(self.run_dir), "--status"], True),
            (["status", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir), "--summary"], False),
            (["stages", "list", "--backend", "codex", "--workspace", str(self.workspace), "--json"], True),
            (["modes", "list", "--json"], True),
            (["connections", "list", "--json"], True),
            (["registry", "list", "--json"], True),
            (["worktrees", "list", "--workspace", str(self.workspace), "--json"], True),
            (["runs", "--workspace", str(self.workspace), "--json"], True),
        )
        for argv, is_json in queries:
            with self.subTest(argv=argv):
                cold = self._run_cli(*argv, warm=False)
                warm = self._run_cli(*argv, warm=True)
                warm_entry = self._run_cli(*argv, warm=True, entry_module=True)
                self.assertEqual(cold.returncode, warm.returncode, (argv, cold.stderr, warm.stderr))
                self.assertEqual(cold.returncode, warm_entry.returncode, (argv, cold.stderr, warm_entry.stderr))
                self.assertEqual(cold.stderr, warm.stderr)
                self.assertEqual(cold.stderr, warm_entry.stderr)
                if is_json:
                    if "--status" in argv:
                        cold_obs = json.loads(cold.stdout)["view"]["observation"]["sampled_at"]
                        warm_obs = json.loads(warm.stdout)["view"]["observation"]["sampled_at"]
                        self.assertTrue(isinstance(cold_obs, str) and cold_obs)
                        self.assertTrue(isinstance(warm_obs, str) and warm_obs)
                    self.assertEqual(self._normalize_json(cold.stdout), self._normalize_json(warm.stdout))
                    self.assertEqual(self._normalize_json(cold.stdout), self._normalize_json(warm_entry.stdout))
                else:
                    self.assertEqual(cold.stdout, warm.stdout)
                    self.assertEqual(cold.stdout, warm_entry.stdout)
                current = host.host_status(Path(started["socket"]))
                self.assertEqual((True, warm_pid), (current["running"], current["pid"]))

    def test_per_call_cwd_and_env_overrides_stay_isolated_across_warm_calls(self):
        project_b, _ = self._make_project("project-b", task="Second workspace")
        home_a = self.temp / "reg-a"
        home_b = self.temp / "reg-b"
        home_a.mkdir()
        home_b.mkdir()

        # Warm the host on the shared GIT_ENV socket.
        self._run_cli("modes", "list", "--json", warm=True)
        info = self._wait_for_warm_host(self._env(warm=True))
        warm_pid = info["pid"]

        # 1. Per-call cwd without explicit --workspace: worktrees list --json must reflect each caller's cwd.
        wt_a = json.loads(self._run_cli("worktrees", "list", "--json", warm=True, cwd=self.workspace).stdout)
        wt_b = json.loads(self._run_cli("worktrees", "list", "--json", warm=True, cwd=project_b).stdout)
        self.assertEqual(str(self.workspace.resolve()), str(Path(wt_a["folder"]).resolve()))
        self.assertEqual(str(project_b.resolve()), str(Path(wt_b["folder"]).resolve()))

        # 2. Per-call AUTOCODE_HOME override and unset AUTOCODE_HOME isolation on the same warm host PID.
        loc_a = json.loads(
            self._run_cli("registry", "location", "--json", warm=True, extra_env={"AUTOCODE_HOME": str(home_a)}).stdout
        )
        loc_b = json.loads(
            self._run_cli("registry", "location", "--json", warm=True, extra_env={"AUTOCODE_HOME": str(home_b)}).stdout
        )
        self.assertEqual(str((home_a / "registry.json").resolve()), loc_a["registry_path"])
        self.assertEqual(str((home_b / "registry.json").resolve()), loc_b["registry_path"])
        self.assertEqual(warm_pid, host.host_status(Path(info["socket"]))["pid"])

        # 3. XDG_CONFIG_HOME stage choices override and PATH isolation.
        xdg_custom = self.temp / "xdg-custom"
        (xdg_custom / "autocode").mkdir(parents=True)
        (xdg_custom / "autocode" / "stages.toml").write_text(
            '[codex]\nbuilder = { model = "gpt-5.6-sol", effort = "low" }\n',
            encoding="utf-8",
        )
        custom_env = {"XDG_CONFIG_HOME": str(xdg_custom)}
        self._run_cli("stages", "list", "--backend", "codex", "--json", warm=True, extra_env=custom_env)
        custom_host = self._wait_for_warm_host(self._env(warm=True, extra=custom_env))
        self.assertNotEqual(info["socket"], custom_host["socket"])
        warm_custom = json.loads(
            self._run_cli("stages", "list", "--backend", "codex", "--json", warm=True, extra_env=custom_env).stdout
        )
        warm_default = json.loads(self._run_cli("stages", "list", "--backend", "codex", "--json", warm=True).stdout)
        builder_custom = next(s for s in warm_custom["stages"] if s["id"] == "builder")
        builder_default = next(s for s in warm_default["stages"] if s["id"] == "builder")
        self.assertEqual({"model": "gpt-5.6-sol", "effort": "low"}, builder_custom["chosen"])
        self.assertIsNone(builder_default["chosen"])

        empty_bin = self.temp / "empty-bin"
        empty_bin.mkdir()
        path_env = {"PATH": str(empty_bin)}
        cold_conns = json.loads(self._run_cli("connections", "list", "--json", warm=False, extra_env=path_env).stdout)
        self._run_cli("connections", "list", "--json", warm=True, extra_env=path_env)
        self._wait_for_warm_host(self._env(warm=True, extra=path_env))
        warm_conns = json.loads(self._run_cli("connections", "list", "--json", warm=True, extra_env=path_env).stdout)
        self.assertEqual(cold_conns, warm_conns)

    def test_editing_any_tools_module_invalidates_warm_host_digest_and_serves_fresh_code(self):
        copied_root = self.temp / "checkout"
        copied_root.mkdir()
        shutil.copy2(ROOT / "pyproject.toml", copied_root / "pyproject.toml")
        shutil.copytree(TOOLS, copied_root / "tools", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        copied_autocode = copied_root / "tools" / "autocode.py"
        copied_modes = copied_root / "tools" / "autocode_run_modes.py"

        env = self._env(warm=True)
        first = self._run_cli("modes", "list", "--json", warm=True, script=copied_autocode)
        self.assertEqual(0, first.returncode, first.stderr)
        host_v1 = self._wait_for_warm_host(env, tools_dir=copied_root / "tools")
        warm_v1 = json.loads(self._run_cli("modes", "list", "--json", warm=True, script=copied_autocode).stdout)
        labels_v1 = [row["label"] for row in warm_v1["modes"]]
        self.assertTrue(labels_v1)

        original_source = copied_modes.read_text(encoding="utf-8")
        target_label = labels_v1[0]
        updated_label = f"{target_label} [edited-code]"
        self.assertIn(f'"{target_label}"', original_source)
        copied_modes.write_text(original_source.replace(f'"{target_label}"', f'"{updated_label}"', 1), encoding="utf-8")

        sock_v2 = REAL_SOCKET_PATH(env, tools_dir=copied_root / "tools")
        self._tracked_sockets.add(sock_v2)
        self.assertNotEqual(Path(host_v1["socket"]), sock_v2)

        after_edit = self._run_cli("modes", "list", "--json", warm=True, script=copied_autocode)
        self.assertEqual(0, after_edit.returncode, after_edit.stderr)
        labels_v2 = [row["label"] for row in json.loads(after_edit.stdout)["modes"]]
        self.assertIn(updated_label, labels_v2)
        host_v2 = self._wait_for_warm_host(env, tools_dir=copied_root / "tools")
        self.assertNotEqual(host_v1["pid"], host_v2["pid"])

    def test_source_host_zero_bypasses_and_starts_nothing_and_mutating_flags_are_ineligible(self):
        res = self._run_cli("--workspace", str(self.workspace), "--run-dir", str(self.run_dir), "--status", warm=False)
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertEqual("PAUSED_FOR_REVIEW", json.loads(res.stdout)["view"]["status"])
        sock = REAL_SOCKET_PATH(self._env(warm=False))
        self.assertFalse(sock.exists())
        self.assertFalse(host.host_status(sock)["running"])

        for ineligible in (
            [],
            ["--help"],
            ["--status", "--owner-lifeline-fd", "3"],
            ["--status", "--worker-receipt", "/tmp/r.json"],
            ["--status", "--approve-goal", "tok"],
            ["--brief", "Build feature"],
            ["--resume-paused"],
            ["--pause"],
            ["stages", "list"],
            ["stages", "choose"],
            ["stages", "set", "builder", "--model", "gpt-5.6-terra", "--effort", "low"],
            ["stages", "reset"],
            ["connections", "login", "--provider", "codex"],
            ["connections", "setup"],
            ["connections", "check", "--provider", "codex", "--json"],
            ["registry", "add", str(self.workspace), "--json"],
            ["registry", "delete", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir), "--yes"],
            ["serve"],
            ["serve", "start"],
        ):
            with self.subTest(argv=ineligible):
                self.assertFalse(host.eligible_cli(ineligible))

    def test_autocode_serve_runs_foreground_warm_host_reports_status_and_stops_on_signal_command_and_idle(self):
        env = self._env(warm=True)
        sock = REAL_SOCKET_PATH(env)
        self._tracked_sockets.add(sock)

        # 1. Foreground `autocode serve` answers warm queries and exits 0 on SIGINT.
        proc = subprocess.Popen(
            [sys.executable, str(TOOLS / "autocode.py"), "serve", "--idle-seconds", "60"],
            cwd=str(self.workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._tracked_procs.append(proc)
        info = self._wait_for_warm_host(env)
        self.assertEqual(proc.pid, info["pid"])

        status_cli = self._run_cli("serve", "--status", "--json", warm=False)
        self.assertEqual(0, status_cli.returncode, status_cli.stderr)
        self.assertEqual(
            (True, proc.pid),
            (json.loads(status_cli.stdout)["running"], json.loads(status_cli.stdout)["pid"]),
        )
        status_text = self._run_cli("serve", "--status", warm=False)
        self.assertEqual(0, status_text.returncode, status_text.stderr)
        self.assertIn(f"Warm CLI host: running (pid {proc.pid},", status_text.stdout)

        conflict = self._run_cli("serve", "--status", "--stop", warm=False)
        self.assertEqual(2, conflict.returncode)
        self.assertIn("Choose either --status or --stop", conflict.stderr)

        warm_modes = self._run_cli("modes", "list", "--json", warm=True)
        self.assertEqual(0, warm_modes.returncode, warm_modes.stderr)

        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=15)
        self.assertEqual(0, proc.returncode)
        self.assertFalse(sock.exists())
        after_sigint = json.loads(self._run_cli("serve", "--status", "--json", warm=False).stdout)
        self.assertFalse(after_sigint["running"])
        stopped_text = self._run_cli("serve", "--status", warm=False)
        self.assertIn("Warm CLI host: not running", stopped_text.stdout)

        # 2. `autocode serve host` stops cleanly via `autocode serve host --stop --json`.
        proc2 = subprocess.Popen(
            [sys.executable, str(TOOLS / "autocode.py"), "serve", "host", "--idle-seconds", "60"],
            cwd=str(self.workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._tracked_procs.append(proc2)
        self._wait_for_warm_host(env)
        stop_cli = self._run_cli("serve", "host", "--stop", "--json", warm=False)
        self.assertEqual(0, stop_cli.returncode, stop_cli.stderr)
        self.assertTrue(json.loads(stop_cli.stdout)["stopped"])
        proc2.communicate(timeout=15)
        self.assertEqual(0, proc2.returncode)
        self.assertFalse(sock.exists())

        # 3. `autocode serve --idle-seconds` exits cleanly on idle timeout.
        proc3 = subprocess.Popen(
            [sys.executable, str(TOOLS / "autocode.py"), "serve", "--idle-seconds", "0.25"],
            cwd=str(self.workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._tracked_procs.append(proc3)
        self._wait_for_warm_host(env)
        proc3.communicate(timeout=15)
        self.assertEqual(0, proc3.returncode)
        self.assertFalse(sock.exists())


if __name__ == "__main__":
    unittest.main()
