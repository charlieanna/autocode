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
        self.assertEqual(sorted(CASES), [case["id"] for case in acceptance.job_cases(state)])
        acceptance.record_receipt(
            state, acceptance.job_cases(state), {"contract_hash": "h1", "files": {}, "case_ids": sorted(CASES)}
        )
        state["next_stage"] = "terra"
        acceptance.route(state)
        self.assertEqual("terra", state["next_stage"], "a receipt for these cases means the tests exist")

    def test_a_parallel_wave_and_a_later_milestone_get_only_their_unwritten_cases(self):
        state = approved()
        body = state["goal_contract"]["body"]
        body["milestones"] = [
            {"id": "M1", "acceptance_criteria": ["C1"]},
            {"id": "M2", "acceptance_criteria": ["C2"]},
            {"id": "M3", "acceptance_criteria": ["C3"]},
        ]
        state.update(next_stage="orchestrator", current_task={"milestone_id": "M1"})
        acceptance.route(state, wave=lambda: ["M1", "M2"])
        self.assertEqual(["C1", "C2"], [case["id"] for case in acceptance.job_cases(state)], "M3 is not built yet")
        acceptance.record_receipt(state, acceptance.job_cases(state), {"contract_hash": "h1", "case_ids": ["C1", "C2"]})
        state.update(
            next_stage="terra",
            current_task={"milestone_id": "M3"},
            milestone_progress={
                "h1:M1": {"accepted": True, "contract_hash": "h1", "milestone_ids": ["M1"]},
                "h1:M2": {"accepted": True, "contract_hash": "h1", "milestone_ids": ["M2"]},
            },
        )
        acceptance.route(state)
        self.assertEqual(["C3"], [case["id"] for case in acceptance.job_cases(state)], "C1 and C2 already pass now")

    def test_off_or_an_unapproved_plan_builds_as_before(self):
        for state in (
            {**approved(), "settings": {}},
            approved() | {"goal_contract": {**approved()["goal_contract"], "approval_status": "draft"}},
        ):
            acceptance.route(state)
            self.assertEqual("terra", state["next_stage"])

    def test_a_send_back_relabels_only_the_cases_seen_passing_and_happens_once(self):
        import autocode_acceptance_tests_job as job

        state = approved()
        body = job.corrected_body(state, {"C1": "test_c1_move_transfers_units"})
        methods = {row["id"]: row["verification_method"] for row in body["acceptance_criteria"]}
        self.assertEqual("guard: test_c1_move_transfers_units", methods["C1"])
        self.assertEqual(CASES["C2"], methods["C2"])
        self.assertEqual(CASES["C1"], state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"])
        acceptance.record_send_back(state, ["C1"], "out.json")
        self.assertEqual({"C1"}, acceptance.sent_back(state))

    def test_a_changed_test_file_is_restored_and_the_builder_goes_again_twice_then_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            workspace, archive = Path(root) / "w", Path(root) / "archive"
            (workspace / "tests").mkdir(parents=True)
            original = b"def test_c1_move_transfers_units():\n    assert False\n"
            archive.write_bytes(original)
            target = workspace / "tests" / "test_stock.py"
            state = approved()
            entry = {"sha256": acceptance.sha256(archive), "archive": str(archive)}
            acceptance.record_receipt(
                state, acceptance.due(state), {"contract_hash": "h1", "files": {"tests/test_stock.py": entry}}
            )
            for attempt in range(acceptance.DIRECT_RETRIES):
                target.write_bytes(b"def test_c1_move_transfers_units():\n    pass\n")
                state["next_stage"] = "sol"
                self.assertTrue(acceptance.check_untouched(state, workspace), attempt)
                self.assertEqual(("terra", original), (state["next_stage"], target.read_bytes()))
                self.assertIn(
                    "Your previous attempt changed the Tester's lines in tests/test_stock.py",
                    acceptance.builder_note(state),
                )
            target.unlink()  # a deletion counts too
            with self.assertRaisesRegex(StrayWrites, "tests/test_stock.py"):
                acceptance.check_untouched(state, workspace)
            self.assertEqual(original, target.read_bytes())
            self.assertFalse(acceptance.check_untouched(state, workspace))  # a clean attempt
            self.assertNotIn("Your previous attempt", acceptance.builder_note(state))
            self.assertIn("tests/test_stock.py", acceptance.builder_note(state))

    def test_the_builder_may_add_tests_to_a_testers_file_but_not_change_its_lines(self):
        archived = b"import unittest\n\nclass T(unittest.TestCase):\n    def test_c1(self):\n        self.fail()\n"
        self.assertTrue(acceptance.kept(archived, archived + b"\n    def test_c9(self):\n        pass\n"))
        self.assertTrue(acceptance.kept(archived, b"import os\n" + archived))
        self.assertFalse(acceptance.kept(archived, archived.replace(b"self.fail()", b"pass")))
        self.assertFalse(acceptance.kept(archived, None))

    def test_a_bug_fix_also_gets_its_plans_own_cases(self):
        state = approved({"C9": "test: test_c9_holds_for_all_carts"})
        state["goal_contract"]["body"]["task_kind"] = "bugfix"
        state["investigation"] = {
            "outcome": "reproduced",
            "test_cases": [{"id": "T1", "given": "a cart", "when": "it is charged", "then": "no cent is lost"}],
        }
        self.assertEqual(["T1", "C9"], [case["id"] for case in acceptance.due(state)])

    def test_a_parallel_batch_that_edited_a_testers_file_is_not_integrated(self):
        import autocode_dispatch as dispatch
        import autocode_support as support

        with tempfile.TemporaryDirectory() as root:
            patch = Path(root) / "batch.patch"
            added_only = (
                b"diff --git a/tests/test_stock.py b/tests/test_stock.py\n--- a/tests/test_stock.py\n"
                b"+++ b/tests/test_stock.py\n@@ -1,2 +1,4 @@\n def test_c1():\n     assert False\n+def test_extra():\n+    pass\n"
            )
            changed = added_only.replace(b" def test_c1():\n", b"-def test_c1():\n+def test_c1_weakened():\n")
            patch.write_bytes(changed)
            state = approved()
            entry = {"sha256": "0" * 64, "archive": str(Path(root) / "archive")}
            acceptance.record_receipt(
                state, acceptance.due(state), {"contract_hash": "h1", "files": {"tests/test_stock.py": entry}}
            )
            batch = {
                "patch": str(patch),
                "patch_hash": support.file_hash(patch),
                "changed_files": ["stock.py", "tests/test_stock.py"],
                "status": "BUILT",
            }
            with self.assertRaisesRegex(support.Paused, "tests/test_stock.py") as caught:
                dispatch.integrate(state, Path(root), Path(root), batch)
            self.assertEqual("PAUSED_ACCEPTANCE_TESTS", caught.exception.status)
            self.assertEqual("BUILT", batch["status"], "nothing was applied")
            self.assertEqual([], acceptance.edited(state, added_only), "adding tests to the file is allowed")


class AcceptanceTestsCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="acceptance-tests-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        loaded = catalog.load("feature-stock-refusals")
        self.scenario = dataclasses.replace(loaded, fake_fault="", fake_criteria=dict(CASES))

    def run_to_end(self, *planning, env=None):
        self.plan_approvals = []
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
                self.plan_approvals.append(need["token"])
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
        # In the order the stages ran: each stage's event log is written while it runs.
        logs = sorted(Path(run.run_dir).glob("iterations/*/*-[0-9][0-9].jsonl"), key=lambda path: path.stat().st_mtime)
        return [path.stem.rsplit("-", 1)[0] for path in logs]

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

    def test_a_case_that_already_works_goes_back_to_plan_review_as_a_guard(self):
        self.scenario = dataclasses.replace(
            self.scenario, fake_criteria={"C0": "test: test_receive_adds_units", **CASES}
        )
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning", "--spec-first")
        # The runner refuses the passing test; the Tester's repair reports C0 as already working; the runner
        # relabels it guard: and plan review runs again; the user approves the corrected plan; then the build.
        self.assertTrue(view["done"], self.details(view))
        self.assertEqual(2, len(self.plan_approvals), "the corrected plan needs its own approval")
        criteria = {row["id"]: row for row in view["approved_contract"]["body"]["acceptance_criteria"]}
        self.assertEqual("guard: test_receive_adds_units", criteria["C0"]["verification_method"])
        self.assertEqual("passes", view["acceptance_tests"]["results"]["C0"])
        order = self.stage_order(run)
        repair = order.index("acceptance-tests-report-repair")
        self.assertIn("plan-challenge", order[repair:], order)  # plan review runs again on the corrected draft
        self.assertLess(repair, order.index("acceptance-tests"), order)  # then the Tester, on the approved plan
        self.assertLess(order.index("acceptance-tests"), order.index("builder"), order)

    def test_a_builder_that_edits_the_testers_file_goes_again_with_the_file_restored(self):
        run, project, view = self.run_to_end(
            "--joint-planning", "--no-adaptive-planning", "--spec-first", env={"SCENARIO_FAKE_BUILDER_EDITS_TESTS": "1"}
        )
        # The runner puts the file back and sends the Builder straight back, naming what it changed.
        self.assertTrue(view["done"], self.details(view))
        self.assertGreaterEqual(self.stage_order(run).count("builder"), 2, self.stage_order(run))
        reference = (self.scenario.dir / "reference" / "tests" / "test_stock.py").read_text()
        self.assertEqual(reference, (project / "tests" / "test_stock.py").read_text())
        prompts = sorted(
            Path(run.run_dir).glob("iterations/*/builder-*.prompt.md"), key=lambda path: path.stat().st_mtime
        )
        self.assertIn(
            "Your previous attempt changed the Tester's lines in tests/test_stock.py", prompts[-1].read_text()
        )

    def test_a_bug_fix_gets_its_reproduction_tests_from_the_tester_before_the_fix(self):
        self.scenario = catalog.load("bugfix-trivial")
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning", "--spec-first")
        self.assertTrue(view["done"], self.details(view))
        order = self.stage_order(run)
        self.assertLess(order.index("bug-investigation"), order.index("acceptance-tests"), order)
        self.assertLess(order.index("acceptance-tests"), order.index("builder"), order)
        results = view["acceptance_tests"]["results"]
        self.assertTrue(results)
        self.assertEqual({"fails"}, set(results.values()), "a reproduction must really fail before the fix")

    def test_without_the_setting_the_builder_writes_the_tests_as_before(self):
        run, project, view = self.run_to_end("--joint-planning", "--no-adaptive-planning")
        self.assertTrue(view["done"], self.details(view))
        self.assertNotIn("acceptance-tests", self.stage_order(run))
        self.assertIsNone(view["acceptance_tests"])


if __name__ == "__main__":
    unittest.main()
