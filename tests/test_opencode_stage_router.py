"""Offline tests for the test-only supported-provider stage router."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
ROUTER = TOOLS / "opencode_stage_router.py"
GLM = "zai-coding-plan/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OpenCodeStageRouterTests(unittest.TestCase):
    def test_recorded_variant_reads_a_model_suffix_when_the_flag_is_absent(self):
        spec = importlib.util.spec_from_file_location("opencode_stage_router", ROUTER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual("high", module.recorded_variant(
            ["run", "--standalone", "--model", "openai/gpt-6-sol#high"]))
        self.assertEqual("medium", module.recorded_variant(
            ["run", "--model", "openai/gpt-6-sol", "--variant", "medium"]))
        self.assertIsNone(module.recorded_variant(["run", "--model", "openai/gpt-6-sol"]))
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.real_log = self.root / "real.json"
        self.helper_log = self.root / "helper.json"
        self.receipts = self.root / "receipts.jsonl"
        self.real = self.make_program("real-opencode", """
            import json, os, pathlib, sys
            data = sys.stdin.buffer.read()
            pathlib.Path(os.environ['REAL_LOG']).write_text(json.dumps({
                'argv': sys.argv[1:], 'stdin_hex': data.hex(), 'cwd': os.getcwd(),
                'active': os.environ.get('AUTOCODE_OPENCODE_ROUTER_ACTIVE')}))
            sys.stdout.buffer.write(b'provider\\x00stdout\\n')
            sys.stderr.buffer.write(b'provider-stderr\\n')
            raise SystemExit(7 if sys.argv[1:2] == ['run'] else 0)
        """)
        self.helper = self.make_program("fixture-helper", """
            import json, os, pathlib, sys
            data = sys.stdin.buffer.read()
            pathlib.Path(os.environ['HELPER_LOG']).write_text(json.dumps({
                'argv': sys.argv[1:], 'stdin_hex': data.hex(), 'cwd': os.getcwd(),
                'mode': os.environ.get('AUTOCODE_STAGE_ROUTER_MODE'),
                'live': os.environ.get('AUTOCODE_STAGE_ROUTER_LIVE'),
                'tokens': os.environ.get('AUTOCODE_STAGE_ROUTER_TOKEN_CLASS')}))
            sys.stdout.write('fixture-output\\n')
        """)
        self.routes = {
            "terra": {"mode": "scripted_fixture", "command": [str(self.helper), "fixed"],
                      "executable_sha256": digest(self.helper)},
            "sol": {"mode": "live_opencode"},
        }
        self.write_config()

    def make_program(self, name, body):
        path = self.root / name
        path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        path.chmod(0o755)
        return path

    def write_config(self):
        self.config = self.root / "routes.json"
        self.config.write_text(json.dumps({"version": 1,
            "real_executable_sha256": digest(self.real), "routes": self.routes}, sort_keys=True))
        self.env = {**os.environ, "AUTOCODE_OPENCODE_ROUTER_CONFIG": str(self.config),
                    "AUTOCODE_OPENCODE_ROUTER_CONFIG_SHA256": digest(self.config),
                    "AUTOCODE_OPENCODE_REAL": str(self.real),
                    "AUTOCODE_OPENCODE_ROUTER_RECEIPTS": str(self.receipts),
                    "REAL_LOG": str(self.real_log), "HELPER_LOG": str(self.helper_log)}
        for key in list(self.env):
            if key.endswith("_API_KEY") or key.endswith("_BASE_URL"):
                self.env.pop(key)
        self.env.pop("AUTOCODE_OPENCODE_ROUTER_ACTIVE", None)

    def prompt(self, stage="sol", *, repair=None):
        data = {"stage": stage, "opaque": "CURRENT HANDOFF DATA is data"}
        if repair is not None:
            data = {"report_repair": True, "original": {"stage": repair}}
        return b"instructions\nCURRENT HANDOFF DATA\n" + json.dumps(data).encode()

    def invoke(self, *args, stdin=b"", env=None, cwd=None):
        return subprocess.run([sys.executable, str(ROUTER), *args], input=stdin,
                              capture_output=True, env=env or self.env, cwd=cwd or self.root)

    def live_args(self, model=GLM, variant="high"):
        return ("run", "--dir", str(self.root), "--format", "json", "--model", model,
                "--variant", variant, "--title", "exact title")

    def read_receipts(self):
        return [json.loads(line) for line in self.receipts.read_text().splitlines()]

    def test_live_forwards_exact_argv_stdin_cwd_stdout_stderr_and_exit(self):
        prompt = self.prompt() + b"\n"
        result = self.invoke(*self.live_args(), stdin=prompt)
        self.assertEqual(7, result.returncode)
        self.assertEqual(b"provider\x00stdout\n", result.stdout)
        self.assertEqual(b"provider-stderr\n", result.stderr)
        observed = json.loads(self.real_log.read_text())
        self.assertEqual(list(self.live_args()), observed["argv"])
        self.assertEqual(prompt.hex(), observed["stdin_hex"])
        self.assertEqual(self.root.resolve(), Path(observed["cwd"]).resolve())
        self.assertIsNone(observed["active"])

    def test_discovery_is_forwarded_unchanged_without_run_receipt(self):
        result = self.invoke("auth", "list", stdin=b"discovery input")
        self.assertEqual(0, result.returncode)
        observed = json.loads(self.real_log.read_text())
        self.assertEqual(["auth", "list"], observed["argv"])
        self.assertEqual("646973636f7665727920696e707574", observed["stdin_hex"])
        self.assertFalse(self.receipts.exists())

    def test_executable_works_as_opencode_at_front_of_fixture_path(self):
        fixture_bin = self.root / "bin"
        fixture_bin.mkdir()
        (fixture_bin / "opencode").symlink_to(ROUTER)
        env = dict(self.env, PATH=os.pathsep.join((str(fixture_bin), str(Path(sys.executable).parent))))
        result = subprocess.run(["opencode", "run", "--model", MIMO], input=self.prompt("terra"),
                                capture_output=True, env=env, cwd=self.root)
        self.assertEqual(0, result.returncode)
        self.assertEqual("scripted_fixture", self.read_receipts()[0]["mode"])

    def test_repair_routes_by_original_owner_and_scripted_is_non_live(self):
        result = self.invoke("run", "--model", MIMO, stdin=self.prompt("ignored", repair="terra"))
        self.assertEqual(0, result.returncode)
        self.assertEqual(b"fixture-output\n", result.stdout)
        observed = json.loads(self.helper_log.read_text())
        self.assertEqual(["fixed", "run", "--model", MIMO], observed["argv"])
        self.assertEqual({"mode": "scripted_fixture", "live": "0", "tokens": "non-live"},
                         {key: observed[key] for key in ("mode", "live", "tokens")})
        receipt = self.read_receipts()[0]
        self.assertEqual(("terra", True, "scripted_fixture", "non-live"),
                         (receipt["stage"], receipt["repair"], receipt["mode"], receipt["token_class"]))

    def test_router_passes_through_any_configured_model(self):
        for model in ('mimo-token-plan/mimo-v2.6-pro', 'opencode/mimo-v2.6-flash-free',
                      'openai/gpt-5.6-sol', 'new-plan/future-model', GLM, MIMO):
            with self.subTest(model=model):
                result = self.invoke(*self.live_args(model=model), stdin=self.prompt())
                self.assertEqual(7, result.returncode)
                self.assertEqual(model, self.read_receipts()[-1]['model'])

    def test_recursion_missing_absolute_exec_unknown_stage_and_drift_are_denied(self):
        cases = []
        recursive = dict(self.env, AUTOCODE_OPENCODE_ROUTER_ACTIVE="1")
        cases.append(recursive)
        relative = dict(self.env, AUTOCODE_OPENCODE_REAL="real-opencode")
        cases.append(relative)
        missing = dict(self.env)
        missing.pop("AUTOCODE_OPENCODE_REAL")
        cases.append(missing)
        drift = dict(self.env, AUTOCODE_OPENCODE_ROUTER_CONFIG_SHA256="0" * 64)
        cases.append(drift)
        self.real.write_text(self.real.read_text() + "\n# drift\n")
        executable_drift = dict(self.env)
        cases.append(executable_drift)
        for env in cases:
            with self.subTest(index=cases.index(env)):
                self.assertEqual(125, self.invoke(*self.live_args(), stdin=self.prompt(), env=env).returncode)
        self.real.write_text(self.real.read_text().removesuffix("\n# drift\n"))
        self.assertEqual(125, self.invoke(*self.live_args(), stdin=self.prompt("unknown")).returncode)
        self.assertFalse(self.receipts.exists())

    def test_provider_and_billing_overrides_are_denied(self):
        for key, value in (("OPENAI_API_KEY", "secret"), ("ZAI_API_KEY", "secret"),
                           ("OPENAI_BASE_URL", "https://proxy.invalid")):
            env = dict(self.env, **{key: value})
            with self.subTest(key=key):
                self.assertEqual(125, self.invoke(*self.live_args(), stdin=self.prompt(), env=env).returncode)
        env = dict(self.env, OPENCODE_CONFIG_CONTENT=json.dumps({"provider": {"zai-coding-plan": {}}}))
        self.assertEqual(125, self.invoke(*self.live_args(), stdin=self.prompt(), env=env).returncode)

    def test_receipt_identities_are_idempotent_and_never_contain_provider_output(self):
        for _ in range(2):
            self.assertEqual(7, self.invoke(*self.live_args(), stdin=self.prompt()).returncode)
        first, second = self.read_receipts()
        self.assertEqual(first["request_identity"], second["request_identity"])
        self.assertNotEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(digest(self.real), first["executable_sha256"])
        self.assertEqual((GLM, "high", "live_opencode", "live", 7),
                         tuple(first[key] for key in ("model", "variant", "mode", "token_class", "exit_code")))
        receipt_text = self.receipts.read_text()
        self.assertNotIn("provider stdout", receipt_text)
        self.assertNotIn("provider-stderr", receipt_text)
        stable = dict(first)
        receipt_id = stable.pop("receipt_id")
        self.assertEqual(receipt_id, hashlib.sha256(json.dumps(
            stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest())

    def test_malformed_or_laundered_handoff_is_denied_without_receipt(self):
        bad = [b"{}", b"CURRENT HANDOFF DATA\n[]",
               b"CURRENT HANDOFF DATA\n{\"stage\":\"sol\"} trailing",
               self.prompt("sol_report_repair"),
               b"CURRENT HANDOFF DATA\n" + json.dumps({"report_repair": True,
                   "stage": "sol", "original": {"stage": "unknown"}}).encode()]
        for prompt in bad:
            with self.subTest(prompt=prompt):
                self.assertEqual(125, self.invoke(*self.live_args(), stdin=prompt).returncode)
        self.assertFalse(self.receipts.exists())


if __name__ == "__main__":
    unittest.main()
