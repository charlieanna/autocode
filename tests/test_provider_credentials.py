"""Configured provider credential boundaries through real children and public CLI.

Linux metadata children also receive the authenticated capture-family context.
The stage mapping and actual model children retain their credential membrane.
"""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import autocode_agent_env as agent_env
import autocode_doctor as doctor
import autocode_provider_env as provider_env
import autocode_taskrun as taskrun
import provider_conformance_transport as conformance

from tools.providers import command

from . import test_subprocess

ROLES = "\n[roles]\n" + "\n".join(
    f'{role} = {{ model = "demo/{"builder" if role == "builder" else "reviewer"}", effort = "medium" }}'
    for role in ("task_planner", "builder", "tester", "completion", "planner", "plan_reviewer")
)
CANARY = "fixture-secret-value-never-print-975"
WRAPPER = """import json, os, re, socket, stat, struct, sys
def metadata_family_owned():
    raw = os.environ.get('AUTOCODE_CAPTURE_FAMILY')
    if raw is None:
        return None
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
            return False
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {'endpoint', 'nonce'}
                or not isinstance(row['nonce'], str) or not re.fullmatch('[0-9a-f]{32}', row['nonce'])
                or not isinstance(row['endpoint'], str) or not os.path.isabs(row['endpoint'])):
                return False
            endpoint, directory = os.lstat(row['endpoint']), os.lstat(os.path.dirname(row['endpoint']))
            if (not stat.S_ISSOCK(endpoint.st_mode) or not stat.S_ISDIR(directory.st_mode)
                or endpoint.st_uid != os.geteuid() or directory.st_uid != os.geteuid()
                or endpoint.st_mode & 0o077 or directory.st_mode & 0o077):
                return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as channel:
            channel.settimeout(1)
            channel.connect(rows[-1]['endpoint'])
            pid, uid, _ = struct.unpack('3i', channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        return pid == os.getppid() and uid == os.geteuid()
    except (OSError, ValueError, TypeError, KeyError):
        return False
with open(os.environ['AUTOCODE_TEST_ENV_LOG'], 'a') as log:
    log.write(json.dumps({'args': sys.argv[1:], 'names': sorted(os.environ),
                         'metadata_family_owned': metadata_family_owned()}) + '\\n')
if sys.argv[1:] == ['--version']:
    if os.environ.get('AUTOCODE_TEST_VERSION_REQUIRES') and 'FIXTURE_API_KEY' not in os.environ:
        raise SystemExit(1)
    print('1.18.33')
elif sys.argv[1:] == ['models']:
    print('demo/builder\\ndemo/reviewer')
elif sys.argv[1:] == ['auth', 'list']:
    if os.environ.get('AUTOCODE_TEST_AUTH_REQUIRES') and 'FIXTURE_API_KEY' not in os.environ:
        raise SystemExit(1)
    print('Fixture api')
elif sys.argv[1:2] == ['exec']:
    if os.environ.get('AUTOCODE_TEST_AUTH_ACCESS_DENIED'):
        with open(sys.argv[sys.argv.index('--output-schema') + 1]) as source:
            schema = json.load(source)
        if set(schema.get('properties', {})) == {'ok'}:
            with open(sys.argv[sys.argv.index('-o') + 1], 'w') as report:
                json.dump({'ok': True}, report)
            print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
            raise SystemExit(0)
        print(json.dumps({'type':'error','error':{'name':'APIError','data':{'statusCode':403},'message':'private-auth-canary'}}))
        print(json.dumps({'type':'turn.failed','error':{'code':'authentication_failed','message':'oauth_org_not_allowed private-auth-canary'}}))
        raise SystemExit(1)
    if 'FIXTURE_API_KEY' not in os.environ:
        print(json.dumps({'type':'error','error':{'name':'APIError','data':{'statusCode':401,'message':'Unauthorized'}}}))
        raise SystemExit(1)
    fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'codex-fixture')
    os.execv(sys.executable, [sys.executable, fixture, *sys.argv[1:]])
else:
    raise SystemExit(2)
"""


