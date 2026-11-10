"""The Builder sees the project's own tests closest to its task (autocode_test_examples)."""

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class BuilderPromptTests(unittest.TestCase):
    """The Builder's stage prompt: static notes first, then the attempt-specific section (A5, #2113)."""

    def setUp(self):
        self.root = repo({"app.py": "x = 1\n", "test_app.py": "import app\n"})

    def test_the_section_lands_after_the_static_notes_before_the_handoff(self):
        prompt = examples.builder_prompt(
            "P\nCURRENT HANDOFF DATA\n{}", self.root, {"affected_paths": ["app.py"]}, notes="BUILD OUTPUT: scratch\n"
        )
        prefix, after = prompt.split("\nCURRENT HANDOFF DATA\n")
        self.assertEqual("{}", after)
        notes = prefix.find("BUILD OUTPUT: scratch")
        self.assertLess(notes, prefix.find("EXISTING TEST STYLE"))
        self.assertIn("--- test_app.py", prefix)

    def test_without_tests_the_notes_still_precede_the_handoff(self):
        prompt = examples.builder_prompt(
            "P\nCURRENT HANDOFF DATA\n{}", Path(tempfile.mkdtemp()), {"affected_paths": ["app.py"]}, notes="NOTES\n"
        )
        prefix, after = prompt.split("\nCURRENT HANDOFF DATA\n")
        self.assertEqual("{}", after)
        self.assertIn("NOTES\n", prefix)
        self.assertNotIn("EXISTING TEST STYLE", prompt)

    def test_an_exact_complete_attached_test_replaces_only_its_duplicate_excerpt(self):
        body = "import app\r\n" + "# preserve the project's existing test conventions\r\n" * 35
        (self.root / "test_app.py").write_bytes(body.encode())
        data = {
            "stage": "terra",
            "review_source": {
                "files": [{"path": "test_app.py", "sha256": hashlib.sha256(body.encode()).hexdigest(), "content": body}]
            },
        }
        handoff = json.dumps(data)
        prompt = examples.builder_prompt(
            "P\nCURRENT HANDOFF DATA\n" + handoff,
            self.root,
            {"affected_paths": ["app.py"]},
            notes="NOTES\n",
        )
        prefix, after = prompt.split("\nCURRENT HANDOFF DATA\n")
        self.assertEqual(handoff, after, "the authoritative handoff bytes must remain untouched")
        self.assertIn(examples.HEADER, prefix)
        self.assertIn("review_source.files[0].content", prefix)
        self.assertNotIn("# preserve the project's existing test conventions", prefix)
        self.assertLess(
            len(prompt),
            len(
                "P\nNOTES\n"
                + examples.section(self.root, {"affected_paths": ["app.py"]})
                + "\nCURRENT HANDOFF DATA\n"
                + handoff
            ),
        )

    def test_unmatched_or_ineligible_attached_source_keeps_the_exact_excerpt(self):
        body = "import app\n" + "# existing conventions\n" * 35
        (self.root / "test_app.py").write_text(body)
        original = {
            "stage": "terra",
            "review_source": {
                "files": [{"path": "test_app.py", "sha256": hashlib.sha256(body.encode()).hexdigest(), "content": body}]
            },
        }
        for change in ("absent", "stage", "path", "escape", "changed", "truncated", "hash", "content_only"):
            with self.subTest(change=change):
                data = copy.deepcopy(original)
                row = data["review_source"]["files"][0]
                if change == "absent":
                    data.pop("review_source")
                elif change == "stage":
                    data["stage"] = "sol"
                elif change in ("path", "escape"):
                    row["path"] = "other_test.py" if change == "path" else "../test_app.py"
                elif change in ("changed", "truncated", "content_only"):
                    row["content"] = body.replace("import app", "import other") if change == "changed" else body[:20]
                    if change != "content_only":
                        row["sha256"] = hashlib.sha256(row["content"].encode()).hexdigest()
                else:
                    row["sha256"] = "0" * 64
                handoff = json.dumps(data)
                prompt = examples.add_to_prompt(
                    "P\nCURRENT HANDOFF DATA\n" + handoff, self.root, {"affected_paths": ["app.py"]}
                )
                prefix, after = prompt.split("\nCURRENT HANDOFF DATA\n")
                self.assertEqual(handoff, after)
                self.assertEqual("P" + examples.section(self.root, {"affected_paths": ["app.py"]}), prefix)

    def test_short_or_lossy_examples_keep_the_original_excerpt(self):
        for content in (b"import app\n", b"# existing conventions\n" * 35 + b"\xff"):
            with self.subTest(content_bytes=len(content)):
                (self.root / "test_app.py").write_bytes(content)
                data = {
                    "stage": "terra",
                    "review_source": {
                        "files": [
                            {
                                "path": "test_app.py",
                                "sha256": hashlib.sha256(content).hexdigest(),
                                "content": content.decode(errors="replace"),
                            }
                        ]
                    },
                }
                original = "P\nCURRENT HANDOFF DATA\n" + json.dumps(data)
                rendered = examples.add_to_prompt(original, self.root, {"affected_paths": ["app.py"]})
                self.assertEqual(
                    original.replace(
                        "\nCURRENT HANDOFF DATA\n",
                        examples.section(self.root, {"affected_paths": ["app.py"]}) + "\nCURRENT HANDOFF DATA\n",
                    ),
                    rendered,
                )


