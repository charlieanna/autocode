"""autocode doctor and autocode --version (issue #67)."""
import json
import os
import shutil
import importlib.util
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_doctor as doctor

REPO_ROOT = Path(__file__).resolve().parents[1]


def fake_runner(outputs):
    """A command runner answering from a table: tuple(cmd) -> (exit code, stdout)."""
    def runner(cmd, cwd=None):
        code, out = outputs.get(tuple(cmd), (127, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")
    return runner


def on_path(*names):
    return lambda name: f"/bin/{name}" if name in names else None


class EngineTests(unittest.TestCase):
    def test_opencode_1x_and_2x_are_ready_and_other_majors_are_refused(self):
        for version, status in (("1.18.32", doctor.OK), ("2.0.1", doctor.OK),
                                ("opencode v2.0.20", doctor.OK), ("0.9.0", doctor.MISSING),
                                ("3.0.0", doctor.MISSING)):
            with self.subTest(version=version):
                checks = doctor.engine_checks(on_path("opencode"), fake_runner({("opencode", "--version"): (0, version)}))
                self.assertEqual(status, {c.name: c.status for c in checks}["engine:opencode"])

    def test_codex_must_be_logged_in(self):
        for code, status in ((0, doctor.OK), (1, doctor.MISSING)):
            checks = doctor.engine_checks(on_path("codex"), fake_runner({("codex", "login", "status"): (code, "")}))
            self.assertEqual(status, {c.name: c.status for c in checks}["engine:codex"])

    def test_the_default_engine_must_be_ready_and_a_ready_codex_is_named_as_the_alternative(self):
        codex_only = doctor.engine_checks(on_path("codex"), fake_runner({("codex", "login", "status"): (0, "")}))
        verdict = doctor.engine_verdict(codex_only, None)
        self.assertEqual(doctor.MISSING, verdict.status)
        self.assertIn("--engine codex", verdict.fix)
        self.assertFalse(doctor.passed([verdict]))
        self.assertEqual(doctor.OK, doctor.engine_verdict(codex_only, "codex").status)
        self.assertEqual(doctor.MISSING, doctor.engine_verdict(codex_only, "opencode").status)
        opencode_only = doctor.engine_checks(on_path("opencode"), fake_runner({("opencode", "--version"): (0, "1.18.33")}))
        self.assertEqual(doctor.OK, doctor.engine_verdict(opencode_only, None).status)
        nothing = doctor.engine_verdict(doctor.engine_checks(on_path(), fake_runner({})), None)
        self.assertEqual(doctor.MISSING, nothing.status)
        self.assertNotIn("--engine codex", nothing.fix)

    def test_the_default_is_resolved_as_a_run_resolves_it(self):
        import autocode_providers
        runner_choice = lambda: autocode_providers.select(None, {}, default=None)  # autocode.py, no --engine codex
        with tempfile.TemporaryDirectory() as config, patch.dict(os.environ, {"XDG_CONFIG_HOME": config}):
            os.environ.pop("AUTOCODE_PROVIDER", None)
            self.assertEqual(("opencode", "opencode"), (runner_choice(), doctor.default_provider()))
            (Path(config) / "autocode").mkdir()
            (Path(config) / "autocode" / "config.toml").write_text('default_provider = "fixturetool"\n')
            self.assertEqual(("fixturetool", "fixturetool"), (runner_choice(), doctor.default_provider()))
            os.environ["AUTOCODE_PROVIDER"] = "othertool"
            self.assertEqual(("othertool", "othertool"), (runner_choice(), doctor.default_provider()))

    def test_a_configured_default_provider_is_checked_and_decides_the_verdict(self):
        class Provider:
            def __init__(self, error=None):
                self.error = error

            def local_settings(self):
                if self.error:
                    raise self.error
                return {"version": "1.0", "config_path": "/config/fixturetool.toml"}

            def available_models(self, workspace=None):
                return None
        ready = doctor.provider_check("fixturetool", lambda name: Provider())
        self.assertEqual(("provider:fixturetool", doctor.OK), (ready.name, ready.status))
        self.assertEqual(doctor.OK, doctor.engine_verdict([ready], None, "fixturetool").status)
        absent = doctor.provider_check("fixturetool", lambda name: Provider(RuntimeError("'fixture' is not on PATH")))
        self.assertEqual(doctor.MISSING, absent.status)
        opencode = doctor.engine_checks(on_path("opencode"), fake_runner({("opencode", "--version"): (0, "1.18.33")}))
        self.assertEqual(doctor.MISSING, doctor.engine_verdict([*opencode, absent], None, "fixturetool").status)
        with tempfile.TemporaryDirectory() as config, patch.dict(os.environ, {"XDG_CONFIG_HOME": config,
                                                                              "AUTOCODE_PROVIDER": "nosuchtool"}):
            checks = {c.name: c for c in doctor.all_checks(Path(config), None, on_path("opencode"),
                                                           fake_runner({("opencode", "--version"): (0, "1.18.33")}))}
        self.assertEqual(doctor.MISSING, checks["provider:nosuchtool"].status)
        self.assertIn("nosuchtool", checks["engine"].detail)
        self.assertEqual(doctor.MISSING, checks["engine"].status)
        # --engine opencode runs the default provider too, so it is the one checked.
        with tempfile.TemporaryDirectory() as config, patch.dict(os.environ, {"XDG_CONFIG_HOME": config,
                                                                              "AUTOCODE_PROVIDER": "fixturetool"}):
            flagged = {c.name: c for c in doctor.all_checks(
                Path(config), "opencode", on_path("opencode"), fake_runner({("opencode", "--version"): (0, "1.18.33")}),
                lambda name: Provider(RuntimeError("'fixture' is not on PATH")))}
        self.assertEqual(doctor.OK, flagged["engine:opencode"].status)
        self.assertEqual(doctor.MISSING, flagged["provider:fixturetool"].status)
        self.assertEqual(doctor.MISSING, flagged["engine"].status)
        self.assertFalse(doctor.passed(list(flagged.values())))

    def test_a_default_provider_named_codex_is_a_provider_config_not_the_codex_engine(self):
        codex = fake_runner({("codex", "login", "status"): (0, "")})
        with tempfile.TemporaryDirectory() as config, patch.dict(os.environ, {"XDG_CONFIG_HOME": config,
                                                                              "AUTOCODE_PROVIDER": "codex"}):
            checks = {c.name: c for c in doctor.all_checks(Path(config), None, on_path("codex"), codex)}
            codex_engine = {c.name: c for c in doctor.all_checks(Path(config), "codex", on_path("codex"), codex)}
        self.assertEqual(doctor.OK, checks["engine:codex"].status)
        self.assertEqual(doctor.MISSING, checks["provider:codex"].status)
        self.assertIn("no provider config for 'codex'", checks["provider:codex"].detail)
        self.assertEqual(doctor.MISSING, checks["engine"].status)
        self.assertIn("pass --engine codex", checks["engine"].fix)
        self.assertFalse(doctor.passed(list(checks.values())))
        self.assertEqual(doctor.OK, codex_engine["engine"].status)
        self.assertNotIn("provider:codex", codex_engine)

    def test_the_default_routes_must_be_in_the_model_list(self):
        import providers.opencode as real

        def opencode(listed):
            class Facade:
                DEFAULT_MODELS = real.DEFAULT_MODELS

                @staticmethod
                def available_models(workspace=None):
                    if isinstance(listed, Exception):
                        raise listed
                    return listed
            return lambda name: Facade

        ready = fake_runner({("opencode", "--version"): (0, "1.18.31"), ("codex", "login", "status"): (0, "")})
        everything = set(real.DEFAULT_MODELS.values())
        cases = (({"opencode/big-pickle"}, doctor.MISSING, doctor.MISSING),
                 (everything, doctor.OK, doctor.OK),
                 (RuntimeError("OpenCode model listing timed out"), doctor.WARN, doctor.OK))
        with tempfile.TemporaryDirectory() as config, patch.dict(os.environ, {"XDG_CONFIG_HOME": config}):
            os.environ.pop("AUTOCODE_PROVIDER", None)
            for listed, routes, verdict in cases:
                with self.subTest(listed=listed):
                    checks = {c.name: c for c in doctor.all_checks(Path(config), None, on_path("opencode", "codex"),
                                                                   ready, opencode(listed))}
                    self.assertEqual((routes, verdict), (checks["routes"].status, checks["engine"].status))
            codex = {c.name: c for c in doctor.all_checks(Path(config), "codex", on_path("opencode", "codex"),
                                                          ready, opencode({"opencode/big-pickle"}))}
        no_logins = doctor.route_check("opencode", None, opencode({"opencode/big-pickle"}))
        self.assertIn("zai-coding-plan/glm-5.3", no_logins.detail)
        self.assertIn("openai/gpt-6-sol", no_logins.detail)
        for hint in ("Z.AI Coding Plan", "ChatGPT", "autocode models"):
            self.assertIn(hint, no_logins.fix)
        verdict = doctor.engine_verdict([*doctor.engine_checks(on_path("opencode", "codex"), ready), no_logins])
        self.assertIn("pass --engine codex", verdict.fix)
        self.assertNotIn("routes", codex)  # Codex's own transport has no OpenCode routes
        self.assertEqual(doctor.OK, codex["engine"].status)

    def test_old_python_is_missing(self):
        self.assertEqual(doctor.MISSING, doctor.python_check((3, 10, 9)).status)
        self.assertEqual(doctor.OK, doctor.python_check((3, 11, 0)).status)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="doctor-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.test", *args], cwd=self.root,
                       check=True, capture_output=True)

    def statuses(self):
        return {c.name: c.status for c in doctor.workspace_check(self.root)}

    def test_a_folder_that_is_not_a_repository_is_missing(self):
        self.assertEqual({"workspace": doctor.MISSING}, self.statuses())

    def test_a_repository_needs_a_commit(self):
        self.git("init", "-q")
        self.assertEqual({"workspace": doctor.MISSING}, self.statuses())
        self.git("commit", "-q", "--allow-empty", "-m", "start")
        self.assertEqual({"workspace": doctor.OK}, self.statuses())

    def test_uncommitted_changes_warn_but_do_not_block(self):
        self.git("init", "-q")
        self.git("commit", "-q", "--allow-empty", "-m", "start")
        (self.root / "notes.txt").write_text("draft")
        checks = doctor.workspace_check(self.root)
        self.assertEqual(doctor.WARN, {c.name: c.status for c in checks}["workspace:clean"])
        self.assertTrue(doctor.passed(checks))


