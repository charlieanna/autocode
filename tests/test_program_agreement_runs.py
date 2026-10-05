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
import unittest
from pathlib import Path

from .test_program import ProgramHarness, git, manifest, program, with_requirements


class AgreementTests(ProgramHarness):
    """The program agreement (#22, #23): approval, pins, inheritance, revisions, interfaces, skeleton, journeys."""

    def records(self, result):
        return {row["id"]: row for row in result["workstreams"]}

    def launches_of(self, wid):
        return [row for row in self.launches if row["id"] == wid]

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
        brief = self.launches_of("a")[0]["brief"]
        self.assertIn("Program agreement revision 1, approved by the user.", brief)
        self.assertIn("with exactly this id (C2)", brief)
        integration_brief = self.launches_of("integration")[0]["brief"]
        self.assertIn("with exactly this id (C1, C2, C3, J1)", integration_brief)
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

    def test_repeated_dropping_plans_pause_for_a_person(self):
        path = self.write_manifest(with_requirements(manifest()))
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        for number in range(1, 4):
            self.child_view["a"] = {"displayed_plan": {"token": f"r{number}:draft", "acceptance_criteria": []}}
            code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INHERITANCE"), (code, result["status"]))
        self.assertEqual(program.MAX_PLAN_REJECTIONS, len(self.feedback))
        self.assertTrue(self.records(result)["a"]["plan_check"]["exhausted"])

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
        self.assertIn("a answers within 100 ms", recheck["brief"])
        # A merged workstream is re-checked from the current integration head in a fresh worktree.
        self.assertNotEqual(self.launches_of("a")[0]["workspace"], recheck["workspace"])
        self.assertIn("b/service.py", recheck["files"])

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
        self.assertIsNone(result["skeleton"])
        self.assertIn("waiting for the walking skeleton", self.records(result)["a"].get("blocked_reason", "waiting for the walking skeleton"))
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

    def test_every_merge_reruns_the_cumulative_checks_and_a_failing_merge_is_undone(self):
        path = self.write_manifest(manifest())
        self.child_checks["a"] = ["test -f a/service.py", "test ! -f b/service.py"]  # b's delivery breaks a
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INTEGRATION_CHECK"), (code, result["status"]))
        self.assertIn("test ! -f b/service.py", result["next"])
        self.assertIn("The merge was undone", result["next"])
        records = self.records(result)
        self.assertEqual(("MERGED", "COMPLETE"), (records["a"]["status"], records["b"]["status"]))
        self.assertNotIn("b/service.py", self.integration_files(result))
        self.assertEqual("", git(Path(result["integration_workspace"]), "status", "--porcelain", "--untracked-files=no"))
        # The same delivery is not merged again until something changes.
        head = git(Path(result["integration_workspace"]), "rev-parse", "HEAD")
        code, again = self.run_program(path)
        self.assertEqual((2, "PAUSED_INTEGRATION_CHECK"), (code, again["status"]))
        self.assertEqual(head, git(Path(result["integration_workspace"]), "rev-parse", "HEAD"))

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
        self.assertEqual("failed", result["journeys"][0]["status"])
        self.assertNotIn("final_check", result)


if __name__ == "__main__":
    unittest.main()
