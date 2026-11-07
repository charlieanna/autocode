"""The memory qualification tests its CLI and justifies only its exact scripted lines."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scenarios.harness import catalog
from scenarios.harness import project_memory_provider as provider
from scenarios.harness.driver import fake_setup


class ProjectMemoryFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="memory-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.scenario = catalog.load("feature-timesheet-by-project")
        self.reference = provider.prepare_fixture(self.root, self.scenario.reference)
        self.workspace = self.root / "project"
        shutil.copytree(self.scenario.seed, self.workspace)
        shutil.copytree(self.reference, self.workspace, dirs_exist_ok=True)
        self.producer = {
            "reference": str(self.reference),
            "paths": list(provider.FIXTURE_REASONS),
            "fault": "project_memory",
        }
        self.data = {
            "regression_proof": {
                "path": "regression/current/proof.json",
                "changed_code_evidence": {
                    "obligations": [
                        {
                            "gap_id": "changed-code:" + str(number) * 64,
                            "path": path,
                            "sha256": pin,
                            "line": next(iter(reasons)),
                            "status": "reason_required",
                            "mutation_outcomes": [],
                        }
                        for number, (path, (pin, reasons)) in enumerate(provider.FIXTURE_REASONS.items(), start=1)
                    ]
                },
            }
        }

    def judgments(self, data=None, producer=None):
        with patch.object(provider.Path, "cwd", return_value=self.workspace):
            return provider.fixture_judgments(
                self.data if data is None else data, producer=self.producer if producer is None else producer
            )

    def test_exact_unsupported_scripted_lines_bind_current_gap_and_test_evidence(self):
        found = self.judgments()
        rows = self.data["regression_proof"]["changed_code_evidence"]["obligations"]
        self.assertEqual([row["gap_id"] for row in rows], [row["gap_id"] for row in found])
        for row in found:
            self.assertEqual("accept", row["decision"])
            self.assertEqual("instrumentation_unavailable", row["reason_kind"])
            self.assertIn(provider.CLI_TEST_PATH, row["evidence_refs"])
            self.assertIn(self.data["regression_proof"]["path"], row["evidence_refs"])
            self.assertTrue(row["reason"])

    def test_changed_unknown_or_mutated_lines_and_unscripted_files_get_no_reason(self):
        for changes in (
            {"line": 999},
            {"sha256": "0" * 64},
            {"path": "unowned.py"},
            {"mutation_outcomes": ["survived"]},
            {"mutation_outcomes": ["killed"]},
            {"status": "tested"},
        ):
            with self.subTest(changes=changes):
                data = copy.deepcopy(self.data)
                for row in data["regression_proof"]["changed_code_evidence"]["obligations"]:
                    row.update(changes)
                self.assertEqual([], self.judgments(data))
        for changes in ({"paths": []}, {"reference": ""}, {"fault": "another-task"}):
            with self.subTest(changes=changes):
                self.assertEqual([], self.judgments(producer={**self.producer, **changes}))
        data = copy.deepcopy(self.data)
        data["regression_proof"]["path"] = ""
        self.assertEqual([], self.judgments(data))

    def test_source_or_cli_test_drift_in_workspace_or_reference_gets_no_reason(self):
        for root in (self.workspace, self.reference):
            for path in (provider.CLI_TEST_PATH, *provider.FIXTURE_REASONS):
                with self.subTest(root=root.name, path=path):
                    target = root / path
                    original = target.read_bytes()
                    target.write_bytes(original + b"\n# changed bytes\n")
                    found = self.judgments()
                    self.assertEqual(0 if path == provider.CLI_TEST_PATH else 1, len(found))
                    target.write_bytes(original)
            (root / provider.CLI_TEST_PATH).unlink()
            self.assertEqual([], self.judgments())
            (root / provider.CLI_TEST_PATH).write_text(provider.CLI_TESTS)
            for path in provider.FIXTURE_REASONS:
                target = root / path
                original = target.read_bytes()
                target.unlink()
                self.assertEqual(1, len(self.judgments()))
                target.write_bytes(original)

    def test_task_local_reviewer_composes_with_the_unchanged_shared_provider(self):
        fake_setup(self.scenario, self.root, self.reference)
        provider.install_fixture_reviewer(self.root)
        copied_fault = self.reference.parents[2] / "harness/project_memory_provider.py"
        self.assertEqual(Path(provider.__file__).read_bytes(), copied_fault.read_bytes())
        self.assertTrue((self.reference.parent / "scenario.toml").is_file())
        command = [
            sys.executable,
            "-c",
            "import sys,json; sys.path.insert(0,sys.argv[1]); import fake_changed_code; "
            "print(json.dumps(fake_changed_code.judgments(json.loads(sys.argv[2]), producer=json.loads(sys.argv[3]))))",
            str(self.root / "bin"),
            json.dumps(self.data),
            json.dumps(self.producer),
        ]
        result = subprocess.run(command, cwd=self.workspace, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(self.judgments(), json.loads(result.stdout))

    def test_cli_assertions_fail_on_seed_and_pass_on_the_scripted_candidate(self):
        seed = self.root / "seed"
        shutil.copytree(self.scenario.seed, seed)
        (seed / provider.CLI_TEST_PATH).write_text(provider.CLI_TESTS)
        command = [sys.executable, "-m", "unittest", "tests.test_project_memory_cli", "-v"]
        base = subprocess.run(command, cwd=seed, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(0, base.returncode, base.stdout + base.stderr)
        self.assertIn("unrecognized arguments: --by-project", base.stderr)
        self.assertIn("test_unflagged_cli_keeps_weekly_output", base.stderr)
        candidate = subprocess.run(command, cwd=self.workspace, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, candidate.returncode, candidate.stdout + candidate.stderr)
        self.assertIn("Ran 3 tests", candidate.stderr)
