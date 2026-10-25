"""Original brief output is a public completion gate, independently of delivered tests."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

# Catalog oracle files import harness.oracle when loaded outside the runner.
SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog, verdict
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance


class BriefCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="brief-cli-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scenario = catalog.load("greenfield-todo-cli")

    @staticmethod
    def details(view):
        return {key: view.get(key) for key in ("status", "next_stage", "needs", "stop_reason")}

    def start(self, *, planning, solution="reference", env=None, drive=True):
        project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, provider_env = fake_setup(self.scenario, self.root, self.scenario.dir / solution)
        environment = {
            **provider_env,
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            **(env or {}),
        }
        # The fake and the product's python3 checks use this fixture interpreter.
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        run = taskrun.TaskRun.start(
            project, self.scenario.brief, options=(*flags, *planning), env=environment, timeout=180, cwd=self.root
        )
        return run, self.drive(run) if drive else run.status()

    def test_hash_bound_final_review_completes_without_copying_or_report_repair(self):
        run, view = self.start(
            planning=("--joint-planning", "--no-adaptive-planning"),
            env={"SCENARIO_FAKE_RETAIN_PLAN": "1"},
        )
        self.assertEqual("TASK_COMPLETE", view["status"], self.details(view))
        self.assert_reference(run, view)
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertFalse(any(row.get("report_only") for row in attempts))
        final = next(row for row in attempts if row["stage"] == "astra_finalize")
        report = json.loads(Path(final["output"]).read_text())
        self.assertEqual(
            {"retain_contract", "initial_task", "open_blocking_questions", "acceptance_criteria"},
            set(report["contract"]),
        )
        self.assertTrue(view["approvals"])

    def test_final_review_corrects_one_proof_through_the_criteria_delta_without_another_planning_cycle(self):
        # The revision binds C1 to a wrong command; the final review's reference corrects that one proof (#1991).
        run, view = self.start(
            planning=("--joint-planning", "--no-adaptive-planning"), env={"SCENARIO_FAKE_FINAL_PROOF_FIX": "1"}
        )
        self.assertEqual("TASK_COMPLETE", view["status"], self.details(view))
        self.assert_reference(run, view)
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertFalse(any(row.get("report_only") for row in attempts))
        planning = [row for row in attempts if row["stage"] in ("glm_revise", "astra_finalize")]
        self.assertEqual(["glm_revise", "astra_finalize"], [row["stage"] for row in planning])
        self.assertEqual(1, len(view["approvals"]))
        revised = json.loads(Path(planning[0]["output"]).read_text())["contract"]["acceptance_criteria"]
        self.assertTrue(revised[0]["verification_method"].endswith("--planted-by-the-revision"), revised[0])
        delta = json.loads(Path(planning[1]["output"]).read_text())["contract"]["acceptance_criteria"]
        self.assertEqual(([], [], ["C1"]), (delta["upsert"], delta["remove"], [row["id"] for row in delta["proofs"]]))
        approved = {row["id"]: row for row in view["approved_contract"]["body"]["acceptance_criteria"]}
        self.assertEqual(self.scenario.fake_check, approved["C1"]["verification_method"])

    def test_retained_final_verdict_can_be_repaired_without_reauthoring_the_plan(self):
        run, view = self.start(
            planning=("--joint-planning", "--no-adaptive-planning"),
            env={"SCENARIO_FAKE_RETAIN_PLAN": "1", "SCENARIO_FAKE_RETAIN_REPAIR": "1"},
        )
        self.assertEqual("TASK_COMPLETE", view["status"], self.details(view))
        self.assert_reference(run, view)
        repairs = [row for row in view["usage"]["accounting"]["attempts"] if row.get("report_only")]
        self.assertEqual(["astra_finalize_report_repair"], [row["stage"] for row in repairs])
        report = json.loads(Path(repairs[0]["output"]).read_text())
        self.assertIn("retain_contract", report["contract"])

    def test_completed_small_turn_gets_fresh_default_reviews_in_the_same_chat(self):
        run, first = self.start(
            planning=("--joint-planning",),
            env={"SCENARIO_FAKE_BLOCKING_REVIEW": "1", "SCENARIO_FAKE_EXISTING_SOURCE": "1"},
        )
        self.assert_reference(run, first)
        before = first["usage"]["accounting"]["attempts"]
        self.assertEqual(2, sum(row["stage"] in ("astra_challenge", "astra_finalize") for row in before))
        run.follow_up(self.scenario.brief)
        second = self.drive(run)
        self.assertEqual("TASK_COMPLETE", second["status"], self.details(second))
        self.assert_reference(run, second)
        after = second["usage"]["accounting"]["attempts"]
        self.assertGreater(len(after), len(before))
        self.assertEqual(4, sum(row["stage"] in ("astra_challenge", "astra_finalize") for row in after))

    def test_feedback_interpretation_correction_keeps_review_and_token_bound_approval(self):
        # #2083: a valid feedback ID made a new model inference immutable, even though the person
        # never waived proof. Keep an older approved turn to exercise the actual historical boundary.
        run, first = self.start(planning=("--joint-planning",), env={"SCENARIO_FAKE_EXISTING_SOURCE": "1"})
        self.assert_reference(run, first)
        before = len(first["usage"]["accounting"]["attempts"])
        feedback = (
            "AC6 and AC8 were added in the current unapproved draft after revision 6. "
            "The saved approval covers AC1 through AC5. The installed core now distinguishes those identities "
            "under the corrected revision guard. Re-evaluate the pending corrections with the requested feature "
            "and test scope preserved. This is corrective information and grants no plan approval."
        )
        invented = (
            "The corrected revision guard distinguishes the approved criteria from the new draft additions, "
            "so the completed fail-first proofs are carried forward unchanged and only the new behavior needs "
            "fresh fail-first proof."
        )
        corrected = (
            "The feedback distinguishes approved identities from new draft additions; it grants no plan "
            "approval and waives no fresh runner proof."
        )
        provider = self.root / "bin" / "codex"
        script = provider.read_text()
        self.assertEqual(1, script.count("def report_for(stage: str, data: dict) -> dict:\n"))
        self.assertEqual(1, script.count("def _main() -> int:\n"))
        amendment = textwrap.dedent(
            """
            def report_for(stage, data):
                if data.get("report_repair"):
                    witness = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("feedback-interpretation-repair-error.txt")
                    if not witness.exists():
                        witness.write_text(str(data.get("error")))
                report = base_report_for(stage, data)
                feedback = [row for row in data.get("brief_feedback") or [] if row.get("text") == CORRECTIVE]
                if not feedback:
                    return report
                event_id = feedback[-1]["id"]
                if stage == "astra_challenge":
                    report.update(summary="Correct the invented waiver and cover list preservation", concerns=[
                        {"id": "B1", "concern": "The model inferred a proof waiver the feedback never grants",
                         "evidence_refs": ["goal_contract", "feedback:" + event_id],
                         "requested_change": "Correct only the model interpretation; fresh runner proof remains required",
                         "acceptance_test": CHECK, "blocking": True},
                        {"id": "B2", "concern": "The list-preservation behavior needs an explicit criterion",
                         "evidence_refs": ["task"], "requested_change": "Add C2 covering the saved list and IDs",
                         "acceptance_test": CHECK, "blocking": True}])
                if stage in ("astra_discovery", "glm_revise"):
                    body = report["contract"]
                    body["accepted_assumptions"].append({
                        "text": INVENTED if stage == "astra_discovery" else CORRECTED,
                        "basis": "user_feedback", "answer_id": event_id})
                    if stage == "glm_revise":
                        # The correction changes no approved identity, proof selector, permission or source.
                        body["acceptance_criteria"].append({
                            "id": "C2", "criterion": "Given two saved to-dos, listing preserves both items and their IDs",
                            "verification_method": CHECK, "human_review": False,
                            "sources": body["acceptance_criteria"][0]["sources"]})
                        body["milestones"][0]["acceptance_criteria"].append("C2")
                        body["initial_task"]["acceptance_criteria"].append("C2")
                if stage == "astra_finalize" and any(
                    row.get("id") == "C2"
                    for row in ((data.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria", [])
                ):
                    report["contract"]["initial_task"]["acceptance_criteria"].append("C2")
                return report

            """
        )
        amendment = f"CORRECTIVE = {feedback!r}\nINVENTED = {invented!r}\nCORRECTED = {corrected!r}\n" + amendment
        provider.write_text(
            script.replace(
                "def report_for(stage: str, data: dict) -> dict:\n",
                "def base_report_for(stage: str, data: dict) -> dict:\n",
            ).replace("def _main() -> int:\n", amendment + "def _main() -> int:\n")
        )
        # The follow-up adds the newly requested coverage, so its Builder has a real source change.
        # Keep the original turn's reference untouched and reuse its implementation and three tests.
        second_solution = self.root / "follow-up-reference"
        shutil.copytree(self.scenario.dir / "reference", second_solution)
        tests = second_solution / "test_todo.py"
        test_marker = "    def test_unknown_id_leaves_store_identical(self):\n"
        self.assertEqual(1, tests.read_text().count(test_marker))
        tests.write_text(
            tests.read_text().replace(
                test_marker,
                "    def test_list_preserves_saved_items_and_ids(self):\n"
                "        self.todo('add', 'first')\n"
                "        self.todo('add', 'second')\n"
                "        before = self.store.read_bytes()\n"
                "        self.assertEqual('1 first [open]\\n2 second [open]\\n', self.todo('list').stdout)\n"
                "        self.assertEqual(before, self.store.read_bytes())\n\n" + test_marker,
            )
        )
        config = self.root / "fake-config.json"
        fixture = json.loads(config.read_text())
        fixture["reference"] = str(second_solution)
        config.write_text(json.dumps(fixture))
        run.follow_up(feedback)
        view = run.advance_until_input()
        repair_error = self.root / "feedback-interpretation-repair-error.txt"
        self.assertFalse(repair_error.exists(), repair_error.read_text() if repair_error.exists() else "")
        self.assertEqual("approve_plan", view["needs"]["kind"], self.details(view))
        attempts = view["usage"]["accounting"]["attempts"][before:]
        self.assertFalse(any(row.get("report_only") for row in attempts), attempts)
        self.assertEqual(
            ["astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"],
            [row["stage"] for row in attempts if row["stage"] != "requirements_gather"],
        )
        self.assertFalse(any(row["stage"] == "terra" for row in attempts))
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan("r0:not-the-plan-that-was-shown")
        self.assertEqual("approve_plan", run.status()["needs"]["kind"])
        run.approve_plan(view["needs"]["token"])
        complete = self.drive(run)
        self.assert_reference(run, complete)
        body = complete["approved_contract"]["body"]
        self.assertIn(corrected, [row["text"] for row in body["accepted_assumptions"]])
        self.assertNotIn(invented, [row["text"] for row in body["accepted_assumptions"]])
        self.assertEqual(["C1", "C2"], [row["id"] for row in body["acceptance_criteria"]])
        self.assertEqual(2, len(complete["approvals"]))

    def test_period_separated_commands_complete_without_a_phantom_add_observation(self):
        self.assertIn("exits 0. `todo.py list`", self.scenario.brief)
        run, view = self.start(planning=("--joint-planning", "--no-adaptive-planning"))
        self.assert_reference(run, view)
        rows = view["approved_contract"]["body"]["brief_acceptance"]["manifest"]["observations"]
        self.assertEqual([["list"]], [row["declaration"]["observe_argv"] for row in rows])

    def drive(self, run):
        """Serve this fixture's delegated plan approval through the task-run API."""
        for _ in range(12):
            view = run.advance_until_input()
            if view["done"]:
                return view
            need = view.get("needs") or {}
            if need.get("kind") == "approve_plan":
                run.approve_plan(need["token"])
            elif need.get("kind") == "review":
                for criterion in need["criteria"]:
                    run.approve_review(criterion, need["token"])
            else:
                return view
        self.fail("The scripted task did not stop within twelve public approval gates")

    def reattach(self, run):
        return taskrun.TaskRun(run.workspace, run.run_dir, options=run.options, env=run.env, timeout=180, cwd=self.root)

    def assert_reference(self, run, view):
        checked = verdict.evaluate(self.scenario, run.workspace)
        self.assertTrue(checked.passed, checked.summary)
        self.assertEqual(10, len(checked.checks))
        self.assertEqual("TASK_COMPLETE", view["status"], {"view": self.details(view), "oracle": checked.summary})
        replay = view["evidence"]["check_replay"]
        self.assertEqual("PASS", replay["verdict"])
        receipt = replay["brief_acceptance"]
        self.assertIsInstance(receipt, dict)
        self.assertEqual("PASS", receipt["verdict"])
        self.assertTrue(receipt["checks"], "Completion needs an independently executed brief case")
        self.assertTrue(Path(receipt["summary"]).is_file())
        self.assertEqual(view["evidence"]["validator_source_revision"], receipt["source_revision"])
        for check in receipt["checks"]:
            observed = json.loads(Path(check["output"]).read_text())
            self.assertEqual("PASS", observed["verdict"])
            self.assertEqual(check["observation_hash"], observed["observation_hash"])
            self.assertEqual([0] * len(observed["steps"]), [step["exit_code"] for step in observed["steps"]])
            # The live Plan Reviewers' shape (#452 runs on d6aded9): several to-dos, one line each.
            listing = base64.b64decode(observed["steps"][-1]["stdout_base64"]).decode().splitlines()
            self.assertEqual(2, len(listing), listing)
        self.assertEqual(1, view["efficiency"]["delivery"]["verified_deliveries"])
        return receipt

    def assert_mutant(self, run, view):
        self.assertNotEqual("TASK_COMPLETE", view["status"], self.details(view))
        self.assertFalse(view["done"], self.details(view))
        delivered = subprocess.run(
            [sys.executable, "-m", "unittest", "test_todo.py"],
            cwd=run.workspace,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, delivered.returncode, (delivered.stdout + delivered.stderr)[-800:])
        self.assertIn("Ran 3 tests", delivered.stderr)
        checked = verdict.evaluate(self.scenario, run.workspace)
        self.assertEqual(10, len(checked.checks))
        self.assertEqual(
            {"add_then_list", "complete_marks_done", "ids_stable_across_restarts"},
            {check.name for check in checked.checks if not check.ok},
            checked.summary,
        )
        # Read owned runner evidence, without changing a checkpoint or provider result.
        failures = [
            json.loads(path.read_text()) for path in run.run_dir.glob("check-replay/**/brief-acceptance/*/summary.json")
        ]
        self.assertTrue(failures, "The runtime must retain its independently executed rejection")
        format_failures = []
        for receipt in failures:
            if receipt["verdict"] != "FAIL":
                continue
            for check in receipt["checks"]:
                observed = json.loads(Path(check["output"]).read_text())
                if "original brief format" in observed.get("reason", ""):
                    format_failures.append(observed)
        self.assertTrue(format_failures, "The failure must be actual CLI output, not an unrelated gate")
        self.assertTrue(all(step["exit_code"] == 0 for observed in format_failures for step in observed["steps"]))

    def check_mutant_restart(self, planning):
        run, view = self.start(planning=planning, solution="broken/unbracketed-status")
        self.assert_mutant(run, view)
        again = self.reattach(run)
        self.assertFalse(again.status()["done"])
        try:
            again.resume_paused()
        except taskrun.TaskRunError as error:
            # A bounded retry may already be exhausted; it still cannot claim completion.
            self.assertIsNotNone(error.process, str(error))
        restarted = self.drive(again)
        self.assert_mutant(again, restarted)

    def start_items(self, items, *, omit_first=False):
        solution = self.scenario.dir / "reference"
        if omit_first:
            solution = self.root / "omitted-item"
            shutil.copytree(self.scenario.dir / "reference", solution)
            program = solution / "todo.py"
            source = program.read_text()
            loop = '        for todo in data["todos"]:\n'
            self.assertEqual(1, source.count(loop))
            program.write_text(
                source.replace(
                    loop, '        for todo in data["todos"][1:] if len(data["todos"]) > 1 else data["todos"]:\n'
                )
            )
        return self.start(
            planning=("--no-adaptive-planning",),
            solution=solution,
            env={"SCENARIO_FAKE_BRIEF_ITEMS": json.dumps(items)},
        )

    def assert_item_steps(self, view, check, observed, items):
        cases = view["approved_contract"]["body"]["brief_acceptance"]["manifest"]["observations"]
        self.assertEqual(1, len(cases))
        expected = [["add", items[0]], ["add", items[1]], ["list"]]
        self.assertEqual(expected, [step["argv"] for step in cases[0]["proposal"]["steps"]])
        self.assertEqual(2, cases[0]["proposal"]["observe_step"])
        self.assertEqual(
            [{"placeholder": "TEXT", "step": step, "argument": 1} for step in (0, 1)],
            cases[0]["proposal"]["expect"],
        )
        self.assertEqual(cases[0]["hash"], check["observation_hash"])
        self.assertEqual(check["observation_hash"], observed["observation_hash"])
        self.assertEqual(expected, [step["argv"] for step in observed["steps"]])
        self.assertEqual([0, 0, 0], [step["exit_code"] for step in observed["steps"]])

    def assert_item_reference(self, run, view, items):
        receipt = self.assert_reference(run, view)
        for check in receipt["checks"]:
            observed = json.loads(Path(check["output"]).read_text())
            self.assert_item_steps(view, check, observed, items)
            output = base64.b64decode(observed["steps"][2]["stdout_base64"]).decode()
            self.assertEqual(f"1 {items[0]} [open]\n2 {items[1]} [open]\n", output)

    def assert_omission_mutant(self, run, view, items):
        delivered = subprocess.run(
            [sys.executable, "-m", "unittest", "test_todo.py"],
            cwd=run.workspace,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, delivered.returncode, (delivered.stdout + delivered.stderr)[-800:])
        self.assertIn("Ran 3 tests", delivered.stderr)
        checked = verdict.evaluate(self.scenario, run.workspace)
        self.assertEqual(10, len(checked.checks))
        self.assertEqual({"ids_stable_across_restarts"}, {check.name for check in checked.checks if not check.ok})
        replays = []
        for path in run.run_dir.glob("check-replay/**/brief-acceptance/*/summary.json"):
            receipt = json.loads(path.read_text())
            for check in receipt["checks"]:
                observed = json.loads(Path(check["output"]).read_text())
                self.assert_item_steps(view, check, observed, items)
                output = base64.b64decode(observed["steps"][2]["stdout_base64"]).decode()
                self.assertEqual(f"2 {items[1]} [open]\n", output)
                replays.append(observed)
        diagnostic = {"view": self.details(view), "oracle": checked.summary, "replays": replays}
        self.assertTrue(replays, diagnostic)
        self.assertNotEqual("TASK_COMPLETE", view["status"], diagnostic)
        self.assertFalse(view["done"], diagnostic)
        self.assertEqual(0, view["efficiency"]["delivery"]["verified_deliveries"], diagnostic)
        self.assertTrue(
            all(
                observed["verdict"] == "FAIL"
                and observed["reason"] == "CLI output omits an item the observation introduced"
                for observed in replays
            ),
            diagnostic,
        )

    def test_disjoint_reference_has_full_cli_receipt(self):
        items = ["milk", "bread"]
        run, view = self.start_items(items)
        self.assert_item_reference(run, view, items)

    def test_overlap_reference_preserves_distinct_text_fields(self):
        items = ["milk", "buy milk"]
        run, view = self.start_items(items)
        self.assert_item_reference(run, view, items)

    def test_disjoint_omission_cannot_complete_after_reattach(self):
        items = ["milk", "bread"]
        run, view = self.start_items(items, omit_first=True)
        self.assert_omission_mutant(run, view, items)
        again = self.reattach(run)
        self.assertFalse(again.status()["done"])
        try:
            again.resume_paused()
        except taskrun.TaskRunError as error:
            self.assertIsNotNone(error.process, str(error))
        self.assert_omission_mutant(again, self.drive(again), items)

    def test_overlap_omission_cannot_complete(self):
        items = ["milk", "buy milk"]
        run, view = self.start_items(items, omit_first=True)
        self.assert_omission_mutant(run, view, items)

    def test_fixed_reference_has_current_brief_receipt_and_tampering_invalidates_completion(self):
        run, view = self.start(planning=("--no-adaptive-planning",))
        receipt = self.assert_reference(run, view)
        again = self.reattach(run)
        summary = Path(receipt["summary"])
        original = summary.read_bytes()
        summary.write_bytes(original + b"\n")
        self.assertEqual(0, again.status()["efficiency"]["delivery"]["verified_deliveries"])
        summary.write_bytes(original)
        self.assertEqual(1, again.status()["efficiency"]["delivery"]["verified_deliveries"])

    def test_v2_reference_has_current_brief_receipt_and_source_change_invalidates_completion(self):
        run, view = self.start(planning=("--planning-v2", "--no-adaptive-planning"))
        self.assert_reference(run, view)
        again = self.reattach(run)
        source = run.workspace / "todo.py"
        original = source.read_bytes()
        source.write_bytes((self.scenario.dir / "broken/unbracketed-status/todo.py").read_bytes())
        self.assertEqual(0, again.status()["efficiency"]["delivery"]["verified_deliveries"])
        source.write_bytes(original)
        self.assertEqual(1, again.status()["efficiency"]["delivery"]["verified_deliveries"])

    def test_adaptive_early_review_reference_has_current_brief_receipt(self):
        run, view = self.start(planning=("--adaptive-planning",))
        self.assert_reference(run, view)

    def test_duplicate_first_review_observation_repairs_the_review_without_replanning(self):
        run, _ = self.start(
            planning=("--joint-planning", "--adaptive-planning", "--no-spec-first"),
            env={
                "SCENARIO_FAKE_DUPLICATE_REVIEW_OBSERVATION": "1",
                "SCENARIO_FAKE_SIDE": "review-observation-ownership",
            },
            drive=False,
        )
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], self.details(view))
        self.assertTrue((self.root / "fake-duplicate-review-observation").exists())
        calls = [json.loads(line) for line in (self.root / "fake-calls.jsonl").read_text().splitlines()]
        self.assertEqual(1, sum(row["stage"] == "astra_challenge" and row["repair"] for row in calls), calls)
        self.assertFalse(any(row["stage"] in ("glm_revise", "astra_finalize") for row in calls), calls)
        self.assertTrue(view["planning_depth"]["approved_early"])
        self.assertEqual(1, view["planning_depth"]["challenges"])
        self.assertNotIn("approved_contract", view)
        self.assertFalse((run.workspace / "todo.py").exists(), "The user still owns build approval")
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertTrue(any(row["stage"] == "astra_challenge" and row.get("rejected") for row in attempts))
        self.assertTrue(any(row["stage"] == "astra_challenge_report_repair" for row in attempts))

    def test_persistently_duplicate_review_observations_stay_rejected_and_bounded(self):
        run, view = self.start(
            planning=("--joint-planning", "--adaptive-planning", "--no-spec-first"),
            env={
                "SCENARIO_FAKE_DUPLICATE_REVIEW_OBSERVATION": "always",
                "SCENARIO_FAKE_SIDE": "review-observation-ownership",
            },
            drive=False,
        )
        self.assertFalse(view["done"])
        self.assertNotEqual("approve_plan", view["needs"].get("kind"), self.details(view))
        self.assertFalse((run.workspace / "todo.py").exists())
        calls = [json.loads(line) for line in (self.root / "fake-calls.jsonl").read_text().splitlines()]
        repairs = [row for row in calls if row["stage"] == "astra_challenge" and row["repair"]]
        self.assertGreater(len(repairs), 0, calls)
        self.assertLessEqual(len(repairs), 3, calls)
        self.assertFalse(any(row["stage"] in ("glm_revise", "astra_finalize") for row in calls), calls)

    def test_fixed_selfconsistent_unbracketed_mutant_cannot_complete_after_restart(self):
        self.check_mutant_restart(("--no-adaptive-planning",))

    def test_v2_selfconsistent_unbracketed_mutant_cannot_complete_after_restart(self):
        self.check_mutant_restart(("--planning-v2", "--no-adaptive-planning"))

    def test_builder_report_without_evidence_gets_the_usual_report_repair(self):
        # Live #452 run jb1acns9: after approval, AutoResolver escalated this ordinary
        # rejection as "invalid contract or declarative input" because of brief_acceptance.
        run, view = self.start(planning=("--no-adaptive-planning",), env={"SCENARIO_FAKE_BUILDER_NO_EVIDENCE": "1"})
        self.assertTrue((self.root / "fake-builder-no-evidence").exists(), "The Builder report cited no evidence")
        self.assert_reference(run, view)
        self.assertNotIn("invalid contract", view.get("stop_reason") or "")
        self.assertGreaterEqual(view["efficiency"]["by_category"]["report_repair"]["attempts"], 1)

    def test_omitted_reviewer_observations_cannot_reach_build_or_completion(self):
        run, view = self.start(planning=("--no-adaptive-planning",), env={"SCENARIO_FAKE_BRIEF_OMIT": "1"})
        self.assertFalse(view["done"], self.details(view))
        self.assertFalse(
            (run.workspace / "todo.py").exists(), "Missing source-linked proof must stop before the Builder"
        )
        self.assertEqual(0, view["efficiency"]["delivery"]["verified_deliveries"])
        self.assertFalse(self.reattach(run).status()["done"])


if __name__ == "__main__":
    unittest.main()