def provider_config(path, wrapper, *, forbidden=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'name = "credentialfixture"\noutput = "report_file"\n'
        + "command = "
        + json.dumps([str(wrapper), "exec", "--output-schema", "{schema}", "-o", "{report}", "--model", "{model}", "-"])
        + "\nversion_command = "
        + json.dumps([str(wrapper), "--version"])
        + "\nmodels_command = "
        + json.dumps([str(wrapper), "models"])
        + ROLES
        + "\n[auth]\ncommand = "
        + json.dumps([str(wrapper), "auth", "list"])
        + "\nforbid_env = "
        + json.dumps(list(forbidden))
        + '\n[[auth.routes]]\nmodels = "demo/"\npattern = "^Fixture (\\\\S+)$"\nexpect = "api"\n'
    )


class ConfiguredEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        wrapper = self.root / "provider"
        wrapper.write_text(f"#!{sys.executable}\n" + WRAPPER)
        wrapper.chmod(0o755)
        self.log = self.root / "calls.jsonl"
        self.path = self.root / "config/autocode/providers/credentialfixture.toml"
        provider_config(self.path, wrapper)
        self.mapping = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "AUTOCODE_TEST_ENV_LOG": str(self.log),
            "FIXTURE_API_KEY": CANARY,
            "OTHER_PASSWORD": CANARY,
            "AUTOCODE_RESOLVER_TOKEN": CANARY,
        }
        self.provider = command.CommandProvider(tomllib.loads(self.path.read_text()), self.path)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def assert_metadata_environment(self, expected, call):
        if sys.platform == "linux":
            self.assertIs(call["metadata_family_owned"], True, "private context belongs to the capture parent")
            expected = {*expected, "AUTOCODE_CAPTURE_FAMILY"}
        else:
            self.assertIsNone(call["metadata_family_owned"])
        self.assertEqual(set(expected), set(call["names"]) - {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"})

    def preflight(self, **kwargs):
        self.provider.local_settings(self.root, **kwargs)
        self.assertEqual({"demo/builder", "demo/reviewer"}, self.provider.available_models(self.root, **kwargs))
        self.provider.check_subscription_routes({"builder": {"model": "demo/builder"}}, self.root, **kwargs)

    def test_implicit_preflights_use_stage_credential_projection(self):
        with mock.patch.dict(os.environ, self.mapping, clear=True):
            self.preflight()
        self.assertEqual(3, len(self.calls()))
        expected = set(agent_env.scrubbed(self.mapping))
        for call in self.calls():
            with self.subTest(call=call["args"]):
                self.assert_metadata_environment(expected, call)

    def test_implicit_launch_matches_preflight_projection_and_explicit_pass(self):
        for passed in ("", "FIXTURE_API_KEY,AUTOCODE_RESOLVER_TOKEN"):
            mapping = {**self.mapping, "AUTOCODE_PASS_ENV": passed}
            with self.subTest(passed=bool(passed)), mock.patch.dict(os.environ, mapping, clear=True):
                self.preflight()
                _, launched, _ = self.provider.launch(
                    "builder", self.root, self.root, None, "demo/builder", "medium", True
                )
            self.assertEqual(agent_env.scrubbed(mapping), launched)
            for call in self.calls()[-3:]:
                self.assert_metadata_environment(set(launched), call)
            self.assertNotIn("OTHER_PASSWORD", launched)
            self.assertNotIn("AUTOCODE_RESOLVER_TOKEN", launched)
            self.assertEqual(bool(passed), "FIXTURE_API_KEY" in launched)

    def test_explicit_provider_mapping_is_intentional_and_unchanged(self):
        before = dict(self.mapping)
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}, clear=True):
            self.preflight(env=self.mapping)
            _, launched, _ = self.provider.launch(
                "builder", self.root, self.root, None, "demo/builder", "medium", True, env=self.mapping
            )
        self.assertEqual(before, self.mapping)
        self.assertEqual(before, launched)
        for call in self.calls():
            self.assert_metadata_environment(set(before), call)

    def test_explicit_pass_reaches_all_preflights_without_private_control_tokens(self):
        mapping = {**self.mapping, "AUTOCODE_PASS_ENV": "FIXTURE_API_KEY,AUTOCODE_RESOLVER_TOKEN"}
        with mock.patch.dict(os.environ, mapping, clear=True):
            self.preflight()
        for call in self.calls():
            self.assertIn("FIXTURE_API_KEY", call["names"])
            self.assertNotIn("OTHER_PASSWORD", call["names"])
            self.assertNotIn("AUTOCODE_RESOLVER_TOKEN", call["names"])

    def test_forbidden_guard_sees_ambient_and_explicit_inputs_before_projection(self):
        provider_config(self.path, self.root / "provider", forbidden=("FIXTURE_API_KEY",))
        self.provider = command.CommandProvider(tomllib.loads(self.path.read_text()), self.path)
        for ambient, explicit in ((self.mapping, {}), ({"PATH": self.mapping["PATH"]}, self.mapping)):
            with (
                self.subTest(ambient=bool(ambient.get("FIXTURE_API_KEY"))),
                mock.patch.dict(os.environ, ambient, clear=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "FIXTURE_API_KEY.*subscription") as error:
                    self.provider.check_subscription_routes(
                        {"builder": {"model": "demo/builder"}}, self.root, env=explicit
                    )
                self.assertNotIn(CANARY, str(error.exception))
        self.assertFalse(self.log.exists())

    def test_auth_failure_offers_names_only_opt_in_guidance(self):
        mapping = {**self.mapping, "AUTOCODE_TEST_AUTH_REQUIRES": "1"}
        with mock.patch.dict(os.environ, mapping, clear=True):
            with self.assertRaisesRegex(RuntimeError, "AUTOCODE_PASS_ENV") as error:
                self.provider.check_subscription_routes({"builder": {"model": "demo/builder"}}, self.root)
        self.assertIn("FIXTURE_API_KEY", str(error.exception))
        self.assertNotIn("AUTOCODE_RESOLVER_TOKEN", str(error.exception))
        self.assertNotIn(CANARY, str(error.exception))

    def test_doctor_observes_withheld_and_explicitly_passed_version_environment(self):
        mapping = {**self.mapping, "AUTOCODE_TEST_VERSION_REQUIRES": "1"}
        with mock.patch.dict(os.environ, mapping, clear=True):
            withheld = doctor.provider_check("credentialfixture")
        with mock.patch.dict(os.environ, {**mapping, "AUTOCODE_PASS_ENV": "FIXTURE_API_KEY"}, clear=True):
            passed = doctor.provider_check("credentialfixture")
        self.assertEqual(doctor.MISSING, withheld.status)
        self.assertEqual(doctor.OK, passed.status)
        self.assertNotIn(CANARY, str(withheld))
        self.assertNotIn(CANARY, str(passed))

    def test_conformance_preflight_projects_its_explicit_app_environment(self):
        before = dict(self.mapping)
        with mock.patch.dict(os.environ, {}, clear=True):
            conformance.preflight("credentialfixture", "demo/builder", self.provider, self.root, self.mapping)
        self.assertEqual(before, self.mapping)
        expected = set(agent_env.scrubbed(before))
        self.assertEqual(3, len(self.calls()))
        for call in self.calls():
            self.assert_metadata_environment(expected, call)

    def test_conformance_rejects_explicit_forbidden_input_before_any_child(self):
        provider_config(self.path, self.root / "provider", forbidden=("FIXTURE_API_KEY",))
        self.provider = command.CommandProvider(tomllib.loads(self.path.read_text()), self.path)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "FIXTURE_API_KEY.*subscription"):
                conformance.preflight("credentialfixture", "demo/builder", self.provider, self.root, self.mapping)
        self.assertFalse(self.log.exists())

    def test_conformance_auth_failure_has_names_only_guidance(self):
        mapping = {**self.mapping, "AUTOCODE_TEST_AUTH_REQUIRES": "1"}
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "AUTOCODE_PASS_ENV") as error:
                conformance.preflight("credentialfixture", "demo/builder", self.provider, self.root, mapping)
        self.assertIn("FIXTURE_API_KEY", str(error.exception))
        self.assertNotIn("AUTOCODE_RESOLVER_TOKEN", str(error.exception))
        self.assertNotIn(CANARY, str(error.exception))


