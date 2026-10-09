"""Offline qualification of T02/T03 oracles; no providers or git operations."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import live_scenarios as scenarios

BUGFIX_REFERENCE = scenarios.BUGFIX_SOURCE.replace(
    '    print(f"Hello, {args[0]}!")',
    '    if not args[0].strip():\n'
    '        print("error: name must not be blank", file=sys.stderr)\n'
    '        return 2\n'
    '    print(f"Hello, {args[0]}!")',
)
FEATURE_REFERENCE = scenarios.NOTES_SOURCE.replace(
    '    if not (args == ["list"] or',
    '    filtering = len(args) == 3 and args[:2] == ["list", "--tag"] and bool(args[2].strip())\n'
    '    if not (filtering or args == ["list"] or',
).replace(
    '        for n in notes:\n',
    '        for n in notes:\n'
    '            if filtering and n["tag"].casefold() != args[2].casefold():\n'
    '                continue\n',
)

BUGFIX_REGRESSION = '''\
import subprocess
import sys
p = subprocess.run([sys.executable, "-I", "-B", "greet.py", " "], capture_output=True, text=True, timeout=2)
assert (p.returncode, p.stdout, p.stderr) == (2, "", "error: name must not be blank\\n")
'''
FEATURE_REGRESSION = '''\
import subprocess
import sys
p = subprocess.run([sys.executable, "-I", "-B", "notes.py", "list", "--tag", "wOrK"], capture_output=True, text=True, timeout=2)
assert (p.returncode, p.stdout, p.stderr) == (0, "2: Ship draft [Work]\\n9: Review draft [WORK]\\n", "")
'''


class CampaignOracles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="campaign-control-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def artifact(self, ident, reference=True):
        project = self.root / ident
        project.mkdir(exist_ok=True)
        spec = scenarios.scenario(ident)
        for rel, content in spec["seed"].items():
            (project / rel).write_text(content, encoding="utf-8")
        source, regression = ((BUGFIX_REFERENCE, BUGFIX_REGRESSION) if ident == "BUGFIX-01"
                              else (FEATURE_REFERENCE, FEATURE_REGRESSION))
        if reference:
            (project / spec["allowed_paths"][0]).write_text(source, encoding="utf-8")
        (project / "test_regression.py").write_text(regression)
        return project, spec

    def score(self, project, spec, expected):
        before = {p.name: (p.read_bytes(), p.stat().st_mode) for p in project.iterdir() if p.is_file()}
        result = spec["oracle"](project)
        self.assertEqual(result.status, expected, result.summary + repr(result.failed))
        self.assertTrue(result.checks)
        json.dumps(result.checks)  # Evidence must be serializable, including failures.
        after = {p.name: (p.read_bytes(), p.stat().st_mode) for p in project.iterdir() if p.is_file()}
        self.assertEqual(before, after, "oracle mutated candidate workspace")
        return result

    def test_registry_and_seed_contract(self):
        self.assertTrue({"LIVE-01", "LIVE-02", "LIVE-05", "LIVE-06", "LIVE-07"} <= scenarios.SCENARIOS.keys())
        for ident, label in (("BUGFIX-01", "T02"), ("FEATURE-01", "T03")):
            spec = scenarios.scenario(ident)
            self.assertEqual(spec["plan_id"], label)
            self.assertTrue(spec["synthetic_seed_only"])
            self.assertNotIn("seed_from", spec)
            self.assertEqual(set(spec["protected_paths"]), set(spec["seed"]) - set(spec["allowed_paths"]))

    def test_positive_references_are_read_only_and_repeatable(self):
        for ident in ("BUGFIX-01", "FEATURE-01"):
            with self.subTest(ident=ident):
                project, spec = self.artifact(ident)
                first = self.score(project, spec, scenarios.PASS)
                second = self.score(project, spec, scenarios.PASS)
                self.assertEqual(first.checks, second.checks)

    def test_seed_original_tests_pass_but_new_regression_fails(self):
        for ident in ("BUGFIX-01", "FEATURE-01"):
            with self.subTest(ident=ident):
                project, spec = self.artifact(ident, reference=False)
                original = scenarios._campaign_process(project, "test_original.py")
                self.assertEqual(original, {"exit": 0, "stdout": "original tests: OK\n", "stderr": ""})
                regression = scenarios._campaign_process(project, "test_regression.py")
                self.assertNotEqual(regression["exit"], 0)
                result = self.score(project, spec, scenarios.FAIL)
                self.assertTrue(any(r["name"].startswith(("blank.", "filter.")) for r in result.failed))

    def test_noop_with_green_candidate_tests_still_fails(self):
        for ident in ("BUGFIX-01", "FEATURE-01"):
            with self.subTest(ident=ident):
                project, spec = self.artifact(ident, reference=False)
                (project / "test_regression.py").write_text("raise SystemExit(0)\n")
                result = self.score(project, spec, scenarios.FAIL)
                self.assertTrue(next(r["ok"] for r in result.checks if r["name"] == "candidate_regressions.exit"))

    def test_wrong_blank_edges_and_changed_valid_behavior_fail(self):
        for source in (BUGFIX_REFERENCE.replace("not args[0].strip()", 'args[0] == ""'),
                       BUGFIX_REFERENCE.replace("{args[0]}", "{args[0].strip()}"),
                       BUGFIX_REFERENCE.replace('return 2\n    print', 'return 0\n    print'),
                       BUGFIX_REFERENCE.replace('blank", file=sys.stderr', 'blank"')):
            with self.subTest(source=source):
                project, spec = self.artifact("BUGFIX-01")
                (project / "greet.py").write_text(source)
                self.score(project, spec, scenarios.FAIL)

    def test_wrong_filter_edges_fail(self):
        for source in (FEATURE_REFERENCE.replace('n["tag"].casefold() != args[2].casefold()', 'n["tag"] != args[2]'),
                       FEATURE_REFERENCE.replace('n["tag"].casefold() != args[2].casefold()', 'args[2].casefold() not in n["tag"].casefold()'),
                       FEATURE_REFERENCE.replace('.casefold()', '.lower()'),
                       FEATURE_REFERENCE.replace('args[2].casefold()', 'args[2].strip().casefold()'),
                       FEATURE_REFERENCE.replace(' and bool(args[2].strip())', '')):
            with self.subTest(source=source):
                project, spec = self.artifact("FEATURE-01")
                (project / "notes.py").write_text(source)
                self.score(project, spec, scenarios.FAIL)

    def test_weakened_original_tests_rejected_even_on_correct_delivery(self):
        for ident in ("BUGFIX-01", "FEATURE-01"):
            project, spec = self.artifact(ident)
            (project / "test_original.py").write_text('print("original tests: OK")\n')
            result = self.score(project, spec, scenarios.FAIL)
            self.assertIn("protected.test_original.py", [r["name"] for r in result.failed])

    def test_green_noop_regression_rejected_even_on_correct_delivery(self):
        for ident in ("BUGFIX-01", "FEATURE-01"):
            project, spec = self.artifact(ident)
            (project / "test_regression.py").write_text("raise SystemExit(0)\n")
            result = self.score(project, spec, scenarios.FAIL)
            self.assertEqual([r["name"] for r in result.failed], ["seed_regressions.detect_defect"])

    def test_modified_protected_files_and_scope_fail(self):
        for ident in ("BUGFIX-01", "FEATURE-01"):
            for rel in scenarios.scenario(ident)["protected_paths"]:
                with self.subTest(ident=ident, rel=rel):
                    project, spec = self.artifact(ident)
                    (project / rel).write_text("changed\n")
                    result = self.score(project, spec, scenarios.FAIL)
                    self.assertIn(f"protected.{rel}", [r["name"] for r in result.failed])
            project, spec = self.artifact(ident)
            (project / "unrelated.py").write_text("# scope expansion\n")
            self.score(project, spec, scenarios.FAIL)

    def test_filenames_or_expected_substrings_cannot_pass(self):
        for ident in ("BUGFIX-01", "FEATURE-01"):
            project, spec = self.artifact(ident)
            (project / spec["allowed_paths"][0]).write_text(
                'print("Hello, Ada! error: name must not be blank 2: Ship draft [Work]")\n')
            (project / "test_regression.py").write_text("# forged green\n")
            self.score(project, spec, scenarios.FAIL)

    def test_protected_mode_symlinks_and_dependencies_rejected(self):
        project, spec = self.artifact("BUGFIX-01")
        (project / "settings.json").chmod(0o755)
        self.score(project, spec, scenarios.FAIL)
        (project / "settings.json").chmod(0o644)
        (project / "link").symlink_to(project / "settings.json")
        result = self.score(project, spec, scenarios.FAIL)
        self.assertIn("scope.regular_files", [r["name"] for r in result.failed])
        (project / "link").unlink()
        (project / "greet.py").write_text("import requests\n" + BUGFIX_REFERENCE)
        result = self.score(project, spec, scenarios.FAIL)
        self.assertIn("scope.stdlib_imports", [r["name"] for r in result.failed])

    def test_timeouts_and_launch_errors_are_structured(self):
        with patch.object(scenarios.subprocess, "run", side_effect=subprocess.TimeoutExpired("python", 3)) as run:
            result = scenarios._campaign_process(self.root, "greet.py")
            self.assertEqual(result, {"error": "TIMEOUT", "timeout_seconds": 3})
            self.assertEqual(run.call_args.kwargs["timeout"], 3)
            self.assertIn("-I", run.call_args.args[0])
        with patch.object(scenarios.subprocess, "run", side_effect=OSError("unavailable")):
            self.assertEqual(scenarios._campaign_process(self.root, "greet.py")["error"], "OSError")
        project, spec = self.artifact("BUGFIX-01")
        with patch.object(scenarios, "_campaign_process", return_value={"error": "TIMEOUT"}):
            self.score(project, spec, scenarios.FAIL)

    def test_runtime_data_rewrites_and_corruption_fail(self):
        for insertion in (
            '    store.write_text(json.dumps(notes))\n',
            '    notes = []\n',
        ):
            project, spec = self.artifact("FEATURE-01")
            (project / "notes.py").write_text(FEATURE_REFERENCE.replace('    if args[0] == "add":', insertion + '    if args[0] == "add":'))
            self.score(project, spec, scenarios.FAIL)

    def test_missing_artifact_is_structured_failure(self):
        for ident in ("BUGFIX-01", "FEATURE-01"):
            project, spec = self.artifact(ident)
            (project / spec["allowed_paths"][0]).unlink()
            self.score(project, spec, scenarios.FAIL)


if __name__ == "__main__":
    unittest.main()
