"""Per-test results from Minitest's built-in verbose output (#3467)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_contract_identity as contracts
import autocode_minitest_tests as minitest_tests
import autocode_named_reporters as reporters
import autocode_plan_test_suite as plan_test_suite
import autocode_regression as regression
import autocode_superseded_tests as superseded_tests
import autocode_test_cases as test_cases
import autocode_util as util
import autocode_verification_commands as verification_commands
import autocode_verify as verify
from autocode_test_framework import Framework

from .test_verify import Project

ruby = unittest.skipUnless(shutil.which("ruby"), "Ruby runs live Minitest verification")

FIXED = (
    "module Pager\n  def self.page_count(total, size)\n    total.zero? ? 0 : (total + size - 1) / size\n  end\nend\n"
)
BROKEN = FIXED.replace("total.zero? ? 0 : (total + size - 1) / size", "total / size")
ONE_PAGE = FIXED.replace("total.zero? ? 0 :", "total.zero? ? 1 :")

CLASS_BASE = (
    'require "minitest/autorun"\nrequire_relative "../lib/pager"\n\nclass PagerTest < Minitest::Test\n'
    "  def test_exact_multiple\n    assert_equal 2, Pager.page_count(10, 5)\n  end\nend\n"
)
CLASS_CAND = CLASS_BASE.replace(
    "end\n",
    "\n  def test_partial_last_page\n    assert_equal 3, Pager.page_count(11, 5)\n  end\nend\n",
)
SPEC_BASE = (
    'require "minitest/autorun"\nrequire_relative "../lib/pager"\n\ndescribe "Pager" do\n'
    '  it "handles empty input" do\n    assert_equal 0, Pager.page_count(0, 5)\n  end\nend\n'
)
SPEC_ONE = SPEC_BASE.replace('it "handles empty input"', 'it "gives empty input one page"').replace(
    "0, Pager", "1, Pager"
)


def _contract(method: str, *, kind: str = "bugfix") -> dict:
    sealed = {
        "task_id": "task-1",
        "revision": 1,
        "body": {
            "task_kind": kind,
            "acceptance_criteria": [{"id": "C1", "criterion": "pages", "verification_method": method}],
        },
    }
    return {**sealed, "hash": util.digest(sealed), "approval_status": "approved"}


class CommandAndParsingTests(unittest.TestCase):
    def test_command_words_and_instrumentation(self):
        self.assertIsNotNone(minitest_tests.command_words("ruby -Ilib -Itest test/pager_test.rb"))
        self.assertIsNotNone(minitest_tests.command_words("bundle exec rake test"))
        for bad in (
            "ruby -e 'puts 1'",
            "ruby test/pager_test.rb -q",
            "rake test TESTOPTS=--pride",
            "ruby test/pager_test.rb; ls",
            'ruby test/pager_test.rb "\'x" ; ls',
            'ruby test/pager_test.rb "$(id)"',
        ):
            self.assertIsNone(minitest_tests.command_words(bad), bad)
            self.assertEqual(bad, minitest_tests.instrument(bad))

        self.assertEqual(
            "ruby -Ilib -Itest test/pager_test.rb -v",
            minitest_tests.instrument("ruby -Ilib -Itest test/pager_test.rb"),
        )
        self.assertEqual(
            "bundle exec ruby -Ilib -Itest -e 'Dir.glob(\"test/**/*_test.rb\")' -- -v",
            minitest_tests.instrument("bundle exec ruby -Ilib -Itest -e 'Dir.glob(\"test/**/*_test.rb\")'"),
        )
        self.assertEqual("rake test TESTOPTS=-v", minitest_tests.instrument("rake test"))
        self.assertIn("-v", minitest_tests.instrument("rake test TESTOPTS=--seed=1"))

        fw = verification_commands.command_framework("ruby -Ilib -Itest test/pager_test.rb")
        self.assertEqual("minitest", fw.name)
        self.assertTrue(Framework("minitest", "ruby ...").per_test)
        self.assertTrue(
            Framework("minitest", "bundle exec ruby -Ilib -Itest -e '...'")
            .targeted(["test/a_test.rb", "test/b_test.rb"])
            .startswith("bundle exec ruby -Ilib -Itest -r ./test/a_test.rb -r ./test/b_test.rb -e")
        )

    def test_results_parser_maps_classes_specs_errors_and_rejects_mismatches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "test").mkdir()
            (root / "test/pager_test.rb").write_text(CLASS_CAND)
            (root / "test/other_test.rb").write_text(SPEC_BASE)
            out = (
                "PagerTest#test_exact_multiple = 0.00 s = .\n"
                "PagerTest#test_partial_last_page = 0.00 s = F\n"
                "Pager#test_0001_handles empty input = 0.00 s = S\n"
                "3 runs, 2 assertions, 1 failures, 0 errors, 1 skips\n"
            )
            parsed = minitest_tests.results(out, root)
            self.assertEqual(["test/pager_test.rb::PagerTest::test_exact_multiple"], parsed["passed"])
            self.assertEqual(["test/pager_test.rb::PagerTest::test_partial_last_page"], parsed["failed"])
            self.assertEqual(["test/other_test.rb::Pager::test_0001_handles empty input"], parsed["skipped"])
            self.assertIsNone(minitest_tests.results(out.replace("3 runs", "4 runs"), root))
            self.assertIsNone(minitest_tests.results("incomplete output", root))

            load_err = (
                f"{root / 'test/pager_test.rb'}:1:in `require_relative': cannot load such file -- foo (LoadError)\n"
            )
            err_parsed = minitest_tests.results(load_err, root)
            self.assertEqual(["test/pager_test.rb::[collection]"], err_parsed["collection_errors"])
            self.assertEqual(["test/pager_test.rb::[collection]"], err_parsed["uncollected"])


@ruby
class LiveRubyTests(unittest.TestCase):
    def prove(self, seed: dict[str, str], files: dict[str, str], *methods: str, kind: str = "bugfix", supersedes=None):
        project = Project(seed)
        self.addCleanup(project.close)
        project.write(files)
        rows = [{"id": f"AC{n}", "criterion": "pages", "verification_method": m} for n, m in enumerate(methods, 1)]
        state: dict = {"workspace": str(project.root), "base_commit": project.base, "settings": {"regression": {}}}
        state["goal_contract"] = {
            "body": {"task_kind": kind, "acceptance_criteria": rows, "open_blocking_questions": []}
        }
        if supersedes:
            rows[0]["supersedes"] = supersedes
            sealed = {"task_id": "t", "revision": 1, "body": state["goal_contract"]["body"]}
            sealed["hash"] = util.digest(sealed)
            event = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(sealed)}
            state["goal_contract"] = {**sealed, "approval_status": "approved", "approval_event": event}
            state["goal_contract"]["origin"] = "planner_draft"
            state["user_events"] = [event]
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(state, project.root, project.evidence / "proof")

    def test_bugfix_and_guard_with_real_minitest_test(self):
        project = Project({"lib/pager.rb": BROKEN, "test/pager_test.rb": CLASS_BASE})
        self.addCleanup(project.close)
        framework = verify.detect_framework(project.root)
        self.assertEqual("minitest", framework.name)
        self.assertTrue(framework.per_test)
        self.assertIsNone(
            plan_test_suite.unnamed_cases(
                str(project.root),
                framework,
                [],
                _contract("test: test_partial_last_page")["body"],
                lambda _d: None,
            )
        )
        proof = self.prove(
            {"lib/pager.rb": BROKEN, "test/pager_test.rb": CLASS_BASE},
            {"lib/pager.rb": FIXED, "test/pager_test.rb": CLASS_CAND},
            "test: test_partial_last_page",
            "guard: test_exact_multiple",
        )
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test/pager_test.rb::PagerTest::test_partial_last_page"], proof["fail_to_pass"])
        self.assertEqual(
            {
                "AC1": ["test/pager_test.rb::PagerTest::test_partial_last_page"],
                "AC2": ["test/pager_test.rb::PagerTest::test_exact_multiple"],
            },
            proof["case_tests"],
        )

    def test_minitest_spec_supersedes_and_title_matching(self):
        project = Project({"lib/pager.rb": FIXED, "test/pager_test.rb": SPEC_BASE})
        self.addCleanup(project.close)
        self.assertTrue(reporters.reports(verify.detect_framework(project.root).suite))
        spec_id = "test/pager_test.rb::Pager::test_0001_handles empty input"
        self.assertTrue(
            superseded_tests.matches({"file": "test/pager_test.rb", "test": "handles empty input"}, spec_id)
        )
        matched = test_cases.match_cases(
            [{"id": "C1", "title": "gives empty input one page"}],
            ["test/pager_test.rb::Pager::test_0001_gives empty input one page"],
            framework="minitest",
        )
        self.assertEqual({"C1": ["test/pager_test.rb::Pager::test_0001_gives empty input one page"]}, matched)

        entry = {"file": "test/pager_test.rb", "test": "handles empty input"}
        proof = self.prove(
            {"lib/pager.rb": FIXED, "test/pager_test.rb": SPEC_BASE},
            {"lib/pager.rb": ONE_PAGE, "test/pager_test.rb": SPEC_ONE},
            "test: gives empty input one page",
            kind="build",
            supersedes=[entry],
        )
        self.assertEqual(verify.PASS, proof["verdict"], proof)


if __name__ == "__main__":
    unittest.main()
