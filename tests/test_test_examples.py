"""The Builder sees the project's own tests closest to its task (autocode_test_examples)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_test_examples as examples


def repo(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="test-examples-"))
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


class ChooseTests(unittest.TestCase):
    TESTS = [
        "tests/test_weeks.py",
        "tests/test_report.py",
        "timesheet/test_util.py",
        "web/app.test.ts",
        "go/pkg/weeks_test.go",
    ]

    def test_recognizes_test_files_across_languages(self):
        for path in self.TESTS + ["spec/user_spec.rb", "src/a.spec.js"]:
            self.assertTrue(examples.is_test_file(path), path)
        for path in ("timesheet/weeks.py", "docs/testing.md", "tests/__init__.py", "tests/conftest.py"):
            self.assertFalse(examples.is_test_file(path), path)

    def test_the_tasks_own_tests_come_first_then_tests_named_after_its_sources(self):
        self.assertEqual(
            ["tests/test_report.py", "tests/test_weeks.py"],
            examples.choose(["timesheet/weeks.py", "tests/test_report.py"], self.TESTS),
        )
        self.assertEqual(
            ["tests/test_weeks.py", "go/pkg/weeks_test.go"], examples.choose(["timesheet/weeks.py"], self.TESTS)
        )
        # A test directory in affected_paths counts as the task's own tests.
        self.assertEqual(["tests/test_report.py", "tests/test_weeks.py"], examples.choose(["tests/"], self.TESTS))

    def test_same_directory_tests_are_next(self):
        self.assertEqual(["timesheet/test_util.py"], examples.choose(["timesheet/export.py"], self.TESTS))

    def test_a_task_in_a_new_area_still_sees_one_test(self):
        self.assertEqual(["tests/test_report.py"], examples.choose(["cli/main.py"], self.TESTS))
        self.assertEqual(["web/app.test.ts"], examples.choose(["web/new/page.ts"], self.TESTS))
        self.assertEqual([], examples.choose(["cli/main.py"], []))


class SectionTests(unittest.TestCase):
    def test_the_section_shows_the_opening_lines_of_the_closest_tests(self):
        body = (
            "import unittest\nfrom timesheet.weeks import iso_week\n\n\nclass WeekTests(unittest.TestCase):\n"
            + "".join(f"    def test_{n}(self):\n        self.assertEqual({n}, {n})\n" for n in range(60))
        )
        root = repo({"timesheet/weeks.py": "x = 1\n", "tests/test_weeks.py": body, "tests/test_other.py": "import x\n"})
        text = examples.section(root, {"affected_paths": ["timesheet/weeks.py"]})
        self.assertIn("EXISTING TEST STYLE", text)
        self.assertIn("--- tests/test_weeks.py (lines 1-60 of 125) ---", text)
        self.assertIn("from timesheet.weeks import iso_week", text)
        self.assertNotIn("def test_59", text)
        self.assertNotIn("test_other.py", text)

    def test_untracked_files_and_projects_without_tests_add_nothing(self):
        root = repo({"app.py": "x = 1\n"})
        (root / "test_scratch.py").write_text("import secrets\n")
        self.assertEqual("", examples.section(root, {"affected_paths": ["app.py"]}))
        self.assertEqual("", examples.section(Path(tempfile.mkdtemp()), {"affected_paths": ["app.py"]}))

    def test_the_section_goes_before_the_handoff_data(self):
        root = repo({"app.py": "x = 1\n", "test_app.py": "import app\n"})
        prompt = examples.add_to_prompt("Build it.\nCURRENT HANDOFF DATA\n{}", root, {"affected_paths": ["app.py"]})
        before, after = prompt.split("\nCURRENT HANDOFF DATA\n")
        self.assertIn("--- test_app.py", before)
        self.assertEqual("{}", after)
        self.assertEqual(
            "x\nCURRENT HANDOFF DATA\n{}",
            examples.add_to_prompt("x\nCURRENT HANDOFF DATA\n{}", root, {"affected_paths": ["none.md"]}).replace(
                examples.section(root, {"affected_paths": ["none.md"]}), ""
            ),
        )


class BuilderRequestTests(unittest.TestCase):
    def test_the_builders_request_carries_the_examples_and_the_validators_does_not(self):
        from units import common

        from tests.test_bug_job import approved_small_fix

        state = approved_small_fix()
        root = Path(state["workspace"])
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "test_client.py").write_text("import unittest  # the house style\n")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        schemas = Path(examples.__file__).with_name("autocode-schemas")
        builder = common.execution_request(state, "terra", root / "state.json", schemas)
        self.assertIn("import unittest  # the house style", builder.prompt.split("\nCURRENT HANDOFF DATA\n")[0])
        self.assertEqual((len(builder.prompt.encode()) + 3) // 4, builder.metrics["estimated_prompt_tokens"])
        validator = common.execution_request(state, "sol", root / "state.json", schemas)
        self.assertNotIn("EXISTING TEST STYLE", validator.prompt)


if __name__ == "__main__":
    unittest.main()
