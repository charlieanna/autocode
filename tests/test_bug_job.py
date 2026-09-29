"""The bug-fix workflow's Investigator: diagnose before fixing, and end the run when
the report does not reproduce."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_bug_job as bug_job
import autocode_jobs as jobs
import autocode_run_view as run_view
import autocode_workflows as workflows
import autopilot
from units import autoresolver


def state_for(workspace="/nowhere", task="Occasionally we renew the same domain twice after a timeout. Fix it."):
    return {"version": 3, "task": task, "workspace": workspace, "status": "RUNNING", "stages": [],
            "workflow": {"kind": "bugfix", "then": "requirements_gather"},
            "settings": {"joint_planning": True, "roles": {
                "requirements": {"model": "r"}, "glm": {"model": "g"}, "plan_reviewer": {"model": "p"},
                "astra": {"model": "a", "engine": "codex"}, "terra": {"model": "t"}, "sol": {"model": "s"}}}}


CASE = {"id": "T1", "given": "the registry times out once after applying a renew",
        "when": "renew('example.com') runs", "then": "the registry records exactly 1 renew mutation"}


def diagnosis(outcome="reproduced", **overrides):
    value = {"outcome": outcome, "note_path": "docs/bugs/duplicate-renew.json",
             "observed": "Renewed twice after a timeout", "reproduction": "fail_next('timeout-after'); renew() -> 2 mutations",
             "root_cause": "retries after an uncertain timeout with a fresh cl_trid",
             "affected_paths": ["epp/client.py"], "test_paths": ["tests/test_client.py"],
             "invariant": "one logical renew, at most one mutation",
             "test_cases": [CASE], "probe": "", "untestable": "The fixture has no registry to replay against",
             "conclusion": "Reconcile before resending.", "fix_size": "small",
             "fix_plan": ["keep one cl_trid", "poll before resending"], "questions": [], "tests_run": ["python3 -m unittest"],
             "plan_approval_requested": False}
    if outcome == "not_reproduced":
        value.update(root_cause="", affected_paths=[], test_paths=[], invariant="", fix_size="none", fix_plan=[],
                     test_cases=[], probe="", untestable="",
                     note_path="docs/bugs/none-cells.json", conclusion="export() already writes None as empty.",
                     questions=["Which version is the reporter running?"])
    value.update(overrides)
    return value


class RoutingTests(unittest.TestCase):
    def test_a_recognized_bug_goes_to_the_investigator_and_remembers_the_build_entry(self):
        state = state_for()
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "bugfix", "reason": "", "signals": []}, {})
        self.assertEqual(bug_job.STAGE, state["next_stage"])
        self.assertEqual("requirements_gather", state["workflow"]["then"])
        self.assertEqual("autoresolver", autopilot.unit_for(bug_job.STAGE))
        self.assertIn(bug_job.STAGE, jobs.STAGES)


class PrepareTests(unittest.TestCase):
    def test_investigator_gets_its_own_route_and_a_scratch_copy_but_no_plan(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace)
            request = autoresolver.prepare(state, bug_job.STAGE, "/run/state.json", None)
        self.assertEqual(("astra", "investigator", True), (request.role, request.route_role, request.allow_write))
        self.assertEqual(state["settings"]["roles"]["astra"], state["settings"]["roles"]["investigator"])
        self.assertEqual(bug_job.SCHEMA, request.schema)
        self.assertIn("renew the same domain twice", request.prompt)
        self.assertEqual("INVESTIGATING", state["phase"])


class ApplyTests(unittest.TestCase):
    def apply(self, value, changed=()):
        workspace = tempfile.mkdtemp()
        state = state_for(workspace)
        bug_job.apply(state, value, {"changed_files": list(changed), "output": "/run/investigate_bug-01.json"}, workspace)
        return state, Path(workspace)

    def test_a_large_reproduced_bug_writes_the_diagnosis_and_goes_to_the_planner(self):
        state, workspace = self.apply(diagnosis(fix_size="large"))
        note = json.loads((workspace / "docs/bugs/duplicate-renew.json").read_text())
        self.assertEqual((True, []), (note["reproduced"], note["changed"]))
        for field in ("observed", "reproduction", "root_cause", "affected_paths", "invariant"):
            self.assertTrue(note[field], field)
        self.assertEqual(("RUNNING", "astra_discovery"), (state["status"], state["next_stage"]))
        self.assertIsNone(jobs.ended_in(state))
        self.assertEqual("docs/bugs/duplicate-renew.json", bug_job.large_correction(state)["note_path"])

    def test_a_report_that_does_not_reproduce_ends_the_run_with_questions(self):
        state, workspace = self.apply(diagnosis("not_reproduced"))
        note = json.loads((workspace / "docs/bugs/none-cells.json").read_text())
        self.assertEqual((False, []), (note["reproduced"], note["changed"]))
        self.assertEqual(["Which version is the reporter running?"], note["questions"])
        view = run_view.view(state)
        self.assertTrue(view["done"])
        self.assertEqual("bugfix", view["workflow"])
        self.assertIs(bug_job, jobs.ended_in(state))
        rendered = jobs.render(state, lambda _: "build completion")
        self.assertIn("NOT REPRODUCED", rendered)
        self.assertIn("Question for the reporter: Which version", rendered)

    def test_an_investigation_that_changed_the_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not change the repository.*epp/client.py"):
            self.apply(diagnosis(), changed=["epp/client.py", "docs/bugs/scratch.json"])

    def test_the_note_must_live_under_docs_bugs(self):
        for path in ("notes/x.json", "docs/bugs/x.md", "docs/bugs/../../etc.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "note_path"):
                self.apply(diagnosis(note_path=path))

    def test_a_reproduced_bug_needs_a_cause_paths_and_an_invariant(self):
        with self.assertRaisesRegex(ValueError, "root cause"):
            self.apply(diagnosis(invariant=""))
        with self.assertRaisesRegex(ValueError, "root cause"):
            self.apply(diagnosis(fix_size="none"))

    def test_a_report_that_did_not_reproduce_may_not_propose_a_fix(self):
        with self.assertRaisesRegex(ValueError, "must not propose a fix"):
            self.apply(diagnosis("not_reproduced", fix_plan=["strip the text None defensively"]))

    def test_an_investigation_must_say_what_it_tried(self):
        with self.assertRaisesRegex(ValueError, "what it ran"):
            self.apply(diagnosis(reproduction="  "))

    def test_a_reproduced_bug_needs_its_regression_tests_in_plain_english(self):
        with self.assertRaisesRegex(ValueError, "needs test_cases"):
            self.apply(diagnosis(test_cases=[]))
        with self.assertRaisesRegex(ValueError, "needs given, when and then"):
            self.apply(diagnosis(test_cases=[{**CASE, "then": " "}]))
        with self.assertRaisesRegex(ValueError, "unique"):
            self.apply(diagnosis(test_cases=[CASE, {**CASE, "id": "t1"}]))
        with self.assertRaisesRegex(ValueError, "short name"):
            self.apply(diagnosis(test_cases=[{**CASE, "id": "T 1"}]))
        with self.assertRaisesRegex(ValueError, "must not propose a fix"):
            self.apply(diagnosis("not_reproduced", test_cases=[CASE]))

    def test_the_note_and_the_planner_get_the_english_tests(self):
        state, workspace = self.apply(diagnosis(fix_size="large"))
        self.assertEqual([CASE], json.loads((workspace / "docs/bugs/duplicate-renew.json").read_text())["test_cases"])
        self.assertEqual([CASE], bug_job.large_correction(state)["test_cases"])
        self.assertEqual([CASE], bug_job.test_cases(state))

    def test_paths_must_stay_inside_the_repository(self):
        for path in ("/etc/passwd", "../outside.py", ".git/config"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "inside the repository"):
                self.apply(diagnosis(affected_paths=[path]))


class SmallCorrectionTests(unittest.TestCase):
    """A small reproduced bug becomes one Builder task, approved by the recorded policy, not the user.
    The short path is off by default (FullPathTests); these tests keep it working for when it returns."""

    def setUp(self):
        patcher = mock.patch.object(bug_job, "SMALL_CORRECTION_ENABLED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, **overrides):
        workspace = Path(tempfile.mkdtemp())
        (workspace / "epp").mkdir()
        (workspace / "epp" / "client.py").write_text("x = 1\n")
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *command],
                           cwd=workspace, check=True)
        state = {**state_for(str(workspace)), "task_id": "task-under-test", "iteration": 1, "answers": {},
                 "user_events": [], "history": [], "sessions": {}}
        autoresolver.apply_job(bug_job.STAGE, state, diagnosis(**overrides), {"changed_files": [], "output": "o"},
                               str(workspace))
        return state

    def test_the_diagnosis_becomes_an_approved_one_task_contract(self):
        import autocode_goals as goals
        state = self.start()
        contract = state["goal_contract"]
        self.assertEqual(bug_job.ORIGIN, contract["origin"])
        self.assertTrue(goals.approved(state))
        self.assertEqual(("workflow_policy", bug_job.SMALL_FIX_POLICY),
                         (contract["approval_event"]["actor"], contract["approval_event"]["policy"]))
        criterion, case = contract["body"]["acceptance_criteria"]
        self.assertEqual("one logical renew, at most one mutation", criterion["criterion"])
        self.assertEqual(bug_job.case_text(CASE), case["criterion"])
        task = state["current_task"]
        self.assertEqual(["epp/client.py", "tests/test_client.py", "docs/bugs/duplicate-renew.json"],
                         task["affected_paths"])
        self.assertIn("fails on the original code", " ".join(task["requirements"]))
        self.assertIn(state["next_stage"], ("terra", "orchestrator"))
        self.assertEqual("RUNNING", state["status"])

    def test_each_english_test_becomes_a_criterion_and_a_named_test(self):
        second = {"id": "T2", "given": "no timeout", "when": "renew('example.com') runs", "then": "1 mutation"}
        state = self.start(test_cases=[CASE, second])
        criteria = state["goal_contract"]["body"]["acceptance_criteria"]
        self.assertEqual(["C1", "C2", "C3"], [row["id"] for row in criteria])
        self.assertEqual(bug_job.case_text(CASE), criteria[1]["criterion"])
        self.assertIn("test_t1_", criteria[1]["verification_method"])
        task = state["current_task"]
        self.assertEqual(["C1", "C2", "C3"], task["acceptance_criteria"])
        self.assertIn("as a test named test_t2_", " ".join(task["requirements"]))

    def test_a_large_fix_is_not_auto_approved(self):
        state = self.start(fix_size="large")
        self.assertNotEqual(bug_job.ORIGIN, (state.get("goal_contract") or {}).get("origin"))
        # Planned from the diagnosis: no requirements gathering, but plan review and the user's approval.
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertIsNone(bug_job.large_correction({**state, "investigation": {**state["investigation"], "fix_size": "small"}}))

    def test_a_small_fix_the_user_asked_to_approve_is_planned_and_put_to_the_user(self):
        state = self.start(fix_size="small", plan_approval_requested=True)
        self.assertNotEqual(bug_job.ORIGIN, (state.get("goal_contract") or {}).get("origin"))
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertFalse(bug_job.small_correction(state))
        self.assertIsNotNone(bug_job.large_correction(state))

    def test_the_schema_requires_the_approval_flag(self):
        self.assertIn("plan_approval_requested", bug_job.SCHEMA["required"])
        self.assertEqual({"type": "boolean"}, bug_job.SCHEMA["properties"]["plan_approval_requested"])

    def test_the_planner_plans_a_large_fix_from_the_diagnosis(self):
        from units import autoplanner
        state = self.start(fix_size="large")
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        prompt, _ = autoplanner.context(state, "astra_discovery", Path(state["workspace"]) / "state.json")
        self.assertIn(autoplanner.BUG_DIAGNOSIS_RULE, prompt)
        self.assertIn('"bug_diagnosis"', prompt)
        self.assertIn(json.dumps(state["investigation"]["invariant"]), prompt)
        small, _ = autoplanner.context({**state, "investigation": {**state["investigation"], "fix_size": "small"}},
                                       "astra_discovery", Path(state["workspace"]) / "state.json")
        self.assertNotIn(autoplanner.BUG_DIAGNOSIS_RULE, small)

    def test_planning_is_told_how_execution_captures_evidence(self):
        from units import autoplanner
        state = self.start(fix_size="large")
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        state_path = Path(state["workspace"]) / "state.json"
        for stage in ("astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"):
            prompt, _ = autoplanner.context(state, stage, state_path)
            self.assertIn(autoplanner.EVIDENCE_FACTS, prompt, stage)
            self.assertIn('"capture_command"', prompt, stage)
            self.assertIn(" capture", json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])["capture_command"])
        requirements, _ = autoplanner.context(state, "requirements_gather", state_path)
        self.assertNotIn(autoplanner.EVIDENCE_FACTS, requirements)

    def test_the_policy_actor_cannot_approve_an_ordinary_contract(self):
        import autocode_goals as goals
        state = self.start()
        state["goal_contract"]["origin"] = "glm_draft"
        self.assertFalse(goals.approved(state))
        self.assertTrue(workflows.approval_actor_ok("anything", {"actor": "user_cli"}))
        self.assertFalse(workflows.approval_actor_ok("glm_draft", {"actor": "workflow_policy"}))


def approved_small_fix(**overrides):
    """A state whose one-task contract is approved and assigned: the short path is the quickest way there."""
    with mock.patch.object(bug_job, "SMALL_CORRECTION_ENABLED", True):
        return SmallCorrectionTests.start(SmallCorrectionTests(), **overrides)


class FullPathTests(unittest.TestCase):
    """With the short path off, a small reproduced bug is planned and put to the user like any other."""

    def test_a_small_fix_is_planned_and_put_to_the_user(self):
        self.assertFalse(bug_job.SMALL_CORRECTION_ENABLED)
        state = SmallCorrectionTests.start(SmallCorrectionTests(), fix_size="small")
        self.assertNotEqual(bug_job.ORIGIN, (state.get("goal_contract") or {}).get("origin"))
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertFalse(bug_job.small_correction(state))
        self.assertEqual("docs/bugs/duplicate-renew.json", bug_job.large_correction(state)["note_path"])


if __name__ == "__main__":
    unittest.main()


class CaseMatchTests(unittest.TestCase):
    """The runner links an English case to its test by name, with no model: T1 -> test_t1_..."""

    def test_ids_match_whole_words_in_any_runners_test_id(self):
        cases = [{"id": "T1"}, {"id": "T2"}, {"id": "reported_row"}]
        tests = ["tests.test_client.RenewTests.test_t1_one_mutation", "tests/test_x.py::test_t12_other",
                 "TestT2RenewsOnce", "tests/test_x.py::test_reported_row[a-b]"]
        self.assertEqual({"T1": ["tests.test_client.RenewTests.test_t1_one_mutation"], "T2": ["TestT2RenewsOnce"],
                          "reported_row": ["tests/test_x.py::test_reported_row[a-b]"]},
                         bug_job.match_cases(cases, tests))

    def test_the_proof_fails_a_case_with_no_test_of_its_own(self):
        import autocode_regression as regression
        proof = {"verdict": "PASS", "failures": [], "unverified": [],
                 "fail_to_pass": ["tests.test_client.RenewTests.test_t1_one_mutation"]}
        second = {"id": "T2", "given": "no timeout", "when": "renew() runs", "then": "1 mutation"}
        regression.check_cases(proof, [CASE, second])
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual({"T1": ["tests.test_client.RenewTests.test_t1_one_mutation"], "T2": []}, proof["case_tests"])
        [failure] = proof["failures"]
        self.assertIn("T2: Given no timeout", failure)
        self.assertIn("test_t2_", failure)

    def test_the_proof_passes_when_every_case_has_its_test(self):
        import autocode_regression as regression
        proof = {"verdict": "PASS", "failures": [], "unverified": [],
                 "fail_to_pass": ["tests.test_client.RenewTests.test_t1_one_mutation"]}
        regression.check_cases(proof, [CASE])
        self.assertEqual(("PASS", []), (proof["verdict"], proof["failures"]))

    def test_without_per_test_results_the_cases_are_unverified_not_passed(self):
        import autocode_regression as regression
        proof = {"verdict": "PASS", "failures": [], "unverified": [], "fail_to_pass": None}
        regression.check_cases(proof, [CASE])
        self.assertEqual(("UNVERIFIED", {"T1": []}), (proof["verdict"], proof["case_tests"]))

    def test_a_proof_that_already_failed_gets_no_misleading_note(self):
        import autocode_regression as regression
        proof = {"verdict": "FAIL", "failures": ["The regression tests fail on the candidate: x"], "unverified": [],
                 "fail_to_pass": None}
        regression.check_cases(proof, [CASE])
        self.assertEqual(("FAIL", [], {"T1": []}), (proof["verdict"], proof["unverified"], proof["case_tests"]))

    def test_runs_without_english_tests_are_unchanged(self):
        import autocode_regression as regression
        proof = {"verdict": "PASS", "failures": [], "unverified": [], "fail_to_pass": ["x.test_a"]}
        regression.check_cases(proof, [])
        self.assertEqual({"verdict": "PASS", "failures": [], "unverified": [], "fail_to_pass": ["x.test_a"]}, proof)
        self.assertEqual([], bug_job.test_cases({"investigation": {"outcome": "reproduced"}}))


class ReproductionProbeTests(unittest.TestCase):
    """The runner runs the Investigator's probe in a scratch copy; "reproduced" is not taken on trust."""

    BUGGY = "def page_count(total, size):\n    return total // size\n"
    SHOWS_BUG = "python3 -c 'from pager import page_count; assert page_count(5, 2) == 2'"

    def apply(self, **overrides):
        root = Path(tempfile.mkdtemp(prefix="bug-probe-"))
        (root / "pager").mkdir()
        (root / "pager" / "__init__.py").write_text(self.BUGGY)
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *command],
                           cwd=root, check=True)
        state = state_for(str(root))
        value = diagnosis(fix_size="large", affected_paths=["pager/__init__.py"], test_paths=["tests/test_pager.py"],
                          note_path="docs/bugs/page-count.json", **overrides)
        autoresolver.apply_job(bug_job.STAGE, state, value, {"changed_files": [], "output": str(root / "o.json")},
                               str(root))
        return state, root

    def test_a_probe_that_shows_the_bug_is_recorded_in_the_state_and_the_note(self):
        state, root = self.apply(probe=self.SHOWS_BUG, untestable="")
        self.assertEqual(0, state["investigation"]["probe_result"]["exit_code"])
        self.assertEqual(self.SHOWS_BUG, json.loads((root / "docs/bugs/page-count.json").read_text())["proven_by"])
        self.assertEqual(("RUNNING", "astra_discovery"), (state["status"], state["next_stage"]))
        self.assertEqual(self.BUGGY, (root / "pager" / "__init__.py").read_text())  # the workspace is untouched

    def test_a_probe_that_does_not_show_the_bug_rejects_the_reproduction(self):
        with self.assertRaisesRegex(ValueError, "reproduction claims' probes did not exit 0"):
            self.apply(probe="python3 -c 'from pager import page_count; assert page_count(5, 2) == 3'", untestable="")

    def test_a_reproduced_bug_needs_exactly_one_of_probe_and_untestable(self):
        with self.assertRaisesRegex(ValueError, "exactly one of probe"):
            self.apply(probe="", untestable="")
        with self.assertRaisesRegex(ValueError, "exactly one of probe"):
            self.apply(probe=self.SHOWS_BUG, untestable="needs a registry")

    def test_an_untestable_reproduction_says_why_and_runs_nothing(self):
        state, root = self.apply(probe="", untestable="The race needs a real registry; see tests_run")
        self.assertIsNone(state["investigation"]["probe_result"])
        self.assertEqual("", json.loads((root / "docs/bugs/page-count.json").read_text())["proven_by"])

    def test_a_report_that_did_not_reproduce_carries_no_probe(self):
        root = Path(tempfile.mkdtemp())
        with self.assertRaisesRegex(ValueError, "must not propose a fix"):
            bug_job.apply(state_for(str(root)), diagnosis("not_reproduced", untestable="x"),
                          {"changed_files": []}, str(root))
