"""An approved plan may replace an original test that asserts the bug it fixes (#2748)."""

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_approval_view as approval_view
import autocode_contract_identity as contracts
import autocode_pause_category as pause_category
import autocode_protected_oracles as guard
import autocode_regression as regression
import autocode_run_records as run_records
import autocode_superseded_correction as correction
import autocode_superseded_tests as superseded
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project

SHIPPING = "def shipping_fee(cents):\n    return 0 if cents > 5000 else 499\n"
FIXED = "def shipping_fee(cents):\n    return 0 if cents >= 5000 else 499\n"
ORIGINAL = """import unittest
from shipping import shipping_fee
class Shipping(unittest.TestCase):
    def test_fifty_dollar_order_pays_standard_fee(self):
        self.assertEqual(shipping_fee(5000), 499)
    def test_small_order_pays_fee(self):
        self.assertEqual(shipping_fee(1000), 499)
    def test_large_order_ships_free(self):
        self.assertEqual(shipping_fee(9000), 0)
"""
UPDATED = ORIGINAL.replace("shipping_fee(5000), 499", "shipping_fee(5000), 0")
ENTRY = {"file": "tests/test_shipping.py", "test": "test_fifty_dollar_order_pays_standard_fee"}


def results(failed, passed=(), **extra):
    total = len(failed) + len(passed)
    return {
        "exit_code": 1,
        "error": "",
        "timed_out": False,
        "results": {
            "passed": list(passed),
            "failed": list(failed),
            "skipped": [],
            "uncollected": [],
            "total": total,
            "complete": True,
            **extra,
        },
    }


class Matching(unittest.TestCase):
    def test_each_runner_id_of_the_named_test_matches(self):
        for test_id in (
            "tests.test_shipping.Shipping.test_fifty_dollar_order_pays_standard_fee",
            "tests/test_shipping.py::Shipping::test_fifty_dollar_order_pays_standard_fee",
            "tests/test_shipping.py::test_fifty_dollar_order_pays_standard_fee[5000]",
            "test_shipping.py::test_fifty_dollar_order_pays_standard_fee",  # a package run's relative path
        ):
            with self.subTest(test_id=test_id):
                self.assertTrue(superseded.matches(ENTRY, test_id))

    def test_another_test_or_file_or_fixture_does_not_match(self):
        for test_id in (
            "tests.test_shipping.Shipping.test_small_order_pays_fee",
            "tests.test_other.Shipping.test_fifty_dollar_order_pays_standard_fee",
            "tests/test_other.py::test_fifty_dollar_order_pays_standard_fee",
            "tests/test_shipping.py::test_fifty_dollar_order_pays_standard_fee_twice",
            "tests.test_shipping.Shipping::setUpClass",
            "tests/test_shipping.py::[collection]",
        ):
            with self.subTest(test_id=test_id):
                self.assertFalse(superseded.matches(ENTRY, test_id))

    def test_go_and_node_ids(self):
        go = {"file": "pkg/fee/fee_test.go", "test": "TestFiftyDollarFee"}
        self.assertTrue(superseded.matches(go, "example.com/m/pkg/fee::TestFiftyDollarFee"))
        self.assertTrue(superseded.matches(go, "example.com/m/pkg/fee::TestFiftyDollarFee/exact"))
        self.assertFalse(superseded.matches(go, "example.com/m/pkg/other::TestFiftyDollarFee"))
        self.assertFalse(superseded.matches(go, "example.com/m/pkg/fee::TestFiftyDollarFeeTwice"))
        node = {"file": "test/fee.test.js", "test": "fifty dollars pays the fee"}
        self.assertTrue(superseded.matches(node, "test/fee.test.js::shipping::fifty dollars pays the fee"))
        self.assertFalse(superseded.matches(node, "test/fee.test.js::fifty dollars ships free"))

    def test_vitest_jest_and_rspec_ids(self):
        # Vitest and Jest join a test's groups and its title with " > " (autocode_vitest_tests, autocode_jest_tests).
        grouped = {"file": "test/fee.test.js", "test": "fifty dollars pays the fee"}
        self.assertTrue(superseded.matches(grouped, "test/fee.test.js::shipping > fees > fifty dollars pays the fee"))
        self.assertTrue(superseded.matches(grouped, "test/fee.test.js::fifty dollars pays the fee"))
        self.assertFalse(superseded.matches(grouped, "test/fee.test.js::shipping > fifty dollars ships free"))
        self.assertFalse(superseded.matches(grouped, "test/other.test.js::shipping > fifty dollars pays the fee"))
        # An RSpec id is the example's full description: its groups' descriptions, then its own.
        example = {"file": "spec/fee_spec.rb", "test": "charges the fee at fifty dollars"}
        self.assertTrue(superseded.matches(example, "spec/fee_spec.rb::Shipping.fee charges the fee at fifty dollars"))
        self.assertTrue(superseded.matches(example, "spec/fee_spec.rb::charges the fee at fifty dollars"))
        self.assertFalse(superseded.matches(example, "spec/fee_spec.rb::Shipping.fee waives the fee at fifty dollars"))
        self.assertFalse(
            superseded.matches(example, "spec/fee_spec.rb::Shipping.fee overcharges the fee at fifty dollars")
        )
        self.assertFalse(
            superseded.matches(grouped, "test/fee.test.js::it is not true that fifty dollars pays the fee")
        )


