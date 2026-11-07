"""Navigation preserves exact task inputs without widening filesystem access."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_review_job as review
from units import autoplanner as planner
from units import autoreview


class WorkspaceInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def write(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# source is not read or executed\n")
        return path

    def test_exact_diff_and_unittest_sources_outrank_generic_report_words(self):
        for n in range(50):
            self.write(f"archive/review/changes/report/tests/findings{n}.json")
        expected = ["review-change.diff", "tests/test_actual.py", "tools/guard.py"]
        for name in expected:
            self.write(name)
        task = (
            "Review changes in review-change.diff and report findings. Run tests with "
            "python -m unittest tests.test_actual.Case.test_guard -q; inspect tools/guard.py:12."
        )
        result = planner.workspace_inventory(self.root, task, limit=3)
        self.assertEqual(expected, result["files"])
        self.assertTrue(result["truncated"])

    def test_explicit_sources_survive_fallback_scan_limit(self):
        for n in range(10):
            self.write(f"aaa/{n}.txt")
        self.write("zzz/target.py")
        result = planner.workspace_inventory(self.root, "Fix zzz/target.py", limit=2, scan_limit=2)
        self.assertEqual("zzz/target.py", result["files"][0])
        self.assertLessEqual(len(result["files"]), 2)
        self.assertTrue(result["truncated"])

    def test_real_reviewer_handoff_retains_task_and_explicit_inputs(self):
        for n in range(45):
            self.write(f"archive/review/report/findings{n}.json")
        self.write("review-change.diff")
        self.write("tests/test_actual.py")
        task = "Review review-change.diff; report findings. Run tests.test_actual.Case.test_guard."
        state = {"task": task, "workspace": str(self.root), "settings": {"engine": "codex"}}
        request = autoreview.job_request(state, review, "sol", "sol")
        packet = json.loads(request.prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(task, packet["task"])
        self.assertEqual(["review-change.diff", "tests/test_actual.py"], packet["workspace_inventory"]["files"][:2])
        self.assertEqual(review.SCHEMA, request.schema)
        self.assertIn("Prove every blocking finding", request.prompt)

    def test_explicit_input_cannot_bypass_exclusions_or_symlink_policy(self):
        for name in (".autocode/private.py", ".git/config", "node_modules/x.py", "cache.pyc"):
            self.write(name)
        self.write("safe.py")
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "secret.py"
            external.write_text("secret")
            (self.root / "link.py").symlink_to(external)
            (self.root / "linked").symlink_to(outside, target_is_directory=True)
            task = (
                f"{external} ../secret.py .autocode/private.py .git/config node_modules/x.py "
                "cache.pyc link.py linked/secret.py https://example.test/safe.py "
                "and safe.py"
            )
            result = planner.workspace_inventory(self.root, task)
        self.assertEqual(["safe.py"], result["files"])

    def test_named_files_are_hints_not_file_contents_or_imports(self):
        self.write("tests/test_actual.py")
        self.write("docs/a note.md")
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("must not read source")):
            result = planner.workspace_inventory(
                self.root, "`docs/a note.md` and tests.test_actual.Case.test_guard and tests.test_missing"
            )
        self.assertEqual(["docs/a note.md", "tests/test_actual.py"], result["files"])
        self.assertIn("not evidence", result["instruction"])

    def test_deterministic_fallback_and_deduplication(self):
        for name in ("z.py", "a.py", "dashboard/view.html"):
            self.write(name)
        self.assertEqual(
            ["dashboard/view.html", "a.py", "z.py"],
            planner.workspace_inventory(self.root, "dashboard")["files"],
        )
        self.assertEqual(
            ["z.py", "a.py", "dashboard/view.html"],
            planner.workspace_inventory(self.root, "z.py z.py missing.py", limit=3)["files"],
        )