class LocalComposeTests(unittest.TestCase):
    def test_missing_docker_and_failed_readiness_are_optional_warnings(self):
        checks = doctor.local_compose_checks(on_path(), fake_runner({}))
        self.assertEqual([("docker", doctor.WARN)], [(c.name, c.status) for c in checks])
        self.assertTrue(doctor.passed(checks))
        outputs = {("docker", "compose", "version", "--short"): (0, "2.29.1"),
                   ("docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"): (0, "unix:///tmp/docker.sock"),
                   ("docker", "--host", "unix:///tmp/docker.sock", "version", "--format", "{{.Server.Version}}"): (0, "27.0.0")}
        with patch.dict(os.environ, {"DOCKER_HOST": "", "DOCKER_CONTEXT": ""}):
            checks = doctor.local_compose_checks(on_path("docker"), fake_runner(outputs))
            self.assertTrue(all(c.status == doctor.OK for c in checks))
            for command, replacement, name in (
                    (("docker", "compose", "version", "--short"), (0, "2.16.9"), "docker:compose"),
                    (("docker", "--host", "unix:///tmp/docker.sock", "version", "--format", "{{.Server.Version}}"), (1, "stopped"), "docker:daemon"),
                    (("docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"), (0, "ssh://other"), "docker:context")):
                checks = doctor.local_compose_checks(on_path("docker"), fake_runner(outputs | {command: replacement}))
                self.assertEqual(doctor.WARN, next(c.status for c in checks if c.name == name))
                self.assertTrue(doctor.passed(checks))


