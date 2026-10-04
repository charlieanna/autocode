"""Explicit provider-environment preparation: fake providers only, no model calls.

Issue #226: a pilot launcher built the child environment after clearing the
process environment, so PATH was lost before provider preflight. These tests
pin the maintained seam (providers/opencode.py and providers/command.py) to the
explicit-environment-first contract: preflight and launch resolve executables,
configuration roots, inline OPENCODE_* inputs and billing-route guards from a
passed environment mapping, never from a mutated process environment.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
import textwrap
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.providers import command as command_provider
from tools.providers import opencode as oc

REPO = Path(__file__).resolve().parents[1]
HEX64 = re.compile(r"[0-9a-f]{64}")
MODELS = {"mimo-token-plan/mimo-v2.6-pro", "opencode/mimo-v2.6-flash-free", "xiaomi-token-plan-sgp/mimo-v2.6-pro", "zai-coding-plan/glm-5.3", "openai/gpt-6-astra",
          "openai/gpt-6-sol", "openai/gpt-6-luna", "openai/gpt-5.6-terra", "openai/gpt-5.6-sol"}

ROLES = """
[roles]
astra = { model = "demo", effort = "high" }
terra = { model = "demo", effort = "medium" }
sol = { model = "demo", effort = "high" }
completion = { model = "demo", effort = "medium" }
glm = { model = "demo", effort = "medium" }
plan_reviewer = { model = "demo", effort = "high" }
"""

OLD_VERSION_FAKE = """import sys
print('0.9.0')
raise SystemExit(0)
"""

MODELS_FAIL_FAKE = """import sys
raise SystemExit(1 if sys.argv[1:] == ['models'] else 0)
"""

ROSTER_LOGGING_FAKE = """import json, os, sys
if sys.argv[1:] == ['models']:
    with open(os.environ['WORKER_LOG'], 'a') as stream:
        stream.write(json.dumps({'worker_path': os.environ.get('PATH', '')}) + '\\n')
    print('provider/model-a')
    print('provider/model-b')
raise SystemExit(0)
"""

INVOCATION_LOGGING_WRAPPER = """import os, sys
with open(os.environ['INVOCATION_LOG'], 'a') as log:
    log.write(' '.join(sys.argv[1:]) + '\\n')
fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'opencode-fixture')
os.execv(sys.executable, [sys.executable, fixture, *sys.argv[1:]])
"""


class ProviderEnvPrepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original_path = os.environ["PATH"]
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / ".git").mkdir()
        self.fixture_home = self.root / "fixture-home"
        self.fixture_config = self.root / "fixture-config"
        self.fixture_managed = self.root / "fixture-managed"
        for directory in (self.fixture_home, self.fixture_config, self.fixture_managed):
            directory.mkdir()
        self.fixture_bin = self.make_bin("fixture-bin")
        self.fixture_opencode = self.install_fake_opencode(self.fixture_bin)

    def make_bin(self, name):
        directory = self.root / name
        directory.mkdir()
        return directory

    def install_fake(self, directory, body, name="opencode"):
        script = Path(directory) / name
        script.write_text(f"#!{sys.executable}\n" + body)
        script.chmod(0o755)
        return script

    def install_fake_opencode(self, directory):
        target = Path(directory) / "opencode"
        shutil.copy2(REPO / "tools" / "fake_opencode.py", target)
        target.chmod(0o755)
        return target

    def mapping_for(self, bin_dir, **extra):
        env = {"PATH": str(bin_dir) + os.pathsep + self.original_path,
               "HOME": str(self.fixture_home),
               "XDG_CONFIG_HOME": str(self.fixture_config)}
        env.update(extra)
        return env

    def isolated_environ(self, bin_dir):
        return {"PATH": str(bin_dir), "HOME": str(self.fixture_home),
                "XDG_CONFIG_HOME": str(self.fixture_config)}

    def _restore_environ(self, saved):
        os.environ.clear()
        os.environ.update(saved)

    def load_provider(self, name, body):
        config_home = self.root / "provider-config"
        (config_home / "autocode" / "providers").mkdir(parents=True, exist_ok=True)
        (config_home / "autocode" / "providers" / f"{name}.toml").write_text(textwrap.dedent(body))
        saved = dict(os.environ)
        self.addCleanup(self._restore_environ, saved)
        os.environ["XDG_CONFIG_HOME"] = str(config_home)
        return command_provider.load(name)

    def test_ac1_fake_provider_found_via_explicit_env(self):
        mapping = self.mapping_for(self.fixture_bin)
        self.assertNotIn(str(self.fixture_bin), os.environ["PATH"])
        settings = oc.local_settings(self.workspace, env=mapping)
        self.assertEqual("opencode", settings["engine"])
        self.assertEqual(2, settings["identity_version"])
        self.assertEqual(str(self.fixture_opencode), settings["executable"])
        self.assertEqual("1.18.31", settings["version"])
        for section in ("config_hashes", "environment_config_hashes"):
            for value in settings[section].values():
                self.assertTrue(value is None or HEX64.fullmatch(value),
                                f"{section} leaked an environment value: {value!r}")
        self.assertEqual(MODELS, oc.available_models(self.workspace, env=mapping))
        command, env, overrides = oc.launch("terra", self.workspace, self.root / "run", None,
                                            "zai-coding-plan/glm-5.3", "medium", True, env=mapping)
        self.assertEqual("opencode", command[0])
        self.assertEqual(mapping["PATH"], env["PATH"])
        inline = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertIn("autocode_terra", inline["agent"])
        self.assertEqual("deny", inline["agent"]["autocode_terra"]["permission"]["task"])

    def test_ac2_parent_environment_unchanged_success_and_failure(self):
        saved = dict(os.environ)
        self.addCleanup(self._restore_environ, saved)
        os.environ["PATH"] = "/ac2-sentinel-path"
        os.environ["HOME"] = "/ac2-sentinel-home"
        os.environ["XDG_CONFIG_HOME"] = "/ac2-sentinel-config"
        sentinel = dict(os.environ)

        self.assertEqual("1.18.31", oc.local_settings(self.workspace, env=self.mapping_for(self.fixture_bin))["version"])
        self.assertEqual(sentinel, dict(os.environ))
        self.assertEqual(MODELS, oc.available_models(self.workspace, env=self.mapping_for(self.fixture_bin)))
        self.assertEqual(sentinel, dict(os.environ))

        old_bin = self.make_bin("old-version-bin")
        self.install_fake(old_bin, OLD_VERSION_FAKE)
        with self.assertRaisesRegex(RuntimeError, "This adapter requires OpenCode 1.x"):
            oc.local_settings(self.workspace, env=self.mapping_for(old_bin))
        self.assertEqual(sentinel, dict(os.environ))

        fail_bin = self.make_bin("models-fail-bin")
        self.install_fake(fail_bin, MODELS_FAIL_FAKE)
        with self.assertRaisesRegex(RuntimeError, "Cannot list OpenCode models"):
            oc.available_models(self.workspace, env=self.mapping_for(fail_bin))
        self.assertEqual(sentinel, dict(os.environ))

        with self.assertRaisesRegex(RuntimeError, "OpenCode is not on PATH"):
            oc.local_settings(self.workspace)
        self.assertEqual(sentinel, dict(os.environ))
        with self.assertRaisesRegex(RuntimeError, "OpenCode is not on PATH.*no agent was launched"):
            oc.available_models(self.workspace)
        self.assertEqual(sentinel, dict(os.environ))

    def test_ac3_concurrent_workers_environment_independent(self):
        log = self.root / "workers.jsonl"
        bin_a, bin_b = self.make_bin("bin-a"), self.make_bin("bin-b")
        self.install_fake(bin_a, ROSTER_LOGGING_FAKE)
        self.install_fake(bin_b, ROSTER_LOGGING_FAKE)
        results, failures = {}, []

        def worker(name, bin_dir):
            try:
                results[name] = oc.available_models(self.workspace, env=self.mapping_for(bin_dir, WORKER_LOG=str(log)))
            except Exception as error:  # reported after both threads join
                failures.append(f"{name}: {error!r}")

        threads = [threading.Thread(target=worker, args=("a", bin_a)),
                   threading.Thread(target=worker, args=("b", bin_b))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], failures)
        self.assertEqual({"provider/model-a", "provider/model-b"}, results["a"])
        self.assertEqual({"provider/model-a", "provider/model-b"}, results["b"])
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(2, len(rows))
        for row in rows:
            self.assertTrue(row["worker_path"], "a worker observed a cleared environment")
        paths = [row["worker_path"] for row in rows]
        self.assertTrue(any("bin-a" in path and "bin-b" not in path for path in paths))
        self.assertTrue(any("bin-b" in path and "bin-a" not in path for path in paths))

    def test_ac5_no_environment_or_oauth_values_in_outputs(self):
        api_secret, oauth_secret = "ac5-secret-api-key-value", "ac5-oauth-file-secret"
        (self.fixture_home / ".opencode").mkdir()
        (self.fixture_home / ".opencode" / "auth.json").write_text(oauth_secret)
        (self.fixture_config / "opencode").mkdir()
        (self.fixture_config / "opencode" / "auth.json").write_text(oauth_secret)
        mapping = self.mapping_for(self.fixture_bin, OPENAI_API_KEY=api_secret)
        captured_out, captured_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            settings = oc.local_settings(self.workspace, env=mapping)
            models = oc.available_models(self.workspace, env=mapping)
            oc.check_subscription_routes({"sol": {"model": "openai/gpt-6-sol"}}, env=mapping)
        self.assertEqual(MODELS, models)
        for text in (captured_out.getvalue(), captured_err.getvalue(),
                     json.dumps(settings)):
            self.assertNotIn(api_secret, text)
            self.assertNotIn(oauth_secret, text)
        for section in ("config_hashes", "environment_config_hashes"):
            for value in settings[section].values():
                self.assertTrue(value is None or HEX64.fullmatch(value),
                                f"{section} leaked an environment value: {value!r}")

    def test_ac6_admission_failures_state_no_launch(self):
        empty_bin = self.make_bin("ac6-empty-bin")
        empty_mapping = self.isolated_environ(empty_bin)
        with self.assertRaisesRegex(RuntimeError, "OpenCode is not on PATH.*no provider request was launched"):
            oc.local_settings(self.workspace, env=empty_mapping)

        fail_bin = self.make_bin("ac6-models-fail-bin")
        self.install_fake(fail_bin, MODELS_FAIL_FAKE)
        with self.assertRaisesRegex(RuntimeError, "Cannot list OpenCode models.*no agent was launched"):
            oc.available_models(self.workspace, env=self.mapping_for(fail_bin))

        provider = self.load_provider("ac6prov", 'name = "ac6prov"\ncommand = ["ac6-tool"]\n' + ROLES)
        with self.assertRaisesRegex(RuntimeError, "is not on PATH.*no provider request was launched"):
            provider.local_settings(self.workspace, env=empty_mapping)

    def test_ac10_missing_executable_at_model_listing_is_an_honest_prelaunch_error(self):
        empty_bin = self.make_bin("ac10-empty-bin")
        empty_mapping = self.isolated_environ(empty_bin)
        with self.assertRaisesRegex(RuntimeError, "OpenCode is not on PATH.*no agent was launched"):
            oc.available_models(self.workspace, env=empty_mapping)

        provider = self.load_provider("ac10prov",
                                      'name = "ac10prov"\ncommand = ["ac10prov-tool", "run"]\n'
                                      'models_command = ["ac10prov-tool", "models"]\n' + ROLES)
        with self.assertRaisesRegex(RuntimeError, "Cannot list ac10prov models.*no agent was launched"):
            provider.available_models(self.workspace, env=empty_mapping)

        tool_bin = self.make_bin("ac10-tool-bin")
        self.install_fake(tool_bin, MODELS_FAIL_FAKE, name="ac10prov-tool")
        tool_mapping = self.isolated_environ(tool_bin)
        with self.assertRaisesRegex(RuntimeError, "^Cannot list ac10prov models.*no agent was launched"):
            provider.available_models(self.workspace, env=tool_mapping)

    def test_ac11_configuration_discovery_uses_explicit_env(self):
        decoy_config, decoy_home = self.root / "decoy-config", self.root / "decoy-home"
        (decoy_config / "opencode").mkdir(parents=True)
        (decoy_config / "opencode" / "config.json").write_text('{"decoy": true}')
        (decoy_home / ".opencode").mkdir(parents=True)
        (decoy_home / ".opencode" / "config.json").write_text('{"decoy": true}')
        saved = dict(os.environ)
        self.addCleanup(self._restore_environ, saved)
        os.environ["XDG_CONFIG_HOME"] = str(decoy_config)
        os.environ["HOME"] = str(decoy_home)

        (self.fixture_config / "opencode").mkdir()
        config = self.fixture_config / "opencode" / "config.json"
        config.write_text('{"permission":{"edit":"ask"}}')
        mapping = self.mapping_for(self.fixture_bin, OPENCODE_TEST_MANAGED_CONFIG_DIR=str(self.fixture_managed))
        settings = oc.local_settings(self.workspace, env=mapping)
        self.assertEqual(str(self.fixture_opencode), settings["executable"])
        self.assertEqual(hashlib.sha256(b'{"permission":{"edit":"ask"}}').hexdigest(),
                         settings["config_hashes"][str(config)])
        for key in settings["config_hashes"]:
            self.assertFalse(key.startswith(str(decoy_config)), key)
            self.assertFalse(key.startswith(str(decoy_home)), key)
        self.assertEqual(1, len([value for value in settings["config_hashes"].values() if value is not None]))

    def test_ac12_opencode_env_variables_resolve_from_mapping(self):
        decoy_root = self.root / "decoy-root"
        decoy_root.mkdir()
        (decoy_root / "custom.json").write_text('{"decoy": true}')
        saved = dict(os.environ)
        self.addCleanup(self._restore_environ, saved)
        for key in [name for name in os.environ if name.startswith("OPENCODE_")]:
            del os.environ[key]
        os.environ["OPENCODE_PERMISSION"] = "ac12-decoy-inline"
        os.environ["OPENCODE_CONFIG"] = str(decoy_root / "custom.json")

        fixture_root = self.root / "fixture-root"
        fixture_root.mkdir()
        custom = fixture_root / "custom.json"
        custom.write_text('{"x":1}')
        env = self.mapping_for(self.fixture_bin,
                               OPENCODE_TEST_MANAGED_CONFIG_DIR=str(self.fixture_managed),
                               OPENCODE_PERMISSION="ac12-fixture-inline",
                               OPENCODE_CONFIG=str(custom))
        settings = oc.local_settings(self.workspace, env=env)
        self.assertEqual(hashlib.sha256(b'{"x":1}').hexdigest(), settings["config_hashes"][str(custom)])
        for key in settings["config_hashes"]:
            self.assertFalse(key.startswith(str(decoy_root)), key)
        inline = settings["environment_config_hashes"]
        self.assertEqual({"OPENCODE_CONFIG_CONTENT", "OPENCODE_PERMISSION", "OPENCODE_CONFIG_DIR",
                          "OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_PURE", "OPENCODE_TEST_MANAGED_CONFIG_DIR"},
                         set(inline))
        self.assertEqual(hashlib.sha256(b"ac12-fixture-inline").hexdigest(), inline["OPENCODE_PERMISSION"])
        self.assertNotEqual(hashlib.sha256(b"ac12-decoy-inline").hexdigest(), inline["OPENCODE_PERMISSION"])
        self.assertEqual(hashlib.sha256(str(self.fixture_managed).encode()).hexdigest(),
                         inline["OPENCODE_TEST_MANAGED_CONFIG_DIR"])
        for key in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR", "OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_PURE"):
            self.assertIsNone(inline[key])

    def test_explicit_home_resolves_tilde_configuration_inputs(self):
        custom = self.fixture_home / "custom-opencode"
        custom.mkdir()
        config = custom / "config.json"
        config.write_text('{"declared": true}')
        for key, value in (("OPENCODE_CONFIG_DIR", "~/custom-opencode"),
                           ("OPENCODE_CONFIG", "~/custom-opencode/config.json")):
            with self.subTest(key=key):
                mapping = self.mapping_for(self.fixture_bin, **{key: value},
                                           OPENCODE_TEST_MANAGED_CONFIG_DIR=str(self.fixture_managed))
                inputs = oc.configuration_inputs(self.workspace, env=mapping)
                self.assertIn(config, inputs)
                self.assertNotIn(Path(os.environ["HOME"]) / "custom-opencode" / "config.json", inputs)
    def test_ac13_route_guards_read_explicit_env_mapping(self):
        wrapper_bin = self.make_bin("ac13-wrapper-bin")
        fixture_copy = wrapper_bin / "opencode-fixture"
        shutil.copy2(REPO / "tools" / "fake_opencode.py", fixture_copy)
        fixture_copy.chmod(0o755)
        log = self.root / "ac13-invocations.log"
        wrapper = wrapper_bin / "opencode"
        wrapper.write_text(INVOCATION_LOGGING_WRAPPER)
        wrapper.chmod(0o755)
        saved = dict(os.environ)
        self.addCleanup(self._restore_environ, saved)
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            os.environ.pop(key, None)

        env = self.mapping_for(wrapper_bin, INVOCATION_LOG=str(log), OPENAI_API_KEY="ac13-mapping-secret")
        oc.check_subscription_routes({"sol": {"model": "openai/gpt-6-sol"}}, env=env)
        self.assertTrue(not log.exists() or not log.read_text().strip(), "an auth-list subprocess ran")

        marker = self.root / "ac13-auth-ran"
        auth_bin = self.make_bin("ac13-auth-bin")
        auth_script = auth_bin / "ac13prov-auth"
        auth_script.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
        auth_script.chmod(0o755)
        provider = self.load_provider("ac13prov", 'name = "ac13prov"\ncommand = ["ac13prov-tool"]\n' + textwrap.dedent("""
            [auth]
            command = ["ac13prov-auth"]
            forbid_env = ["OPENAI_API_KEY"]

            [[auth.routes]]
            models = "openai/"
            pattern = "^\\\\s*OpenAI\\\\s+(\\\\S+)\\\\s*$"
            expect = "oauth"
            """) + ROLES)
        mapping = self.mapping_for(auth_bin, OPENAI_API_KEY="ac13-mapping-secret")
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY") as raised:
            provider.check_subscription_routes({"astra": {"model": "openai/x"}}, env=mapping)
        self.assertIn("will not silently change billing", str(raised.exception))
        self.assertNotIn("ac13-mapping-secret", str(raised.exception))
        self.assertFalse(marker.exists())

        os.environ["OPENAI_API_KEY"] = "ac13-process-secret"
        oc.check_subscription_routes({"sol": {"model": "openai/gpt-6-sol"}})
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY") as raised:
            provider.check_subscription_routes({"astra": {"model": "openai/x"}})
        self.assertIn("will not silently change billing", str(raised.exception))
        self.assertNotIn("ac13-mapping-secret", str(raised.exception))

    def test_g1_ambient_path_default_still_works(self):
        config_root = self.root / "g1-config"
        config_root.mkdir()
        saved = dict(os.environ)
        self.addCleanup(self._restore_environ, saved)
        os.environ["PATH"] = str(self.fixture_bin) + os.pathsep + self.original_path
        os.environ["XDG_CONFIG_HOME"] = str(config_root)
        settings = oc.local_settings(self.workspace)
        self.assertEqual(str(self.fixture_opencode), settings["executable"])
        self.assertEqual("1.18.31", settings["version"])
        self.assertEqual(MODELS, oc.available_models(self.workspace))

    def test_g2_post_start_failure_retains_uncertainty_wording(self):
        rows = [
            {"type": "step_start", "sessionID": "ses_g2", "part": {
                "id": "prt_g2_start", "sessionID": "ses_g2", "messageID": "msg_g2", "type": "step-start"}},
            {"type": "step_finish", "sessionID": "ses_g2", "part": {
                "id": "prt_g2_finish", "sessionID": "ses_g2", "messageID": "msg_g2", "type": "step-finish",
                "reason": "tool-calls",
                "tokens": {"input": 10, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}}}},
        ]
        events = self.root / "g2-events.jsonl"
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaisesRegex(RuntimeError, "preserve it without automatic retry"):
            oc.final_report(events)


if __name__ == "__main__":
    unittest.main()