class Excused(unittest.TestCase):
    changed = ["tests/test_shipping.py"]
    failing = "tests.test_shipping.Shipping.test_fifty_dollar_order_pays_standard_fee"

    def test_only_the_superseded_failure_is_excused(self):
        row = results([self.failing], ["tests.test_shipping.Shipping.test_small_order_pays_fee"])
        self.assertEqual([self.failing], superseded.excused(row, [ENTRY], self.changed))

    def test_any_other_failure_closes_the_gate(self):
        row = results([self.failing, "tests.test_shipping.Shipping.test_small_order_pays_fee"])
        self.assertIsNone(superseded.excused(row, [ENTRY], self.changed))

    def test_incomplete_crashed_or_unchanged_runs_are_not_excused(self):
        cases = {
            "unchanged file": (results([self.failing]), []),
            "incomplete": (results([self.failing], complete=False), self.changed),
            "uncollected": (results([self.failing], uncollected=["tests/test_x.py::[collection]"]), self.changed),
            "timed out": ({**results([self.failing]), "timed_out": True}, self.changed),
            "error": ({**results([self.failing]), "error": "boom"}, self.changed),
            "no results": ({**results([self.failing]), "results": None}, self.changed),
            "passed": ({**results([self.failing]), "exit_code": 0}, self.changed),
            "no failure": (results([], ["a.b.test_x"]), self.changed),
        }
        for name, (row, changed) in cases.items():
            with self.subTest(name):
                self.assertIsNone(superseded.excused(row, [ENTRY], changed))


