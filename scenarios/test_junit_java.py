"""Real Maven/JUnit catalog controls; no model or XML-producing fake is used."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import catalog
from harness.processes import run_cli
from harness.project import materialize

REPO = Path(__file__).resolve().parents[1]


class JavaJUnitScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ("java", "javac", "mvn"):
            if not shutil.which(tool):
                raise RuntimeError(f"Java catalog prerequisite missing: {tool}; run harness/java_toolchain.py")
        checked = subprocess.run(["javac", "-version"], capture_output=True, text=True, timeout=15)
        if checked.returncode:
            raise RuntimeError("javac is not a working JDK; prepare the pinned Java toolchain")
        cls.scenario = catalog.load("feature-java-page-count")

    def test_public_cli_completes_with_persisted_project_junit_selection(self):
        # No fake-only command flags may mask the seed's project settings.
        self.assertFalse(self.scenario.fake_flags)
        settings = tomllib.loads((self.scenario.seed / "pyproject.toml").read_text())["tool"]["autocode"]
        with tempfile.TemporaryDirectory(prefix="junit-java-cli-") as directory:
            root = Path(directory)
            out = root / "results"
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "AUTOCODE_HOME": str(root / "registry")}
            command = [
                sys.executable,
                str(REPO / "scenarios/run.py"),
                "run",
                self.scenario.id,
                "--fake",
                "--out",
                str(out),
                "--timeout-minutes",
                "10",
            ]
            completed = run_cli(command, env=env, cwd=REPO, timeout=720)
            files = list(out.glob("*/result.json"))
            self.assertEqual(1, len(files), completed.stdout + completed.stderr)
            result = json.loads(files[0].read_text())
            diagnostic = (
                completed.stdout
                + completed.stderr
                + json.dumps(
                    {key: result.get(key) for key in ("runner_status", "harness_error", "oracle_error", "checks")}
                )
            )
            self.assertEqual(0, completed.returncode, diagnostic)
            self.assertEqual("TASK_COMPLETE", result["runner_status"], diagnostic)
            self.assertTrue(result["oracle_passed"], diagnostic)
            checks = {row["name"]: row for row in result["checks"]}
            self.assertEqual([], [name for name, row in checks.items() if not row["ok"]], diagnostic)
            for name in (
                "real_junit_suite_passes",
                "guard_and_regression_are_real_cases",
                "hidden_java_behavior",
                "regression_fails_on_original_product",
                "existing_guard_passes_on_original",
                "named_regression_proof",
            ):
                self.assertIn(name, checks, diagnostic)
                self.assertTrue(checks[name]["ok"], checks[name])

            # Inspect the public contract after approval/resume has persisted
            # the settings. No runtime module or saved state is read here.
            project = Path(result["evidence"]) / "project"
            self.assertEqual(settings, tomllib.loads((project / "pyproject.toml").read_text())["tool"]["autocode"])
            inspected = run_cli(
                [
                    sys.executable,
                    str(REPO / "tools/autocode.py"),
                    "--workspace",
                    str(project),
                    "--run-dir",
                    result["run_dir"],
                    "--status",
                    "--inspect-evidence",
                ],
                env=env,
                cwd=REPO,
                timeout=60,
            )
            self.assertEqual(0, inspected.returncode, inspected.stdout + inspected.stderr)
            inspection = json.loads(inspected.stdout)
            self.assertIs(inspection["completion_current"], True, inspection)
            view = inspection["view"]
            self.assertEqual("TASK_COMPLETE", view["status"], view)
            proof = view["evidence"]["regression_proof"]
            self.assertEqual("PASS", proof["verdict"], proof)
            self.assertEqual([], proof["failures"], proof)
            self.assertEqual([], proof["unverified"], proof)
            self.assertEqual(settings["test-command"], proof["commands"]["suite"], proof)
            self.assertEqual("derived:junit", proof["commands"]["regression_source"], proof)
            self.assertEqual(settings["test-command"], proof["commands"]["regression"], proof)
            expected = {
                "C1": ["example.PageCountTest::test_c1_partial_page_is_counted"],
                "C2": ["example.PageCountTest::test_c2_exact_multiple_remains_two_pages"],
            }
            self.assertEqual(expected, proof["case_tests"], proof)
            self.assertIn(expected["C1"][0], proof["fail_to_pass"], proof)
            self.assertEqual("current", view["verification"]["freshness"], view["verification"])

    def evaluate(self, variant):
        with tempfile.TemporaryDirectory(prefix="junit-java-oracle-") as tmp:
            project = materialize(self.scenario.seed, Path(tmp) / "project", self.scenario.dir / variant)
            return {row.name: row for row in self.scenario.oracle()(project, self.scenario)}

    def test_real_reference_passes_guard_regression_and_hidden_behavior(self):
        checks = self.evaluate("reference")
        self.assertTrue(
            all(row.ok for row in checks.values()), [(row.name, row.detail) for row in checks.values() if not row.ok]
        )

    def test_wrong_rounding_is_rejected_by_real_junit_and_hidden_behavior(self):
        checks = self.evaluate("broken/floor-rounding")
        self.assertFalse(checks["real_junit_suite_passes"].ok)
        self.assertFalse(checks["hidden_java_behavior"].ok)

    def test_a_green_regression_that_also_passes_on_the_base_is_rejected(self):
        checks = self.evaluate("broken/vacuous-regression")
        self.assertTrue(checks["real_junit_suite_passes"].ok)
        self.assertTrue(checks["hidden_java_behavior"].ok)
        self.assertFalse(checks["regression_fails_on_original_product"].ok)
        self.assertTrue(checks["existing_guard_passes_on_original"].ok)


if __name__ == "__main__":
    unittest.main()
