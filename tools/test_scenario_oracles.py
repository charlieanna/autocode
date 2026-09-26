"""Exercise positive and targeted negative controls for each task-type oracle.

Protocol (docs/testing-plan.md §4.2): the reference delivery must PASS and
each deliberately broken variant must FAIL. A no-op delivery (seed only) must
never pass. These tests never launch a provider or the runner.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_scenarios  # noqa: E402
import scenario_references as references  # noqa: E402
import task_scenarios as scenarios  # noqa: E402


def _project(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    temp = tempfile.TemporaryDirectory(prefix="oracle-")
    references.write(files, temp.name)
    return temp


def _variant(base: dict[str, str], changes: dict[str, str | None]) -> dict[str, str]:
    """Copy of a reference delivery with files replaced (or removed when None)."""
    files = dict(base)
    for rel, body in changes.items():
        if body is None:
            files.pop(rel, None)
        else:
            files[rel] = body
    return files


def _browser_available():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = scenarios._chromium(playwright)
            browser.close()
        return True
    except (ImportError, RuntimeError):
        return False


class RegistryTest(unittest.TestCase):
    def test_task_scenarios_are_registered_with_types(self):
        registry = live_scenarios.registry()
        for scenario_id, task_type in (("BUGFIX-01", "bugfix"), ("FEATURE-01", "feature"), ("ARCH-01", "architecture"),
                                       ("PROGRAM-01", "program"), ("UI-01", "ui")):
            self.assertIn(scenario_id, registry)
            self.assertEqual(task_type, registry[scenario_id]["task_type"])
            self.assertEqual("NOT_RUN", registry[scenario_id]["baseline"]["status"])
        self.assertIn("LIVE-01", registry)
        with self.assertRaisesRegex(ValueError, "BUGFIX-01"):
            live_scenarios.scenario("NOPE-00")

    def test_program_manifest_matches_the_program_brief(self):
        import autocode_program
        spec = live_scenarios.scenario("PROGRAM-01")
        manifest = spec["program_manifest"]
        self.assertEqual(spec["task"], manifest["brief"])
        kinds = {row["id"]: row["kind"] for row in manifest["workstreams"]}
        self.assertEqual("code", kinds["deploy"])
        self.assertEqual("integration", kinds["integration"])
        self.assertIn("No deployment is executed", manifest["shared"]["constraints"])
        self.assertIn("external system", next(row["brief"] for row in manifest["workstreams"] if row["id"] == "deploy"))
        autocode_program.validate_manifest(manifest)


class OracleProcessTest(unittest.TestCase):
    def test_timeout_kills_child_service_and_bounds_wait(self):
        port = scenarios._free_port()
        script = ('import subprocess, sys, time\n'
                  'from http.server import HTTPServer, BaseHTTPRequestHandler\n'
                  'if len(sys.argv) > 1:\n'
                  f'    HTTPServer(("127.0.0.1", {port}), BaseHTTPRequestHandler).serve_forever()\n'
                  'else:\n'
                  '    subprocess.Popen([sys.executable, __file__, "child"])\n'
                  '    time.sleep(60)\n')
        with _project({"hang.py": script}) as root:
            started = time.monotonic()
            code, _, err = scenarios._run([sys.executable, "hang.py"], Path(root), timeout=1)
            self.assertEqual((-1, "TIMEOUT"), (code, err))
            self.assertLess(time.monotonic() - started, 8)
            with socket.socket() as sock:
                sock.settimeout(0.5)
                self.assertNotEqual(0, sock.connect_ex(("127.0.0.1", port)))

    def test_non_utf8_stdout_is_decoded_safely(self):
        with _project({}) as root:
            code, out, _ = scenarios._run([sys.executable, "-c", "import os; os.write(1, b'\\xff')"], Path(root))
            self.assertEqual(0, code)
            self.assertEqual("\ufffd", out)

    def test_cleanup_drain_timeout_preserves_result_and_bounds_reaping(self):
        for wait_error in (None, subprocess.TimeoutExpired("child", 5)):
            with self.subTest(wait_error=wait_error):
                proc = mock.Mock()
                proc.communicate.side_effect = [subprocess.TimeoutExpired("child", 1),
                                                subprocess.TimeoutExpired("child", 5)]
                proc.wait.side_effect = wait_error
                with mock.patch.object(scenarios.subprocess, "Popen", return_value=proc), \
                        mock.patch.object(scenarios.os, "killpg") as killpg:
                    self.assertEqual((-1, "", "TIMEOUT"), scenarios._run(["child"], HERE, timeout=1))
                killpg.assert_called_once_with(proc.pid, scenarios.signal.SIGKILL)
                self.assertEqual([mock.call(input=None, timeout=1), mock.call(timeout=5)],
                                 proc.communicate.call_args_list)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    stream.close.assert_called_once_with()
                proc.wait.assert_called_once_with(timeout=5)


class BugfixOracleTest(unittest.TestCase):
    def test_reference_passes_and_seed_alone_fails(self):
        with _project(references.BUGFIX_REFERENCE) as root:
            result = scenarios.bugfix01_oracle(Path(root))
            self.assertEqual(scenarios.PASS, result.status, result.summary)
        with _project(scenarios.BUGFIX_SEED) as root:
            result = scenarios.bugfix01_oracle(Path(root))
            self.assertEqual(scenarios.FAIL, result.status)
            self.assertIn("blank.exit", [row["name"] for row in result.failed])

    def test_fix_without_regression_test_fails(self):
        files = _variant(references.BUGFIX_REFERENCE, {"test_greet.py": scenarios.BUGFIX_SEED["test_greet.py"]})
        with _project(files) as root:
            result = scenarios.bugfix01_oracle(Path(root))
            self.assertEqual(scenarios.FAIL, result.status)
            self.assertIn("regression.fails_on_seed", [row["name"] for row in result.failed])

    def test_dropping_a_seed_test_or_touching_readme_fails(self):
        weakened = references.BUGFIX_REFERENCE["test_greet.py"].replace("def test_two_arg(", "def removed_two_arg(")
        with _project(_variant(references.BUGFIX_REFERENCE, {"test_greet.py": weakened})) as root:
            result = scenarios.bugfix01_oracle(Path(root))
            self.assertIn("seed_test_kept.test_two_arg", [row["name"] for row in result.failed])
        with _project(_variant(references.BUGFIX_REFERENCE, {"README.md": "# rewritten\n"})) as root:
            result = scenarios.bugfix01_oracle(Path(root))
            self.assertIn("readme.unchanged", [row["name"] for row in result.failed])

    def test_rewrite_that_breaks_valid_input_fails(self):
        broken = references.BUGFIX_REFERENCE["greet.py"].replace('return f"Hello, {name}"', 'return f"Hi, {name}"')
        with _project(_variant(references.BUGFIX_REFERENCE, {"greet.py": broken})) as root:
            result = scenarios.bugfix01_oracle(Path(root))
            self.assertIn("ada.output", [row["name"] for row in result.failed])


class FeatureOracleTest(unittest.TestCase):
    def test_missing_or_modified_delivered_store_fails(self):
        for store in (None, '{"notes": []}\n', scenarios.FEATURE_SEED["notes.json"] + "\n"):
            with self.subTest(store=store), _project(_variant(references.FEATURE_REFERENCE, {"notes.json": store})) as root:
                result = scenarios.feature01_oracle(Path(root))
                self.assertIn("store.delivered_unchanged", [row["name"] for row in result.failed])

    def test_reference_passes_and_seed_alone_fails(self):
        with _project(references.FEATURE_REFERENCE) as root:
            result = scenarios.feature01_oracle(Path(root))
            self.assertEqual(scenarios.PASS, result.status, result.summary)
        with _project(scenarios.FEATURE_SEED) as root:
            result = scenarios.feature01_oracle(Path(root))
            self.assertEqual(scenarios.FAIL, result.status)

    def test_case_sensitive_filter_fails(self):
        broken = references.FEATURE_REFERENCE["notes.py"].replace("tag.casefold() == wanted", "tag == rest[1]")
        with _project(_variant(references.FEATURE_REFERENCE, {"notes.py": broken})) as root:
            result = scenarios.feature01_oracle(Path(root))
            self.assertEqual(scenarios.FAIL, result.status)
            self.assertIn("filter[work]", [row["name"] for row in result.failed])

    def test_filter_that_rewrites_the_store_fails(self):
        broken = references.FEATURE_REFERENCE["notes.py"].replace(
            "        for note in load()[\"notes\"]:\n            if wanted",
            "        data = load()\n        save({\"notes\": data[\"notes\"], \"touched\": True})\n"
            "        for note in data[\"notes\"]:\n            if wanted")
        self.assertNotEqual(broken, references.FEATURE_REFERENCE["notes.py"])
        with _project(_variant(references.FEATURE_REFERENCE, {"notes.py": broken})) as root:
            result = scenarios.feature01_oracle(Path(root))
            self.assertIn("store.unchanged_after_reads", [row["name"] for row in result.failed])

    def test_accepting_an_empty_tag_fails(self):
        broken = references.FEATURE_REFERENCE["notes.py"].replace(" or not rest[1].strip()", "")
        with _project(_variant(references.FEATURE_REFERENCE, {"notes.py": broken})) as root:
            result = scenarios.feature01_oracle(Path(root))
            self.assertIn("filter.empty_tag_rejected", [row["name"] for row in result.failed])


class ArchOracleTest(unittest.TestCase):
    def test_relative_comma_and_nested_imports_fail_both_checkers(self):
        variants = [
            ("services/catalog/api.py", "from ..notifications import api as notifications\n"),
            ("services/cart/api.py", "import services.cart.api, services.catalog.api\n"),
            ("services/cart/api.py", "from services import cart, catalog\n"),
            ("services/cart/api.py", "from .. import cart, catalog\n"),
            ("services/cart/nested/__init__.py", "from ...catalog import api\n"),
        ]
        for path, statement in variants:
            with self.subTest(statement=statement):
                files = _variant(references.ARCH_REFERENCE, {path: statement + references.ARCH_REFERENCE.get(path, "")})
                with _project(files) as root:
                    result = scenarios.arch01_oracle(Path(root))
                    self.assertIn(f"boundary[{path.split('/')[1]}].imports_only_declared_deps",
                                  [row["name"] for row in result.failed])
                    code, out, err = scenarios._run([sys.executable, "architecture/check.py"], Path(root))
                    self.assertEqual(1, code, out + err)
                    self.assertIn("without declaring it", out)
                    self.assertNotIn("Traceback", err)

    def test_docstrings_relative_allowed_imports_and_stdout_are_harmless(self):
        source = ('"""Example only:\nimport services.notifications.api\n"""\n'
                  'from __future__ import annotations\nprint("Catalog initialized")\n'
                  + references.ARCH_REFERENCE["services/catalog/api.py"])
        checkout = references.ARCH_REFERENCE["services/checkout/api.py"].replace(
            "from services.cart import", "from ..cart import").replace("from services.catalog import", "from ..catalog import")
        with _project(_variant(references.ARCH_REFERENCE, {"services/catalog/api.py": source,
                                                         "services/checkout/api.py": checkout})) as root:
            result = scenarios.arch01_oracle(Path(root))
            self.assertEqual(scenarios.PASS, result.status, result.summary)
            code, out, err = scenarios._run([sys.executable, "architecture/check.py"], Path(root))
            self.assertEqual(0, code, out + err)

    def test_future_import_does_not_make_import_only_checker_effective(self):
        checker = ('import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path.cwd()))\n'
                   'import services.catalog.api\n')
        source = '"""Catalog."""\nfrom __future__ import annotations\n' + references.ARCH_REFERENCE["services/catalog/api.py"]
        with _project(_variant(references.ARCH_REFERENCE, {"architecture/check.py": checker,
                                                         "services/catalog/api.py": source})) as root:
            result = scenarios.arch01_oracle(Path(root))
            self.assertIn("check.py.rejects_injected_violation", [row["name"] for row in result.failed])
            self.assertNotIn("check.py.passes_on_candidate", [row["name"] for row in result.failed])

    def test_unrelated_checker_errors_do_not_count_as_rejection(self):
        for checker in ('raise RuntimeError("broken checker")\n',
                        'from pathlib import Path\nif "if False:" in Path("services/catalog/api.py").read_text():\n'
                        '    raise SystemExit(1)\n',
                        'from pathlib import Path\nif "import services.notifications.api" in Path("services/catalog/api.py").read_text():\n'
                        '    raise RuntimeError("unrelated")\n'):
            with self.subTest(checker=checker), _project(_variant(references.ARCH_REFERENCE, {"architecture/check.py": checker})) as root:
                result = scenarios.arch01_oracle(Path(root))
                self.assertIn("check.py.rejects_injected_violation", [row["name"] for row in result.failed])

    def test_invalid_probe_stdout_is_failure_not_oracle_exception(self):
        original = scenarios._run
        def run(command, **kwargs):
            if "-c" in command:
                return 0, "not json", ""
            return original(command, **kwargs)
        with _project(references.ARCH_REFERENCE) as root, mock.patch.object(scenarios, "_run", side_effect=run):
            result = scenarios.arch01_oracle(Path(root))
            self.assertIn("api[catalog].implements_contract", [row["name"] for row in result.failed])

    def test_reference_passes_and_empty_workspace_fails(self):
        with _project(references.ARCH_REFERENCE) as root:
            result = scenarios.arch01_oracle(Path(root))
            self.assertEqual(scenarios.PASS, result.status, result.summary)
        with _project({}) as root:
            self.assertEqual(scenarios.FAIL, scenarios.arch01_oracle(Path(root)).status)

    def test_undeclared_import_is_a_boundary_violation(self):
        leaking = "import services.notifications.api\n" + references.ARCH_REFERENCE["services/catalog/api.py"]
        with _project(_variant(references.ARCH_REFERENCE, {"services/catalog/api.py": leaking})) as root:
            result = scenarios.arch01_oracle(Path(root))
            names = [row["name"] for row in result.failed]
            self.assertIn("boundary[catalog].imports_only_declared_deps", names)
            # The candidate's own checker must catch it too, otherwise it is vacuous.
            self.assertIn("check.py.passes_on_candidate", names)

    def test_vacuous_checker_fails(self):
        with _project(_variant(references.ARCH_REFERENCE, {"architecture/check.py": "raise SystemExit(0)\n"})) as root:
            result = scenarios.arch01_oracle(Path(root))
            self.assertIn("check.py.rejects_injected_violation", [row["name"] for row in result.failed])

    def test_contract_operation_without_implementation_fails(self):
        contract = json.loads(references.ARCH_REFERENCE["contracts/cart.json"])
        contract["operations"].append({"name": "merge_carts", "description": "", "input": {}, "output": {}})
        with _project(_variant(references.ARCH_REFERENCE, {"contracts/cart.json": json.dumps(contract)})) as root:
            result = scenarios.arch01_oracle(Path(root))
            self.assertIn("api[cart].implements_contract", [row["name"] for row in result.failed])

    def test_wrong_graph_shape_fails(self):
        spec = json.loads(references.ARCH_REFERENCE["architecture/components.json"])
        spec["components"][0]["depends_on"] = ["checkout"]  # catalog -> checkout -> catalog
        with _project(_variant(references.ARCH_REFERENCE, {"architecture/components.json": json.dumps(spec)})) as root:
            result = scenarios.arch01_oracle(Path(root))
            names = [row["name"] for row in result.failed]
            self.assertIn("components.acyclic", names)
            self.assertIn("components.roots_have_no_deps", names)


class ProgramOracleTest(unittest.TestCase):
    def test_missing_contracts_and_empty_launcher_and_e2e_fail(self):
        changes = {f"contracts/{shape}.json": None for shape in scenarios.PROGRAM_SHAPES}
        changes.update({"scripts/run_local.py": "", "tests/test_e2e.py": ""})
        with _project(_variant(references.PROGRAM_REFERENCE, changes)) as root:
            result = scenarios.program01_oracle(Path(root))
            names = [row["name"] for row in result.failed]
            for shape in scenarios.PROGRAM_SHAPES:
                self.assertIn(f"file[contracts/{shape}.json]", names)
                self.assertIn(f"contract[{shape}].shape", names)
            self.assertIn("e2e.passes_with_tests", names)
            self.assertIn("launcher.running", names)
            self.assertIn("health[gateway]", names)

    def test_malformed_or_incomplete_contract_shapes_fail(self):
        changes = {"contracts/Item.json": "[]", "contracts/CartItem.json": "not JSON",
                   "contracts/Cart.json": '{"type": "object", "required": ["cartId"]}',
                   "contracts/Order.json": '{"type": "object", "required": [{"orderId": "str"}]}'}
        with _project(_variant(references.PROGRAM_REFERENCE, changes)) as root:
            result = scenarios.program01_oracle(Path(root))
            for shape in scenarios.PROGRAM_SHAPES:
                self.assertIn(f"contract[{shape}].shape", [row["name"] for row in result.failed])

    def test_launcher_miswiring_fails_even_with_good_servers(self):
        launcher = references.RUN_LOCAL.replace('"--cart-url", url("cart")', '"--cart-url", url("catalog")')
        with _project(_variant(references.PROGRAM_REFERENCE, {"scripts/run_local.py": launcher})) as root:
            result = scenarios.program01_oracle(Path(root))
            self.assertIn("cart.add_first", [row["name"] for row in result.failed])

    def test_launcher_shutdown_is_checked_and_leftovers_are_killed(self):
        for launcher in (references.RUN_LOCAL.replace("signal.signal(signal.SIGTERM, stop)",
                                                     "signal.signal(signal.SIGTERM, signal.SIG_IGN)"),
                         references.RUN_LOCAL.replace("child.terminate()", "pass").replace(
                             "child.wait(timeout=max(0.01, deadline - time.monotonic()))", "pass")):
            with self.subTest(launcher=launcher), _project(_variant(references.PROGRAM_REFERENCE,
                                                                  {"scripts/run_local.py": launcher})) as root:
                services = scenarios._Services(Path(root))
                with mock.patch.object(scenarios, "_Services", return_value=services):
                    result = scenarios.program01_oracle(Path(root))
                self.assertIn("launcher.stops_services", [row["name"] for row in result.failed])
                self.assertIsNotNone(services.proc.poll())
                for port in services.ports.values():
                    with socket.socket() as sock:
                        sock.settimeout(0.5)
                        self.assertNotEqual(0, sock.connect_ex(("127.0.0.1", port)))

    def test_e2e_empty_failed_and_erroring_suites_fail(self):
        for test in ("", "raise RuntimeError('not a test')\n",
                     "import unittest\nclass Test(unittest.TestCase):\n    def test_error(self):\n        raise RuntimeError('broken test')\n",
                     "import unittest\nclass Test(unittest.TestCase):\n    def test_bad(self):\n        self.fail('always fails')\n"):
            with self.subTest(test=test), _project(_variant(references.PROGRAM_REFERENCE, {"tests/test_e2e.py": test})) as root:
                checks = scenarios.Checks("e2e")
                scenarios._program_e2e(Path(root), checks)
                self.assertIn("e2e.passes_with_tests", [row["name"] for row in checks.failed])

    def test_alternate_stdlib_serializers_pass_the_full_journey(self):
        source = references.CHECKOUT_SERVER
        variants = {
            "JSONEncoder.encode": source.replace("json.dumps(payload).encode()",
                                                  "json.JSONEncoder().encode(payload).encode()"),
            "json.dump": source.replace("import argparse\n", "import argparse\nimport io\n").replace(
                'data = b"" if payload is None else json.dumps(payload).encode()',
                'buffer = io.StringIO()\n        if payload is not None:\n'
                '            json.dump(payload, buffer)\n        data = buffer.getvalue().encode()'),
        }
        for name, server in variants.items():
            with self.subTest(serializer=name), _project(_variant(
                    references.PROGRAM_REFERENCE, {"services/checkout/server.py": server})) as root:
                self.assertNotEqual(source, server)
                result = scenarios.program01_oracle(Path(root))
                self.assertEqual(scenarios.PASS, result.status, result.summary)

    def test_reference_passes_the_full_journey(self):
        with _project(references.PROGRAM_REFERENCE) as root:
            result = scenarios.program01_oracle(Path(root))
            self.assertEqual(scenarios.PASS, result.status, result.summary)
            self.assertIn("never executed", result.summary)

    def test_green_noop_tests_cannot_hide_wrong_total_and_uncleared_cart(self):
        broken = references.PROGRAM_REFERENCE["services/checkout/server.py"].replace(
            'sum(line["quantity"] * line["price_cents"] for line in lines)',
            'sum(line["price_cents"] for line in lines)').replace(
            'call("DELETE", f"{CONFIG[\'cart\']}/carts/{cart_id}")', 'pass')
        self.assertNotEqual(broken, references.PROGRAM_REFERENCE["services/checkout/server.py"])
        green_test = "import unittest\nclass Test(unittest.TestCase):\n    def test_green(self):\n        pass\n"
        with _project(_variant(references.PROGRAM_REFERENCE, {"services/checkout/server.py": broken,
                                                            "tests/test_e2e.py": green_test})) as root:
            result = scenarios.program01_oracle(Path(root))
            self.assertEqual(scenarios.FAIL, result.status)
            names = [row["name"] for row in result.failed]
            self.assertIn("checkout.total", names)
            self.assertIn("cart.cleared_after_checkout", names)
            self.assertNotIn("e2e.passes_with_tests", names)

    def test_missing_service_and_compose_entry_fail(self):
        files = _variant(references.PROGRAM_REFERENCE, {
            "gateway/server.py": None,
            "deploy/docker-compose.yml": references.PROGRAM_REFERENCE["deploy/docker-compose.yml"].replace("  gateway:", "  edge:")})
        with _project(files) as root:
            result = scenarios.program01_oracle(Path(root))
            names = [row["name"] for row in result.failed]
            self.assertIn("health[gateway]", names)
            self.assertIn("compose.services", names)

    def test_compose_parser_reads_service_names_without_yaml(self):
        names = scenarios.compose_services(references.PROGRAM_REFERENCE["deploy/docker-compose.yml"])
        self.assertEqual(["catalog", "cart", "checkout", "gateway"], names)
        self.assertEqual([], scenarios.compose_services("version: '3'\nvolumes:\n  data:\n"))

    def test_reference_e2e_test_passes(self):
        with _project(references.PROGRAM_REFERENCE) as root:
            proc = subprocess.run([sys.executable, "-m", "unittest", "-q", "tests.test_e2e"], cwd=root,
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(0, proc.returncode, proc.stderr)


class UiOracleTest(unittest.TestCase):
    def test_browser_helper_with_unset_paths_and_missing_browser(self):
        playwright = mock.Mock()
        playwright.chromium.launch.side_effect = RuntimeError("browser absent")
        manager = mock.MagicMock()
        manager.__enter__.return_value = playwright
        module = mock.Mock(sync_playwright=mock.Mock(return_value=manager))
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(Path, "is_file", return_value=False), \
                mock.patch.dict(sys.modules, {"playwright.sync_api": module}):
            self.assertFalse(_browser_available())
        playwright.chromium.launch.assert_called_once_with()

    def test_browser_helper_accepts_an_explicit_executable(self):
        playwright = mock.Mock()
        with mock.patch.dict(os.environ, {"PLAYWRIGHT_CHROMIUM_EXECUTABLE": "/browser"}, clear=True), \
                mock.patch.object(Path, "is_file", side_effect=lambda: True):
            self.assertIs(playwright.chromium.launch.return_value, scenarios._chromium(playwright))
        playwright.chromium.launch.assert_called_once_with(executable_path="/browser")

    def test_reference_passes_or_defers_without_a_browser(self):
        with _project(references.UI_REFERENCE) as root:
            result = scenarios.ui01_oracle(Path(root))
            if _browser_available():
                self.assertEqual(scenarios.PASS, result.status, result.summary)
                self.assertIn("layout.mobile.buttons_stacked", [row["name"] for row in result.checks])
            else:
                self.assertEqual(scenarios.DEFERRED, result.status, result.summary)

    def test_static_checks_reject_missing_role_and_external_resources(self):
        page = references.UI_REFERENCE["web/index.html"].replace(' role="status"', "").replace(
            "<style>", '<link rel="stylesheet" href="https://example.invalid/x.css"><style>')
        with _project(_variant(references.UI_REFERENCE, {"web/index.html": page})) as root:
            result = scenarios.ui01_oracle(Path(root))
            self.assertEqual(scenarios.FAIL, result.status)
            names = [row["name"] for row in result.failed]
            self.assertIn("status.role", names)
            self.assertIn("no_external_resources", names)

    def test_static_only_delivery_never_passes(self):
        # Seed only: no page at all.
        with _project(scenarios.UI_SEED) as root:
            self.assertEqual(scenarios.FAIL, scenarios.ui01_oracle(Path(root)).status)

    def test_browser_catches_wrong_transitions_and_unstacked_mobile_layout(self):
        if not _browser_available():
            self.skipTest("needs Chromium for interaction checks")
        page = references.UI_REFERENCE["web/index.html"].replace(
            'state = "PAUSED"; render();', 'state = "CANCELLED"; render();').replace(
            "    .actions { flex-direction: column; }\n", "")
        with _project(_variant(references.UI_REFERENCE, {"web/index.html": page})) as root:
            result = scenarios.ui01_oracle(Path(root))
            self.assertEqual(scenarios.FAIL, result.status)
            names = [row["name"] for row in result.failed]
            self.assertIn("state[PAUSED].status_text", names)
            self.assertIn("layout.mobile.buttons_stacked", names)


if __name__ == "__main__":
    unittest.main()