class Gate(unittest.TestCase):
    """The original-test replay and completion readiness, on a real unittest project."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / "project"
        (self.project / "tests").mkdir(parents=True)
        (self.project / "shipping.py").write_text(SHIPPING)
        (self.project / "tests" / "__init__.py").write_text("")
        (self.project / "tests" / "test_shipping.py").write_text(ORIGINAL)
        (self.project / "tests" / "test_rounding.py").write_text(
            "import unittest\nclass R(unittest.TestCase):\n    def test_small_order_pays_fee(self):\n        self.assertEqual(1, 1)\n"
        )
        git = ["git", "-C", str(self.project)]
        subprocess.run([*git[:1], "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-qm", "o"], check=True)
        self.run_dir = self.project / ".autocode/runs/superseded"
        command = shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])
        self.state = {"settings": {}, "workspace": str(self.project), "user_events": []}
        guard.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            self.project,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: command,
        )

    def approve(self, supersedes, method="test: test_fifty_dollar_order_pays_standard_fee", state=None):
        state = self.state if state is None else state
        row = {"id": "AC1", "criterion": "A $50 order ships free", "verification_method": method}
        if supersedes is not None:
            row["supersedes"] = supersedes
        body = {"acceptance_criteria": [row], "open_blocking_questions": []}
        contract = {"task_id": "t", "revision": 1, "body": body}
        contract["hash"] = util.digest(contract)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(contract)}
        contract.update(approval_status="approved", approval_event=event, origin="planner_draft")
        state["goal_contract"] = contract
        state["user_events"] = [event]
        self.assertTrue(contracts.approved(state))
        return contract

    def fix(self, test=UPDATED):
        (self.project / "shipping.py").write_text(FIXED)
        (self.project / "tests" / "test_shipping.py").write_text(test)

    def replay(self):
        return guard.replay(self.state, self.project, self.run_dir / "replays", verify.scratch_run, timeout=30)

    def accept(self, result):
        """Record the replay as check_replay records it, then ask completion whether it is ready."""
        self.state["validation"] = {
            "check_replay": {"verdict": "PASS", "source_revision": result["source_revision"], "protected_tests": result}
        }
        return guard.ready(self.state, result["source_revision"])

    def test_approved_supersedes_lets_the_fix_pass_and_completion_rechecks_it(self):
        self.approve([ENTRY])
        self.fix()
        result = self.replay()
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(0, result["candidate"]["exit_code"])
        self.assertEqual(1, result["original"]["exit_code"])
        self.assertEqual(
            {
                "tests": [{"criterion": "AC1", **ENTRY}],
                "excused_failures": ["tests.test_shipping.Shipping.test_fifty_dollar_order_pays_standard_fee"],
            },
            result["superseded"],
        )
        self.assertEqual(result["superseded"], json.loads(Path(result["receipt"]).read_text())["superseded"])
        self.assertTrue(self.accept(result))
        # A later plan without the entry cannot reuse the receipt.
        self.approve(None)
        self.assertFalse(guard.ready(self.state, result["source_revision"]))

    def test_without_supersedes_the_gate_still_refuses_and_names_the_failing_test(self):
        self.approve(None)
        self.fix()
        with self.assertRaises(ValueError) as caught:
            self.replay()
        message = str(caught.exception)
        self.assertTrue(message.startswith(guard.commands.ORIGINAL_GATE_FAILED))
        self.assertIn("test_fifty_dollar_order_pays_standard_fee", message)
        self.assertIn("supersedes", message)

    def test_unapproved_plan_supersedes_nothing(self):
        self.approve([ENTRY])
        self.state["user_events"] = []
        self.fix()
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.replay()

    def test_another_broken_original_test_still_fails(self):
        self.approve([ENTRY])
        self.fix(UPDATED.replace("shipping_fee(1000), 499", "shipping_fee(1000), 1"))
        (self.project / "shipping.py").write_text(
            "def shipping_fee(cents):\n    return 0 if cents >= 5000 else (1 if cents == 1000 else 499)\n"
        )
        with self.assertRaisesRegex(ValueError, "test_small_order_pays_fee"):
            self.replay()

    def test_a_weakened_candidate_still_fails_on_the_unchanged_test(self):
        # The superseded test changes, but the fix breaks a test the plan did not list.
        self.approve([ENTRY])
        (self.project / "shipping.py").write_text("def shipping_fee(cents):\n    return 0\n")
        (self.project / "tests" / "test_shipping.py").write_text(
            UPDATED.replace("shipping_fee(1000), 499", "shipping_fee(1000), 0")
        )
        with self.assertRaisesRegex(ValueError, "test_small_order_pays_fee"):
            self.replay()

    def test_failing_candidate_suite_is_never_excused(self):
        self.approve([ENTRY])
        self.fix(UPDATED.replace("shipping_fee(9000), 0", "shipping_fee(9000), 7"))
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.replay()

    def test_draft_validation(self):
        valid = {"id": "AC1", "verification_method": "test: test_fifty_dollar_order_pays_standard_fee"}
        superseded.validate(self.state, [{**valid, "supersedes": [ENTRY]}])
        superseded.validate(self.state, [{**valid, "supersedes": []}])
        refused = {
            "guard criterion": ({**valid, "verification_method": "guard: test_x"}, [ENTRY], "not proven by a test:"),
            "unknown file": (valid, [{**ENTRY, "file": "tests/test_missing.py"}], "not an original test file"),
            "undefined test": (valid, [{**ENTRY, "test": "test_never_written"}], "does not define"),
            "blank name": (valid, [{**ENTRY, "test": " "}], "single-line"),
            "duplicate": (valid, [ENTRY, ENTRY], "superseded twice"),
            "same directory": (valid, [{**ENTRY, "test": "test_small_order_pays_fee"}], "test_rounding.py defines"),
        }
        for name, (row, entries, message) in refused.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, message):
                    superseded.validate(self.state, [{**row, "supersedes": entries}])
        with self.assertRaisesRegex(ValueError, "no original-test gate"):
            superseded.validate({"settings": {}}, [{**valid, "supersedes": [ENTRY]}])

    def test_builder_context_names_the_superseded_tests(self):
        self.approve([ENTRY])
        packet = guard.context(self.state["settings"], ["tests/"], self.state)
        self.assertEqual([ENTRY], packet["superseded_tests"])
        self.assertIn("superseded_tests", packet["instruction"])
        # A listed test may be renamed or removed (owner decision, 2026-11-13); no other original test may.
        self.assertIn("You may rename one to say what it now checks, or remove it", packet["instruction"])
        self.assertIn("Rename, move or delete no other original test", packet["instruction"])
        self.assertNotIn("superseded_tests", guard.context(self.state["settings"], ["tests/"]))


RENAMED = UPDATED.replace("test_fifty_dollar_order_pays_standard_fee", "test_fifty_dollar_order_ships_free")
REMOVED = (
    ORIGINAL.replace(
        "    def test_fifty_dollar_order_pays_standard_fee(self):\n        self.assertEqual(shipping_fee(5000), 499)\n",
        "",
    )
    + "    def test_fifty_dollar_order_ships_free(self):\n        self.assertEqual(shipping_fee(5000), 0)\n"
)


class Renamed(unittest.TestCase):
    """A test the approved plan replaces may be renamed or removed with the change (owner decision, 2026-11-13).

    "page_count(0, 5) should return 1. Update the tests to match" (Claude models, 2026-11-13), in a Python and
    in a node:test project: the plan listed the test it replaced, the Builder wrote the new test and took the
    old one out, and the proof failed twice with "Existing tests were removed" before an Investigator had the
    old name put back, around an assertion of the new behavior: test_empty_list_has_no_pages asserting one page.
    """

    def prove(self, test, supersedes, code=FIXED):
        project = Project({"shipping.py": SHIPPING, "tests/__init__.py": "", "tests/test_shipping.py": ORIGINAL})
        self.addCleanup(project.close)
        project.write({"shipping.py": code, "tests/test_shipping.py": test})
        state = {"workspace": str(project.root), "base_commit": project.base, "settings": {"regression": {}}}
        state["user_events"] = []
        Gate.approve(self, supersedes, method="test: test_fifty_dollar_order_ships_free", state=state)
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(state, project.root, project.evidence / "proof")

    def test_the_replaced_test_may_take_the_name_of_what_it_now_checks(self):
        proof = self.prove(RENAMED, [ENTRY])
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertIn(
            "Tests the approved plan replaces are no longer in the suite: "
            "tests.test_shipping.Shipping.test_fifty_dollar_order_pays_standard_fee",
            proof["notes"],
        )
        self.assertEqual(["tests.test_shipping.Shipping.test_fifty_dollar_order_ships_free"], proof["fail_to_pass"])

    def test_the_replaced_test_may_be_removed_for_the_new_test_beside_it(self):
        self.assertEqual(verify.PASS, self.prove(REMOVED, [ENTRY])["verdict"])

    def test_a_test_the_plan_does_not_list_may_not_go(self):
        proof = self.prove(RENAMED, None)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn(
            "Existing tests were removed: tests/test_shipping.py::test_fifty_dollar_order_pays_standard_fee",
            proof["failures"],
        )
        self.assertIn("renamed or missing): tests.test_shipping.Shipping.test_fifty", " ".join(proof["failures"]))
        # Nor may another test go because one is listed.
        other = RENAMED.replace(
            "    def test_small_order_pays_fee(self):\n        self.assertEqual(shipping_fee(1000), 499)\n", ""
        )
        proof = self.prove(other, [ENTRY])
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual(
            ["Existing tests were removed: tests/test_shipping.py::test_small_order_pays_fee"], proof["failures"][:1]
        )

    def test_a_plan_that_was_never_sealed_replaces_nothing(self):
        unsealed = {
            "body": {"acceptance_criteria": [{"id": "AC1", "supersedes": [ENTRY]}]},
            "approval_status": "approved",
        }
        self.assertIsNone(superseded.replaced({"goal_contract": unsealed}))
        self.assertIsNone(superseded.replaced({}))

    def test_a_replaced_test_that_is_skipped_is_not_gone(self):
        skipped = (
            UPDATED.replace(
                "    def test_fifty_dollar_order_pays_standard_fee(self):\n",
                "    @unittest.skip('later')\n    def test_fifty_dollar_order_pays_standard_fee(self):\n",
            )
            + "    def test_fifty_dollar_order_ships_free(self):\n        self.assertEqual(shipping_fee(5000), 0)\n"
        )
        proof = self.prove(skipped, [ENTRY])
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("renamed or missing): tests.test_shipping.Shipping.test_fifty", " ".join(proof["failures"]))


JS_FEE = "exports.shippingFee = (cents) => (cents > 5000 ? 0 : 499);\n"
JS_FIXED = JS_FEE.replace("cents > 5000", "cents >= 5000")
JS_ORIGINAL = (
    "const test = require('node:test');\nconst assert = require('node:assert/strict');\n"
    "const { shippingFee } = require('../src/shipping.js');\n\n"
    "test('a fifty dollar order pays the fee', () => {\n  assert.equal(shippingFee(5000), 499);\n});\n\n"
    "test('a small order pays the fee', () => {\n  assert.equal(shippingFee(1000), 499);\n});\n"
)
JS_UPDATED = JS_ORIGINAL.replace("shippingFee(5000), 499", "shippingFee(5000), 0")
JS_ENTRY = {"file": "tests/shipping.test.js", "test": "a fifty dollar order pays the fee"}


@unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "the project's suite is npm test")
class NpmGate(unittest.TestCase):
    """The same gate where the original suite is ``npm test``, as a Node project's detected suite is.

    "pageCount(0, 5) should return 1. Update the tests to match" in a node:test project (Claude models,
    2026-11-13): the approved plan listed the test it replaced, the Builder changed that test in place, and
    the gate still refused the Tester's PASS. It ran ``npm test --silent`` for its exit code alone, so it
    could not see that the one failing original test was the listed one, and the run stopped for the person.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / "project"
        files = {
            "package.json": '{"name": "shipping", "private": true, "scripts": {"test": "node --test tests/*.test.js"}}\n',
            "src/shipping.js": JS_FEE,
            "tests/shipping.test.js": JS_ORIGINAL,
        }
        for name, text in files.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text)
        git = ["git", "-C", str(self.project)]
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-qm", "o"], check=True)
        self.run_dir = self.project / ".autocode/runs/superseded"
        self.state = {"settings": {}, "workspace": str(self.project), "user_events": []}
        suite = verify.detect_framework(self.project).suite
        self.assertEqual("npm test --silent", suite)
        guard.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            self.project,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: suite,
        )

    approve = Gate.approve

    def replay(self, code=JS_FIXED, test=JS_UPDATED):
        (self.project / "src/shipping.js").write_text(code)
        (self.project / "tests/shipping.test.js").write_text(test)
        return guard.replay(self.state, self.project, self.run_dir / "replays", verify.scratch_run, timeout=60)

    def test_the_listed_test_is_the_one_failure_the_gate_accepts(self):
        self.approve([JS_ENTRY], method="test: a fifty dollar order pays the fee")
        result = self.replay()
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(
            ["tests/shipping.test.js::a fifty dollar order pays the fee"], result["superseded"]["excused_failures"]
        )
        self.assertEqual(2, result["candidate"]["results"]["total"])

    def test_a_failing_test_the_plan_does_not_list_is_named_and_refused(self):
        self.approve([JS_ENTRY], method="test: a fifty dollar order pays the fee")
        with self.assertRaisesRegex(ValueError, "Failing original tests: .*a small order pays the fee"):
            self.replay(
                JS_FIXED.replace(": 499", ": 500"),
                JS_UPDATED.replace("shippingFee(1000), 499", "shippingFee(1000), 500"),
            )

    def test_without_the_entry_the_gate_refuses_and_the_runner_drafts_the_plan_that_lists_it(self):
        self.approve(None, method="test: a fifty dollar order pays the fee")
        with self.assertRaisesRegex(
            ValueError, "Failing original tests: .*a fifty dollar order pays the fee"
        ) as caught:
            self.replay()
        found = correction.proposal(self.state, caught.exception.receipt, self.project)
        self.assertEqual([{"criterion": "AC1", **JS_ENTRY}], found["tests"])

    def test_a_grouped_title_is_the_test_the_draft_lists(self):
        # Vitest and Jest ids: the groups, then the title.
        entry = correction._entry(
            "tests/shipping.test.js::fees > a fifty dollar order pays the fee", [JS_ENTRY["file"]]
        )
        self.assertEqual(JS_ENTRY, entry)


