"""autocode doctor and autocode --version (issue #67)."""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
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


class KeeperDependencyTests(unittest.TestCase):
    def test_pythonpath_only_psutil_is_missing_for_the_keeper(self):
        import venv

        import psutil

        with tempfile.TemporaryDirectory() as folder:
            prefix = Path(folder) / "isolated"
            venv.EnvBuilder(with_pip=False).create(prefix)
            python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            env = dict(os.environ, PYTHONPATH=str(Path(psutil.__file__).resolve().parents[1]))
            script = (
                "import json, sys; sys.path.insert(0, "
                + repr(str(REPO_ROOT / "tools"))
                + "); import autocode_doctor as d; from dataclasses import asdict; "
                + "print(json.dumps(asdict(d.psutil_check())))"
            )
            result = subprocess.run(
                [str(python), "-c", script], env=env, capture_output=True, text=True, timeout=30, check=True
            )
            check = json.loads(result.stdout)
            self.assertEqual(doctor.MISSING, check["status"])
            self.assertIn("-E", check["detail"])
            self.assertIn("site-packages", check["fix"])
            self.assertIn("PYTHONPATH", check["fix"])

    def test_workspace_psutil_module_does_not_shadow_the_keeper_dependency(self):
        import contextlib

        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "psutil.py").write_text("raise RuntimeError('workspace shadow')\n")
            with contextlib.chdir(folder):
                check = doctor.psutil_check()
            self.assertEqual(doctor.OK, check.status)

    def test_a_ready_keeper_environment_passes(self):
        self.assertEqual(doctor.OK, doctor.psutil_check().status)

    def test_a_failed_keeper_probe_is_actionable_without_exposing_stderr(self):
        def runner(cmd, cwd=None):
            self.assertEqual([sys.executable, "-E", "-c", "import psutil"], cmd)
            self.assertEqual(Path(doctor.__file__).resolve().parent, cwd)
            return subprocess.CompletedProcess(cmd, -1, "", "private setup detail")

        check = doctor.psutil_check(runner=runner)
        self.assertEqual(doctor.MISSING, check.status)
        self.assertNotIn("private setup detail", check.detail)
        self.assertIn("virtualenv", check.fix)


