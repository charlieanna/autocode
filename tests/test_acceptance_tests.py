"""Spec-first runs: the Tester writes the acceptance tests before the Builder, and they stay fixed (#1200).

The CLI tests drive real runs through the task-run interface with the scenario harness's fake provider on
feature-stock-refusals, whose reference tests are named after its cases (test_c1_... to test_c7_...).
The status view, the stage artifacts and the workspace are the oracles.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_acceptance_tests as acceptance
import autocode_taskrun as taskrun
from autocode_stray_writes import StrayWrites

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

CASES = {
    "C1": "test: test_c1_move_transfers_units",
    "C2": "test: test_c2_remove_takes_units_and_drops_empty_holdings",
    "C3": "test: test_c3_move_more_than_on_hand_is_refused",
}


def approved(cases=CASES, **extra):
    criteria = [{"id": cid, "criterion": text, "verification_method": text} for cid, text in cases.items()]
    return {
        "settings": {"spec_first": True},
        "goal_contract": {"hash": "h1", "approval_status": "approved", "body": {"acceptance_criteria": criteria}},
        "next_stage": "terra",
        **extra,
    }


class RouteTests(unittest.TestCase):
    def test_a_build_with_unwritten_cases_goes_to_the_tester_once(self):
        state = approved()
        acceptance.route(state)
        self.assertEqual(acceptance.STAGE, state["next_stage"])
        self.assertEqual("terra", acceptance.resume_stage(state))
        acceptance.record_receipt(state, acceptance.due(state), {"contract_hash": "h1", "files": {}})
        state["next_stage"] = "terra"
        acceptance.route(state)
        self.assertEqual("terra", state["next_stage"], "a receipt for these cases means the tests exist")

    def test_off_a_bug_fix_or_an_unapproved_plan_builds_as_before(self):
        for state in (
            {**approved(), "settings": {}},
            approved() | {"goal_contract": {**approved()["goal_contract"], "approval_status": "draft"}},
        ):
            acceptance.route(state)
            self.assertEqual("terra", state["next_stage"])
        bug = approved()
        bug["goal_contract"]["body"]["task_kind"] = "bugfix"
        acceptance.route(bug)
        self.assertEqual("terra", bug["next_stage"])

    def test_a_changed_test_file_is_restored_and_the_attempt_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, archive = Path(root) / "w", Path(root) / "archive"
            (workspace / "tests").mkdir(parents=True)
            original = b"def test_c1_move_transfers_units():\n    assert False\n"
            archive.write_bytes(original)
            (workspace / "tests" / "test_stock.py").write_bytes(b"def test_c1_move_transfers_units():\n    pass\n")
            state = approved()
            entry = {"sha256": acceptance.sha256(archive), "archive": str(archive)}
            acceptance.record_receipt(
                state, acceptance.due(state), {"contract_hash": "h1", "files": {"tests/test_stock.py": entry}}
            )
            with self.assertRaisesRegex(StrayWrites, "tests/test_stock.py"):
                acceptance.check_untouched(state, workspace)
            self.assertEqual(original, (workspace / "tests" / "test_stock.py").read_bytes())
            acceptance.check_untouched(state, workspace)  # unchanged now: no rejection
            self.assertIn("tests/test_stock.py", acceptance.builder_note(state))


class AcceptanceTestsCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="acceptance-tests-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        loaded = catalog.load("feature-stock-refusals")
        self.scenario = dataclasses.replace(loaded, fake_fault="", fake_criteria=dict(CASES))

    def run_to_end(self, *planning, env=None):
        project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, provider_env = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        environment = {
            **provider_env,
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            **(env or {}),
        }
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        run = taskrun.TaskRun.start(
            project, self.scenario.brief, options=(*flags, *planning), env=environment, timeout=240, cwd=self.root
        )
        for _ in range(16):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"] or need.get("kind") not in ("answer", "approve_plan", "review"):
                return run, project, view
            if need["kind"] == "approve_plan":
                run.approve_plan(need["token"])
            elif need["kind"] == "review":
                for criterion in need["criteria"]:
                    run.approve_review(criterion, need["token"])
            else:
                question = need["questions"][0]
                run.answer(
                    question["id"], question.get("proposed_default") or "plan", resolver_token=need["resolver_token"]
                )
        self.fail("The run did not finish within sixteen public gates")

    @staticmethod
    def stage_order(run):
        return [
            path.stem.rsplit("-", 1)[0] for path in sorted(Path(run.run_dir).glob("iterations/*/*-[0-9][0-9].jsonl"))
        ]

    def details(self, view):
        return {key: view.get(key) for key in ("status", "next_stage", "needs", "stop_reason")}

    def test_the_tester_writes_failing_tests_before_the_builder_and_the_run_completes(self):
        # C0 guards behavior the seed already has: its test must pass before the Builder starts.
        guard = {"C0": "guard: test_receive_adds_units"}
        self.scenario = dataclasses.replace(self.scenario, fake_criteria={**guard, **CASES})
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning", "--spec-first")
        self.assertTrue(view["done"], self.details(view))
        order = self.stage_order(run)
        self.assertIn("acceptance-tests", order)
        self.assertLess(order.index("acceptance-tests"), order.index("builder"), order)
        seen = view["acceptance_tests"]
        self.assertEqual(["tests/test_stock.py"], seen["files"])
        self.assertEqual(sorted(["C0", *CASES]), sorted(seen["case_ids"]))
        self.assertEqual("passes", seen["results"]["C0"])
        self.assertTrue({seen["results"][cid] for cid in CASES} <= {"fails", "cannot load"}, seen["results"])

    def test_a_new_behavior_test_that_already_passes_is_refused_before_any_building(self):
        self.scenario = dataclasses.replace(self.scenario, fake_criteria={"C1": "test: test_receive_adds_units"})
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning", "--spec-first")
        # The runner refuses the passing test; the Tester's report repair reports the case as already
        # working, and the run stops for the user before any Builder call.
        self.assertEqual("PAUSED_ACCEPTANCE_TESTS", view["status"], self.details(view))
        self.assertIn("C1 already works", view["stop_reason"])
        self.assertIn("--feedback", view["stop_reason"])
        self.assertNotIn("builder", self.stage_order(run))

    def test_a_builder_that_edits_the_testers_file_is_rejected_and_the_file_restored(self):
        run, project, view = self.run_to_end(
            "--joint-planning", "--no-adaptive-planning", "--spec-first", env={"SCENARIO_FAKE_BUILDER_EDITS_TESTS": "1"}
        )
        # The rejected Builder report takes the runner's ordinary invalid-output path: the stuck-stage
        # Investigator decides the retry (the offline fixture cannot, so the run pauses there).
        self.assertFalse(view["done"], self.details(view))
        self.assertEqual("PAUSED_INVALID_OUTPUT", view["status"], self.details(view))
        self.assertIn("acceptance tests are fixed for the Builder", view["stop_reason"])
        self.assertIn("tests/test_stock.py (restored)", view["stop_reason"])
        reference = (self.scenario.dir / "reference" / "tests" / "test_stock.py").read_text()
        self.assertEqual(reference, (project / "tests" / "test_stock.py").read_text())

    def test_without_the_setting_the_builder_writes_the_tests_as_before(self):
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning")
        self.assertTrue(view["done"], self.details(view))
        self.assertNotIn("acceptance-tests", self.stage_order(run))
        self.assertIsNone(view["acceptance_tests"])


if __name__ == "__main__":
    unittest.main()
