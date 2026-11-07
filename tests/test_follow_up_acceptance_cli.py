"""A real two-turn public run keeps inherited tests and builds its approved feature."""

import ast
import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

SEED_TESTS = (
    "import unittest\nfrom totals import sum_positive\n"
    "class Totals(unittest.TestCase):\n"
    " def test_positive(self): self.assertEqual(sum_positive([2]), 2)\n"
)
FIRST_TESTS = SEED_TESTS + (
    " def test_old_negative(self):\n"
    "  self.assertEqual(sum_positive([-3, 2]), 2)\n"
    "  self.assertEqual(sum_positive([-1, 0.5]), 0.5)\n"
    " def test_empty(self): self.assertEqual(sum_positive([]), 0)\n"
)
SECOND_TESTS = FIRST_TESTS + (
    " def test_start_positive(self): self.assertEqual(sum_positive([-3, 2], start=10), 12)\n"
    " def test_start_negative(self): self.assertEqual(sum_positive([], start=-5), -5)\n"
    " def test_start_generator(self): self.assertEqual(sum_positive((v for v in [-3, 2]), start=-5), -3)\n"
    " def test_follow_up_guard(self): self.assertEqual(sum_positive([-4, 2]), 2)\n"
)
FIRST = {"C1": "test: test_old_negative", "C0": "guard: test_empty"}
SECOND = {
    **FIRST,
    "C2": "test: test_start_positive",
    "C3": "test: test_start_negative",
    "C4": "test: test_start_generator",
    "C5": "guard: test_follow_up_guard",
}