class AuthenticationAdviceTests(unittest.TestCase):
    def test_native_auth_errors_suggest_only_actual_withheld_names(self):
        names = ["FIXTURE_API_KEY", "OTHER_PASSWORD", "AUTOCODE_RESOLVER_TOKEN", "bad name", 401]
        for code in (401, 403):
            with self.subTest(code=code):
                hint = provider_env.authentication_hint(
                    [{"type": "error", "error": {"name": "APIError", "data": {"statusCode": code, "message": CANARY}}}],
                    names,
                )
                self.assertIn("FIXTURE_API_KEY, OTHER_PASSWORD", hint)
                self.assertIn("If this provider requires one", hint)
                self.assertIn("AUTOCODE_PASS_ENV", hint)
                self.assertNotIn("AUTOCODE_RESOLVER_TOKEN", hint)
                self.assertNotIn(CANARY, hint)

    def test_prose_tool_output_malformed_or_other_status_never_get_auth_guidance(self):
        negatives = [
            {"type": "text", "part": {"text": "APIError 401"}},
            {"type": "tool_use", "error": {"name": "APIError", "data": {"statusCode": 401}}},
            {"type": "turn.failed", "error": {"message": "401 Unauthorized"}},
            {"type": "error", "error": {"name": "OtherError", "data": {"statusCode": 401}}},
            {"type": "error", "error": {"name": "APIError", "data": None}},
        ]
        negatives.extend(
            {"type": "error", "error": {"name": "APIError", "data": {"statusCode": code}}}
            for code in (True, False, "401", 429, 500, None)
        )
        for row in negatives:
            with self.subTest(row=row):
                self.assertEqual("", provider_env.authentication_hint([row], ["FIXTURE_API_KEY"]))

    def test_absent_or_only_controller_candidates_do_not_suggest_passing_secrets(self):
        row = {"type": "error", "error": {"name": "APIError", "data": {"statusCode": 401}}}
        for names in ([], ["AUTOCODE_RESOLVER_TOKEN"], "FIXTURE_API_KEY", None):
            with self.subTest(names=names):
                self.assertEqual("", provider_env.authentication_hint([row], names))


