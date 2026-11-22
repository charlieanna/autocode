"""A bug fix's milestone checkpoint gives its regression proof something that can fail first (#3962).

An approved bug-fix plan began with M0, a survey milestone holding one guard criterion. The Builder delivered
it, the guard passed on the original code and with the change, and the regression proof then failed with an
empty fail_to_pass: an uncoordinated bug fix owes a test that fails first (regression.required, preserve_only).
The plan check had approved a milestone the gate can never accept, and the Resolver asked the person to repair
the plan. The plan check and the gate now read one answer (autocode_fail_first): such a milestone is merged
into the next milestone with a test: criterion before approval, or refused when that cannot be done safely,
and a saved approved plan in that state goes back to planning without a person question.

These tests drive only interfaces that exist before the fix, so the guards also pass on the original code.
"""

from __future__ import annotations

import copy
import importlib
import json
import shutil
import signal
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import autocode_changeset_store as changesets
import autocode_completion_result as completion_result
import autocode_goal_lifecycle as lifecycle
import autocode_report_check as report_check
import autocode_resolver_human as human
import autocode_util as util
from goal_fixtures import body as fixture_body

PHRASE = "nothing that can fail first"


def criterion(cid, method):
    return {"id": cid, "criterion": f"{cid} holds", "verification_method": method, "human_review": False}


AC22 = criterion("AC22", "guard: test_ac22_existing_pages")
AC23 = criterion("AC23", "guard: test_ac23_fixture_pages")
AC1 = criterion("AC1", "test: test_ac1_last_page_counted")
AC2 = criterion("AC2", "Read docs/bugs/pager.md and confirm it names the last-page rule")
AC30 = criterion("AC30", "Read docs/bugs/pager.md and confirm it names the cause")


def milestone(mid, criteria, paths, depends_on):
    return {
        "id": mid,
        "objective": f"Deliver {mid}",
        "acceptance_criteria": list(criteria),
        "depends_on": list(depends_on),
        "affected_paths": list(paths),
    }


def draft(criteria, milestones, *, task_kind="bugfix"):
    value = fixture_body(task_kind=task_kind)
    value["acceptance_criteria"] = copy.deepcopy(criteria)
    value["milestones"] = copy.deepcopy(milestones)
    first = milestones[0]
    value["initial_task"] = {
        "objective": first["objective"],
        "affected_paths": list(first["affected_paths"]),
        "kind": "implement",
        "milestone_id": first["id"],
        "requirements": ["R1"],
        "acceptance_criteria": list(first["acceptance_criteria"]),
        "validation_plan": ["python3 -m unittest tests.test_pager"],
    }
    return value


def guard_first_draft(task_kind="bugfix"):
    """M0 holds only a guard; M1, which depends on it, holds the test: criterion."""
    return draft(
        [AC22, AC1],
        [
            milestone("M0", ["AC22"], ["tests/test_pager.py", "docs/"], []),
            milestone("M1", ["AC1"], ["pager/", "tests/test_pager.py"], ["M0"]),
        ],
        task_kind=task_kind,
    )


def unmergeable_draft():
    """M0 holds only a guard and no milestone holds a test: criterion."""
    return draft(
        [AC22, AC2],
        [
            milestone("M0", ["AC22"], ["tests/test_pager.py"], []),
            milestone("M1", ["AC2"], ["docs/bugs/pager.md", "pager/"], ["M0"]),
        ],
    )


def planning_state(**extra):
    return {
        "version": 3,
        "task_id": "task-3962",
        "task": "Fix the pager so the last partial page is counted",
        "workspace": "/absent-workspace",
        "status": "RUNNING",
        "next_stage": "draft_plan",
        "settings": {"joint_planning": True},
        "answers": {},
        "user_events": [],
        **extra,
    }


def shape(body):
    return [(row["id"], row["acceptance_criteria"], row["depends_on"]) for row in body["milestones"]]


AC24 = criterion("AC24", "guard: test_ac24_short_pages")
GUARD, TEST = {"id": "G", "kind": "preserve"}, {"id": "T"}


class Bounded(unittest.TestCase):
    """A dependency walk the Planner drew must finish: each test here fails, rather than hangs, when it does not.

    Defined first, so a walk that stops terminating fails here before a later test can hang on it."""

    LIMIT = 10

    def setUp(self):
        if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
            return

        def expire(signum, frame):
            raise TimeoutError(f"the plan check did not finish within {self.LIMIT} seconds")

        previous = signal.signal(signal.SIGALRM, expire)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        signal.setitimer(signal.ITIMER_REAL, self.LIMIT)
        self.addCleanup(signal.setitimer, signal.ITIMER_REAL, 0)


