"""Program runs under the program agreement (#22, #23), end to end through ``autocode program``.

Real Git worktrees and merges with a scripted child run, as in test_program: approval of the
agreement, per-workstream pins, rejected plans that drop an inherited requirement, revisions
that retire only the affected workstreams, interface change requests, the walking-skeleton
gate, cumulative re-verification after each merge, and the final check by journey name.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path

from .test_program import ProgramHarness, git, manifest, program, with_requirements


class AgreementTests(ProgramHarness):
    """The program agreement (#22, #23): approval, pins, inheritance, revisions, interfaces, skeleton, journeys."""

    def records(self, result):
        return {row["id"]: row for row in result["workstreams"]}

    def launches_of(self, wid):
        return [row for row in self.launches if row["id"] == wid]

    def request_change(self, path, interface, by):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, program.cli(["request-change", str(path), "--workspace", str(self.project),
                                             "--interface", interface, "--by", by, "--reason", "It is wrong"]))
        return json.loads(output.getvalue())["change_request"]

    def status(self, path):
        output = io.StringIO()
        with self.scripted_invocations(), contextlib.redirect_stdout(output):
            self.assertEqual(0, program.cli(["status", str(path), "--workspace", str(self.project)]))
        return json.loads(output.getvalue())

    def test_each_workstream_is_a_normal_run_pinned_to_the_agreement_it_was_built_from(self):
        value = with_requirements(manifest())
        path = self.write_manifest(value)
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        approved = program.validate_manifest(copy.deepcopy(value))
        for wid, record in self.records(result).items():
            pin = {"revision": 1, "scope": program.agreement.scope_digest(approved, wid)}
            self.assertEqual(pin, record["pin"], wid)
            self.assertEqual(pin, record["merged_under"], wid)
        self.assertEqual(1, result["agreement"]["revision"])
        self.assertTrue(all(row["in_place"] and not row["resume"] for row in self.launches))
        # A workstream is a build job by construction; its brief never decides the workflow.
        self.assertEqual({"build"}, {row["workflow"] for row in self.launches})
        brief = self.launches_of("a")[0]["brief"]
        self.assertIn("Program agreement revision 1, approved by the user.", brief)
        self.assertIn("with exactly this id (C2)", brief)
        self.assertIn("check only what the finished product keeps", brief)
        skeleton = self.launches_of("contracts")[0]["brief"]
        self.assertIn("Its own end-to-end flow is the part of each journey its objective covers", skeleton)
        self.assertNotIn("check only what the finished product keeps", self.launches_of("integration")[0]["brief"])
        integration_brief = self.launches_of("integration")[0]["brief"]
        self.assertIn("with exactly this id (C1, C2, C3, J1)", integration_brief)
        # Its own row lists no requirement, yet it keeps each by id: it is told what each one says.
        for line in ("- C1: Contracts exist", "- C2: a answers", "- C3: b answers"):
            self.assertIn(line, integration_brief)
        self.assertIn("- J1 Order through both services: call a -> call b", integration_brief)
        # The final product check names the agreement's journeys.
        self.assertEqual(["J1 Order through both services"], result["final_check"]["journeys"])
        self.assertEqual("integration", result["final_check"]["workstream"])
        self.assertEqual("verified", result["journeys"][0]["status"])

    def test_a_draft_that_drops_an_inherited_requirement_is_sent_back_before_anyone_approves_it(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        self.child_view["a"] = {"displayed_plan": {"token": "r1:draft", "acceptance_criteria": [{"id": "X1"}]}}
        code, result = self.run_program(path)
        self.assertEqual(2, code)
        self.assertEqual(1, len(self.feedback))
        self.assertEqual("a", self.feedback[0][0])
        self.assertIn("drops inherited requirement(s) C2", self.feedback[0][1])
        record = self.records(result)["a"]
        self.assertEqual(1, record["plan_rejections"])
        self.assertNotEqual("MERGED", record["status"])
        # The record shows the child after the feedback, not the plan it rejected.
        self.assertEqual(("WAITING", "RUNNING"), (record["status"], record["run_status"]))
        self.assertNotEqual("r1:draft", (record.get("needs") or {}).get("token"))
        # The child re-plans (feedback made it continuable); the new plan keeps C2 and is approved.
        self.child_outcome["a"] = "TASK_COMPLETE"
        self.child_view["a"] = {"approved_contract": {"token": "r2:kept", "body": {"acceptance_criteria": [{"id": "C2"}]}}}
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertTrue(self.launches_of("a")[1]["resume"])
        self.assertEqual("r2:kept", self.records(result)["a"]["approved_plan"]["token"])

    def test_an_approved_plan_that_drops_an_inherited_requirement_never_merges(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_view["a"] = {"approved_contract": {"token": "r2:dropped", "body": {"acceptance_criteria": [{"id": "X1"}]}}}
        code, result = self.run_program(path)
        self.assertEqual(2, code)
        record = self.records(result)["a"]
        self.assertEqual("STALE", record["status"])
        self.assertIn("dropped inherited requirement(s) C2", record["retired_runs"][0]["reason"])
        self.assertNotIn("a/service.py", self.integration_files(result))
        # The next pass starts a fresh run in the same worktree: it plans and is approved again.
        self.child_view["a"] = {"approved_contract": {"token": "r1:kept", "body": {"acceptance_criteria": [{"id": "C2"}]}}}
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        first, second = self.launches_of("a")
        self.assertFalse(second["resume"])
        self.assertEqual(first["workspace"], second["workspace"])
        self.assertNotEqual(first["run_dir"], second["run_dir"])
        self.assertIn("RE-CHECK", second["brief"])

    def test_a_child_that_completes_without_showing_a_conforming_plan_never_merges(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        self.child_view["a"] = {"displayed_plan": {"token": "r1:draft", "acceptance_criteria": [{"id": "X1"}]}}
        self.run_program(path)
        self.assertEqual(1, len(self.feedback))
        # The child completes, but its view shows no plan: the last plan the program saw drops C2.
        self.child_outcome["a"] = "TASK_COMPLETE"
        self.child_view["a"] = {"approved_contract": None}
        code, result = self.run_program(path)
        self.assertEqual(2, code)
        record = self.records(result)["a"]
        self.assertEqual("COMPLETE", record["status"])
        self.assertIn("drops inherited requirement(s) C2", record["blocked_reason"])
        self.assertNotIn("a/service.py", self.integration_files(result))

    def test_a_child_that_completes_without_showing_an_approved_plan_is_not_merged(self):
        path = self.write_manifest(with_requirements(manifest()))
        # The program never saw a plan from a's run, so nothing says it keeps C2 (e.g. a run completed by hand).
        self.child_view["a"] = {"approved_contract": None}
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]))
        record = self.records(result)["a"]
        self.assertEqual("COMPLETE", record["status"])
        self.assertNotIn("approved_plan", record)
        self.assertIn(f"Workstream a completed (run {record['run_dir']}) without showing an approved plan", result["next"])
        self.assertIn(f"--run-dir {record['run_dir']} --follow-up", result["next"])
        self.assertNotIn("a/service.py", self.integration_files(result))
        code, again = self.run_program(path)  # nobody acted: the same pause, nothing merged or relaunched
        self.assertEqual((2, "PAUSED_INHERITANCE", result["next"]), (code, again["status"], again["next"]))
        self.assertEqual(1, len(self.launches_of("a")))
        # The person follows up the run and approves the plan it asks for; it completes under that plan and merges.
        self.set_child(record, status="RUNNING")
        self.child_view["a"] = {}
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertTrue(self.launches_of("a")[-1]["resume"])
        self.assertIn("a/service.py", self.integration_files(result))
        self.assertEqual(1, self.records(result)["a"]["approved_plan"]["revision"])

    def test_a_run_approved_while_it_ran_merges_only_if_it_completes_showing_that_plan(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING"), (code, result["status"]), result)
        # The person approves a conforming plan; the program reads it from the running run before resuming it...
        self.set_child(self.records(result)["a"], status="RUNNING",
                       view={"approved_contract": {"token": "r1:a", "body": {"acceptance_criteria": [{"id": "C2"}]}}})
        # ...but the run completes showing no plan in force, so that approval says nothing about what it delivered.
        self.child_outcome["a"] = "TASK_COMPLETE"
        self.child_view["a"] = {"approved_contract": None}
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]), result)
        record = self.records(result)["a"]
        self.assertEqual(("COMPLETE", True), (record["status"], self.launches_of("a")[-1]["resume"]))
        self.assertNotIn("approved_plan", record)
        self.assertNotIn("a/service.py", self.integration_files(result))

    def test_a_conflict_resolved_by_hand_lands_only_under_a_checked_plan(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        _, waiting = self.run_program(path)
        integration = Path(waiting["integration_workspace"])
        (integration / "a").mkdir()
        (integration / "a/service.py").write_text("external repair\n")
        git(integration, "add", "a/service.py")
        git(integration, *program.GIT_IDENTITY, "commit", "-qm", "External repair")
        self.set_child(self.records(waiting)["a"], status="RUNNING")
        self.child_outcome["a"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_MERGE_CONFLICT"), (code, result["status"]), result)
        record = self.records(result)["a"]
        self.assertEqual("CONFLICT", record["status"])
        # Meanwhile the person followed the run up by hand and it completed showing no plan in force; then they
        # resolve the conflict as the pause asks.
        self.set_child(record, view={"approved_contract": None})
        merge = subprocess.run(["git", "-C", str(integration), "-c", "user.name=H", "-c", "user.email=h@example.test",
                                "merge", "--no-ff", "-X", "theirs", "--no-edit", record["branch"]],
                               capture_output=True, text=True)
        self.assertEqual(0, merge.returncode, merge.stdout + merge.stderr)
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]), result)
        self.assertEqual("CONFLICT", self.records(result)["a"]["status"])
        self.assertIn(f"Workstream a completed (run {record['run_dir']}) without showing an approved plan", result["next"])

    def test_a_conflict_resolved_by_hand_after_a_follow_up_drops_a_requirement_names_it(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        for number in range(1, 4):  # every automatic rejection is spent
            self.child_view["a"] = {"displayed_plan": {"token": f"r{number}:draft", "acceptance_criteria": []}}
            code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]))
        integration = Path(result["integration_workspace"])
        (integration / "a").mkdir()
        (integration / "a/service.py").write_text("external repair\n")
        git(integration, "add", "a/service.py")
        git(integration, *program.GIT_IDENTITY, "commit", "-qm", "External repair")
        # The person gives feedback; the run plans again, keeps C2, completes, and its merge conflicts.
        self.set_child(self.records(result)["a"], status="RUNNING", view={})
        self.child_outcome["a"] = "TASK_COMPLETE"
        self.child_view["a"] = {}
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_MERGE_CONFLICT"), (code, result["status"]), result)
        record = self.records(result)["a"]
        # A follow-up by hand completes under a plan that drops C2; then the person resolves the conflict.
        self.set_child(record, view={"approved_contract": {"token": "r5:dropped", "body": {"acceptance_criteria": []}}})
        merge = subprocess.run(["git", "-C", str(integration), "-c", "user.name=H", "-c", "user.email=h@example.test",
                                "merge", "--no-ff", "-X", "theirs", "--no-edit", record["branch"]],
                               capture_output=True, text=True)
        self.assertEqual(0, merge.returncode, merge.stdout + merge.stderr)
        for _ in range(2):  # the pause names what holds it, not the conflict the person already resolved
            code, result = self.run_program(path)
            self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]), result)
            self.assertIn("The conflict of workstream a was resolved by hand, but its plan drops inherited "
                          "requirement(s) C2", result["next"])
            self.assertEqual("CONFLICT", self.records(result)["a"]["status"])

    def test_the_final_check_and_a_deployment_merge_only_under_an_approved_plan(self):
        path = self.write_manifest(manifest(deploy=True))
        self.child_view["integration"] = {"approved_contract": None}
        code, result = self.run_program(path, "--authorize-deployment")
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]))
        record = self.records(result)["integration"]
        self.assertIn(f"Workstream integration completed (run {record['run_dir']})", result["next"])
        self.assertNotIn("tests/test_flow.py", self.integration_files(result))  # nothing committed
        self.assertEqual([], self.launches_of("deploy"))
        self.set_child(record, status="RUNNING")
        self.child_view.update(integration={}, deploy={"approved_contract": None})
        code, result = self.run_program(path, "--authorize-deployment")
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]))
        records = self.records(result)
        self.assertEqual(("MERGED", "COMPLETE"), (records["integration"]["status"], records["deploy"]["status"]))
        self.assertIn(f"Workstream deploy completed (run {records['deploy']['run_dir']})", result["next"])
        self.assertNotIn("deploy/compose.yml", self.integration_files(result))

    def test_a_child_that_refuses_the_programs_feedback_pauses_for_a_person(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        self.child_view["a"] = {"displayed_plan": {"token": "r1:draft", "acceptance_criteria": [{"id": "X1"}]}}
        self.fake_feedback = lambda command: subprocess.CompletedProcess(
            command, 2, "", "Input rejected: Brief feedback needs nonempty text at a conversation checkpoint\n")
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]))
        check = self.records(result)["a"]["plan_check"]
        self.assertEqual((["C2"], True), (check["dropped"], check["exhausted"]))
        self.assertIn("Input rejected", check["feedback_error"])
        saved = json.loads(Path(result["state_file"]).read_text())["workstreams"]["a"]
        self.assertEqual(1, saved["plan_rejections"])  # the pass saved what it counted

    def test_repeated_dropping_plans_pause_for_a_person(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        for number in range(1, 4):
            self.child_view["a"] = {"displayed_plan": {"token": f"r{number}:draft", "acceptance_criteria": []}}
            code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]))
        self.assertEqual(program.MAX_PLAN_REJECTIONS, len(self.feedback))
        self.assertTrue(self.records(result)["a"]["plan_check"]["exhausted"])

    def test_after_a_person_acts_on_a_paused_plan_the_child_re_plans_and_merges_only_a_conforming_plan(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        for number in range(1, 4):
            self.child_view["a"] = {"displayed_plan": {"token": f"r{number}:draft", "acceptance_criteria": []}}
            code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE", 3), (code, result["status"], len(self.launches_of("a"))))
        code, result = self.run_program(path)  # nobody acted: nothing is relaunched
        self.assertEqual((2, "PAUSED_INHERITANCE", 3), (code, result["status"], len(self.launches_of("a"))))
        # The person gives the run feedback: the child is RUNNING with no plan in its view, and re-plans.
        # Its new plan, approved and built, still drops C2: it is not merged.
        self.set_child(self.records(result)["a"], status="RUNNING", view={})
        self.child_outcome["a"] = "TASK_COMPLETE"
        self.child_view["a"] = {"approved_contract": {"token": "r4:dropped", "body": {"acceptance_criteria": []}}}
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE", 4), (code, result["status"], len(self.launches_of("a"))))
        self.assertTrue(self.launches_of("a")[-1]["resume"])
        self.assertNotIn("a/service.py", self.integration_files(result))
        # The person follows up again; this plan keeps C2, so it merges and the program completes.
        self.set_child(self.records(result)["a"], status="RUNNING", view={})
        self.child_view["a"] = {"approved_contract": {"token": "r5:kept", "body": {"acceptance_criteria": [{"id": "C2"}]}}}
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertEqual("r5:kept", self.records(result)["a"]["approved_plan"]["token"])
        self.assertEqual(program.MAX_PLAN_REJECTIONS, len(self.feedback))  # the program sent no more of its own

    def test_a_revision_takes_approval_only_from_the_workstreams_it_affects(self):
        value = with_requirements(manifest())
        path = self.write_manifest(value)
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertEqual(4, len(self.launches))
        value["requirements"][1]["criterion"] = "a answers within 100 ms"
        path.write_text(json.dumps(value))
        code, result = self.run_program(path, approve=False)
        self.assertEqual((2, "WAITING_AGREEMENT_APPROVAL"), (code, result["status"]))
        self.assertEqual(["a", "integration"], result["agreement"]["pending"]["affected"])
        self.assertIn("requirement C2 changed", result["agreement"]["pending"]["changes"])
        self.assertEqual(4, len(self.launches))  # nothing starts under an unapproved revision
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertEqual(["a", "integration"], [row["id"] for row in self.launches[4:]])
        records = self.records(result)
        for wid in ("a", "integration"):
            self.assertEqual(2, records[wid]["pin"]["revision"], wid)
            self.assertEqual(1, len(records[wid]["retired_runs"]), wid)
            self.assertIn("agreement revision 2 changed", records[wid]["retired_runs"][0]["reason"])
        for wid in ("contracts", "b"):
            self.assertEqual(1, records[wid]["pin"]["revision"], wid)
            self.assertNotIn("retired_runs", records[wid])
        recheck = self.launches_of("a")[1]
        self.assertFalse(recheck["resume"])
        self.assertIn("RE-CHECK: agreement revision 2 changed", recheck["brief"])
        self.assertIn("plan a validation-only task (kind validate)", recheck["brief"])
        # A fresh worktree from the integration head holds the earlier merge, so its proof base does too.
        self.assertIn("Mark a criterion the code on this branch already satisfies guard:", recheck["brief"])
        self.assertIn("a answers within 100 ms", recheck["brief"])
        # A merged workstream is re-checked from the current integration head in a fresh worktree.
        self.assertNotEqual(self.launches_of("a")[0]["workspace"], recheck["workspace"])
        self.assertIn("b/service.py", recheck["files"])

    def test_a_retired_runs_approval_never_merges_the_run_that_re_checks_it(self):
        value = with_requirements(manifest())
        path = self.write_manifest(value)
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        merged = self.records(result)["a"]["approved_plan"]
        value["requirements"][1]["criterion"] = "a answers within 100 ms"
        path.write_text(json.dumps(value))
        # a's re-check completes without showing a plan: the retired run's approval does not count for it.
        self.child_view["a"] = {"approved_contract": None}
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]))
        record = self.records(result)["a"]
        self.assertEqual(("COMPLETE", 2), (record["status"], record["pin"]["revision"]))
        self.assertNotIn("approved_plan", record)
        self.assertEqual(merged, record["retired_runs"][0]["approved_plan"])  # kept with the run it approved
        self.assertIn(f"Workstream a completed (run {record['run_dir']})", result["next"])
        # Once the re-check's own plan is seen and checked, it is the approval the workstream merges under.
        self.set_child(record, status="RUNNING")
        self.child_view["a"] = {}
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        approved = self.records(result)["a"]["approved_plan"]
        self.assertEqual(2, approved["revision"])
        self.assertNotEqual(merged["token"], approved["token"])

    def test_a_re_checked_workstream_merges_only_once_a_person_approves_its_new_plan(self):
        def approved(token, *ids):
            return {"approved_contract": {"token": token, "body": {"acceptance_criteria": [{"id": i} for i in ids]}}}

        value = with_requirements(manifest())
        path = self.write_manifest(value)
        self.child_view.update(contracts=approved("r1:contracts", "C1"), a=approved("r1:a", "C2"),
                               b=approved("r1:b", "C3"), integration=approved("r1:final", "C1", "C2", "C3", "J1"))
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        merged = git(self.project, "rev-parse", result["integration_branch"])
        value["requirements"][1]["criterion"] = "a answers within 100 ms"
        path.write_text(json.dumps(value))
        # a was merged under its approved plan; the revision changes what it is built from, so it plans again.
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        self.child_extra_files["a"] = {"a/timing.txt": "100 ms\n"}  # what its re-check delivers once it completes
        self.child_view["a"] = {"displayed_plan": {"token": "r1:a-recheck", "acceptance_criteria": [{"id": "C2"}]},
                                "needs": {"kind": "approve_plan", "token": "r1:a-recheck"}}
        for _ in range(2):  # a second pass changes nothing while the new plan waits for a person
            code, result = self.run_program(path)
            self.assertEqual((2, "WAITING"), (code, result["status"]), result)
            first, recheck = self.launches_of("a")
            self.assertFalse(recheck["resume"])
            self.assertNotEqual(first["run_dir"], recheck["run_dir"])
            record = self.records(result)["a"]
            self.assertEqual(("WAITING", "AWAITING_GOAL_APPROVAL", recheck["run_dir"]),
                             (record["status"], record["run_status"], record["run_dir"]))
            self.assertEqual({"kind": "approve_plan", "token": "r1:a-recheck"}, record["needs"])
            # The retired run's approval no longer counts; nothing merges, and the final check waits for a.
            self.assertNotIn("approved_plan", record)
            self.assertEqual(("r1:a", first["run_dir"]), (record["retired_runs"][0]["approved_plan"]["token"],
                                                         record["retired_runs"][0]["run_dir"]))
            self.assertEqual(merged, git(self.project, "rev-parse", result["integration_branch"]))
            self.assertEqual(("STALE", 1), (self.records(result)["integration"]["status"],
                                            len(self.launches_of("integration"))))
        # The person approves the new plan in the run: the program resumes that run, merges it and re-checks the end.
        self.set_child(record, status="RUNNING")
        self.child_outcome["a"] = "TASK_COMPLETE"
        self.child_view.update(a=approved("r1:a-recheck", "C2"),
                               integration=approved("r1:final-recheck", "C1", "C2", "C3", "J1"))
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertEqual([False, False, True], [row["resume"] for row in self.launches_of("a")])
        self.assertIn("a/timing.txt", self.integration_files(result))
        records = self.records(result)
        for wid, token in (("a", "r1:a-recheck"), ("integration", "r1:final-recheck")):
            self.assertEqual((token, 2), (records[wid]["approved_plan"]["token"], records[wid]["approved_plan"]["revision"]))
            self.assertEqual(2, records[wid]["merged_under"]["revision"], wid)
        # The workstreams the revision did not affect keep the approval they had.
        for wid, token in (("contracts", "r1:contracts"), ("b", "r1:b")):
            self.assertEqual((token, 1), (records[wid]["approved_plan"]["token"], records[wid]["approved_plan"]["revision"]))
            self.assertEqual(1, len(self.launches_of(wid)), wid)
            self.assertNotIn("retired_runs", records[wid])

    def run_recording_final_check_bases(self, path):
        """Run the program; return (code, result, [(base the new final-check run reads, worktree HEAD)])."""
        self.approve(path)
        seen = []

        def recording(command, **kwargs):
            if self.is_launch(command) and "--run-dir" not in command and "PROGRAM WORKSTREAM integration " in command[2]:
                workspace = Path(command[command.index("--workspace") + 1])
                meta = json.loads((workspace / ".autocode/task-workspace.json").read_text())
                seen.append((meta["base_commit"], git(workspace, "rev-parse", "HEAD")))
            return self.fake_run(command, **kwargs)

        output = io.StringIO()
        with self.scripted_invocations(recording), contextlib.redirect_stdout(output):
            code = program.cli(["run", str(path), "--workspace", str(self.project), "--max-parallel", "2"])
        return code, json.loads(output.getvalue()), seen

    # A new run takes its regression-proof base from its worktree's task-workspace.json. The shared integration
    # worktree was made from the seed, and a live final check proved the merged product against it (2026-10-06).
    def test_each_new_final_check_run_starts_from_the_integration_head(self):
        value = with_requirements(manifest())
        path = self.write_manifest(value)
        self.child_outcome["integration"] = "AWAITING_GOAL_APPROVAL"
        code, result, seen = self.run_recording_final_check_bases(path)
        self.assertEqual(1, len(seen), seen)
        first_base, first_head = seen[0]
        self.assertEqual(first_head, first_base)
        self.assertNotEqual(self.head, first_base)  # every code workstream is merged on top of the seed
        record = self.records(result)["integration"]
        self.assertEqual(first_base, record["base_commit"])
        self.assertIn("Proof marks: every code workstream is merged on this branch", self.launches_of("integration")[0]["brief"])
        # Re-checking an upstream workstream retires the final check; its next run starts from the head after the
        # re-check merges.
        value["requirements"][1]["criterion"] = "a answers within 100 ms"
        path.write_text(json.dumps(value))
        self.child_extra_files["a"] = {"a/timing.txt": "100 ms\n"}
        code, result, seen = self.run_recording_final_check_bases(path)
        self.assertEqual(1, len(seen), seen)
        second_base, second_head = seen[0]
        self.assertEqual(second_head, second_base)
        self.assertNotEqual(first_base, second_base)
        self.assertIn("a/timing.txt", git(self.project, "ls-tree", "-r", "--name-only", second_base).splitlines())
        # A revision of the final check alone retires its run in place; a commit made on the branch since moves
        # the head, and the new run starts there while the program's own checks keep their earlier base.
        workspace = Path(self.records(result)["integration"]["workspace"])
        (workspace / "tests").mkdir(exist_ok=True)
        (workspace / "tests" / "fixture.txt").write_text("kept\n")
        git(workspace, "add", "tests/fixture.txt")
        git(workspace, *program.GIT_IDENTITY, "commit", "-qm", "A person's fixture")
        value["workstreams"][3]["brief"] = "Validate the whole flow twice"
        path.write_text(json.dumps(value))
        code, result, seen = self.run_recording_final_check_bases(path)
        self.assertEqual(1, len(seen), seen)
        third_base, third_head = seen[0]
        self.assertEqual((third_head, git(workspace, "rev-parse", "HEAD")), (third_base, third_base))
        self.assertNotEqual(second_base, third_base)
        self.assertEqual(second_base, self.records(result)["integration"]["base_commit"])

    def test_a_workstream_waiting_for_approval_loses_it_when_its_part_of_the_agreement_changes(self):
        value = with_requirements(manifest())
        path = self.write_manifest(value)
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        self.run_program(path)
        first = self.launches_of("a")[0]
        value["requirements"][1]["criterion"] = "a answers twice"
        path.write_text(json.dumps(value))
        self.child_outcome["a"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        second = self.launches_of("a")[1]
        self.assertFalse(second["resume"])  # the old plan's run is retired, never resumed
        self.assertEqual(first["workspace"], second["workspace"])
        # Retired in place: its proof base is the worktree's original commit, without the earlier run's work.
        self.assertIn("RE-CHECK:", second["brief"])
        self.assertNotIn("guard:", second["brief"])
        self.assertEqual(first["run_dir"], self.records(result)["a"]["retired_runs"][0]["run_dir"])

    def test_an_interface_change_request_takes_approval_from_its_producer_and_users(self):
        value = with_requirements(manifest())
        path = self.write_manifest(value)
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        self.run_program(path)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, program.cli(["request-change", str(path), "--workspace", str(self.project),
                                             "--interface", "contracts", "--by", "a",
                                             "--reason", "Orders need a reason field"]))
        request = json.loads(output.getvalue())["change_request"]
        self.assertEqual(("CR-1", "open", 1), (request["id"], request["status"], request["from_version"]))
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING_CHANGE_REQUEST"), (code, result["status"]))
        # Nobody edits the interface quietly: the same version with a new definition is refused.
        value["shared"]["interfaces"][0]["behavior"] = "Every order carries a reason"
        path.write_text(json.dumps(value))
        error = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(error):
            self.run_program(path, approve=False)
        self.assertIn("changed without a new version", error.getvalue())
        # Accepting it is an approved agreement revision that publishes version 2.
        value["shared"]["interfaces"][0]["version"] = 2
        path.write_text(json.dumps(value))
        self.child_outcome["contracts"] = "AWAITING_GOAL_APPROVAL"
        self.child_outcome["a"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual("accepted", result["change_requests"][0]["status"])
        records = self.records(result)
        saved = json.loads(Path(result["state_file"]).read_text())
        self.assertEqual(["contracts", "a", "b", "integration"], saved["agreement"]["history"][-1]["affected"])
        # The users of version 1 lost their approval and cannot be done until checked against version 2.
        self.assertEqual("STALE", records["b"]["status"])
        self.assertEqual("STALE", records["a"]["status"])
        self.assertEqual("WAITING", records["contracts"]["status"])
        self.assertIsNone(result["skeleton"])
        self.assertNotEqual("COMPLETE", result["status"])
        self.child_outcome["contracts"] = "TASK_COMPLETE"
        self.set_child(records["contracts"], status="RUNNING")  # the person approves the re-checked plan
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        records = self.records(result)
        for wid in ("a", "b", "integration"):
            self.assertEqual(2, records[wid]["pin"]["revision"], wid)
        self.assertIn("version 2", self.launches_of("b")[1]["brief"])
        # a was waiting on a plan built over version 1 of the contracts, which were re-checked too:
        # its fresh run starts from the integration head that holds the re-checked contracts.
        first, recheck = self.launches_of("a")[0], self.launches_of("a")[-1]
        self.assertNotEqual(first["workspace"], recheck["workspace"])
        self.assertEqual(first["workspace"], records["a"]["retired_runs"][0]["workspace"])

    def test_an_open_change_request_holds_the_final_check_and_outranks_completion(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["integration"] = "AWAITING_GOAL_APPROVAL"
        _, result = self.run_program(path)
        self.request_change(path, "contracts", "a")
        self.set_child(self.records(result)["integration"], status="RUNNING")  # the person approves its plan
        self.child_outcome["integration"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING_CHANGE_REQUEST", 1), (code, result["status"], len(self.launches_of("integration"))))
        self.assertIn("change request CR-1", self.records(result)["integration"]["blocked_reason"])
        with contextlib.redirect_stdout(io.StringIO()):
            program.cli(["resolve-change", str(path), "--workspace", str(self.project), "--request", "CR-1",
                         "--reject", "--reason", "The interface stays"])
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        # A request raised after completion keeps the program open until it is decided.
        self.request_change(path, "contracts", "b")
        self.assertEqual("WAITING_CHANGE_REQUEST", self.status(path)["status"])

    def test_a_request_on_an_interface_without_a_producer_holds_every_workstream_until_withdrawn(self):
        value = manifest()  # its interface has no producer, so it binds every workstream
        path = self.write_manifest(value)
        self.child_outcome["contracts"] = "AWAITING_GOAL_APPROVAL"
        _, result = self.run_program(path)
        self.request_change(path, "contracts", "a")
        self.set_child(self.records(result)["contracts"], status="RUNNING")
        self.child_outcome["contracts"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING_CHANGE_REQUEST", 1), (code, result["status"], len(self.launches_of("contracts"))))
        # A revision that removes the interface withdraws the request instead of leaving it open forever.
        value["shared"]["interfaces"] = []
        path.write_text(json.dumps(value))
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        request = result["change_requests"][0]
        self.assertEqual(("withdrawn", 2), (request["status"], request["withdrawn_in_revision"]))

    def test_a_delivered_interface_is_never_changed_in_place(self):
        value = with_requirements(manifest())
        path = self.write_manifest(value)
        self.child_extra_files["integration"] = {"contracts/spec.json": "integration rewrote the contract\n"}
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INTERFACE_CHANGE"), (code, result["status"]))
        self.assertIn("integration is not its producer (contracts)", result["next"])
        self.assertEqual("contracts result\n", git(self.project, "show", result["integration_branch"] + ":contracts/spec.json") + "\n")

    def test_the_walking_skeleton_is_verified_before_any_other_workstream_starts(self):
        value = manifest()
        path = self.write_manifest(value)
        self.child_checks["contracts"] = []  # nothing runnable proves the skeleton
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_SKELETON_UNVERIFIED"), (code, result["status"]))
        self.assertEqual(["contracts"], [row["id"] for row in self.launches])
        self.assertEqual({"README.md"}, self.integration_files(result))  # the merge was undone
        self.assertIn("so its merge was undone and nothing else starts", result["next"])
        self.assertIsNone(result["skeleton"])
        # The pause ends the pass before anything else is scheduled: a, b and the final check never started (so
        # none carries a blocked_reason), and the pause's message says why.
        self.assertEqual({"PENDING"}, {self.records(result)[wid]["status"] for wid in ("a", "b", "integration")})
        # A program check that walks the journey verifies it; that revision affects no workstream.
        value["checks"] = ["test -f contracts/spec.json"]
        path.write_text(json.dumps(value))
        pending = self.approve(path)
        self.assertEqual([], pending["affected"])
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertEqual("contracts", result["skeleton"]["workstream"])
        self.assertEqual(["contracts", "a", "b", "integration"][:1], [row["id"] for row in self.launches][:1])
        self.assertEqual(1, len(self.launches_of("contracts")))  # not re-run: its part of the agreement did not change

    def test_a_walking_skeleton_whose_checks_fail_merged_is_undone_and_nothing_else_starts(self):
        path = self.write_manifest(manifest())
        # The skeleton's run replayed a journey check that fails on the integrated product.
        self.child_checks["contracts"] = ["test -f contracts/spec.json", "grep -q order contracts/spec.json"]
        for _ in range(2):  # an unchanged rerun repeats the pause, and still nothing else starts
            code, result = self.run_program(path)
            self.assertEqual((2, "PAUSED_INTEGRATION_CHECK"), (code, result["status"]))
            self.assertIn("`grep -q order contracts/spec.json` exited 1", result["next"])
            self.assertIn("The merge was undone. The check is contracts's own", result["next"])
            self.assertIsNone(result["skeleton"])
            self.assertEqual({"README.md"}, self.integration_files(result))
            self.assertEqual(["contracts"], [row["id"] for row in self.launches])
            self.assertEqual({"COMPLETE", "PENDING"}, {row["status"] for row in result["workstreams"]})
        # A follow-up whose checks pass on the integrated product verifies the skeleton; only then does the rest start.
        self.set_child(self.records(result)["contracts"], status="RUNNING")
        self.child_checks["contracts"] = ["test -f contracts/spec.json"]
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertEqual(["contracts", "contracts"], [row["id"] for row in self.launches][:2])
        self.assertTrue(self.launches[1]["resume"])

    def test_a_stale_walking_skeleton_holds_what_extends_it_until_its_re_check_is_verified(self):
        value = manifest()
        value["workstreams"].insert(3, {"id": "c", "kind": "code", "brief": "Build service c on a", "owns": ["c/"],
                                        "depends_on": ["a"]})
        value["workstreams"][-1]["depends_on"] = ["b", "c"]
        path = self.write_manifest(value)
        self.child_outcome.update(b="AWAITING_GOAL_APPROVAL", c="AWAITING_GOAL_APPROVAL")
        _, result = self.run_program(path)
        records = self.records(result)
        self.assertEqual(("contracts", "MERGED"), (result["skeleton"]["workstream"], records["a"]["status"]))
        # A revision of the skeleton's part alone retires its merged run; nothing else loses its approval.
        value["workstreams"][0]["brief"] = "Write the shared contracts with a version field"
        path.write_text(json.dumps(value))
        self.assertEqual(["contracts"], self.approve(path)["affected"])
        # Meanwhile the person resumes b's run themselves (the scripted child, outside the program) until it
        # completes, and approves c's plan.
        self.child_outcome.update(b="TASK_COMPLETE", contracts="AWAITING_GOAL_APPROVAL")
        self.fake_run(["autocode", "--workspace", records["b"]["workspace"], "--run-dir", records["b"]["run_dir"]])
        self.set_child(records["c"], status="RUNNING")
        before = len(self.launches)
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING"), (code, result["status"]))
        self.assertIsNone(result["skeleton"])
        self.assertEqual([("contracts", False)], [(row["id"], row["resume"]) for row in self.launches[before:]])
        records = self.records(result)
        # b completed but is not merged, and c (whose own dependency a is merged) is not resumed.
        for wid, status in (("b", "COMPLETE"), ("c", "WAITING")):
            self.assertEqual((status, "waiting for the walking skeleton to be merged and verified"),
                             (records[wid]["status"], records[wid].get("blocked_reason")), wid)
        self.assertNotIn("b/service.py", self.integration_files(result))
        # The person approves the re-check's plan: the skeleton is merged and verified before b merges.
        self.set_child(records["contracts"], status="RUNNING")
        self.child_outcome.update(contracts="TASK_COMPLETE", c="TASK_COMPLETE")
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        saved = json.loads(Path(result["state_file"]).read_text())
        self.assertEqual(["contracts", "b", "c", "integration"],
                         [row["workstream"] for row in saved["verifications"]][-4:])

    def test_every_merge_reruns_the_cumulative_checks_and_a_failing_merge_is_undone(self):
        path = self.write_manifest(manifest())
        self.child_checks["a"] = ["test -f a/service.py", "test ! -f b/service.py"]  # b's delivery breaks a
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INTEGRATION_CHECK"), (code, result["status"]))
        self.assertIn("test ! -f b/service.py", result["next"])
        self.assertIn("The merge was undone", result["next"])
        # The failing check is a's, not b's: the pause names it and both ways out.
        self.assertIn("The check was left by workstream a. Either b broke what it checks: fix b", result["next"])
        self.assertIn("revise the agreement so a is re-checked", result["next"])
        records = self.records(result)
        self.assertEqual(("MERGED", "COMPLETE"), (records["a"]["status"], records["b"]["status"]))
        self.assertNotIn("b/service.py", self.integration_files(result))
        self.assertEqual("", git(Path(result["integration_workspace"]), "status", "--porcelain", "--untracked-files=no"))
        # The same delivery is not merged again until something changes.
        head = git(Path(result["integration_workspace"]), "rev-parse", "HEAD")
        code, again = self.run_program(path)
        self.assertEqual((2, "PAUSED_INTEGRATION_CHECK"), (code, again["status"]))
        self.assertEqual(head, git(Path(result["integration_workspace"]), "rev-parse", "HEAD"))

    def test_a_failing_check_of_the_merging_workstream_itself_asks_only_to_fix_it(self):
        path = self.write_manifest(manifest())
        self.child_checks["b"] = ["test -f b/service.py", "test ! -f a/service.py"]  # b's own check fails merged
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INTEGRATION_CHECK"), (code, result["status"]))
        self.assertIn("The check is b's own: fix b", result["next"])
        self.assertNotIn("revise the agreement", result["next"])

    def test_a_merge_an_interrupted_controller_never_verified_is_undone_when_its_checks_fail(self):
        path = self.write_manifest(manifest())
        self.child_checks["a"] = ["test -f a/service.py", "test ! -f b/service.py"]  # b's delivery breaks a
        self.approve(path)
        real = program.verify_integration

        def interrupted(value, state, program_dir, wid, timeout):
            if wid == "b":
                raise KeyboardInterrupt  # the controller dies between b's merge and its verdict
            return real(value, state, program_dir, wid, timeout)

        with patch.object(program, "verify_integration", side_effect=interrupted), self.scripted_invocations(), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            program.cli(["run", str(path), "--workspace", str(self.project), "--max-parallel", "2"])
        branch = json.loads(next(self.project.glob(".autocode/programs/*/state.json")).read_text())["integration"]["branch"]
        self.assertIn("b/service.py", git(self.project, "ls-tree", "-r", "--name-only", branch))  # merged, unverified
        code, result = self.run_program(path, approve=False)
        self.assertEqual((2, "PAUSED_INTEGRATION_CHECK"), (code, result["status"]))
        self.assertIn("The merge was undone", result["next"])
        self.assertNotIn("b/service.py", self.integration_files(result))
        self.assertIn("a/service.py", self.integration_files(result))

    def test_a_failing_manual_merge_says_it_is_still_on_the_integration_branch(self):
        path = self.write_manifest(manifest())
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        _, result = self.run_program(path)
        integration = Path(result["integration_workspace"])
        (integration / "a").mkdir()
        (integration / "a/service.py").write_text("external repair\n")
        git(integration, "add", "a/service.py")
        git(integration, *program.GIT_IDENTITY, "commit", "-qm", "External repair")
        self.set_child(self.records(result)["a"], status="RUNNING")
        self.child_outcome["a"] = "TASK_COMPLETE"
        self.child_checks["a"] = ["grep -q external a/service.py"]  # passes only while the external repair stays
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_MERGE_CONFLICT"), (code, result["status"]))
        git(integration, *program.GIT_IDENTITY, "merge", "--no-ff", "-X", "theirs", "--no-edit",
            self.records(result)["a"]["branch"])
        head = git(integration, "rev-parse", "HEAD")
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INTEGRATION_CHECK"), (code, result["status"]))
        self.assertNotIn("The merge was undone", result["next"])
        self.assertIn("Your merge is still on the integration branch", result["next"])
        self.assertEqual(head, git(integration, "rev-parse", "HEAD"))

    def test_brief_md_keeps_the_brief_that_started_the_run(self):
        path = self.write_manifest(manifest())
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        _, result = self.run_program(path)
        started = self.launches_of("a")[0]["brief"]
        brief = Path(result["state_file"]).parent / "a" / "brief.md"
        self.assertEqual(started + "\n", brief.read_text())
        self.set_child(self.records(result)["a"], status="RUNNING")
        self.child_outcome["a"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertTrue(self.launches_of("a")[1]["resume"])
        self.assertEqual(started + "\n", brief.read_text())  # b merged since; resuming a rewrote nothing

    def test_the_final_check_follows_named_journeys_and_says_what_simulations_do_not_prove(self):
        value = manifest()
        value["journeys"].append({"id": "J2", "name": "A thousand simulated students", "steps": ["simulate"],
                                  "simulated": True, "does_not_prove": "that real students learn better"})
        path = self.write_manifest(value)
        self.journey_status = {"J2": "verified"}
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertEqual(["J1 Order through both services", "J2 A thousand simulated students"],
                         result["final_check"]["journeys"])
        self.assertEqual(["J2 A thousand simulated students: that real students learn better"],
                         result["final_check"]["not_proven"])

    def test_a_journey_the_final_check_did_not_verify_keeps_the_program_open(self):
        path = self.write_manifest(manifest())
        self.journey_status = {"J1": "unverified"}
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_JOURNEY_UNVERIFIED"), (code, result["status"]))
        self.assertIn("without verifying user journey(s) J1", result["next"])
        record = self.records(result)["integration"]
        self.assertEqual("COMPLETE", record["status"])  # not merged, so a follow-up can still reach it
        self.assertEqual("pending", result["journeys"][0]["status"])
        self.assertNotIn("final_check", result)
        # A person follows up the final check's run; once it verifies the journey the program completes.
        state = Path(record["run_dir"]) / "state.json"
        state.write_text(json.dumps({**json.loads(state.read_text()), "status": "RUNNING"}))
        self.journey_status = {"J1": "verified"}
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertTrue(self.launches_of("integration")[-1]["resume"])
        self.assertEqual(["J1 Order through both services"], result["final_check"]["journeys"])


class InterfaceVersionTests(unittest.TestCase):
    def test_a_delivered_interface_removed_since_comes_back_only_with_a_higher_version(self):
        value = program.validate_manifest(with_requirements(manifest()))
        state = program.new_state(Path("/nowhere/program.json"), value, Path("/nowhere"), "key")

        def approve(revision):
            program.sync_agreement(state, revision)
            program.approve_agreement(state, revision, state["agreement"]["pending"]["token"])

        approve(value)
        state["interfaces"]["contracts"] = {"version": 1, "commit": "abc", "by": "contracts"}  # delivered
        removed = copy.deepcopy(value)
        removed["shared"]["interfaces"] = []
        approve(removed)
        with self.assertRaisesRegex(ValueError, "delivered as version 1 and removed since; it may come back only "
                                                "as version 2 or later"):
            program.sync_agreement(state, value)
        again = copy.deepcopy(value)
        again["shared"]["interfaces"][0]["version"] = 2
        program.sync_agreement(state, again)
        self.assertEqual(3, state["agreement"]["pending"]["revision"])


class VerificationReceiptTests(unittest.TestCase):
    def test_receipt_directories_keep_counting_past_the_kept_history(self):
        state = {"integration": {"workspace": "/nowhere"}, "workstreams": {}, "verifications": [{}] * 50}
        manifest = {"workstreams": []}
        with patch.object(program, "integration_head", return_value="abc"):
            first = program.verify_integration(manifest, state, Path("/tmp/program"), "a", 5)
            second = program.verify_integration(manifest, state, Path("/tmp/program"), "b", 5)
        self.assertEqual(("051-a", "052-b"), (Path(first["receipts"]).name, Path(second["receipts"]).name))
        self.assertEqual(50, len(state["verifications"]))


if __name__ == "__main__":
    unittest.main()