class ProviderCredentialCLI(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp

    def configured(self, *, auth_requires=False):
        binary = self.root / "fixture-bin"
        (binary / "codex").rename(binary / "codex-fixture")
        wrapper = binary / "credentialfixture"
        wrapper.write_text(f"#!{sys.executable}\n" + WRAPPER)
        wrapper.chmod(0o755)
        provider_config(Path(self.env["XDG_CONFIG_HOME"]) / "autocode/providers/credentialfixture.toml", wrapper)
        self.log = self.root / "calls.jsonl"
        self.env.update(
            {
                "AUTOCODE_TEST_ENV_LOG": str(self.log),
                "FIXTURE_API_KEY": CANARY,
                "OTHER_PASSWORD": CANARY,
                "AUTOCODE_RESOLVER_TOKEN": CANARY,
            }
        )
        self.env.pop("AUTOCODE_PASS_ENV", None)
        if auth_requires:
            self.env["AUTOCODE_TEST_AUTH_REQUIRES"] = "1"
        self.options = (
            "--engine",
            "opencode",
            "--provider",
            "credentialfixture",
            "--workflow",
            "build",
            "--joint-planning",
            "--pause-after-stage",
            "--evidence-provenance",
            "fake",
        )

    def start(self, *, timeout=120):
        return taskrun.TaskRun.start(
            self.project,
            "Build greeting",
            options=self.options,
            command=self.entry,
            env=self.env,
            timeout=timeout,
            cwd=self.root,
        )

    def test_withheld_auth_is_prelaunch_and_explicit_pass_launches(self):
        self.configured(auth_requires=True)
        blocked = self.start()
        blocked_view = blocked.status()
        self.assertFalse(blocked_view["done"])
        self.assertIn("auth listing failed", blocked_view["stop_reason"])
        output = blocked.last_advance.stdout + blocked.last_advance.stderr
        self.assertIn("AUTOCODE_PASS_ENV", output)
        self.assertNotIn(CANARY, output)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertFalse(any(row["args"][:1] == ["exec"] for row in calls))
        self.env["AUTOCODE_PASS_ENV"] = "FIXTURE_API_KEY,AUTOCODE_RESOLVER_TOKEN"
        run = self.start()
        view = run.status()
        self.assertFalse(view["done"])
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        # Model-access probes at build start and the stage launch are all audited model
        # calls; every one of them receives only the explicitly passed secret.
        stages = [row for row in calls if row["args"][:1] == ["exec"]]
        self.assertGreaterEqual(len(stages), 1)
        for row in stages:
            self.assertIn("FIXTURE_API_KEY", row["names"])
            self.assertNotIn("AUTOCODE_RESOLVER_TOKEN", row["names"])
            self.assertNotIn("OTHER_PASSWORD", row["names"])
        self.assertNotIn(CANARY, json.dumps(view))

    def test_static_auth_listing_then_stage_401_explains_withheld_candidates(self):
        self.configured()
        run = self.start()
        view = run.status()
        self.assertFalse(view["done"])
        self.assertIn("AUTOCODE_PASS_ENV", view["stop_reason"])
        self.assertIn("FIXTURE_API_KEY", view["stop_reason"])
        advice = (
            view["stop_reason"]
            .split("Credential-like environment variables were withheld: ", 1)[1]
            .split(". If this provider", 1)[0]
        )
        self.assertNotIn("AUTOCODE_RESOLVER_TOKEN", advice)
        self.assertNotIn(CANARY, json.dumps(view))
        output = run.last_advance.stdout + run.last_advance.stderr
        self.assertNotIn(CANARY, output)
        self.assertIn("AUTOCODE_PASS_ENV", output)

    def test_typed_403_names_account_access_and_retains_launched_attempt_uncertainty(self):
        self.configured()
        self.env["AUTOCODE_TEST_AUTH_ACCESS_DENIED"] = "1"
        run = self.start(timeout=300)
        view = run.status()
        self.assertIn("account access denied (HTTP 403)", view["stop_reason"])
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", view["recovery"]["cause"], view["stop_reason"])
        self.assertIn("no automatic replay", view["stop_reason"])
        # An exhausted uncertain attempt is a blocked stop (#3682): the recovery card says AutoCode stopped,
        # and the saved reason it shows beside it keeps the access denial.
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertIn("account access denied (HTTP 403)", view["recovery"]["saved_reason"])
        self.assertNotIn("private-auth-canary", view["stop_reason"])
        self.assertNotIn("private-auth-canary", json.dumps(view["recovery"]))
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(any(row["args"][:1] == ["exec"] for row in calls))
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if row["stage"] == "requirements_gather"]
        self.assertEqual(
            1, len(attempts), "a rejected launched attempt must remain recorded and must not replay itself"
        )
        self.assertEqual(1, attempts[0]["exit_code"])

    def test_doctor_cli_uses_same_effective_configured_provider_environment(self):
        self.configured()
        self.env.update({"AUTOCODE_PROVIDER": "credentialfixture", "AUTOCODE_TEST_VERSION_REQUIRES": "1"})

        def check():
            result = subprocess.run(
                [*self.entry, "doctor", "--workspace", str(self.project), "--json"],
                env=self.env,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertNotIn(CANARY, result.stdout + result.stderr)
            checks = json.loads(result.stdout)["checks"]
            return next(row for row in checks if row["name"] == "provider:credentialfixture")

        self.assertEqual("missing", check()["status"])
        self.env["AUTOCODE_PASS_ENV"] = "FIXTURE_API_KEY,AUTOCODE_RESOLVER_TOKEN"
        self.assertEqual("ok", check()["status"])
        self.assertFalse(any(json.loads(line)["args"][:1] == ["exec"] for line in self.log.read_text().splitlines()))
