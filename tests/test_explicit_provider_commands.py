"""Explicit provider environments must honor executable lookup boundaries."""

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers import opencode
from providers.command import CommandProvider


class AbsoluteProviderExecutableTests(unittest.TestCase):
    def test_empty_path_preflight_matches_provider_launch_from_workspace(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            workspace = root / "project"
            workspace.mkdir()
            executable = workspace / "opencode"
            executable.write_text(
                f"#!{sys.executable}\nimport sys\n"
                "print('provider/model' if sys.argv[1:] == ['models'] else '1.18.31')\n"
            )
            executable.chmod(0o755)
            config = root / "provider.toml"
            config.write_text('name = "local"\n')
            provider = CommandProvider(
                {
                    "name": "local",
                    "command": ["opencode"],
                    "version_command": ["opencode", "--version"],
                    "models_command": ["opencode", "models"],
                    "roles": {},
                },
                config,
            )
            explicit = {
                "PATH": "",
                "HOME": str(root / "home"),
                "OPENCODE_TEST_MANAGED_CONFIG_DIR": str(root / "managed"),
            }
            for adapter in (opencode, provider):
                with self.subTest(adapter=getattr(adapter, "__name__", "command")):
                    launched, child, _ = adapter.launch(
                        "terra", workspace, root, None, "provider/model", None, True, env=explicit
                    )
                    actual = subprocess.run(
                        launched, cwd=workspace, env=child, capture_output=True, text=True, timeout=10
                    )
                    self.assertEqual(0, actual.returncode, actual.stderr)
                    self.assertEqual("1.18.31", actual.stdout.strip())
                    settings = adapter.local_settings(workspace, env=explicit)
                    self.assertEqual(executable.resolve(), Path(settings["executable"]).resolve())
                    self.assertEqual("1.18.31", settings["version"])
                    self.assertEqual({"provider/model"}, adapter.available_models(workspace, env=explicit))
            with self.assertRaisesRegex(RuntimeError, "not on PATH"):
                opencode.local_settings(workspace, env={key: value for key, value in explicit.items() if key != "PATH"})

    def test_ambient_missing_path_retains_default_shell_admission(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = root / "provider.toml"
            config.write_text('name = "shell"\n')
            provider = CommandProvider({"name": "shell", "command": ["sh"], "roles": {}}, config)
            with patch.dict(os.environ, {}, clear=True):
                settings = provider.local_settings(root)
            self.assertEqual("sh", Path(settings["executable"]).name)

    def test_relative_path_admission_uses_the_subprocess_workspace(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            workspace = root / "project"
            bindir = workspace / "bin"
            bindir.mkdir(parents=True)
            executable = bindir / "opencode"
            executable.write_text(
                f"#!{sys.executable}\nimport sys\n"
                "print('provider/model' if sys.argv[1:] == ['models'] else '1.18.31')\n"
            )
            executable.chmod(0o755)
            tools = Path(__file__).resolve().parents[1] / "tools"
            script = (
                "import sys; sys.path.insert(0,sys.argv[1]); from providers import opencode; "
                "env={'PATH':'bin'}; "
                "assert opencode.available_models(sys.argv[2],env=env)=={'provider/model'}; "
                "assert opencode.local_settings(sys.argv[2],env=env)['version']=='1.18.31'"
            )
            result = subprocess.run(
                [sys.executable, "-c", script, str(tools), str(workspace)],
                cwd=root,
                env={"PATH": "bin"},
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_missing_version_interpreter_is_an_honest_no_launch(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            bindir = root / "bin"
            bindir.mkdir()
            executable = bindir / "opencode"
            executable.write_text(f"#!{root / 'missing-interpreter'}\n")
            executable.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "no provider request was launched"):
                opencode.local_settings(root, env={"PATH": str(bindir), "HOME": str(root)})

    def test_absolute_command_without_path_is_admitted(self):
        scratch = Path.cwd() / ".autocode"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as name:
            root = Path(name)
            executable = root / "provider-tool"
            executable.write_text(f"#!{sys.executable}\nprint('1.0')\n")
            executable.chmod(0o755)
            config = root / "provider.toml"
            config.write_text('name = "absolute"\n')
            provider = CommandProvider(
                {
                    "name": "absolute",
                    "command": [str(executable)],
                    "version_command": [str(executable), "--version"],
                    "roles": {},
                },
                config,
            )

            # execve can run an absolute command even without PATH. Admission
            # should retain that capability and pass the same environment to
            # the version check and eventual launch.
            explicit = {"HOME": str(root)}
            settings = provider.local_settings(root, env=explicit)
            self.assertEqual(str(executable), settings["executable"])
            self.assertEqual("1.0", settings["version"])
            launched, child, _ = provider.launch("terra", root, root, None, "model", None, True, env=explicit)
            self.assertEqual([str(executable)], launched)
            self.assertEqual(explicit, child)

    def test_bare_roster_without_explicit_path_cannot_launch(self):
        scratch = Path.cwd() / ".autocode"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as name:
            root = Path(name)
            config = root / "provider.toml"
            config.write_text('name = "bare"\n')
            marker = root / "launched"
            provider = CommandProvider(
                {
                    "name": "bare",
                    "command": ["sh"],
                    "models_command": ["sh", "-c", f"touch {shlex.quote(str(marker))}; printf 'demo\\n'"],
                    "roles": {},
                },
                config,
            )

            # A direct models lookup is also a preflight. POSIX execvp may use
            # /bin:/usr/bin when PATH is absent; that fallback is not in the
            # caller's explicit mapping and must never launch the command.
            with self.assertRaisesRegex(RuntimeError, "no agent was launched"):
                provider.available_models(root, env={"HOME": str(root)})
            self.assertFalse(marker.exists(), "models command ran outside the explicit PATH")


if __name__ == "__main__":
    unittest.main()
