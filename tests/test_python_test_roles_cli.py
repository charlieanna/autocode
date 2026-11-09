"""Real named proof keeps original suffix-named product bytes on the base side."""

import shlex
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path

import autocode_protected_oracles as protected
import autocode_verify as verify


@unittest.skipUnless(sys.platform == "linux", "supervised test commands require supported Linux")
class PythonRoleProofTests(unittest.TestCase):
    def test_named_proof_keeps_product_on_base_and_custom_tests_identical(self):
        with tempfile.TemporaryDirectory(prefix="python-role-proof-") as temporary:
            root = Path(temporary) / "project"
            root.mkdir()
            (root / "pkg").mkdir()
            (root / "pkg/__init__.py").write_text("")
            (root / "pkg/billing_tests.py").write_text("def charge():\n    return 1\n")
            (root / "pyproject.toml").write_text('[tool.setuptools]\npackages=["pkg"]\n')
            original = (
                "import unittest\nfrom pkg.billing_tests import charge\n"
                "class BillingChecks(unittest.TestCase):\n"
                "    def test_original_integer(self):\n        self.assertIsInstance(charge(), int)\n"
            )
            (root / "checks_tests.py").write_text(original)
            for command in (
                ["git", "init", "-q"],
                ["git", "config", "maintenance.auto", "false"],
                ["git", "config", "gc.auto", "0"],
                ["git", "add", "."],
                ["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "original"],
            ):
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            runtime = Path(temporary) / "runtime"
            venv.EnvBuilder(with_pip=False).create(runtime)
            python = str(runtime / "bin/python")
            suite = f"{shlex.quote(python)} -m unittest -v checks_tests"
            framework = verify.command_framework(suite)
            evidence = Path(temporary) / "evidence"
            baseline = verify.baseline(root, base, evidence, framework=framework, suite_command=suite, timeout=60)
            (root / "pkg/billing_tests.py").write_text("def charge():\n    return 2\n")
            (root / "checks_tests.py").write_text(
                original + "    def test_corrected_charge(self):\n        self.assertEqual(2, charge())\n"
            )
            result = verify.verify(
                root, base, evidence, framework=framework, suite_command=suite, base_suite=baseline, timeout=60
            )
            self.assertEqual(verify.PASS, result["verdict"], result)
            self.assertEqual(["pkg/billing_tests.py"], result["source_files"])
            self.assertEqual(["checks_tests.py"], result["test_files"])
            self.assertIn("checks_tests.BillingChecks.test_corrected_charge", result["fail_to_pass"])
            self.assertIn("checks_tests.BillingChecks.test_original_integer", result["pass_to_pass"])
            self.assertIn("2 != 1", Path(result["checks"]["regression_on_base"]["output"]).read_text())
            self.assertNotIn("pkg/billing_tests.py", protected.inventory(root, verify.is_test_path))
