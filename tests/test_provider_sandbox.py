"""Registered-provider sandbox contract; real CLI qualification is recorded separately."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from autocode_taskrun import TaskRun
from providers import command


class ProviderSandboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / 'project with "quotes" and é'
        self.project.mkdir()
        self.run = self.project / ".autocode/runs/probe"
        self.report = self.run / "iterations/001/validator-01.json"
        self.report.parent.mkdir(parents=True)
        patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")})
        patch.start()
        self.addCleanup(patch.stop)
        self.config = {
            "name": "sandbox_probe",
            "sandbox_adapter": "codex_artifacts",
            "command": [
                "codex",
                "exec",
                "{sandbox_args}",
                "-C",
                "{workspace}",
                "--model",
                "{model}",
                "-o",
                "{report}",
                "--json",
                "-",
            ],
            "version_command": ["codex", "--version"],
            "output": "report_file",
        }

    def provider(self):
        path = command.user_config_path("sandbox_probe")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in self.config.items())
        roles = "\n".join(
            f'{role} = {{ model = "model-{role}", effort = "medium" }}' for role in command.REQUIRED_ROLES
        )
        path.write_text(text + "\n[roles]\n" + roles + "\n")
        return command.load("sandbox_probe")

    def launch(self, **kwargs):
        args = {
            "role": "sol",
            "workspace": self.project,
            "run_dir": self.run,
            "session": None,
            "model": "model-sol",
            "effort": "medium",
            "allow_write": False,
            "report": self.report,
        }
        args.update(kwargs)
        return self.provider().launch(**args)[0]

    def test_readonly_grants_only_operational_paths_and_preserves_argv(self):
        argv = self.launch()
        settings = {}
        for i, part in enumerate(argv[:-1]):
            if part == "-c":
                settings.update(tomllib.loads(argv[i + 1]))
        self.assertEqual("autocode_artifacts", settings["default_permissions"])
        self.assertEqual(
            {
                str(self.project / ".autocode/evidence"): "write",
                str(self.project / ".autocode/output"): "write",
                str(self.report): "write",
            },
            settings["permissions"]["autocode_artifacts"]["filesystem"],
        )
        self.assertEqual(":read-only", settings["permissions"]["autocode_artifacts"]["extends"])
        self.assertIn("--ignore-user-config", argv)
        self.assertNotIn("--sandbox", argv)
        self.assertEqual(str(self.project), argv[argv.index("-C") + 1])
        self.assertEqual(str(self.report), argv[argv.index("-o") + 1])

    def test_builder_retains_workspace_write_and_planning_stays_readonly(self):
        argv = self.launch(role="terra", allow_write=True)
        self.assertEqual("workspace-write", argv[argv.index("--sandbox") + 1])
        self.assertIn("--ignore-user-config", argv)
        planning = self.launch(role="astra", allow_write=True, planning=True)
        self.assertNotIn("--sandbox", planning)
        with self.assertRaisesRegex(ValueError, "source policy"):
            self.launch(sandbox="danger-full-access")

    def test_adapter_is_explicit_and_splice_must_be_one_whole_argument(self):
        original = dict(self.config)
        cases = [
            ({"sandbox_adapter": "unknown"}, "sandbox_adapter"),
            ({"output": "opencode_events", "resume": ["--session", "{session}"]}, "report_file"),
            ({"command": ["codex", "exec", "{sandbox_args}", "{sandbox_args}", "-o", "{report}"]}, "standalone"),
            ({"command": ["codex", "exec", "--args={sandbox_args}", "-o", "{report}"]}, "standalone"),
            ({"command": ["codex", "exec", "-o", "{report}"]}, "standalone"),
            ({"command": ["codex", "exec", "{sandbox_args}"]}, "persistence"),
            ({"version_command": []}, "version_command"),
        ]
        for update, error in cases:
            with self.subTest(update=update):
                self.config = {**original, **update}
                with self.assertRaisesRegex(ValueError, error):
                    self.provider()
        self.config = dict(original)
        del self.config["sandbox_adapter"]
        with self.assertRaisesRegex(ValueError, "requires sandbox_adapter"):
            self.provider()

    def test_conflicting_sandbox_flags_are_rejected_before_launch(self):
        original = self.config["command"]
        for extra in (
            ["--sandbox", "workspace-write"],
            ["--sandbox=workspace-write"],
            ["-sworkspace-write"],
            ["--add-dir", "/"],
            ["-p", "other"],
            ["--full-auto"],
            ["--dangerously-bypass-approvals-and-sandbox"],
            ["-c", 'sandbox_mode="workspace-write"'],
            ['-cdefault_permissions="other"'],
            ["--config=permissions.other={}"],
            ["-c", 'sandbox_workspace_write.writable_roots=["/"]'],
            ["-c", 'projects.other.trust_level="trusted"'],
            ["{sandbox}"],
        ):
            with self.subTest(extra=extra):
                self.config["command"] = original + extra
                with self.assertRaises(ValueError):
                    self.provider()

    def test_workspace_flags_cannot_replace_the_runner_workspace(self):
        original = self.config['command']
        for extra in (['--cd', '/'], ['--cd=/'], ['-C', '/'], ['-C/'], ['-C=/'],
                      ['--cd', '.'], ['--cd', '{workspace}/..'], ['-C{workspace}/child'],
                      ['--cd', '{run_dir}'], ['--cwd', '/'], ['--cd'], ['-C']):
            with self.subTest(extra=extra):
                self.config['command'] = original + extra
                with self.assertRaises(ValueError):
                    self.provider()

    def test_equivalent_sandbox_and_worktree_flags_are_refused(self):
        original = self.config['command']
        for extra in (['--yolo'], ['--not-so-yolo'], ['--worktree']):
            with self.subTest(extra=extra):
                self.config['command'] = original + extra
                with self.assertRaises(ValueError):
                    self.provider()

    def test_config_option_spellings_cannot_override_protected_keys(self):
        original = self.config['command']
        overrides = ('sandbox_mode="danger-full-access"',
                     '"sandbox_workspace_write".writable_roots=["/"]',
                     r'"\u0070ermissions".other=true',
                     "'projects'.outside.trust_level='trusted'",
                     'default_permissions="other"', 'profile="evil"',
                     '"profiles".evil.sandbox_mode="danger-full-access"')
        for override in overrides:
            for extra in (['-c', override], ['--config', override],
                          ['-c' + override], ['-c=' + override], ['--config=' + override]):
                with self.subTest(extra=extra):
                    self.config['command'] = original + extra
                    with self.assertRaises(ValueError):
                        self.provider()

    def test_malformed_or_json_config_is_refused_before_provider(self):
        original = self.config['command']
        # Provider templates escape literal braces before filling argv. These
        # values therefore reach sandbox validation through the public loader.
        values = ('{{"sandbox_mode":"danger-full-access"}}', '{{"display":"x=y"}}',
                  'no-assignment', '=missing-key', 'not a key=1', '[display]\nstyle=x',
                  '\ndisplay=x', 'display\r=x')
        for value in values:
            for extra in (['-c', value], ['--config', value], ['-c' + value],
                          ['-c=' + value], ['--config=' + value]):
                with self.subTest(extra=extra):
                    self.config['command'] = original + extra
                    with self.assertRaises(ValueError):
                        self.provider()
        for extra in (['-c'], ['--config'], ['--config='], ['-c=']):
            with self.subTest(extra=extra):
                self.config['command'] = original + extra
                with self.assertRaises(ValueError):
                    self.provider()

    def test_runner_workspace_bindings_and_harmless_config_remain_valid(self):
        original = self.config['command']
        # Replace the documented cwd pair rather than adding duplicate Codex flags.
        base = original[:3] + original[5:]
        for binding in ([], ['-C', '{workspace}'], ['--cd', '{workspace}'],
                        ['-C{workspace}'], ['-C={workspace}'], ['--cd={workspace}']):
            with self.subTest(binding=binding):
                self.config['command'] = base[:3] + binding + base[3:]
                argv = self.launch()
                for part in binding:
                    self.assertIn(part.replace('{workspace}', str(self.project)), argv)
        overrides = ('model_reasoning_effort="{effort}"', 'approval_policy="never"',
                     'forced_login_method="chatgpt"', '"display".style="literal --cd /"',
                     r'"display\nstyle"="x"')
        for override in overrides:
            for extra in (['-c', override], ['--config', override], ['-c' + override],
                          ['-c=' + override], ['--config=' + override]):
                with self.subTest(extra=extra):
                    self.config['command'] = original + extra
                    argv = self.launch()
                    for part in extra:
                        self.assertIn(part.replace('{effort}', 'medium'), argv)

    def test_documented_artifact_command_remains_compatible(self):
        documentation = (Path(__file__).resolve().parents[1] / 'docs/providers.md').read_text()
        section = documentation.split('### Codex commands that write capture receipts', 1)[1]
        template = tomllib.loads(section.split('```toml\n', 1)[1].split('```', 1)[0])
        self.config['command'] = template['command']
        argv = self.launch()
        self.assertEqual(str(self.project), argv[argv.index('-C') + 1])
        for override in ('model_reasoning_effort="medium"', 'approval_policy="never"',
                         'forced_login_method="chatgpt"'):
            self.assertIn(override, argv)

    def test_legacy_templates_and_escaped_braces_remain_unchanged(self):
        del self.config["sandbox_adapter"]
        self.config["command"] = ["tool", "--sandbox", "{sandbox}", "{{sandbox_args}}", "{effort}"]
        self.assertEqual(["tool", "--sandbox", "read-only", "{sandbox_args}", "medium"], self.launch())

    def test_report_must_be_an_unaliased_stage_json(self):
        for report in (
            self.root / "outside.json",
            self.run / "state.json",
            self.run / "iterations/001/../../state.json",
            self.run / "iterations/001/report.txt",
        ):
            with self.subTest(report=report), self.assertRaises(ValueError):
                self.launch(report=report)
        target = self.project / "source.json"
        target.write_text("{}")
        for link in ("symlink", "hardlink"):
            with self.subTest(link=link):
                if link == "symlink":
                    self.report.symlink_to(target)
                else:
                    self.report.hardlink_to(target)
                with self.assertRaises(ValueError):
                    self.launch()
                self.report.unlink()
        self.assertEqual("{}", target.read_text())

    def test_aliased_operational_roots_and_contents_are_rejected(self):
        source = self.project / "protected.txt"
        source.write_text("protected")
        outside = self.root / "outside"
        outside.mkdir()
        for name in ("evidence", "output"):
            operational = self.project / ".autocode" / name
            operational.symlink_to(outside, target_is_directory=True)
            with self.subTest(name=name, case="directory alias"), self.assertRaises(ValueError):
                self.launch()
            operational.unlink()
            operational.mkdir()
            nested = operational / "nested"
            nested.mkdir()
            link = nested / "alias"
            for kind in ("symlink", "hardlink"):
                if kind == "symlink":
                    link.symlink_to(source)
                else:
                    link.hardlink_to(source)
                with self.subTest(name=name, case=kind), self.assertRaises(ValueError):
                    self.launch()
                self.assertTrue(link.exists())  # refusal must not delete evidence
                link.unlink()
        self.assertEqual("protected", source.read_text())

    def test_report_parent_alias_and_run_traversal_are_rejected(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        alias = self.run / "iterations/alias"
        alias.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.launch(report=alias / "report.json")
        with self.assertRaises(ValueError):
            self.launch(run_dir=self.run / ".." / "probe")

    def test_version_is_checked_before_request(self):
        provider = self.provider()
        for version in ("codex-cli 0.159.0", "wrapper 1.0", "", "codex-cli 0.160.0", "codex-cli 0.161.0"):
            with (
                self.subTest(version=version),
                mock.patch.object(command.env_prep, "resolve_executable", return_value="/bin/codex"),
                mock.patch.object(
                    command.env_prep, "preflight_run", return_value=subprocess.CompletedProcess([], 0, version, "")
                ),
            ):
                if version in ("codex-cli 0.160.0", "codex-cli 0.161.0"):
                    self.assertEqual(version, provider.local_settings()["version"])
                else:
                    with self.assertRaisesRegex(RuntimeError, "no provider request was launched"):
                        provider.local_settings()

    def test_output_contract_uses_host_persistence_and_real_capture_receipts(self):
        prompt = self.provider().prompt_for_schema(
            "Task\nCURRENT HANDOFF DATA\n{}", {}, self.report.with_suffix(".jsonl")
        )
        self.assertIn("Codex persists", prompt)
        self.assertNotIn("Write your final report", prompt)
        self.assertIn("Never create or edit a receipt manually", prompt)

    def test_requirements_inherits_planner_effort_through_public_cli(self):
        del self.config["sandbox_adapter"]
        fixture = Path(__file__).resolve().parents[1] / "tools/fake_command_tool.py"
        self.config["command"] = [
            sys.executable,
            str(fixture),
            "--report",
            "{report}",
            "--sandbox",
            "{sandbox}",
            "--model",
            "{model}",
            "--role",
            "{role}",
        ]
        self.config["version_command"] = [sys.executable, "--version"]
        self.provider()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.project),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=t@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "base",
            ],
            check=True,
        )
        env = {"XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"], "AUTOCODE_HOME": str(self.root / "registry")}
        run = TaskRun.start(
            self.project,
            "Build a minimal greeting CLI",
            options=["--provider", "sandbox_probe", "--pause-after-stage"],
            env=env,
            timeout=30,
        )
        status = subprocess.run(
            [*run.command, "--workspace", str(run.workspace), "--run-dir", str(run.run_dir), "--status"],
            env={**os.environ, **env},
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        requirements = json.loads(status.stdout)["settings"]["roles"]["requirements"]
        self.assertEqual("model-glm", requirements["model"])
        self.assertEqual("medium", requirements["reasoning_effort"])