class BuilderRequestTests(unittest.TestCase):
    def test_a_small_clear_bugfix_reuses_its_complete_test_source_without_changing_the_handoff(self):
        from units import common

        from tests.saved_diagnosis import approved_fix

        state = approved_fix()
        state["workflow"].update(clarity="clear", brief_size="small")
        root = Path(state["workspace"])
        (root / "tests").mkdir(exist_ok=True)
        body = "import unittest  # the house style\n" + "# existing test conventions\n" * 35
        (root / "tests/test_client.py").write_text(body)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        schemas = Path(examples.__file__).with_name("autocode-schemas")
        with patch.object(examples, "add_to_prompt", side_effect=lambda prompt, *_: prompt):
            canonical = common.execution_request(state, "terra", root / "state.json", schemas)
        builder = common.execution_request(state, "terra", root / "state.json", schemas)
        prefix, handoff = builder.prompt.split("\nCURRENT HANDOFF DATA\n")
        self.assertEqual(canonical.prompt.split("\nCURRENT HANDOFF DATA\n")[1], handoff)
        data = json.loads(handoff)
        row = next(row for row in data["review_source"]["files"] if row["path"] == "tests/test_client.py")
        self.assertEqual(body, row["content"])
        self.assertIn("independently execute all required checks", data["review_source"]["instruction"])
        self.assertIn("review_source.files[", prefix)
        self.assertNotIn("# existing test conventions", prefix)
        self.assertIn(examples.HEADER, prefix)
        self.assertEqual((len(builder.prompt.encode()) + 3) // 4, builder.metrics["estimated_prompt_tokens"])

    def test_the_builders_request_carries_the_examples_and_the_validators_does_not(self):
        from units import common

        from tests.saved_diagnosis import approved_fix

        state = approved_fix()
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

    def test_the_examples_follow_the_static_notes_in_the_builders_request(self):
        from units import common

        from tests.saved_diagnosis import approved_fix

        state = approved_fix()
        root = Path(state["workspace"])
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "test_client.py").write_text("import unittest\n")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        schemas = Path(examples.__file__).with_name("autocode-schemas")
        builder = common.execution_request(state, "terra", root / "state.json", schemas)
        prefix = builder.prompt.split("\nCURRENT HANDOFF DATA\n")[0]
        self.assertLess(
            prefix.find("BUILD OUTPUT: do not leave compiled programs"),
            prefix.find("EXISTING TEST STYLE"),
            "the attempt-specific excerpt must come after the static Builder notes (A5, #2113)",
        )
        self.assertIn("EXISTING TEST STYLE", prefix)


if __name__ == "__main__":
    unittest.main()