class Display(unittest.TestCase):
    def test_approval_screen_shows_the_replaced_test(self):
        body = {
            "acceptance_criteria": [
                {
                    "id": "AC1",
                    "criterion": "A $50 order ships free",
                    "verification_method": "test: test_fifty_dollar_order_pays_standard_fee",
                    "supersedes": [ENTRY],
                }
            ]
        }
        lines = approval_view.summary(body, 1, [])
        self.assertIn(
            "    Replaces existing test: tests/test_shipping.py::test_fifty_dollar_order_pays_standard_fee", lines
        )


class OmittedBox(unittest.TestCase):
    def test_an_omitted_supersedes_list_replaces_nothing_and_costs_no_repair(self):
        # The strict generation schema requires the list on every criterion; a provider that leaves it out
        # (the offline fixtures, a provider without enforced structured output) replaces no original test.
        criterion = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "criterion": {"type": "string"},
                "current_behavior": {"type": "string"},
                "supersedes": {"type": "array"},
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            schema = Path(temp) / "schema.json"
            properties = {"acceptance_criteria": {"type": "array", "items": criterion}}
            schema.write_text(
                json.dumps({"type": "object", "properties": {"contract": {"type": "object", "properties": properties}}})
            )
            record = {"stage": "draft_plan", "schema": str(schema)}
            rows = [{"id": "C1", "criterion": "a"}, {"id": "C2", "criterion": "b", "supersedes": [ENTRY]}]
            value = run_records.default_missing_provenance({"contract": {"acceptance_criteria": rows}}, record)
        self.assertEqual(
            [
                {"id": "C1", "criterion": "a", "current_behavior": "", "supersedes": []},
                {"id": "C2", "criterion": "b", "current_behavior": "", "supersedes": [ENTRY]},
            ],
            value["contract"]["acceptance_criteria"],
        )
        self.assertEqual(
            ["contract.acceptance_criteria.current_behavior", "contract.acceptance_criteria.supersedes"],
            record["defaulted_fields"],
        )


class PauseCategory(unittest.TestCase):
    def test_original_gate_conflict_is_autocode_not_the_model(self):
        reason = guard.commands.ORIGINAL_GATE_FAILED + " on this implementation. Receipt: r.json"
        self.assertEqual(
            "autocode", pause_category.category("PAUSED_REPEATED_FAILURE", {"kind": "resume", "reason": reason})
        )
        self.assertEqual(
            "model_or_provider",
            pause_category.category("PAUSED_REPEATED_FAILURE", {"kind": "resume", "reason": "Tester failed twice"}),
        )


if __name__ == "__main__":
    unittest.main()