class EngineTests(unittest.TestCase):
    def test_opencode_1x_and_2x_are_ready_and_other_majors_are_refused(self):
        for version, status in (
            ("1.18.32", doctor.OK),
            ("2.0.1", doctor.OK),
            ("opencode v2.0.20", doctor.OK),
            ("0.9.0", doctor.MISSING),
            ("3.0.0", doctor.MISSING),
        ):
            with self.subTest(version=version):
                checks = doctor.engine_checks(
                    on_path("opencode"), fake_runner({("opencode", "--version"): (0, version)})
                )
                self.assertEqual(status, {c.name: c.status for c in checks}["engine:opencode"])

    def test_codex_must_be_logged_in(self):
        for code, status in ((0, doctor.OK), (1, doctor.MISSING)):
            checks = doctor.engine_checks(on_path("codex"), fake_runner({("codex", "login", "status"): (code, "")}))
            self.assertEqual(status, {c.name: c.status for c in checks}["engine:codex"])

    def test_an_ambient_api_key_on_a_codex_setup_names_the_exact_unset_command(self):
        with tempfile.TemporaryDirectory() as home:
            check = doctor.codex_billing_check(
                environ={"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "sk-fixture", "CODEX_HOME": home},
            )
        self.assertEqual(doctor.MISSING, check.status)
        self.assertIn("OPENAI_API_KEY", check.detail)
        self.assertIn("PAUSED_BILLING_ROUTE", check.detail)
        self.assertIn("unset OPENAI_API_KEY", check.fix)
        self.assertIn("autocode connections setup", check.fix)

    def test_a_codex_config_override_is_named_with_its_file(self):
        with tempfile.TemporaryDirectory() as home:
            config = Path(home) / "config.toml"
            config.write_text('model_provider = "litellm"\nopenai_base_url = "https://gateway.example/v1"\n')
            check = doctor.codex_billing_check(environ={"CODEX_HOME": home})
        self.assertEqual(doctor.MISSING, check.status)
        self.assertIn(str(config), check.detail)
        self.assertIn("model_provider", check.detail)
        self.assertIn("openai_base_url", check.detail)
        self.assertIn(f"remove those lines from {config}", check.fix)

    def test_a_clean_codex_environment_passes_and_engine_checks_include_the_row(self):
        with tempfile.TemporaryDirectory() as home:
            check = doctor.codex_billing_check(environ={"CODEX_HOME": home})
        self.assertEqual(doctor.OK, check.status)
        checks = doctor.engine_checks(on_path("codex"), fake_runner({("codex", "login", "status"): (0, "")}))
        self.assertIn("billing:codex", {c.name: c.status for c in checks})
        absent = doctor.engine_checks(on_path(), fake_runner({}))
        self.assertNotIn("billing:codex", {c.name: c.status for c in absent})

    def test_the_default_engine_must_be_ready_and_a_ready_codex_is_named_as_the_alternative(self):
        codex_only = doctor.engine_checks(on_path("codex"), fake_runner({("codex", "login", "status"): (0, "")}))
        verdict = doctor.engine_verdict(codex_only, None)
        self.assertEqual(doctor.MISSING, verdict.status)
        self.assertIn("--engine codex", verdict.fix)
        self.assertFalse(doctor.passed([verdict]))
        self.assertEqual(doctor.OK, doctor.engine_verdict(codex_only, "codex").status)
        self.assertEqual(doctor.MISSING, doctor.engine_verdict(codex_only, "opencode").status)
        opencode_only = doctor.engine_checks(
            on_path("opencode"), fake_runner({("opencode", "--version"): (0, "1.18.33")})
        )
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
        with (
            tempfile.TemporaryDirectory() as config,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": config, "AUTOCODE_PROVIDER": "nosuchtool"}),
        ):
            checks = {
                c.name: c
                for c in doctor.all_checks(
                    Path(config), None, on_path("opencode"), fake_runner({("opencode", "--version"): (0, "1.18.33")})
                )
            }
        self.assertEqual(doctor.MISSING, checks["provider:nosuchtool"].status)
        self.assertIn("nosuchtool", checks["engine"].detail)
        self.assertEqual(doctor.MISSING, checks["engine"].status)
        # --engine opencode runs the default provider too, so it is the one checked.
        with (
            tempfile.TemporaryDirectory() as config,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": config, "AUTOCODE_PROVIDER": "fixturetool"}),
        ):
            flagged = {
                c.name: c
                for c in doctor.all_checks(
                    Path(config),
                    "opencode",
                    on_path("opencode"),
                    fake_runner({("opencode", "--version"): (0, "1.18.33")}),
                    lambda name: Provider(RuntimeError("'fixture' is not on PATH")),
                )
            }
        self.assertEqual(doctor.OK, flagged["engine:opencode"].status)
        self.assertEqual(doctor.MISSING, flagged["provider:fixturetool"].status)
        self.assertEqual(doctor.MISSING, flagged["engine"].status)
        self.assertFalse(doctor.passed(list(flagged.values())))

    def test_a_default_provider_named_codex_is_a_provider_config_not_the_codex_engine(self):
        codex = fake_runner({("codex", "login", "status"): (0, "")})
        with (
            tempfile.TemporaryDirectory() as config,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": config, "AUTOCODE_PROVIDER": "codex"}),
        ):
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
        cases = (
            ({"opencode/big-pickle"}, doctor.MISSING, doctor.MISSING),
            (everything, doctor.OK, doctor.OK),
            (RuntimeError("OpenCode model listing timed out"), doctor.WARN, doctor.OK),
        )
        with tempfile.TemporaryDirectory() as config, patch.dict(os.environ, {"XDG_CONFIG_HOME": config}):
            os.environ.pop("AUTOCODE_PROVIDER", None)
            for listed, routes, verdict in cases:
                with self.subTest(listed=listed):
                    checks = {
                        c.name: c
                        for c in doctor.all_checks(
                            Path(config), None, on_path("opencode", "codex"), ready, opencode(listed)
                        )
                    }
                    self.assertEqual((routes, verdict), (checks["routes"].status, checks["engine"].status))
            codex = {
                c.name: c
                for c in doctor.all_checks(
                    Path(config), "codex", on_path("opencode", "codex"), ready, opencode({"opencode/big-pickle"})
                )
            }
        no_logins = doctor.route_check("opencode", None, opencode({"opencode/big-pickle"}))
        self.assertIn("zai-coding-plan/glm-5.3", no_logins.detail)
        self.assertIn("openai/gpt-6-sol", no_logins.detail)
        for hint in ("Z.AI Coding Plan", "ChatGPT", "autocode models"):
            self.assertIn(hint, no_logins.fix)
        verdict = doctor.engine_verdict([*doctor.engine_checks(on_path("opencode", "codex"), ready), no_logins])
        self.assertIn("pass --engine codex", verdict.fix)
        self.assertNotIn("routes", codex)  # Codex's own transport has no OpenCode routes
        self.assertEqual(doctor.OK, codex["engine"].status)

    def test_a_route_the_run_would_never_take_is_not_reported_missing(self):
        """#1995: a Kilo login that serves its Z.AI plan under another prefix still passes doctor."""
        import autocode_route_discovery as discovery

        listed = ["zai-coding-plan/glm-5.3", "openai/gpt-6-luna", "openai/gpt-6-sol", "openai/gpt-6-astra"]
        kilo = SimpleNamespace(
            NAME="kilocode",
            DEFAULT_MODELS={
                "requirements": "zhipuai-coding-plan/glm-5.3",
                "glm": "zhipuai-coding-plan/glm-5.3",
                "terra": "openai/gpt-6-luna",
                "plan_reviewer": "openai/gpt-6-sol",
                "sol": "zhipuai-coding-plan/glm-5.3",
                "completion": "zhipuai-coding-plan/glm-5.3",
                "astra": "openai/gpt-6-astra",
            },
            auth_table=lambda: {"command": ["kilo"], "routes": []},
            available_models=lambda workspace=None: listed,
        )
        found = {
            "tool": "kilocode",
            "accounts": [],
            "candidates": [
                {"model": model, "tool": "kilocode", "account": "Z.AI Coding Plan", "billing": "subscription"}
                for model in listed
            ],
        }
        with patch.object(discovery, "discover", return_value=found):
            check = doctor.route_check("kilocode", None, lambda name: kilo)
        self.assertEqual(doctor.OK, check.status)
        self.assertIn("automatic selection replaces", check.detail)
        self.assertIn("zhipuai-coding-plan/glm-5.3 -> zai-coding-plan/glm-5.3", check.detail)

    def test_a_selection_that_cannot_field_a_set_keeps_the_gap_and_says_why(self):
        """The profile gap stays a gap when the tool's listings cannot be read (#1995)."""
        import autocode_route_discovery as discovery

        kilo = SimpleNamespace(
            NAME="kilocode",
            DEFAULT_MODELS={"requirements": "zhipuai-coding-plan/glm-5.3"},
            auth_table=lambda: {"command": ["kilo"], "routes": []},
            available_models=lambda workspace=None: ["zai-coding-plan/glm-5.3"],
        )
        with patch.object(discovery, "discover", side_effect=RuntimeError("Cannot read the tool's model listing")):
            check = doctor.route_check("kilocode", None, lambda name: kilo)
        self.assertEqual(doctor.MISSING, check.status)
        self.assertIn("zhipuai-coding-plan/glm-5.3 (Requirements)", check.detail)
        self.assertIn("automatic selection cannot cover it", check.detail)

    def test_old_python_is_missing(self):
        self.assertEqual(doctor.MISSING, doctor.python_check((3, 10, 9)).status)
        self.assertEqual(doctor.OK, doctor.python_check((3, 11, 0)).status)


class ClaudeTests(unittest.TestCase):
    """#717: Claude Code rows appear only for --engine claude or a claude default provider; the injected
    runner answers for `claude`, so no host claude is executed."""

    LOGGED_IN = {
        ("claude", "--version"): (0, "2.1.299 (Claude Code)\n"),
        ("claude", "auth", "status", "--json"): (
            0,
            '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty", "email": "x@y", "orgName": "Org"}',
        ),
    }

    def setUp(self):
        config = tempfile.TemporaryDirectory()
        self.addCleanup(config.cleanup)
        self.config = Path(config.name)
        patcher = patch.dict(os.environ, {"XDG_CONFIG_HOME": config.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        # Doctor applies a run's billing gate to this process's environment: the developer's shell must not
        # decide these tests.
        for name in ("AUTOCODE_PROVIDER", "AUTOCODE_PASS_ENV", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            os.environ.pop(name, None)
        for name in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
            os.environ.pop(name, None)

    def checks(self, engine, which, outputs):
        return {c.name: c for c in doctor.all_checks(self.config, engine, which, fake_runner(outputs))}

    def test_engine_claude_needs_claude_on_path(self):
        checks = self.checks("claude", on_path(), {})
        self.assertEqual(
            (doctor.MISSING, "claude is not on PATH"), (checks["engine:claude"].status, checks["engine:claude"].detail)
        )
        self.assertEqual(
            (doctor.MISSING, "--engine claude is not ready"), (checks["engine"].status, checks["engine"].detail)
        )
        self.assertNotIn("conformance:claude", checks)

    def test_a_logged_in_claude_is_ready_without_printing_the_account(self):
        checks = self.checks("claude", on_path("claude"), self.LOGGED_IN)
        self.assertEqual(doctor.OK, checks["engine:claude"].status)
        self.assertEqual("Claude Code 2.1.299, logged in (claude.ai, firstParty)", checks["engine:claude"].detail)
        self.assertIn("conformance:claude", checks)
        self.assertEqual((doctor.OK, doctor.OK), (checks["routes"].status, checks["engine"].status))
        self.assertEqual("--engine claude is ready", checks["engine"].detail)
        shown = json.dumps([[c.detail, c.fix] for c in checks.values()])
        self.assertNotIn("x@y", shown)
        self.assertNotIn("Org", shown)

    def test_a_logged_out_claude_names_the_login(self):
        logged_out = {**self.LOGGED_IN, ("claude", "auth", "status", "--json"): (1, '{"loggedIn": false}')}
        checks = self.checks(
            "claude", on_path("claude", "codex"), {**logged_out, ("codex", "login", "status"): (0, "")}
        )
        self.assertEqual(doctor.MISSING, checks["engine:claude"].status)
        self.assertIn("claude auth login", checks["engine:claude"].detail)
        self.assertIn("claude auth login", checks["engine:claude"].fix)
        self.assertEqual(doctor.MISSING, checks["engine"].status)
        self.assertIn("pass --engine codex", checks["engine"].fix)

    def test_a_billing_route_every_run_refuses_is_not_ready(self):
        """With ANTHROPIC_AUTH_TOKEN passed through, Claude Code 2.1.299's `auth status` still reports the
        subscription login (checked live), yet every --engine claude run refuses the token."""
        oauth = {
            **self.LOGGED_IN,
            ("claude", "auth", "status", "--json"): (
                0,
                '{"loggedIn": true, "authMethod": "oauth_token", "apiProvider": "firstParty"}',
            ),
        }
        passed = {"AUTOCODE_PASS_ENV": "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN": "sk-dummy"}
        with patch.dict(os.environ, passed):
            checks = self.checks("claude", on_path("claude"), oauth)
        self.assertEqual(doctor.MISSING, checks["engine:claude"].status)
        self.assertIn("ANTHROPIC_AUTH_TOKEN reaches Claude Code", checks["engine:claude"].detail)
        self.assertIn(".claude/settings.json", checks["engine:claude"].fix)
        self.assertEqual(doctor.MISSING, checks["engine"].status)
        settings = self.config / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text('{"env": {"ANTHROPIC_API_KEY": "sk-project"}}')
        checks = self.checks("claude", on_path("claude"), oauth)
        self.assertEqual(doctor.MISSING, checks["engine:claude"].status)
        self.assertIn("sets env.ANTHROPIC_API_KEY", checks["engine:claude"].detail)
        settings.write_text('{"env": {},}')  # not JSON: the file is the problem, not the login
        checks = self.checks("claude", on_path("claude"), oauth)
        self.assertEqual(doctor.MISSING, checks["engine:claude"].status)
        self.assertIn("fix or remove the project's .claude/settings.json", checks["engine:claude"].fix)
        self.assertNotIn("claude auth login", checks["engine:claude"].fix)

    def test_a_projects_engine_is_the_one_checked(self):
        """A new run takes `engine` from the project's files when no --engine is given (#707), so doctor does."""
        project = self.config / ".autocode" / "config.toml"
        project.parent.mkdir()
        project.write_text('engine = "claude"\n')
        checks = self.checks(None, on_path("claude"), self.LOGGED_IN)
        self.assertEqual((doctor.OK, doctor.OK), (checks["engine:claude"].status, checks["engine"].status))
        self.assertEqual(f"engine claude (set in {project}) is ready", checks["engine"].detail)
        rows = {row["key"]: row for row in doctor.project_settings(self.config)[1]}
        self.assertEqual({"key": "provider", "value": "claude", "source": str(project)}, rows["provider"])
        ready_opencode = {
            ("claude", "--version"): (0, "2.1.299 (Claude Code)\n"),
            ("claude", "auth", "status", "--json"): (1, '{"loggedIn": false}'),
            ("opencode", "--version"): (0, "1.18.33"),
        }
        checks = self.checks(None, on_path("claude", "opencode"), ready_opencode)
        self.assertEqual(doctor.MISSING, checks["engine"].status)  # a ready OpenCode is not what runs
        self.assertIn("engine claude (set in", checks["engine"].detail)
        project.write_text('engine = "codex"\n')
        checks = self.checks(None, on_path("codex"), {("codex", "login", "status"): (0, "")})
        self.assertEqual(
            (doctor.OK, f"engine codex (set in {project}) is ready"), (checks["engine"].status, checks["engine"].detail)
        )
        self.assertNotIn("engine:claude", checks)

    def test_a_claude_default_provider_takes_the_same_checks(self):
        with patch.dict(os.environ, {"AUTOCODE_PROVIDER": "claude"}):
            checks = self.checks(None, on_path("claude", "opencode"), self.LOGGED_IN)
        self.assertEqual(doctor.OK, checks["engine:claude"].status)
        self.assertEqual("provider claude (the default for a new run) is ready", checks["engine"].detail)
        self.assertNotIn("provider:claude", checks)

    def test_a_user_claude_toml_is_named_and_ignored(self):
        user = self.config / "autocode" / "providers"
        user.mkdir(parents=True)
        (user / "claude.toml").write_text('name = "claude"\n')
        checks = self.checks("claude", on_path("claude"), self.LOGGED_IN)
        self.assertEqual(doctor.WARN, checks["provider-config:claude"].status)
        self.assertIn("is ignored: claude is built in", checks["provider-config:claude"].detail)
        self.assertEqual(doctor.OK, checks["engine"].status)

    def test_other_runs_see_no_claude_rows(self):
        ready = fake_runner({("opencode", "--version"): (0, "1.18.33")})
        self.assertEqual("engine:opencode", doctor.engine_checks(on_path("opencode", "claude"), ready)[0].name)
        for engine in (None, "opencode", "codex"):
            with self.subTest(engine=engine):
                names = [c.name for c in doctor.all_checks(self.config, engine, on_path("opencode", "claude"), ready)]
                self.assertFalse([name for name in names if "claude" in name])


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="doctor-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def git(self, *args):
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
        )

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
        outputs = {
            ("docker", "compose", "version", "--short"): (0, "2.29.1"),
            ("docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"): (0, "unix:///tmp/docker.sock"),
            ("docker", "--host", "unix:///tmp/docker.sock", "version", "--format", "{{.Server.Version}}"): (
                0,
                "27.0.0",
            ),
        }
        with patch.dict(os.environ, {"DOCKER_HOST": "", "DOCKER_CONTEXT": ""}):
            checks = doctor.local_compose_checks(on_path("docker"), fake_runner(outputs))
            self.assertTrue(all(c.status == doctor.OK for c in checks))
            for command, replacement, name in (
                (("docker", "compose", "version", "--short"), (0, "2.16.9"), "docker:compose"),
                (
                    ("docker", "--host", "unix:///tmp/docker.sock", "version", "--format", "{{.Server.Version}}"),
                    (1, "stopped"),
                    "docker:daemon",
                ),
                (
                    ("docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"),
                    (0, "ssh://other"),
                    "docker:context",
                ),
            ):
                checks = doctor.local_compose_checks(on_path("docker"), fake_runner(outputs | {command: replacement}))
                self.assertEqual(doctor.WARN, next(c.status for c in checks if c.name == name))
                self.assertTrue(doctor.passed(checks))


class ContainmentTests(unittest.TestCase):
    """#709: doctor names the kernel boundary a new built-in OpenCode run gets here, by run setup's check."""

    def setUp(self):
        config = tempfile.TemporaryDirectory()
        self.addCleanup(config.cleanup)
        # A new run's default provider is the built-in opencode unless a test says otherwise.
        patcher = patch.dict(os.environ, {"XDG_CONFIG_HOME": config.name, "AUTOCODE_PROVIDER": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace = Path(config.name).resolve()

    def host(self, platform="linux", sandbox=None, version="bubblewrap 0.9.0"):
        """This host's sandbox, simulated: its platform, the probe's refusal (None: works) and its version."""
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(doctor.tool_containment, "sys", SimpleNamespace(platform=platform)))
        stack.enter_context(patch.object(doctor.tool_containment, "sandbox_problem", return_value=sandbox))
        stack.enter_context(patch.object(doctor.tool_containment, "backend_version", return_value=version))
        return stack

    def opencode(self, version):
        return subprocess.CompletedProcess(["opencode", "--version"], 0, version, "")

    @staticmethod
    def resolve(name):
        """A provider that answers and lists no models, so no real `opencode models` runs."""
        return SimpleNamespace(available_models=lambda workspace: None, local_settings=lambda: {})

    def test_a_working_sandbox_with_the_qualified_opencode_names_the_backend_and_its_version(self):
        with self.host():
            linux = doctor.containment_status(self.opencode("1.18.33"))
        self.assertEqual({"backend": "bubblewrap", "backend_version": "bubblewrap 0.9.0", "problem": None}, linux)
        check = doctor.containment_check(linux)
        self.assertEqual(("containment", doctor.OK), (check.name, check.status))
        self.assertIn("Linux bubblewrap (/usr/bin/bwrap), bubblewrap 0.9.0", check.detail)
        with self.host("darwin", version="macOS 15.3"):
            mac = doctor.containment_status(self.opencode("1.18.33"))
        self.assertEqual({"backend": "seatbelt", "backend_version": "macOS 15.3", "problem": None}, mac)
        self.assertIn("macOS Seatbelt (/usr/bin/sandbox-exec), macOS 15.3", doctor.containment_check(mac).detail)

    def test_another_opencode_names_the_sandbox_but_warns_that_a_run_is_refused(self):
        for version in ("1.18.31", "2.0.1"):
            with self.subTest(version=version):
                with self.host():
                    status = doctor.containment_status(self.opencode(version))
                self.assertEqual(("bubblewrap", "bubblewrap 0.9.0"), (status["backend"], status["backend_version"]))
                self.assertIn(
                    "qualified only for OpenCode 1.18.33; this machine has OpenCode " + version, status["problem"]
                )
                check = doctor.containment_check(status)
                self.assertEqual(doctor.WARN, check.status)
                self.assertIn("bubblewrap 0.9.0 works, but", check.detail)
                self.assertIn("refused at setup without --allow-uncontained-tools", check.detail)
                self.assertIn("--allow-uncontained-tools", check.fix)
                self.assertIn("docs/execution.md#native-tool-containment", check.fix)
                # Worth knowing, not blocking: a run can still opt out explicitly.
                self.assertTrue(doctor.passed([check]))

    def test_the_engine_line_leaves_the_containment_qualification_to_the_containment_line(self):
        # Before #709 the engine:opencode line carried a 2.x-only note; the containment line now says it.
        checks = doctor.engine_checks(on_path("opencode"), fake_runner({("opencode", "--version"): (0, "2.0.1")}))
        self.assertEqual("OpenCode 2.0.1", {c.name: c for c in checks}["engine:opencode"].detail)

    def test_without_opencode_the_row_names_the_sandbox_and_defers_to_the_engine_line(self):
        # No run starts without OpenCode, so --allow-uncontained-tools would not help: engine:opencode has the fix.
        with self.host():
            status = doctor.containment_status(None)
        self.assertEqual(doctor.tool_containment.NO_OPENCODE, status["problem"])
        check = doctor.containment_check(status)
        self.assertEqual(doctor.WARN, check.status)
        self.assertIn("bubblewrap 0.9.0 works; no opencode is on PATH", check.detail)
        self.assertIn("engine:opencode", check.fix)
        self.assertNotIn("--allow-uncontained-tools", check.detail + check.fix)

    def test_no_working_sandbox_names_no_backend(self):
        problem = "strict tool containment requires macOS sandbox-exec or Linux bubblewrap at /usr/bin/bwrap"
        with self.host(sandbox=problem):
            status = doctor.containment_status(self.opencode("1.18.33"))
        self.assertEqual({"backend": None, "backend_version": None, "problem": problem}, status)
        check = doctor.containment_check(status)
        self.assertEqual(doctor.WARN, check.status)
        self.assertTrue(check.detail.startswith("no kernel boundary: " + problem), check.detail)

    def test_opencode_answers_once_in_the_workspace_and_both_checks_read_it(self):
        # One probe for the engine and containment lines, in the workspace, as run setup runs it.
        calls = []

        def runner(cmd, cwd=None):
            calls.append((tuple(cmd), cwd))
            return subprocess.CompletedProcess(cmd, 0, "1.18.33" if cmd[0] == "opencode" else "", "")

        found = {}
        with self.host():
            checks = doctor.all_checks(self.workspace, None, on_path("opencode"), runner, self.resolve, found=found)
        status = found["containment"]
        self.assertEqual([(("opencode", "--version"), self.workspace)], [c for c in calls if c[0][0] == "opencode"])
        rows = {c.name: c for c in checks}
        self.assertEqual("OpenCode 1.18.33", rows["engine:opencode"].detail)
        self.assertEqual(doctor.containment_check(status), rows["containment"])
        self.assertIsNone(status["problem"])

    def test_only_a_run_that_launches_built_in_opencode_stages_gets_the_row(self):
        ready = fake_runner({("opencode", "--version"): (0, "1.18.33"), ("codex", "login", "status"): (0, "")})
        for engine, provider, shown in (
            (None, "", True),
            ("opencode", "", True),
            ("codex", "", False),
            ("claude", "", False),
            (None, "claude", False),
            (None, "fixturetool", False),  # a configured provider: its own sandbox flag, no kernel boundary
        ):
            with (
                self.subTest(engine=engine, provider=provider),
                patch.dict(os.environ, {"AUTOCODE_PROVIDER": provider}),
            ):
                found = {}
                with self.host():
                    checks = doctor.all_checks(
                        self.workspace, engine, on_path("opencode", "codex"), ready, self.resolve, found=found
                    )
                self.assertEqual(shown, "containment" in {c.name for c in checks})
                self.assertEqual(shown, found["containment"] is not None)


class CliTests(unittest.TestCase):
    def autocode(self, *args, cwd=REPO_ROOT, env=None):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "autocode.py"), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PATH": os.defpath, **(env or {})},
        )

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
                proc = self.autocode(
                    "doctor", "--json", "--workspace", folder, env={"XDG_CONFIG_HOME": folder, "AUTOCODE_PROVIDER": ""}
                )
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

    def test_doctor_json_names_the_containment_backend_this_machine_gives_a_new_run(self):
        # #709: the same sandbox check run setup makes. No opencode is on this empty PATH; a Codex run gets no row.
        import autocode_tool_containment as containment

        with tempfile.TemporaryDirectory() as folder:
            env = {"PATH": folder, "XDG_CONFIG_HOME": folder, "AUTOCODE_PROVIDER": ""}
            report = json.loads(self.autocode("doctor", "--json", "--workspace", folder, env=env).stdout)
            codex = json.loads(
                self.autocode("doctor", "--json", "--engine", "codex", "--workspace", folder, env=env).stdout
            )
        sandbox = containment.sandbox_problem()
        expected = None if sandbox else containment.backend()
        self.assertEqual(expected, report["containment"]["backend"])
        self.assertEqual(
            containment.backend_version(expected) if expected else None, report["containment"]["backend_version"]
        )
        self.assertEqual(sandbox or containment.NO_OPENCODE, report["containment"]["problem"])
        check = {c["name"]: c for c in report["checks"]}["containment"]
        self.assertEqual(doctor.WARN, check["status"])
        self.assertIn(sandbox or "works; no opencode is on PATH", check["detail"])
        self.assertIsNone(codex["containment"])
        self.assertNotIn("containment", {c["name"] for c in codex["checks"]})

    def test_doctor_shows_where_each_project_setting_comes_from_and_fails_on_a_refused_file(self):
        # #707: a new run's settings from .autocode/config.toml or [tool.autocode], with their source.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            config = root / ".autocode" / "config.toml"
            config.parent.mkdir()
            config.write_text('engine = "codex"\n')
            (root / "pyproject.toml").write_text('[tool.autocode]\ntest-command = "pytest -q"\n')
            report = json.loads(self.autocode("doctor", "--json", "--workspace", str(root)).stdout)
            config.write_text('provider = "mine"\n')
            refused = json.loads(self.autocode("doctor", "--json", "--workspace", str(root)).stdout)
        settings = {row["key"]: (row["value"], row["source"]) for row in report["settings"]}
        self.assertEqual(("codex", str(config)), settings["engine"])
        self.assertEqual(("pytest -q", f"{root / 'pyproject.toml'} [tool.autocode]"), settings["test-command"])
        self.assertEqual((None, "built-in default"), settings["sol-model"])
        self.assertIn("provider", settings)
        self.assertEqual(doctor.OK, {c["name"]: c["status"] for c in report["checks"]}["project settings"])
        check = {c["name"]: c for c in refused["checks"]}["project settings"]
        self.assertEqual(doctor.MISSING, check["status"])
        self.assertIn("provider cannot be set by a project", check["detail"])
        self.assertFalse(refused["ok"])


class SourceCommitTests(unittest.TestCase):
    def version(self, *entrypoint, cwd=REPO_ROOT, env=None):
        proc = subprocess.run(
            [sys.executable, *map(str, entrypoint), "--version"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, **(env or {})},
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return proc.stdout.strip()

    def test_commit_is_reported_only_from_the_autocode_checkout(self):
        commit = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        version = self.version(REPO_ROOT / "tools" / "autocode.py")
        self.assertRegex(version, rf"\(commit {commit}(?:\+modified)?\)$")

    def test_an_installed_package_inside_another_repository_reports_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "F"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "f@t"], check=True)
            (project / "app.py").write_text("hi\n")
            subprocess.run(["git", "-C", str(project), "add", "app.py"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "user project"], check=True)
            site = project / ".venv/lib/python3/site-packages/autocode_cli"
            shutil.copytree(REPO_ROOT / "tools", site, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            version = self.version("-m", "autocode_cli", cwd=project, env={"PYTHONPATH": str(site.parent)})
            self.assertIn("(commit unknown)", version)


class SharedHostCheckTests(unittest.TestCase):
    PASSWD = [
        "root:x:0:0:root:/root:/bin/bash\n",
        "me:x:1000:1000:me:/home/me:/bin/bash\n",
        "svc:x:999:999:service:/srv:/usr/sbin/nologin\n",
        "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n",
    ]
    SHARED = PASSWD + ["tenant:x:1001:1001:tenant:/home/tenant:/bin/bash\n"]

    def test_another_interactive_account_warns_with_the_env_channel_fix(self):
        check = doctor.shared_host_check(passwd_lines=self.SHARED, uid=1000)
        self.assertEqual(doctor.WARN, check.status)
        self.assertIn("shared host", check.detail)
        self.assertIn("AUTOCODE_", check.fix)
        self.assertIn("--resolver-token -", check.fix)

    def test_single_user_is_ok(self):
        check = doctor.shared_host_check(passwd_lines=self.PASSWD, uid=1000)
        self.assertEqual(doctor.OK, check.status)

    def test_system_and_nologin_accounts_do_not_count(self):
        lines = [
            "root:x:0:0:root:/root:/bin/bash\n",
            "me:x:1000:1000:me:/home/me:/bin/bash\n",
            "bot:x:1001:1001:bot:/srv:/usr/sbin/nologin\n",
            "svc:x:1002:1002:svc:/srv:/bin/false\n",
            "sysuser:x:105:105:sys:/var:/bin/sh\n",
        ]
        check = doctor.shared_host_check(passwd_lines=lines, uid=1000)
        self.assertEqual(doctor.OK, check.status)

    def test_missing_account_database_skips_the_advisory(self):
        with patch("builtins.open", side_effect=OSError("no /etc/passwd")):
            check = doctor.shared_host_check(uid=1000)
        self.assertEqual(doctor.OK, check.status)
        self.assertIn("skipped", check.detail)

    def test_all_checks_carries_the_shared_host_check(self):
        workspace = Path(tempfile.mkdtemp(prefix="doctor-shared-host-"))
        self.addCleanup(self._wipe, workspace)
        names = [check.name for check in doctor.all_checks(workspace, runner=fake_runner({}))]
        self.assertIn("shared-host", names)

    def _wipe(self, folder):
        import shutil

        shutil.rmtree(folder, ignore_errors=True)


class SessionCheckTests(unittest.TestCase):
    """A root shell in a container holds CAP_SYS_ADMIN, so every stage is refused; doctor must say so."""

    def refuse(self, error):
        def require():
            raise error

        return require

    def test_privileged_shell_fails_doctor_and_names_the_drop(self):
        check = doctor.session_check(
            self.refuse(
                doctor.session_boundary.UnsupportedBoundary(
                    "Provider session ownership refuses CAP_SYS_PTRACE or CAP_SYS_ADMIN"
                )
            )
        )
        self.assertEqual(doctor.MISSING, check.status)
        self.assertIn("the offline demo included", check.detail)
        self.assertIn("setpriv --bounding-set=-sys_admin,-sys_ptrace autocode", check.fix)
        self.assertFalse(doctor.passed([check]))

    def test_other_unsupported_hosts_fail_with_their_own_reason(self):
        check = doctor.session_check(
            self.refuse(doctor.session_boundary.UnsupportedBoundary("Proc namespace does not match"))
        )
        self.assertEqual(doctor.MISSING, check.status)
        self.assertIn("Proc namespace does not match", check.detail)
        self.assertNotIn("setpriv", check.fix)

    def test_macos_portable_supervision_and_a_supported_host_pass(self):
        portable = doctor.session_check(self.refuse(doctor.session_boundary.PortableHost("macOS")))
        self.assertEqual(doctor.OK, portable.status)
        self.assertEqual(doctor.OK, doctor.session_check(lambda: None).status)

    def test_all_checks_carries_the_session_check(self):
        workspace = Path(tempfile.mkdtemp(prefix="doctor-session-"))
        self.addCleanup(shutil.rmtree, workspace, True)
        self.assertIn("session", [check.name for check in doctor.all_checks(workspace, runner=fake_runner({}))])


class PathCheckTests(unittest.TestCase):
    """#1761: a venv install works through .venv/bin/autocode while a new shell says command not found."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="doctor-path-")
        self.addCleanup(directory.cleanup)
        self.bin = Path(directory.name) / "venv" / "bin"
        self.bin.mkdir(parents=True)
        (self.bin / "autocode").write_text("#!/bin/sh\n")

    def test_an_install_off_path_warns_and_names_its_directory(self):
        check = doctor.path_check(which=on_path(), scripts=str(self.bin), environ={})
        self.assertEqual(doctor.WARN, check.status)
        self.assertIn("command not found", check.detail)
        self.assertIn(f'export PATH="{self.bin}:$PATH"', check.fix)
        self.assertIn(f"source {self.bin / 'activate'}", check.fix)
        self.assertIn("pipx install --editable", check.fix)

    def test_without_a_script_to_point_at_the_fix_is_pipx(self):
        check = doctor.path_check(which=on_path(), scripts=str(self.bin.parent / "elsewhere"), environ={})
        self.assertEqual(doctor.WARN, check.status)
        self.assertNotIn("export PATH", check.fix)
        self.assertIn("pipx install --editable", check.fix)

    def test_on_path_only_through_an_activated_venv_says_a_new_shell_needs_it_again(self):
        found = str(self.bin / "autocode")
        check = doctor.path_check(
            which=lambda name: found, scripts=str(self.bin), environ={"VIRTUAL_ENV": str(self.bin.parent)}
        )
        self.assertEqual(doctor.OK, check.status)
        self.assertIn("a new shell finds it only after", check.detail)

    def test_on_path_for_good_is_ok(self):
        check = doctor.path_check(which=on_path("autocode"), scripts=str(self.bin), environ={})
        self.assertEqual(doctor.Check("path", doctor.OK, "/bin/autocode"), check)

    def test_a_missing_path_entry_never_fails_doctor(self):
        workspace = Path(tempfile.mkdtemp(prefix="doctor-path-ws-"))
        self.addCleanup(shutil.rmtree, workspace, True)
        checks = doctor.all_checks(workspace, which=on_path(), runner=fake_runner({}))
        row = next(check for check in checks if check.name == "path")
        self.assertEqual(doctor.WARN, row.status)
        self.assertEqual(doctor.passed(checks), doctor.passed([c for c in checks if c.name != "path"]))


class InstallDriftCheckTests(unittest.TestCase):
    """#972: a separately-installed copy that diverges must warn; the healthy
    linked/editable and single-copy configurations must never warn."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="install-drift-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def tree(self, name, files):
        root = self.root / name
        root.mkdir()
        for path, text in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return root

    def test_no_separately_installed_copy_is_ok(self):
        with mock.patch.object(doctor.importlib.util, "find_spec", return_value=None):
            check = doctor.install_drift_check(checkout=self.root / "checkout")
        self.assertEqual(doctor.OK, check.status)
        self.assertIn("no separately-installed", check.detail)

    def test_the_linked_editable_configuration_never_warns(self):
        shared = self.tree("tools", {"autocode_util.py": "x = 1\n"})
        check = doctor.install_drift_check(checkout=shared, installed=shared)
        self.assertEqual(doctor.OK, check.status)
        self.assertIn("linked", check.detail)

    def test_identical_trees_match(self):
        checkout = self.tree("checkout", {"autocode_util.py": "x = 1\n", "autocode.py": "main\n"})
        installed = self.tree("installed", {"autocode_util.py": "x = 1\n", "autocode.py": "main\n"})
        check = doctor.install_drift_check(checkout=checkout, installed=installed)
        self.assertEqual(doctor.OK, check.status)
        self.assertIn("matches", check.detail)

    def test_a_diverged_file_warns_naming_it(self):
        checkout = self.tree("checkout", {"autocode_util.py": "x = 1\n"})
        installed = self.tree("installed", {"autocode_util.py": "x = 2  # patched install\n"})
        check = doctor.install_drift_check(checkout=checkout, installed=installed)
        self.assertEqual(doctor.WARN, check.status)
        self.assertIn("autocode_util.py", check.detail)
        self.assertIn("reinstall", check.fix)
        self.assertIn("reverts", check.fix)

    def test_missing_and_extra_files_warn(self):
        checkout = self.tree("checkout", {"shared.py": "same\n", "only_checkout.py": "a\n"})
        installed = self.tree("installed", {"shared.py": "same\n", "only_install.py": "b\n"})
        check = doctor.install_drift_check(checkout=checkout, installed=installed)
        self.assertEqual(doctor.WARN, check.status)
        self.assertIn("only_checkout.py", check.detail)
        self.assertIn("only_install.py", check.detail)

    def test_all_checks_carries_the_install_drift_check(self):
        workspace = Path(tempfile.mkdtemp(prefix="doctor-drift-all-"))
        self.addCleanup(self._wipe, workspace)
        names = [check.name for check in doctor.all_checks(workspace, runner=fake_runner({}))]
        self.assertIn("install-drift", names)

    def _wipe(self, folder):
        import shutil

        shutil.rmtree(folder, ignore_errors=True)


class InstalledLauncherDriftTests(unittest.TestCase):
    """Exercise the ordinary CLI with a physical package outside its source Git tree."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="doctor-installed-launcher-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.site = self.directory / "site"
        self.package = self.site / "autocode_cli"
        shutil.copytree(REPO_ROOT / "tools", self.package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("GIT_", "AUTOCODE_", "BUILD_AUDIT_", "REVIEW_AUDIT_"))
            and k not in ("PYTHONPATH", "PYTHONHOME")
        }
        self.env.update(
            PATH=os.defpath,
            PYTHONPATH=str(self.site),
            PYTHONDONTWRITEBYTECODE="1",
            XDG_CONFIG_HOME=str(self.directory / "config"),
            AUTOCODE_PROVIDER="",
        )

    def invoke(self, *args, cwd=REPO_ROOT):
        return subprocess.run(
            [sys.executable, "-B", "-m", "autocode_cli.autocode", *args],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def check(self, workspace):
        result = self.invoke("doctor", "--json", "--workspace", str(workspace))
        self.assertIn(result.returncode, (0, 1), result.stderr)
        checks = {row["name"]: row for row in json.loads(result.stdout)["checks"]}
        return checks["install-drift"]

    def test_matching_relocated_payload_with_unknown_commit_has_no_drift_warning(self):
        (self.package / "__pycache__").mkdir(exist_ok=True)
        (self.package / "__pycache__/generated.pyc").write_bytes(b"ignored bytecode")
        check = self.check(REPO_ROOT)
        self.assertEqual("ok", check["status"], check)
        self.assertIn("application payload matches", check["detail"])
        self.assertIn("unknown (installed package)", check["detail"])

    def test_stale_installed_core_warns_with_source_and_reinstall_commands(self):
        with (self.package / "autocode_util.py").open("a") as file:
            file.write("\n# Owned older-payload marker.\n")
        check = self.check(REPO_ROOT)
        self.assertEqual("warn", check["status"], check)
        self.assertIn("autocode_util.py", check["detail"])
        self.assertIn(str(self.package), check["detail"])
        self.assertIn(str(REPO_ROOT), check["detail"])
        self.assertIn("unknown (installed package)", check["detail"])
        self.assertIn("tools/autocode.py", check["fix"])
        self.assertIn("pip install --upgrade --force-reinstall", check["fix"])
        version = self.invoke("--version")
        self.assertEqual(0, version.returncode, version.stderr)
        self.assertIn("commit unknown", version.stdout)

    def test_foreign_git_workspace_is_not_used_as_runner_provenance(self):
        foreign = self.directory / "customer"
        foreign.mkdir()
        (foreign / "pyproject.toml").write_text('[project]\nname="customer-project"\nversion="1.0"\n')
        git_env = dict(self.env, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        for args in (
            ("init", "-q"),
            ("add", "pyproject.toml"),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-qm", "Customer"),
        ):
            subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-C", str(foreign), *args],
                env=git_env,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        check = self.check(foreign)
        self.assertEqual("ok", check["status"], check)
        self.assertNotIn("Customer", check["detail"])
        version = self.invoke("--version", cwd=foreign)
        self.assertEqual(0, version.returncode, version.stderr)
        self.assertIn("commit unknown", version.stdout)


if __name__ == "__main__":
    unittest.main()