class CliTests(unittest.TestCase):
    def autocode(self, *args, cwd=REPO_ROOT, env=None):
        return subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "autocode.py"), *args], cwd=cwd,
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "PATH": os.defpath, **(env or {})})

    def test_help_names_the_commands_handled_before_the_options(self):
        import autocode_subcommands as sub
        proc = self.autocode("--help")
        self.assertEqual(0, proc.returncode, proc.stderr)
        listed = proc.stdout.split("Commands, typed first:", 1)[1].replace("\n", " ")
        for word in ("--version", "doctor", "status", "resume", "docs/cli.md", *sub.SUBCOMMANDS):
            self.assertIn(word, listed)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_config_is_reported_in_the_json(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "autocode" / "config.toml"
            config.parent.mkdir()
            config.write_text('default_provider = "opencode"\n')
            config.chmod(0)
            try:
                proc = self.autocode("doctor", "--json", "--workspace", folder,
                                     env={"XDG_CONFIG_HOME": folder, "AUTOCODE_PROVIDER": ""})
            finally:
                config.chmod(0o600)
        self.assertEqual(1, proc.returncode, proc.stderr)
        checks = {c["name"]: c for c in json.loads(proc.stdout)["checks"]}
        self.assertEqual(doctor.MISSING, checks["engine"]["status"])
        self.assertIn("Permission denied", checks["engine"]["detail"])
        self.assertEqual(doctor.OK, checks["python"]["status"])

    def test_version_names_the_package_version(self):
        proc = self.autocode("--version")
        self.assertEqual(0, proc.returncode, proc.stderr)
        version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]
        self.assertTrue(proc.stdout.startswith(f"autocode {version} ("), proc.stdout)

    def test_doctor_reports_json_and_fails_without_a_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = self.autocode("doctor", "--json", "--workspace", folder)
        self.assertEqual(1, proc.returncode, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(doctor.MISSING, {c["name"]: c["status"] for c in report["checks"]}["workspace"])


class SourceCommitTests(unittest.TestCase):
    def test_commit_is_reported_only_from_the_autocode_checkout(self):
        import autocode_subcommands as sub
        # Running from this checkout: the commit is AutoCode's.
        commit = sub.source_commit()
        self.assertTrue(commit and commit[:1] in "0123456789abcdef", commit)

    def test_an_installed_package_inside_another_repository_reports_unknown(self):
        import autocode_subcommands as sub
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "F"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "f@t"], check=True)
            (project / "app.py").write_text("hi\n")
            subprocess.run(["git", "-C", str(project), "add", "app.py"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "user project"], check=True)
            site = project / ".venv/lib/python3/site-packages/autocode_cli"
            site.mkdir(parents=True)
            # A real checkout's tools/ module, relocated under the user's repository.
            shutil.copy2(Path(sub.__file__), site / "autocode_subcommands.py")
            loaded = importlib.util.spec_from_file_location("installed_subcommands", site / "autocode_subcommands.py")
            module = importlib.util.module_from_spec(loaded)
            loaded.loader.exec_module(module)
            self.assertIsNone(module.source_commit())
            self.assertIn("commit unknown", module.version_line())


if __name__ == "__main__":
    unittest.main()
