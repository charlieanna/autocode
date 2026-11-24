"""The setup sweep (tools/setup_sweep.py): AutoCode's own setup steps on pinned repositories, no model."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

import setup_sweep

ROOT = Path(__file__).resolve().parents[1]
EXPECT = {"runner": "unittest", "base": "passing", "targeted": True, "mutation": True, "coverage": None}


def entry(**changes):
    return {
        "id": "demo",
        "language": "python",
        "git": "https://example.invalid/demo.git",
        "commit": "0" * 40,
        "prepare": [],
        "expect": dict(EXPECT),
        **changes,
    }


def git(cwd, *args):
    environment = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t"}
    environment["GIT_COMMITTER_EMAIL"] = "t@t"
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=environment)


class Entries(unittest.TestCase):
    def test_the_pinned_list_is_well_formed_and_pins_full_commits(self):
        entries = setup_sweep.load()
        self.assertGreaterEqual(len(entries), 45)
        self.assertGreaterEqual(len({e["language"] for e in entries}), 8)
        for item in entries:
            self.assertRegex(item["commit"], r"^[0-9a-f]{40}$")
            self.assertTrue(item["git"].startswith("https://github.com/"), item["id"])
            expect = item["expect"]
            if not (expect["base"] == "passing" and expect["targeted"] and expect["coverage"]):
                self.assertTrue(item.get("note"), f"{item['id']} expects less than every step and says not why")

    def test_the_nightly_job_sweeps_every_language_and_reports_on_its_issue(self):
        workflow = (ROOT / ".github/workflows/setup-sweep.yml").read_text()
        groups = re.findall(r'languages: "([^"]+)"', workflow)
        swept = [language for group in groups for language in group.split()]
        self.assertEqual(len(swept), len(set(swept)), "a language in two groups is swept twice")
        self.assertLessEqual({e["language"] for e in setup_sweep.load()}, set(swept))
        self.assertIn("ci_nightly_issue.py --kind setup-sweep", workflow)

    def test_an_entry_without_every_expectation_is_refused(self):
        expect = dict(EXPECT)
        del expect["coverage"]
        with self.assertRaisesRegex(setup_sweep.EntryError, "expect needs exactly"):
            setup_sweep.validate(entry(expect=expect))

    def test_a_moving_reference_is_not_a_pin(self):
        with self.assertRaisesRegex(setup_sweep.EntryError, "full 40-character"):
            setup_sweep.validate(entry(commit="main"))

    def test_an_unknown_health_is_refused(self):
        with self.assertRaisesRegex(setup_sweep.EntryError, "expect.base"):
            setup_sweep.validate(entry(expect={**EXPECT, "base": "green"}))

    def test_no_base_health_only_without_a_runner(self):
        setup_sweep.validate(entry(expect={**EXPECT, "runner": None, "base": None}))
        with self.assertRaisesRegex(setup_sweep.EntryError, "expect.base"):
            setup_sweep.validate(entry(expect={**EXPECT, "base": None}))

    def test_selection_by_id_language_and_shard(self):
        items = [entry(id=f"r{n}", language="go" if n % 2 else "python") for n in range(6)]
        self.assertEqual([e["id"] for e in setup_sweep.select(items, only=["r3"])], ["r3"])
        self.assertEqual([e["id"] for e in setup_sweep.select(items, languages=["go"])], ["r1", "r3", "r5"])
        self.assertEqual([e["id"] for e in setup_sweep.select(items, shard=(2, 3))], ["r1", "r4"])
        with self.assertRaisesRegex(setup_sweep.EntryError, "no entry named nope"):
            setup_sweep.select(items, only=["nope"])


class Judging(unittest.TestCase):
    def test_every_difference_from_the_expectation_is_reported(self):
        seen = {"runner": "pytest", "base": "broken", "targeted": True, "mutation": True, "coverage": None}
        self.assertEqual(
            setup_sweep.differences(entry(), seen),
            ["runner: expected 'unittest', saw 'pytest'", "base: expected 'passing', saw 'broken'"],
        )

    def test_a_repository_that_could_not_be_prepared_differs(self):
        self.assertEqual(
            setup_sweep.differences(entry(), {"error": "prepare: npm ci: exit 1"}), ["prepare: npm ci: exit 1"]
        )

    def test_two_tests_from_the_ends_of_the_suite_are_targeted(self):
        self.assertEqual(setup_sweep.pick_tests(["b.t2", "a.t1", "c.t3"]), ["a.t1", "c.t3"])
        self.assertEqual(setup_sweep.pick_tests(["a.t1"]), ["a.t1"])
        self.assertEqual(setup_sweep.pick_tests([]), [])


class Observe(unittest.TestCase):
    """One pinned repository, served from a local Git repository, through every step."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.folder)])
        source = self.folder / "upstream"
        (source / "tests").mkdir(parents=True)
        (source / "pager.py").write_text("def pages(items, size):\n    return -(-len(items) // size)\n")
        (source / "tests" / "__init__.py").write_text("")
        (source / "tests" / "test_pager.py").write_text(
            textwrap.dedent(
                """\
                import unittest

                import pager


                class Pages(unittest.TestCase):
                    def test_empty(self):
                        self.assertEqual(pager.pages([], 2), 0)

                    def test_partial(self):
                        self.assertEqual(pager.pages([1, 2, 3], 2), 2)

                    def test_whole(self):
                        self.assertEqual(pager.pages([1, 2], 2), 1)
                """
            )
        )
        git(source, "init", "-q")
        git(source, "config", "uploadpack.allowAnySHA1InWant", "true")
        git(source, "add", ".")
        git(source, "commit", "-qm", "seed")
        self.commit = git(source, "rev-parse", "HEAD").stdout.strip()
        self.url = source.as_uri()

    def test_a_unittest_project_is_detected_run_narrowed_and_judged(self):
        item = entry(git=self.url, commit=self.commit)
        seen = setup_sweep.observe(item, self.folder / "out")
        self.assertEqual(seen.get("error"), None, seen)
        self.assertEqual((seen["runner"], seen["base"]), ("unittest", "passing"), seen["notes"])
        self.assertEqual(seen["tests"]["passed"], 3)
        self.assertEqual(len(seen["targeted_tests"]), 2)
        self.assertTrue(seen["targeted"], seen["notes"])
        self.assertTrue(seen["mutation"])
        self.assertEqual(setup_sweep.differences(item, {**seen, "coverage": None}), [])

    def test_a_suite_that_names_no_test_is_measured_through_its_own_command_after_it_ran(self):
        # make reports only an exit code, so nothing is narrowed and the proof measures the suite's own
        # command. A native tool (JaCoCo, gcov) reads what that command's run left, so it runs first.
        source = self.folder / "make"
        source.mkdir()
        (source / "Makefile").write_text("test:\n\ttouch ran-by-suite\n")
        git(source, "init", "-q")
        git(source, "config", "uploadpack.allowAnySHA1InWant", "true")
        git(source, "add", ".")
        git(source, "commit", "-qm", "seed")
        commit = git(source, "rev-parse", "HEAD").stdout.strip()
        asked = []

        def prepare(framework, command, tree, evidence):
            asked.append((command, (Path(tree) / "ran-by-suite").is_file()))
            return None

        with unittest.mock.patch.object(setup_sweep.diff_coverage, "prepare", prepare):
            seen = setup_sweep.observe(entry(git=source.as_uri(), commit=commit), self.folder / "out")
        self.assertEqual((seen["runner"], seen["base"], seen["targeted"]), ("make", "passing", False), seen)
        self.assertEqual([(seen["command"], True)], asked)

    def test_a_failing_prepare_step_is_the_repository_s_error(self):
        item = entry(git=self.url, commit=self.commit, prepare=["exit 3"])
        seen = setup_sweep.observe(item, self.folder / "out")
        self.assertIn("prepare: exit 3: exit 3", seen["error"])

    def test_a_pin_the_source_does_not_have_is_a_fetch_error(self):
        seen = setup_sweep.observe(entry(git=self.url, commit="1" * 40), self.folder / "out")
        self.assertTrue(seen["error"].startswith("fetch:"), seen)

    def test_the_command_line_writes_results_and_fails_on_a_difference(self):
        repos = self.folder / "repos.json"
        item = entry(git=self.url, commit=self.commit, expect={**EXPECT, "runner": "pytest"})
        repos.write_text(json.dumps({"repositories": [item]}))
        out = self.folder / "cli"
        code = setup_sweep.main(["run", "--repos", str(repos), "--out", str(out)])
        self.assertEqual(code, 1)
        results = json.loads((out / "results.json").read_text())
        self.assertEqual(results[0]["problems"], ["runner: expected 'pytest', saw 'unittest'"])
        self.assertIn("| demo | python | unittest | passing | 3 |", (out / "summary.md").read_text())
        self.assertFalse((out / "demo" / "checkout").exists())


if __name__ == "__main__":
    unittest.main()