class CheckpointWalkTests(Bounded):
    """The shared answer (autocode_fail_first) the plan check and the gate read, on plan shapes beside #3962's."""

    def test_coverage_is_only_a_coordinated_bug_fix_checkpoint_of_guards(self):
        allowed = shared_answer().coverage_allowed
        self.assertIs(True, allowed("bugfix", True, [GUARD]))
        self.assertIs(True, allowed("bugfix", lambda: 1, [GUARD, GUARD]))
        self.assertIs(False, allowed("bugfix", False, [GUARD]))
        self.assertIs(False, allowed("bugfix", True, [GUARD, TEST]))
        self.assertIs(False, allowed("bugfix", True, []))
        self.assertIs(False, allowed("build", True, [GUARD]))
        asked = []
        self.assertIs(False, allowed("bugfix", lambda: asked.append(1) or True, [TEST]))
        self.assertEqual([], asked, "coordination is read only when the cases qualify")
        self.assertIs(True, shared_answer().owes_fail_first("bugfix", True, [TEST]))
        self.assertIs(False, shared_answer().owes_fail_first("bugfix", True, [GUARD]))
        self.assertIs(False, shared_answer().owes_fail_first("build", False, [GUARD]))

    def test_plans_the_gate_decides_from_the_diff_have_no_unprovable_milestone(self):
        unprovable = shared_answer().unprovable
        state = planning_state()
        test_files_only = draft(
            [AC22, AC23],
            [
                milestone("M0", ["AC22"], ["tests/test_pager.py"], []),
                milestone("M1", ["AC23"], ["tests/conftest.py"], ["M0"]),
            ],
        )
        single = draft([AC22], [milestone("M1", ["AC22"], ["pager/"], [])])
        for name, body in (
            ("build", guard_first_draft(task_kind="build")),
            ("not a plan", "M0"),
            ("one milestone", single),
            ("test files only", test_files_only),
        ):
            with self.subTest(name):
                self.assertEqual([], unprovable(state, body))
        self.assertIsNone(shared_answer().checkpoint_cases(state, single, "M1"))

    def test_the_checkpoint_at_which_every_criterion_is_due_is_left_to_the_gate(self):
        body = draft(
            [AC22, AC23],
            [milestone("M0", ["AC22"], ["pager/"], []), milestone("M1", ["AC23"], ["pager/"], ["M0"])],
        )
        self.assertIsNone(shared_answer().checkpoint_cases(planning_state(), body, "M1"))
        self.assertEqual(["M0"], shared_answer().unprovable(planning_state(), body))

    def test_a_checkpoint_counts_its_transitive_prerequisites(self):
        answer, state = shared_answer(), planning_state()
        chain = draft(
            [AC1, AC22, AC23, AC3],
            [
                milestone("M0", ["AC1"], ["pager/"], []),
                milestone("M1", ["AC22"], ["pager/"], ["M0"]),
                milestone("M2", ["AC23"], ["pager/"], ["M1"]),
                milestone("M3", ["AC3"], ["pager/"], ["M2"]),
            ],
        )
        self.assertEqual(["AC1", "AC22", "AC23"], [case["id"] for case in answer.checkpoint_cases(state, chain, "M2")])
        self.assertEqual([], answer.unprovable(state, chain))
        diamond = draft(
            [AC1, AC22, AC23, AC24, AC3],
            [
                milestone("M0", ["AC1"], ["pager/"], []),
                milestone("MA", ["AC22"], ["pager/"], ["M0"]),
                milestone("MB", ["AC23"], ["pager/"], ["M0"]),
                milestone("MC", ["AC24"], ["pager/"], ["MA", "MB"]),
                milestone("MD", ["AC3"], ["pager/"], ["MC"]),
            ],
        )
        due = sorted(case["id"] for case in answer.checkpoint_cases(state, diamond, "MC"))
        self.assertEqual(["AC1", "AC22", "AC23", "AC24"], due)
        self.assertEqual([], answer.unprovable(state, diamond))

    def test_an_unknown_dependency_or_a_drawn_cycle_does_not_stop_the_walk(self):
        answer, state = shared_answer(), planning_state()
        unknown = draft(
            [AC22, AC1, AC3],
            [
                milestone("M0", ["AC22"], ["pager/"], ["MZ"]),
                milestone("M1", ["AC1"], ["pager/"], ["M0", "MZ"]),
                milestone("M2", ["AC3"], ["pager/"], ["M1"]),
            ],
        )
        self.assertEqual(["AC22"], [case["id"] for case in answer.checkpoint_cases(state, unknown, "M0")])
        self.assertEqual(["M0"], answer.unprovable(state, unknown))
        with self.assertRaises(ValueError) as raised:
            answer.correct(state, unknown)
        self.assertEqual(
            answer.refusal("M0", "after the merge the initial task's milestone M1 has prerequisites"),
            str(raised.exception),
        )
        cycle = draft(
            [AC22, AC1, AC3],
            [
                milestone("M0", ["AC22"], ["pager/"], ["M1"]),
                milestone("M1", ["AC1"], ["pager/"], ["M0"]),
                milestone("M2", ["AC3"], ["pager/"], ["M1"]),
            ],
        )
        self.assertEqual(["AC22", "AC1"], [case["id"] for case in answer.checkpoint_cases(state, cycle, "M0")])
        self.assertEqual([], answer.unprovable(state, cycle))

    def test_a_self_dependency_the_planner_drew_is_left_to_validate_body(self):
        body = draft(
            [AC22, AC1],
            [milestone("M0", ["AC22"], ["pager/"], []), milestone("M1", ["AC1"], ["pager/"], ["M0", "M1"])],
        )
        self.assertEqual(["M0"], shared_answer().unprovable(planning_state(), body))
        corrected, merges = shared_answer().correct(planning_state(), body)
        self.assertIs(body, corrected)
        self.assertEqual([], merges)

    def test_every_unprovable_milestone_is_merged_in_turn(self):
        body = draft(
            [AC22, AC1, AC23, AC3],
            [
                milestone("M0", ["AC22"], ["pager/"], []),
                milestone("M1", ["AC1"], ["pager/"], ["M0"]),
                milestone("M2", ["AC23"], ["docs/notes.md"], []),
                milestone("M3", ["AC3"], ["lib/"], ["M2"]),
            ],
        )
        original = copy.deepcopy(body)
        corrected, merges = shared_answer().correct(planning_state(), body)
        self.assertEqual(original, body, "the Planner's draft itself is not changed")
        self.assertEqual([("M1", ["AC22", "AC1"], []), ("M3", ["AC23", "AC3"], [])], shape(corrected))
        self.assertEqual(
            [
                {"milestone": "M0", "into": "M1", "criteria": ["AC22"]},
                {"milestone": "M2", "into": "M3", "criteria": ["AC23"]},
            ],
            merges,
        )
        self.assertEqual("M1", corrected["initial_task"]["milestone_id"])

    def test_an_unprovable_milestone_nothing_depends_on_joins_the_next_later_milestone_with_a_test(self):
        body = draft(
            [AC1, AC22, AC3],
            [
                milestone("MA", ["AC1"], ["pager/"], []),
                milestone("M0", ["AC22"], ["tests/test_pager.py"], []),
                milestone("M1", ["AC3"], ["lib/"], []),
            ],
        )
        corrected, merges = shared_answer().correct(planning_state(), body)
        self.assertEqual([("MA", ["AC1"], []), ("M1", ["AC22", "AC3"], [])], shape(corrected))
        self.assertEqual([{"milestone": "M0", "into": "M1", "criteria": ["AC22"]}], merges)
        self.assertEqual(body["initial_task"], corrected["initial_task"])

    def test_the_merged_milestone_keeps_every_criterion_path_and_dependency(self):
        body = draft(
            [AC1, AC22, AC3, AC2],
            [
                milestone("MA", ["AC1"], ["lib/"], []),
                milestone("M0", ["AC22"], ["tests/test_pager.py", "docs/"], []),
                milestone("M1", ["AC3"], ["pager/", "tests/test_pager.py"], ["M0", "MA"]),
                milestone("M2", ["AC2"], ["docs/bugs/pager.md"], ["M0", "M1"]),
            ],
        )
        body["milestones"][2]["objective"] = "  "
        corrected, merges = shared_answer().correct(planning_state(), body)
        merged = corrected["milestones"][1]
        self.assertEqual(
            {
                "id": "M1",
                "objective": "Deliver M0",
                "acceptance_criteria": ["AC22", "AC3"],
                "depends_on": ["MA"],
                "affected_paths": ["tests/test_pager.py", "docs/", "pager/"],
            },
            merged,
        )
        self.assertEqual(["M1"], corrected["milestones"][2]["depends_on"])
        self.assertEqual(["MA", "M1", "M2"], [row["id"] for row in corrected["milestones"]])
        self.assertEqual("MA", corrected["initial_task"]["milestone_id"])

    def test_the_initial_task_follows_its_merged_milestone(self):
        body = guard_first_draft()
        body["initial_task"]["kind"] = "validate"
        corrected, _ = shared_answer().correct(planning_state(), body)
        first = corrected["initial_task"]
        self.assertEqual(
            {
                "kind": "implement",
                "milestone_id": "M1",
                "objective": "Deliver M0; Deliver M1",
                "acceptance_criteria": ["AC22", "AC1"],
                "affected_paths": ["tests/test_pager.py", "docs/", "pager/"],
            },
            {key: first[key] for key in ("kind", "milestone_id", "objective", "acceptance_criteria", "affected_paths")},
        )
        self.assertEqual(["R1"], first["requirements"])
        self.assertEqual(["python3 -m unittest tests.test_pager"], first["validation_plan"])

    def test_a_merge_that_would_draw_a_cycle_is_refused_with_its_reason(self):
        body = draft(
            [AC22, AC23, AC1],
            [
                milestone("MA", ["AC22"], ["pager/"], []),
                milestone("M0", ["AC23"], ["pager/"], ["MA"]),
                milestone("M1", ["AC1"], ["pager/"], ["M0"]),
            ],
        )
        with self.assertRaises(ValueError) as raised:
            shared_answer().correct(planning_state(), body)
        self.assertEqual(
            shared_answer().refusal("MA", "merging it into M1 would make the dependencies a cycle"),
            str(raised.exception),
        )

    def test_report_check_refuses_only_a_bug_fix_contract_it_cannot_correct(self):
        refuse = shared_answer().report_refusal
        self.assertIsNone(refuse({**unmergeable_draft(), "task_kind": "build"}, coordinated_run=False))
        self.assertIsNone(refuse("M0", coordinated_run=False))
        self.assertIsNone(refuse(guard_first_draft(), coordinated_run=False), "a mergeable draft is corrected")
        self.assertIsNone(refuse(unmergeable_draft(), coordinated_run=True))
        self.assertEqual(
            shared_answer().refusal("M0", "no other milestone has a test: criterion to merge it into"),
            refuse(unmergeable_draft(), coordinated_run=False),
        )

    def test_the_run_state_contract_names_the_record(self):
        import autocode_run_state as run_state

        self.assertIn(shared_answer().KEY, run_state.KEYS)
        self.assertEqual("fail_first_corrections", shared_answer().KEY)
        self.assertIn("fail_first_corrections", run_state.RunState.__annotations__)