class FollowUpAcceptanceCli(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="follow-up-acceptance-cli-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.seed, self.reference = self.root / "seed", self.root / "reference"
        self.seed.mkdir()
        self.reference.mkdir()
        (self.seed / "totals.py").write_text("def sum_positive(values): return sum(values)\n")
        (self.seed / "test_totals.py").write_text(SEED_TESTS)
        (self.reference / "totals.py").write_text("def sum_positive(values): return sum(v for v in values if v >= 0)\n")
        (self.reference / "test_totals.py").write_text(FIRST_TESTS)

    def drive(self, run):
        approvals = []
        for _ in range(12):
            view = run.advance_until_input()
            if view["done"]:
                return view, approvals
            need = view.get("needs") or {}
            if need.get("kind") == "approve_plan":
                approvals.append(need["token"])
                # This scripted test owner reads the actual plan through the public interface.
                self.assertIn("test_old_negative", run.show_goal())
                run.approve_plan(need["token"])
            elif need.get("kind") == "review":
                for criterion in need["criteria"]:
                    run.approve_review(criterion, need["token"])
            elif need.get("kind") == "answer" and need.get("resolver_scope") == "clarification":
                question = need["questions"][0]
                self.assertEqual("plan", question.get("proposed_default"))
                run.answer(question["id"], "plan", resolver_token=need["resolver_token"])
            else:
                self.fail(str({key: view.get(key) for key in ("status", "next_stage", "needs", "stop_reason")}))
        self.fail("Two-turn fixture exceeded its twelve explicit human gates")

    def test_inherited_current_pass_does_not_reopen_the_approved_follow_up(self):
        scenario = dataclasses.replace(
            catalog.load("feature-stock-refusals"),
            dir=self.root,
            brief="Exclude negative input values in sum_positive. Preserve positive sums and empty input. "
            "Keep the current tests and add focused regression tests. Change only totals.py and test_totals.py.",
            fake_fault="",
            fake_criteria=FIRST,
            fake_milestones=(),
            fake_turn_paths=(),
            turns=(),
            fake_check="python3 -m unittest -v test_totals.py",
            fake_flags=(),
            fake_plan_review_call_limit=None,
        )
        project = without_maintenance(materialize(self.seed, self.root / "project"))
        flags, environment = fake_setup(scenario, self.root, self.reference)
        environment.update(
            AUTOCODE_HOME=str(self.root / "registry"),
            PYTHONDONTWRITEBYTECODE="1",
            SCENARIO_FAKE_BLOCKING_REVIEW="1",
            SCENARIO_FAKE_EXISTING_SOURCE="1",
        )
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        provider = self.root / "bin/codex"
        script = provider.read_text()
        marker = 'if __name__ == "__main__":'
        self.assertEqual(1, script.count(marker))
        amendment = """
base_cite_sources = cite_sources
base_acceptance_tests = acceptance_tests
def cite_sources(body):
    body = base_cite_sources(body)
    inherited = CONFIG.get("completed_criteria") or {}
    body["acceptance_criteria"] = [inherited.get(row["id"], row) for row in body.get("acceptance_criteria", [])]
    return body
def acceptance_tests(data):
    value = base_acceptance_tests(data)
    inherited = CONFIG.get("completed_criteria") or {}
    for row in value["tests"]:
        cid = row["case_id"]
        if cid in inherited and inherited[cid]["verification_method"].startswith("test:"):
            value["already_passing"].append({"case_id": cid, "evidence": "The completed previous turn delivers this unchanged test"})
    return value
"""
        provider.write_text(script.replace(marker, amendment + "\n" + marker))
        run = taskrun.TaskRun.start(
            project, scenario.brief, options=(*flags, "--spec-first"), env=environment, timeout=180, cwd=self.root
        )
        first, approvals1 = self.drive(run)
        self.assertEqual("TASK_COMPLETE", first["status"])
        self.assertEqual(1, len(approvals1))
        old_contract = first["approved_contract"]
        original_methods = {row["id"]: row for row in old_contract["body"]["acceptance_criteria"]}
        old_functions = {
            node.name: ast.get_source_segment(FIRST_TESTS, node)
            for node in ast.walk(ast.parse(FIRST_TESTS))
            if isinstance(node, ast.FunctionDef)
        }
        before = len(first["usage"]["accounting"]["attempts"])
        config_path = self.root / "fake-config.json"
        config = json.loads(config_path.read_text())
        config.update(criteria=SECOND, completed_criteria=original_methods)
        config_path.write_text(json.dumps(config))
        (self.reference / "totals.py").write_text(
            "def sum_positive(values, *, start=0): return start + sum(v for v in values if v >= 0)\n"
        )
        (self.reference / "test_totals.py").write_text(SECOND_TESTS)
        run.follow_up(
            "Add an optional keyword-only start=0 accumulator. Keep negative starts. Preserve all existing "
            "behavior and existing test functions, append exactly four new functions. Change only totals.py "
            "and test_totals.py. Require fresh original-reference regression proof and preserve the prior delivery."
        )
        second, approvals2 = self.drive(run)
        self.assertEqual("TASK_COMPLETE", second["status"])
        self.assertEqual(1, len(approvals2), "current-green inherited cases must not create another plan approval")
        after = second["usage"]["accounting"]["attempts"][before:]
        self.assertEqual(1, sum(row["stage"] == "write_acceptance_tests" for row in after))
        self.assertEqual(1, sum(row["stage"] == "terra" for row in after))
        self.assertEqual(2, sum(row["stage"] in ("astra_challenge", "astra_finalize") for row in after))
        self.assertFalse(any(row.get("report_only") for row in after), after)
        final_contract = second["approved_contract"]
        self.assertEqual(approvals2[0], "r" + str(final_contract["revision"]) + ":" + final_contract["hash"])
        final_rows = {row["id"]: row for row in final_contract["body"]["acceptance_criteria"]}
        for cid, row in original_methods.items():
            self.assertEqual(row, final_rows[cid])
        text = (Path(run.workspace) / "test_totals.py").read_text()
        functions = {
            node.name: ast.get_source_segment(text, node)
            for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(7, len(functions))
        for name, source in old_functions.items():
            self.assertEqual(source, functions[name])
        proof = second["evidence"]["regression_proof"]
        self.assertEqual("PASS", proof["verdict"])
        self.assertTrue(any("test_old_negative" in test for test in proof["case_tests"]["C1"]))
        self.assertTrue(any("test_follow_up_guard" in test for test in proof["case_tests"]["C5"]))
        self.assertIn("follow_up_baseline", proof)
