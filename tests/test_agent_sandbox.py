"""--agent-sandbox: the whole agent process of a stage runs inside an OS sandbox (opt-in).

KernelTests run a real shell inside the sandbox on this host's backend (Linux bubblewrap, macOS
Seatbelt) against a planted home directory: the credential reads and the writes outside the allowed
paths fail there, and the same script outside the sandbox succeeds, so the tests fail without it.
The native containment CI jobs run KernelTests and require them not to skip.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_agent_sandbox as agent_sandbox
import autocode_provider_launch as launch
import autocode_util as util

from . import test_subprocess as subprocess_tests

SECRET = "aws_secret_access_key = planted-secret-4711"


def plant_home(home):
    (home / ".aws").mkdir(parents=True)
    (home / ".aws/credentials").write_text(SECRET + "\n")
    (home / ".ssh").mkdir()
    (home / ".ssh/id_ed25519").write_text("planted-key\n")
    (home / ".netrc").write_text("machine example.test password planted-netrc\n")
    (home / ".bashrc").write_text("# original\n")
    (home / ".claude").mkdir()
    (home / ".codex").mkdir()
    (home / ".codex/auth.json").write_text('{"token": "planted-codex-login"}\n')
    (home / ".cache").mkdir()


class PolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        plant_home(self.home)
        self.workspace = self.root / "work"
        self.run_dir = self.workspace / ".autocode/runs/one"
        self.run_dir.mkdir(parents=True)

    def policy(self, provider="claude", platform="linux"):
        return agent_sandbox.policy(
            workspace=self.workspace,
            run_dir=self.run_dir,
            provider=provider,
            home=self.home,
            environment={},
            platform=platform,
        )

    def test_credential_stores_are_denied_and_only_the_stage_paths_are_writable(self):
        writable, denied = self.policy()
        for name in (".aws", ".ssh", ".netrc", ".config/gcloud", ".kube", ".docker/config.json", ".gnupg"):
            self.assertIn(self.home / name, denied, name)
        for path in (self.workspace, self.run_dir, Path("/tmp"), self.home / ".claude", self.home / ".cache"):
            self.assertIn(path, writable, path)
        self.assertNotIn(self.home, writable)
        self.assertNotIn(self.home / ".bashrc", writable)
        # Another engine's login is denied; the launched engine keeps its own.
        self.assertIn(self.home / ".codex/auth.json", denied)
        self.assertNotIn(self.home / ".claude/.credentials.json", denied)
        self.assertNotIn(self.home / ".codex", writable)

    def test_each_engine_writes_only_its_own_state(self):
        writable, denied = self.policy("codex")
        self.assertIn(self.home / ".codex", writable)
        self.assertNotIn(self.home / ".claude", writable)
        self.assertIn(self.home / ".claude/.credentials.json", denied)
        self.assertNotIn(self.home / ".codex/auth.json", denied)
        # A configured provider AutoCode does not know keeps every engine's login readable.
        _, unknown = self.policy("my_tool")
        self.assertNotIn(self.home / ".codex/auth.json", unknown)
        self.assertIn(self.home / ".aws", unknown)

    def test_bubblewrap_masks_existing_credentials_after_every_writable_bind(self):
        writable, denied = self.policy()
        arguments = agent_sandbox.bubblewrap_arguments(writable, denied)
        prefix = agent_sandbox.userns.bubblewrap(agent_sandbox.BWRAP)
        self.assertEqual([*prefix, "--die-with-parent", "--ro-bind", "/", "/"], arguments[: len(prefix) + 4])
        self.assertEqual("--", arguments[-1])
        binds = [arguments[i + 1] for i, part in enumerate(arguments) if part == "--bind"]
        self.assertIn(str(self.workspace), binds)
        self.assertNotIn(str(self.home), binds)
        last_bind = max(i for i, part in enumerate(arguments) if part == "--bind")
        aws = arguments.index(str(self.home / ".aws"))
        self.assertEqual("--tmpfs", arguments[aws - 1])
        netrc = arguments.index(str(self.home / ".netrc"))
        self.assertEqual(["--ro-bind", "/dev/null"], arguments[netrc - 2 : netrc])
        self.assertGreater(min(aws, netrc), last_bind)
        # A store that does not exist has nothing to mask, and bwrap could not mount on it.
        self.assertNotIn(str(self.home / ".kube"), arguments)

    def test_seatbelt_profile_denies_credential_reads_and_writes_outside_the_stage(self):
        writable, denied = self.policy(platform="darwin")
        profile = agent_sandbox.seatbelt_profile(writable, denied, home=self.home)
        lines = profile.splitlines()
        self.assertEqual(["(version 1)", "(allow default)"], lines[:2])
        self.assertTrue(lines[2].startswith("(deny file-write* (require-not (require-any"))
        self.assertIn(json.dumps(str(self.workspace)), lines[2])
        self.assertIn(".claude\\\\.json(\\\\..*)?$", lines[2])
        self.assertNotIn(json.dumps(str(self.home)) + ")", lines[2])
        self.assertTrue(lines[3].startswith("(deny file-read* file-write* "))
        self.assertIn(json.dumps(str(self.home / ".aws")), lines[3])

    def test_hosts_without_a_sandbox_and_codex_on_macos_are_refused(self):
        self.assertIn("this machine is win32", agent_sandbox.problem(platform="win32"))
        with mock.patch.object(Path, "is_file", return_value=True):
            self.assertIn("Codex on macOS", agent_sandbox.problem({"engine": "codex"}, platform="darwin"))
            self.assertIn("Codex on macOS", agent_sandbox.problem({}, platform="darwin"))
            routes = {"engine": "opencode", "provider": "claude", "roles": {"tester": {"engine": "codex"}}}
            self.assertIn("Codex on macOS", agent_sandbox.problem(routes, platform="darwin"))
            self.assertIsNone(agent_sandbox.problem({"engine": "opencode", "provider": "claude"}, platform="darwin"))
        with self.assertRaises(agent_sandbox.Unavailable):
            agent_sandbox.wrap(
                ["true"], workspace=self.workspace, run_dir=self.run_dir, provider="claude", platform="win32"
            )

    def test_configure_saves_the_request_once_and_refuses_it_where_it_cannot_exist(self):
        state, settings = {}, {"engine": "opencode", "provider": "claude"}
        agent_sandbox.configure(state, settings, enable=False, now=lambda: "t0")
        self.assertEqual(({}, {"engine": "opencode", "provider": "claude"}), (state, settings))
        with mock.patch.object(agent_sandbox, "problem", return_value=None):
            agent_sandbox.configure(state, settings, enable=True, now=lambda: "t1")
            agent_sandbox.configure(state, settings, enable=False, now=lambda: "t2")  # a resume keeps it
        self.assertIs(True, settings["agent_sandbox"])
        self.assertEqual(
            [{"kind": "agent_sandbox_enabled", "actor": "user_cli", "at": "t1", "flag": "--agent-sandbox"}],
            state["user_events"],
        )
        with mock.patch.object(agent_sandbox, "problem", return_value="no sandbox here"):
            for enable, saved in ((True, {}), (False, {"agent_sandbox": True})):
                with self.assertRaisesRegex(ValueError, "Refused before any stage launched.*no sandbox here"):
                    agent_sandbox.configure({}, dict(saved), enable=enable, now=lambda: "t3")


class LaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / ".autocode/runs/one"
        self.run.mkdir(parents=True)
        self.options = dict(
            engine="codex",
            adapter=None,
            role="tester",
            route_role="tester",
            workspace=self.root,
            run_dir=self.run,
            session=None,
            model="gpt-5.6-sol",
            effort="high",
            allow_write=False,
            planning=False,
            report=self.root / "report.json",
            schema=self.root / "schema.json",
            prompt_file=self.root / "prompt.txt",
            sandbox="read-only",
            transport_args=[],
            chatgpt=True,
            provider=None,
        )

    def test_without_the_setting_the_stage_launches_exactly_as_before(self):
        for settings in (None, {}, {"agent_sandbox": "true"}, {"agent_sandbox": 1}):
            command, _, _, worker = launch.prepare(**self.options, settings=settings)
            self.assertEqual(["codex", "exec"], command[:2], settings)
            self.assertNotIn("agent_sandbox", worker)
            self.assertNotIn("agent_sandbox", launch.stage_record(worker))

    def test_the_setting_wraps_the_whole_provider_command_and_the_stage_record_says_so(self):
        plain, _, _, _ = launch.prepare(**self.options, settings={})
        with mock.patch.object(agent_sandbox, "problem", return_value=None):
            command, _, _, worker = launch.prepare(**self.options, settings={"agent_sandbox": True})
        self.assertEqual(plain, command[len(command) - len(plain) :])
        # Root starts bubblewrap through autocode_userns, so it keeps reading other users' files.
        self.assertTrue({agent_sandbox.BWRAP, agent_sandbox.SANDBOX_EXEC} & set(command[:4]), command[:4])
        self.assertEqual(command, worker["command"])
        record = launch.stage_record(worker)["agent_sandbox"]
        self.assertEqual(agent_sandbox.backend(), record["backend"])
        self.assertIn(str(self.root), record["writable"])
        # A preview launches nothing, so it is not wrapped.
        preview, _, _, _ = launch.prepare(**self.options, settings={"agent_sandbox": True}, enforce_tool_boundary=False)
        self.assertEqual(plain, preview)

    def test_a_stage_that_cannot_be_sandboxed_pauses_before_any_provider_launches(self):
        with mock.patch.object(agent_sandbox, "problem", return_value="no sandbox here"):
            with self.assertRaises(util.Paused) as raised:
                launch.prepare(**self.options, settings={"agent_sandbox": True})
        self.assertEqual("PAUSED_TOOL_CONTAINMENT", raised.exception.status)
        self.assertIn("--agent-sandbox", str(raised.exception))
        self.assertIn("no sandbox here", str(raised.exception))


def kernel_problem():
    return agent_sandbox.problem({"engine": "opencode", "provider": "claude"})


@unittest.skipIf(kernel_problem(), f"agent sandbox unavailable: {kernel_problem()}")
class KernelTests(unittest.TestCase):
    """A real shell inside the sandbox. The planted home lies outside every temporary directory, so a write
    there is outside the writable paths on both backends."""

    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix=".autocode-agent-sandbox-test-", dir=Path.home())).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.home = base / "home"
        plant_home(self.home)
        self.workspace = base / "work"
        self.run_dir = self.workspace / ".autocode/runs/one"
        self.run_dir.mkdir(parents=True)

    def shell(self, script, *, sandboxed=True, provider="claude"):
        command = ["/bin/sh", "-c", script]
        if sandboxed:
            command, record = agent_sandbox.wrap(
                command,
                workspace=self.workspace,
                run_dir=self.run_dir,
                provider=provider,
                home=self.home,
                environment={},
            )
            self.assertTrue(record["denied_reads"])
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home)}
        return subprocess.run(command, cwd=self.workspace, env=env, capture_output=True, text=True, timeout=60)

    def test_credentials_cannot_be_read_inside_the_sandbox(self):
        script = 'cat "$HOME/.aws/credentials" "$HOME/.ssh/id_ed25519" "$HOME/.netrc" "$HOME/.codex/auth.json"; ls -A "$HOME/.ssh"'
        outside = self.shell(script, sandboxed=False)
        self.assertIn(SECRET, outside.stdout)  # the control: without the sandbox the shell reads them
        inside = self.shell(script)
        for planted in (SECRET, "planted-key", "planted-netrc", "planted-codex-login"):
            self.assertNotIn(planted, inside.stdout + inside.stderr, planted)
        self.assertNotIn("id_ed25519", inside.stdout)  # the directory lists empty

    def test_writes_land_only_in_the_stage_paths(self):
        script = (
            f'echo built > "$PWD/built.txt"; echo report > "{self.run_dir}/report.json"; echo state > "$HOME/.claude/session.json"; '
            'echo cached > "$HOME/.cache/entry"; echo pwned >> "$HOME/.bashrc"; echo planted > "$HOME/new-file"; '
            'echo replaced > "$HOME/.aws/credentials"; exit 0'
        )
        result = self.shell(script)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("built\n", (self.workspace / "built.txt").read_text())
        self.assertEqual("report\n", (self.run_dir / "report.json").read_text())
        self.assertEqual("state\n", (self.home / ".claude/session.json").read_text())
        self.assertEqual("cached\n", (self.home / ".cache/entry").read_text())
        self.assertEqual("# original\n", (self.home / ".bashrc").read_text())
        self.assertFalse((self.home / "new-file").exists())
        self.assertEqual(SECRET + "\n", (self.home / ".aws/credentials").read_text())

    def test_the_tools_a_stage_runs_still_work(self):
        result = self.shell(f'{sys.executable} -c "print(6 * 7)" && git --version >/dev/null && echo ok')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("42\nok\n", result.stdout)


@unittest.skipIf(sys.platform != "linux" or kernel_problem(), "the fake Codex flow runs sandboxed on Linux")
class SandboxedFakeCodexFlow(unittest.TestCase):
    """A whole fake-Codex run through the CLI with --agent-sandbox: every stage launches inside bubblewrap
    and the run still completes, while a planted credential in the agents' home stays unreadable."""

    launch = subprocess_tests.SubprocessFlow.launch
    saved = subprocess_tests.SubprocessFlow.saved
    new_run_engine_args = subprocess_tests.SubprocessFlow.new_run_engine_args

    def setUp(self):
        subprocess_tests.SubprocessFlow.setUp(self)
        self.home = self.root / "home"
        plant_home(self.home)
        self.env["HOME"] = str(self.home)

    def test_every_stage_runs_sandboxed_and_the_run_completes(self):
        self.launch(["Build greeting", "--chat", "--agent-sandbox"], 0, answers="CLI\nyes\ny\n")
        run, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertIs(True, state["settings"]["agent_sandbox"])
        launched = [record for record in state["stages"] if record.get("command")]
        self.assertTrue(launched)
        for record in launched:
            self.assertIn(agent_sandbox.BWRAP, record["command"][:4], record["stage"])
            self.assertEqual("bubblewrap", record["agent_sandbox"]["backend"], record["stage"])
            self.assertIn(str(self.home / ".aws"), record["agent_sandbox"]["denied_reads"])
        resumed = self.launch(["--run-dir", str(run), "--status"], 0)
        self.assertNotIn("Refused", resumed.stderr)


if __name__ == "__main__":
    unittest.main()