class DraftCorrectionTests(unittest.TestCase):
    def install(self, body, state=None):
        state = state or planning_state()
        lifecycle.install_draft(state, body, origin="planner_draft")
        return state

    def test_ac1_guard_only_first_milestone_merged_into_fail_first_milestone(self):
        state = self.install(guard_first_draft())
        installed = state["goal_contract"]["body"]
        self.assertEqual([("M1", ["AC22", "AC1"], [])], shape(installed))
        only = installed["milestones"][0]
        self.assertEqual({"tests/test_pager.py", "docs/", "pager/"}, set(only["affected_paths"]))
        self.assertEqual("M1", installed["initial_task"]["milestone_id"])
        self.assertEqual(["AC22", "AC1"], installed["initial_task"]["acceptance_criteria"])
        self.assertEqual([], installed["open_blocking_questions"])
        [record] = state["fail_first_corrections"]
        self.assertEqual([("M0", "M1")], [(row["milestone"], row["into"]) for row in record["merged"]])

    def test_ac2_unmergeable_unprovable_bugfix_draft_refused(self):
        state = planning_state()
        with self.assertRaises(ValueError) as raised:
            lifecycle.install_draft(state, unmergeable_draft(), origin="planner_draft")
        self.assertIn("M0", str(raised.exception))
        self.assertIn(PHRASE, str(raised.exception))
        self.assertNotIn("goal_contract", state)

    def test_ac4_coordinated_guard_only_milestone_is_kept(self):
        root = Path(tempfile.mkdtemp(prefix="fail-first-member-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        workspace, run_dir = root / "workspace", root / "run"
        workspace.mkdir()
        run_dir.mkdir()
        environment = patch.dict("os.environ", {"AUTOCODE_HOME": str(root / "catalog")})
        environment.start()
        self.addCleanup(environment.stop)
        record = changesets.reserve(
            source_workspace=workspace,
            source_run_dir=run_dir,
            target="producer",
            workspace=root / "producer",
            brief="Own the actual product correction",
        )
        changesets.set_fields(record["id"], {"automatic_anchor": str(root / "anchor")})
        body = guard_first_draft()
        state = self.install(body, planning_state(workspace=str(workspace), run_dir=str(run_dir)))
        self.assertEqual(body["milestones"], state["goal_contract"]["body"]["milestones"])
        self.assertNotIn("fail_first_corrections", state)

    def test_ac5_doc_only_first_milestone_merged_into_fail_first_milestone(self):
        body = draft(
            [AC30, AC1],
            [
                milestone("M0", ["AC30"], ["docs/bugs/pager.md"], []),
                milestone("M1", ["AC1"], ["pager/", "tests/test_pager.py"], ["M0"]),
            ],
        )
        state = self.install(body)
        self.assertEqual([("M1", ["AC30", "AC1"], [])], shape(state["goal_contract"]["body"]))
        only = state["goal_contract"]["body"]["milestones"][0]
        self.assertEqual({"docs/bugs/pager.md", "pager/", "tests/test_pager.py"}, set(only["affected_paths"]))

    def test_ac6_guard_only_milestone_after_fail_first_prerequisite_is_kept(self):
        body = draft(
            [AC1, AC22],
            [
                milestone("M0", ["AC1"], ["pager/", "tests/test_pager.py"], []),
                milestone("M1", ["AC22"], ["tests/test_pager.py"], ["M0"]),
            ],
        )
        state = self.install(body)
        installed = state["goal_contract"]["body"]
        self.assertEqual([("M0", ["AC1"], []), ("M1", ["AC22"], ["M0"])], shape(installed))
        self.assertNotIn("fail_first_corrections", state)

    def test_ac7_build_guard_only_first_milestone_is_kept(self):
        body = guard_first_draft(task_kind="build")
        state = self.install(body)
        self.assertEqual(body["milestones"], state["goal_contract"]["body"]["milestones"])

    def test_ac12_single_guard_only_bugfix_milestone_is_kept(self):
        body = draft([AC22], [milestone("M1", ["AC22"], ["pager/", "tests/test_pager.py"], [])])
        state = self.install(body)
        self.assertEqual([("M1", ["AC22"], [])], shape(state["goal_contract"]["body"]))
        self.assertNotIn("fail_first_corrections", state)

    def test_ac13_test_files_only_bugfix_plan_is_kept(self):
        body = draft(
            [AC22, AC23],
            [
                milestone("M0", ["AC22"], ["tests/test_pager.py"], []),
                milestone("M1", ["AC23"], ["tests/conftest.py", "tests/test_pager.py"], ["M0"]),
            ],
        )
        state = self.install(body)
        self.assertEqual(body["milestones"], state["goal_contract"]["body"]["milestones"])
        self.assertNotIn("fail_first_corrections", state)


class SavedPlan:
    """A run approved before the fix, saved with its current task on the plan's first milestone."""

    def saved(self, body):
        root = Path(tempfile.mkdtemp(prefix="fail-first-saved-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        workspace, run_dir = root / "workspace", root / "run"
        workspace.mkdir()
        run_dir.mkdir()
        contract = {"task_id": "task-3962", "revision": 1, "body": copy.deepcopy(body)}
        contract["hash"] = util.digest(contract)
        approval = {"kind": "goal_approval", "token": f"r1:{contract['hash']}", "actor": "user_cli", "at": util.now()}
        contract.update(approval_status="approved", approval_event=approval, origin="finalize_plan")
        first = body["milestones"][0]
        state = {
            **planning_state(workspace=str(workspace), run_dir=str(run_dir)),
            "status": "RUNNING",
            "phase": "EXECUTING",
            "iteration": 4,
            "next_stage": "resolve",
            "stages": [],
            "history": [],
            "pending_questions": [],
            "user_events": [approval],
            "goal_contract": contract,
            "acceptance_criteria": [
                {"id": row["id"], "criterion": row["criterion"], "status": "unverified", "evidence": ""}
                for row in body["acceptance_criteria"]
            ],
            "current_task": {
                "id": "task-m0",
                "kind": "implement",
                "milestone_id": first["id"],
                "objective": first["objective"],
                "affected_paths": list(first["affected_paths"]),
                "requirements": ["R1"],
                "acceptance_criteria": list(first["acceptance_criteria"]),
                "validation_plan": ["python3 -m unittest tests.test_pager"],
                "contract_hash": contract["hash"],
                "contract_revision": 1,
            },
        }
        state["settings"] = {}
        return state, workspace, run_dir


class SavedPlanTests(SavedPlan, unittest.TestCase):
    """A run approved before the fix is sent back to planning before the next stage launches."""

    def test_ac3_saved_approved_guard_only_milestone_sent_back_without_person_question(self):
        state, workspace, run_dir = self.saved(guard_first_draft())
        self.assertIs(True, completion_result.before_review(state, "resolve", workspace, run_dir))
        contract = state["goal_contract"]
        self.assertEqual(
            (2, "draft", "fail_first_correction"),
            (contract["revision"], contract["approval_status"], contract["origin"]),
        )
        self.assertEqual([("M1", ["AC22", "AC1"], [])], shape(contract["body"]))
        self.assertEqual("review_plan", state["next_stage"])
        self.assertEqual([], state["pending_questions"])
        self.assertFalse(state.get(human.PUBLIC))

    def test_ac14_saved_approved_unmergeable_milestone_sent_to_planner_without_person_question(self):
        state, workspace, run_dir = self.saved(unmergeable_draft())
        self.assertIs(True, completion_result.before_review(state, "resolve", workspace, run_dir))
        event = state["user_events"][-1]
        self.assertEqual(("technical_decision", "autocode"), (event["kind"], event["actor"]))
        self.assertIn("M0", event["text"])
        self.assertIn(PHRASE, event["text"])
        self.assertEqual("draft_plan", state["next_stage"])
        self.assertEqual([], state["pending_questions"])
        self.assertFalse(state.get(human.PUBLIC))


class ReportCheckTests(unittest.TestCase):
    def test_ac15_report_check_exposes_unprovable_bugfix_refusal(self):
        report = json.dumps({"contract": unmergeable_draft()})
        spec = {"stage": "draft_plan", "schema": {}, "fail_first": {"coordinated": False}}
        errors = report_check.check_report(report, spec=spec)
        self.assertTrue(any("M0" in error and PHRASE in error for error in errors), errors)
        spec["fail_first"] = {"coordinated": True}
        errors = report_check.check_report(report, spec=spec)
        self.assertFalse(any(PHRASE in error for error in errors), errors)
        state = planning_state(workflow={"kind": "bugfix"})
        built = report_check.build_spec(stage="draft_plan", workspace=Path("/absent-workspace"), schema={}, state=state)
        self.assertEqual({"coordinated": False}, built.get("fail_first"))


def shared_answer():
    """The shared module, imported when a test runs: the guards above must load on the original code."""
    import autocode_fail_first

    return autocode_fail_first


AC3 = criterion("AC3", "test: test_ac3_first_page_counted")


class MergeShapeTests(unittest.TestCase):
    def test_a_guard_only_milestone_with_no_dependent_joins_the_next_milestone_with_a_test(self):
        body = draft(
            [AC22, AC1],
            [milestone("M0", ["AC22"], ["tests/test_pager.py"], []), milestone("M1", ["AC1"], ["pager/"], [])],
        )
        state = planning_state()
        lifecycle.install_draft(state, body, origin="planner_draft")
        self.assertEqual([("M1", ["AC22", "AC1"], [])], shape(state["goal_contract"]["body"]))
        self.assertEqual("Deliver M0; Deliver M1", state["goal_contract"]["body"]["milestones"][0]["objective"])

    def test_other_dependents_of_the_merged_milestone_follow_it(self):
        body = draft(
            [AC22, AC1, AC3],
            [
                milestone("M0", ["AC22"], ["tests/test_pager.py"], []),
                milestone("M1", ["AC1"], ["pager/"], ["M0"]),
                milestone("M2", ["AC3"], ["pager/"], ["M0"]),
            ],
        )
        state = planning_state()
        lifecycle.install_draft(state, body, origin="planner_draft")
        self.assertEqual([("M1", ["AC22", "AC1"], []), ("M2", ["AC3"], ["M1"])], shape(state["goal_contract"]["body"]))

    def test_a_dependent_of_the_merged_milestone_keeps_its_other_dependencies(self):
        body = draft(
            [AC22, AC3, AC1, AC24],
            [
                milestone("MX", ["AC3"], ["pager/"], []),
                milestone("M0", ["AC22"], ["tests/test_pager.py"], []),
                milestone("M1", ["AC1"], ["pager/"], ["M0"]),
                milestone("M2", ["AC24"], ["pager/"], ["M0", "MX"]),
            ],
        )
        state = planning_state()
        lifecycle.install_draft(state, body, origin="planner_draft")
        self.assertEqual(
            [("MX", ["AC3"], []), ("M1", ["AC22", "AC1"], []), ("M2", ["AC24"], ["M1", "MX"])],
            shape(state["goal_contract"]["body"]),
        )

    def test_an_unsafe_merge_refuses_the_draft_with_the_correction(self):
        cycle = draft(
            [AC22, AC1, AC2],
            [
                milestone("M0", ["AC22"], ["tests/test_pager.py"], []),
                milestone("M1", ["AC1"], ["pager/"], ["M2"]),
                milestone("M2", ["AC2"], ["docs/bugs/pager.md"], ["M0"]),
            ],
        )
        prerequisites = draft(
            [AC22, AC3, AC1],
            [
                milestone("M0", ["AC22"], ["tests/test_pager.py"], []),
                milestone("MX", ["AC3"], ["pager/"], []),
                milestone("M1", ["AC1"], ["pager/"], ["M0", "MX"]),
            ],
        )
        for body, why in ((cycle, "cycle"), (prerequisites, "has prerequisites")):
            with self.subTest(why=why):
                state = planning_state()
                with self.assertRaises(ValueError) as raised:
                    lifecycle.install_draft(state, body, origin="planner_draft")
                self.assertIn(PHRASE, str(raised.exception))
                self.assertIn(why, str(raised.exception))
                self.assertNotIn("goal_contract", state)
        progressive = guard_first_draft()
        progressive["constraints"] = ["Progressive delegation: the Planner sizes each slice"]
        with self.assertRaisesRegex(ValueError, "a progressive plan is not merged"):
            shared_answer().correct(planning_state(), progressive)

    def test_a_validate_initial_task_left_on_a_milestone_with_prerequisites_refuses_the_draft(self):
        body = draft(
            [AC22, AC1, AC3],
            [
                milestone("M0", ["AC22"], ["tests/test_pager.py"], []),
                milestone("M1", ["AC1"], ["pager/"], ["M0"]),
                milestone("M2", ["AC3"], ["pager/"], ["M1"]),
            ],
        )
        body["initial_task"].update(
            kind="validate", milestone_id="M2", affected_paths=["pager/"], acceptance_criteria=["AC3"]
        )
        state = planning_state()
        with self.assertRaises(ValueError) as raised:
            lifecycle.install_draft(state, body, origin="planner_draft")
        self.assertIn("M0", str(raised.exception))
        self.assertIn(PHRASE, str(raised.exception))
        self.assertNotIn("goal_contract", state)
        self.assertEqual(
            shared_answer().refusal("M0", "after the merge the initial task's milestone M2 has prerequisites"),
            shared_answer().report_refusal(body, coordinated_run=False),
        )

    def test_a_cycle_the_planner_drew_keeps_its_own_refusal(self):
        body = draft(
            [AC22, AC1],
            [milestone("M0", ["AC22"], ["tests/test_pager.py"], ["M1"]), milestone("M1", ["AC1"], ["pager/"], ["M0"])],
        )
        self.assertEqual((body, []), shared_answer().correct(planning_state(), body))

    def test_a_plan_no_planner_drafted_is_refused_rather_than_merged(self):
        for body in (guard_first_draft(), unmergeable_draft()):
            with self.subTest(milestones=shape(body)):
                with self.assertRaisesRegex(ValueError, "Milestone M0 .*" + PHRASE):
                    lifecycle.install_draft(planning_state(), body, origin="fixture")

    def test_report_check_judges_only_a_bug_fix_contract(self):
        report = json.dumps({"contract": {**unmergeable_draft(), "task_kind": "build"}})
        spec = {"stage": "draft_plan", "schema": {}, "fail_first": {"coordinated": False}}
        self.assertFalse(any(PHRASE in error for error in report_check.check_report(report, spec=spec)))


class SendBackGuardTests(SavedPlan, unittest.TestCase):
    def test_a_plan_that_does_not_qualify_is_left_as_it_was(self):
        cases = {
            "build": lambda state: state["goal_contract"]["body"].update(task_kind="build"),
            "unapproved": lambda state: state["goal_contract"].update(approval_status="draft"),
            "sent back already": lambda state: state.update(fail_first_corrections=[{"contract_revision": 1}]),
            "accepted": lambda state: state.update(
                milestone_progress={
                    "M0": {"id": "M0", "accepted": True, "contract_hash": state["goal_contract"]["hash"]}
                }
            ),
        }
        for name, change in cases.items():
            with self.subTest(name):
                state, _, _ = self.saved(guard_first_draft())
                change(state)
                if name == "build":  # the body changed: seal it again
                    contract = state["goal_contract"]
                    contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
                    contract["approval_event"]["token"] = f"r1:{contract['hash']}"
                before = copy.deepcopy(state)
                self.assertFalse(shared_answer().send_back(state, lifecycle.install_draft))
                self.assertEqual(before, state)
        state, _, _ = self.saved(
            draft(
                [AC1, AC22],
                [milestone("M0", ["AC1"], ["pager/"], []), milestone("M1", ["AC22"], ["tests/test_pager.py"], ["M0"])],
            )
        )
        self.assertFalse(shared_answer().send_back(state, lifecycle.install_draft))

    def test_a_correction_that_cannot_be_installed_goes_to_the_planner(self):
        state, _, _ = self.saved(guard_first_draft())

        def refuse(*args, **kwargs):
            raise ValueError("the corrected draft is refused for another reason")

        self.assertTrue(shared_answer().send_back(state, refuse))
        self.assertEqual(1, state["goal_contract"]["revision"])
        self.assertIn(PHRASE, state["user_events"][-1]["text"])
        self.assertEqual(
            ("draft_plan", "planner"), (state["next_stage"], state["fail_first_corrections"][0]["outcome"])
        )
        self.assertNotIn("current_task", state)

    def test_the_v2_flow_restarts_at_requirements(self):
        state, workspace, run_dir = self.saved(unmergeable_draft())
        state["settings"] = {"planning_flow": "v2"}
        self.assertIs(True, completion_result.before_review(state, "builder", workspace, run_dir))
        self.assertEqual(
            ("RUNNING", "DISCOVERING", "requirements"), (state["status"], state["phase"], state["next_stage"])
        )

    def test_a_planning_stage_launches_with_the_saved_plan(self):
        state, workspace, run_dir = self.saved(guard_first_draft())
        state["next_stage"] = "review_plan"
        self.assertFalse(completion_result.before_review(state, "review_plan", workspace, run_dir))
        self.assertEqual(
            (1, "approved"), (state["goal_contract"]["revision"], state["goal_contract"]["approval_status"])
        )

    def test_a_plan_that_does_not_qualify_launches_the_next_stage_unchanged(self):
        for name in ("build", "accepted"):
            with self.subTest(name):
                state, workspace, run_dir = self.saved(
                    guard_first_draft(task_kind=name if name == "build" else "bugfix")
                )
                if name == "accepted":
                    progress = {"id": "M0", "accepted": True, "contract_hash": state["goal_contract"]["hash"]}
                    state["milestone_progress"] = {"M0": progress}
                before = copy.deepcopy(state)
                self.assertIs(False, completion_result.before_review(state, "builder", workspace, run_dir))
                self.assertEqual(before, state)


def timestamp(value):
    """A recorded time, as util.now() writes it."""
    return datetime.fromisoformat(value)


class SendBackRecordTests(SavedPlan, unittest.TestCase):
    """What a saved plan's send-back records, so reviewers and the report can show the decision and its reason."""

    def test_a_corrected_plan_records_its_merge_and_goes_to_plan_review(self):
        state, _, _ = self.saved(guard_first_draft())
        self.assertIs(True, shared_answer().send_back(state, lifecycle.install_draft))
        [record] = state["fail_first_corrections"]
        timestamp(record.pop("at"))
        self.assertEqual(
            {
                "contract_revision": 1,
                "milestones": ["M0"],
                "merged": [{"milestone": "M0", "into": "M1", "criteria": ["AC22"]}],
                "outcome": "plan_review",
            },
            record,
        )
        contract = state["goal_contract"]
        self.assertEqual(
            [
                {
                    "item": "M1",
                    "change": "reworded",
                    "basis": "runner_proof",
                    "answer_id": "",
                    "replacement": "merged M0 into M1: "
                    + shared_answer().refusal("M0")
                    + " The merged milestone keeps every criterion and path of both.",
                }
            ],
            contract["declared_changes"],
        )
        self.assertEqual(
            (2, "draft", "fail_first_correction"),
            (contract["revision"], contract["approval_status"], contract["origin"]),
        )
        self.assertEqual("review_plan", state["next_stage"])
        self.assertNotIn("current_task", state)
        self.assertFalse(any(event.get("kind") == "technical_decision" for event in state["user_events"]))
        self.assertIs(False, shared_answer().send_back(state, lifecycle.install_draft), "the draft awaits review")

    def test_an_unsafe_merge_is_autocodes_own_decision_and_the_planner_drafts_again(self):
        state, _, _ = self.saved(unmergeable_draft())
        state.update(
            planning={},
            next_action="Repair M0",
            user_request={"kind": "goal_change"},
            **{human.PRIVATE: {"origin": {}}, human.PUBLIC: {"question": "Fold M0 into M1?"}},
        )
        token = f"r1:{state['goal_contract']['hash']}"
        self.assertIs(True, shared_answer().send_back(state, lifecycle.install_draft))
        [record] = state["fail_first_corrections"]
        timestamp(record.pop("at"))
        self.assertEqual(
            {"contract_revision": 1, "milestones": ["M0"], "merged": [], "outcome": "planner"},
            record,
        )
        event = state["user_events"][-1]
        timestamp(event.pop("at"))
        self.assertEqual(
            {
                "kind": "technical_decision",
                "actor": "autocode",
                "settled_as": "fail_first",
                "milestones": ["M0"],
                "text": shared_answer().refusal("M0", "no other milestone has a test: criterion to merge it into"),
                "reason": "The approved bug-fix plan has a milestone the regression proof can never accept, and the "
                "runner cannot merge it safely, so the Planner corrects the plan and the person approves the "
                "corrected plan.",
                "contract_token": token,
            },
            event,
        )
        self.assertIs(True, state["planning"]["internal_answer_restart"])
        for key in ("next_action", "user_request", human.PRIVATE, human.PUBLIC, "current_task"):
            self.assertNotIn(key, state)
        self.assertEqual(["task-m0"], [row["id"] for row in state["task_archive"]])
        self.assertEqual(
            ("RUNNING", "DISCOVERING", "draft_plan", []),
            (state["status"], state["phase"], state["next_stage"], state["pending_questions"]),
        )
        self.assertEqual(
            (1, "approved"), (state["goal_contract"]["revision"], state["goal_contract"]["approval_status"])
        )
        before = copy.deepcopy(state)
        self.assertIs(False, shared_answer().send_back(state, lifecycle.install_draft), "once per contract revision")
        self.assertEqual(before, state)


class PreserveOnlyTests(unittest.TestCase):
    """The regression gate reads the same answer: guard-only coverage only for an approved coordinated member."""

    def state(self, criteria, *, task_kind="bugfix", approval_status="approved"):
        root = Path(tempfile.mkdtemp(prefix="fail-first-gate-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        workspace, run_dir = root / "workspace", root / "run"
        workspace.mkdir()
        run_dir.mkdir()
        environment = patch.dict("os.environ", {"AUTOCODE_HOME": str(root / "catalog")})
        environment.start()
        self.addCleanup(environment.stop)
        body = {"task_kind": task_kind, "acceptance_criteria": list(criteria), "milestones": [{"id": "M1"}]}
        contract = {"body": body, "approval_status": approval_status, "hash": "fail-first-gate", "revision": 1}
        return {"settings": {}, "workspace": str(workspace), "run_dir": str(run_dir), "goal_contract": contract}

    def coordinated(self, state):
        """Make the run a bound automatic member of a coordinated change, as test_ac4 does."""
        run_dir = Path(state["run_dir"])
        record = changesets.reserve(
            source_workspace=Path(state["workspace"]),
            source_run_dir=run_dir,
            target="producer",
            workspace=run_dir.parent / "producer",
            brief="Own the actual product correction",
        )
        changesets.set_fields(record["id"], {"automatic_anchor": str(run_dir.parent / "anchor")})
        return record

    def test_an_approved_coordinated_member_of_guards_is_coverage(self):
        import autocode_regression as regression

        state = self.state([AC22, AC23])
        self.assertIs(False, regression.preserve_only(state), "an uncoordinated bug fix owes a fail-first proof")
        record = self.coordinated(state)
        self.assertIs(True, regression.preserve_only(state))
        state["goal_contract"]["approval_status"] = "draft"
        self.assertIs(False, regression.preserve_only(state))
        state["goal_contract"]["approval_status"] = "approved"
        changesets.set_fields(record["id"], {"automatic_anchor": None})
        self.assertIs(False, regression.preserve_only(state))

    def test_a_coordinated_member_with_a_test_case_owes_its_proof(self):
        import autocode_regression as regression

        state = self.state([AC22, AC1])
        self.coordinated(state)
        self.assertIs(False, regression.preserve_only(state))

    def test_a_draft_coordinated_member_owes_its_proof(self):
        import autocode_regression as regression

        state = self.state([AC22], approval_status="draft")
        self.coordinated(state)
        self.assertIs(False, regression.preserve_only(state))

    def test_a_build_of_guards_is_coverage_whatever_its_coordination(self):
        import autocode_regression as regression

        self.assertIs(True, regression.preserve_only(self.state([AC22], task_kind="build")))
        self.assertIs(True, regression.preserve_only(self.state([AC22], task_kind="build", approval_status="draft")))


class RecordedWordsTests(SavedPlan, unittest.TestCase):
    """The words and records the correction leaves: what the Planner reads, and which milestones count as done."""

    def test_the_planner_reads_the_whole_correction(self):
        self.assertEqual(
            "Milestone M0 gives its checkpoint's regression proof nothing that can fail first: in a bug fix every "
            "milestone checkpoint must show a test that fails on the original code and passes with the change, and "
            "the criteria due at M0 (its own and those of the milestones it depends on) name no test: criterion "
            "(no other milestone has a test: criterion to merge it into). Merge M0 into a milestone that has a "
            "test: criterion, or plan the work as one milestone.",
            shared_answer().refusal("M0", "no other milestone has a test: criterion to merge it into"),
        )
        self.assertEqual(
            "Milestone MX gives its checkpoint's regression proof nothing that can fail first: in a bug fix every "
            "milestone checkpoint must show a test that fails on the original code and passes with the change, and "
            "the criteria due at MX (its own and those of the milestones it depends on) name no test: criterion. "
            "Merge MX into a milestone that has a test: criterion, or plan the work as one milestone.",
            shared_answer().refusal("MX"),
        )
        state = planning_state()
        with self.assertRaises(ValueError) as raised:
            lifecycle.install_draft(state, unmergeable_draft(), origin="planner_draft")
        self.assertIn(
            "the criteria due at M0 (its own and those of the milestones it depends on) name no test: criterion "
            "(no other milestone has a test: criterion to merge it into). Merge M0 into a milestone that has a "
            "test: criterion, or plan the work as one milestone.",
            str(raised.exception),
        )

    def test_a_milestone_a_batch_accepted_is_not_sent_back(self):
        state, _, _ = self.saved(guard_first_draft())
        state["milestone_progress"] = {
            "batch-1": {
                "id": "batch-1",
                "milestone_ids": ["M0"],
                "accepted": True,
                "contract_hash": state["goal_contract"]["hash"],
            }
        }
        before = copy.deepcopy(state)
        self.assertIs(False, shared_answer().send_back(state, lifecycle.install_draft))
        self.assertEqual(before, state)
        # The same batch under an older contract accepted nothing of this one.
        state["milestone_progress"]["batch-1"]["contract_hash"] = "older"
        self.assertIs(True, shared_answer().send_back(state, lifecycle.install_draft))
        self.assertEqual("plan_review", state["fail_first_corrections"][0]["outcome"])

    def test_a_saved_plan_checked_before_the_git_status_rule_still_goes_to_plan_review(self):
        body = guard_first_draft()
        step = "python3 -m unittest tests.test_pager && git status --short"
        body["initial_task"]["validation_plan"] = [step]
        state, _, _ = self.saved(body)
        with self.assertRaisesRegex(ValueError, "git status"):
            lifecycle.install_draft(planning_state(), body, origin="planner_draft")
        self.assertIs(True, shared_answer().send_back(state, lifecycle.install_draft))
        self.assertEqual("plan_review", state["fail_first_corrections"][0]["outcome"])
        contract = state["goal_contract"]
        self.assertEqual(
            (2, "draft", "fail_first_correction"),
            (contract["revision"], contract["approval_status"], contract["origin"]),
        )
        self.assertEqual([step], contract["body"]["initial_task"]["validation_plan"])
        self.assertEqual("review_plan", state["next_stage"])
        self.assertFalse(any(event.get("kind") == "technical_decision" for event in state["user_events"]))

    def test_the_install_record_names_the_origin_that_drafted_the_plan(self):
        for origin in ("planner_draft", "draft_plan"):
            with self.subTest(origin=origin):
                state = planning_state()
                lifecycle.install_draft(state, guard_first_draft(), origin=origin)
                [record] = state["fail_first_corrections"]
                self.assertEqual(state["goal_contract"]["created_at"], record.pop("at"))
                self.assertEqual(
                    {
                        "contract_revision": 1,
                        "origin": origin,
                        "merged": [{"milestone": "M0", "into": "M1", "criteria": ["AC22"]}],
                    },
                    record,
                )


def packaged(name):
    """``name`` imported as the installed CLI imports it, inside its package (autocode_cli is tools/ installed),
    so each module's relative imports are the ones that run."""
    return importlib.import_module("tools." + name)


class PackagedImportTests(SavedPlan, unittest.TestCase):
    """The installed package runs the same plan check, send-back and gate through its relative imports."""

    def setUp(self):
        home = tempfile.mkdtemp(prefix="fail-first-home-")
        self.addCleanup(shutil.rmtree, home, True)
        environment = patch.dict("os.environ", {"AUTOCODE_HOME": home})
        environment.start()
        self.addCleanup(environment.stop)

    def test_the_installed_package_sends_a_saved_plan_back_corrected(self):
        state, workspace, run_dir = self.saved(guard_first_draft())
        self.assertIs(True, packaged("autocode_completion_result").before_review(state, "resolve", workspace, run_dir))
        contract = state["goal_contract"]
        self.assertEqual((2, "fail_first_correction"), (contract["revision"], contract["origin"]))
        self.assertEqual([("M1", ["AC22", "AC1"], [])], shape(contract["body"]))
        self.assertEqual("review_plan", state["next_stage"])

    def test_the_installed_package_sends_an_unmergeable_saved_plan_to_the_planner(self):
        state, workspace, run_dir = self.saved(unmergeable_draft())
        self.assertIs(True, packaged("autocode_completion_result").before_review(state, "builder", workspace, run_dir))
        event = state["user_events"][-1]
        self.assertEqual(("technical_decision", "fail_first"), (event["kind"], event["settled_as"]))
        self.assertEqual("draft_plan", state["next_stage"])

    def test_the_installed_package_corrects_a_planner_draft_at_install(self):
        state = planning_state()
        packaged("autocode_goal_lifecycle").install_draft(state, guard_first_draft(), origin="planner_draft")
        self.assertEqual([("M1", ["AC22", "AC1"], [])], shape(state["goal_contract"]["body"]))

    def test_the_installed_package_gate_reads_the_shared_answer(self):
        body = {"task_kind": "bugfix", "acceptance_criteria": [AC22], "milestones": [{"id": "M1"}]}
        state = {
            "settings": {},
            "workspace": "/absent-workspace",
            "run_dir": "/absent-run",
            "goal_contract": {"body": body, "approval_status": "approved", "hash": "h", "revision": 1},
        }
        self.assertIs(False, packaged("autocode_regression").preserve_only(state))

    def test_the_installed_package_report_check_shows_the_refusal(self):
        report = json.dumps({"contract": unmergeable_draft()})
        spec = {"stage": "draft_plan", "schema": {}, "fail_first": {"coordinated": False}}
        errors = packaged("autocode_report_check").check_report(report, spec=spec)
        self.assertTrue(any("M0" in error and PHRASE in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
