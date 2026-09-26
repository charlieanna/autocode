"""Prove each task-type oracle before it may score a live run.

Protocol (docs/testing-plan.md §4.2): the reference delivery must PASS and
each deliberately broken variant must FAIL. A no-op delivery (seed only) must
never pass. These tests never launch a provider or the runner.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
        spec = live_scenarios.scenario("PROGRAM-01")
        manifest = spec["program_manifest"]
        self.assertEqual(spec["task"], manifest["brief"])
        kinds = {row["id"]: row["kind"] for row in manifest["workstreams"]}
        self.assertEqual("deployment", kinds["deploy"])
        self.assertEqual("integration", kinds["integration"])


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
    def test_reference_passes_the_full_journey(self):
        with _project(references.PROGRAM_REFERENCE) as root:
            result = scenarios.program01_oracle(Path(root))
            self.assertEqual(scenarios.PASS, result.status, result.summary)
            self.assertIn("never executed", result.summary)

    def test_wrong_total_and_uncleared_cart_fail(self):
        broken = references.PROGRAM_REFERENCE["services/checkout/server.py"].replace(
            'sum(line["quantity"] * line["price_cents"] for line in lines)',
            'sum(line["price_cents"] for line in lines)').replace(
            'call("DELETE", f"{CONFIG[\'cart\']}/carts/{cart_id}")', 'pass')
        self.assertNotEqual(broken, references.PROGRAM_REFERENCE["services/checkout/server.py"])
        with _project(_variant(references.PROGRAM_REFERENCE, {"services/checkout/server.py": broken})) as root:
            result = scenarios.program01_oracle(Path(root))
            names = [row["name"] for row in result.failed]
            self.assertIn("checkout.total", names)
            self.assertIn("cart.cleared_after_checkout", names)

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
    def _browser_available(self):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            return False
        return any(Path(p).exists() for p in (os.environ.get("AUTOCODE_CHROMIUM") or "", "/opt/pw-browsers/chromium")) \
            or bool(os.environ.get("AUTOCODE_UI_ORACLE_BROWSER"))

    def test_reference_passes_or_defers_without_a_browser(self):
        with _project(references.UI_REFERENCE) as root:
            result = scenarios.ui01_oracle(Path(root))
            if self._browser_available():
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

    @unittest.skipUnless(any(Path(p).exists() for p in ("/opt/pw-browsers/chromium", os.environ.get("AUTOCODE_CHROMIUM") or "x")),
                         "needs Chromium for interaction checks")
    def test_browser_catches_wrong_transitions_and_unstacked_mobile_layout(self):
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
