"""T02 — Plan validation and module handoffs catalogue scenarios (DAG-01..DAG-10).

ReadySetOracle computes the eligible milestone set independently by plain
topology (dependencies ⊆ accepted, ownership present and disjoint).  Existing
regressions in test_milestone_checkpoints / test_units / test_planning are
cited per case.
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autopilot_testkit as kit
import autocode as runner
import autocode_dispatch as dispatch
import autocode_goals as goals
import autocode_milestones as milestones
import autocode_support as support
import test_catalogue_t01 as t01
from goal_fixtures import body, envelope


class ReadySetOracle:
    """Expected schedulable milestones: dependencies accepted, ownership
    declared, and pairwise disjoint paths/criteria among the selected wave."""

    def __init__(self, milestone_rows):
        self.rows = {row["id"]: row for row in milestone_rows}

    def ready(self, accepted):
        return {mid for mid, row in self.rows.items()
                if mid not in accepted and set(row.get("depends_on", [])) <= accepted
                and row.get("affected_paths")}

    def wave(self, primary, accepted, limit=4):
        if primary not in self.ready(accepted):
            return []
        selected = [primary]
        for mid, row in self.rows.items():
            if len(selected) >= limit or mid == primary or mid not in self.ready(accepted):
                continue
            if all(set(row["affected_paths"]).isdisjoint(self.rows[old]["affected_paths"])
                   and set(row["acceptance_criteria"]).isdisjoint(self.rows[old]["acceptance_criteria"])
                   for old in selected):
                selected.append(mid)
        return selected


def diamond():
    """FX06: A -> (B, C) -> D with disjoint owned paths and one criterion each."""
    criteria = [{"id": f"C{i}", "criterion": f"Diamond step {i}", "verification_method": "execute",
                 "human_review": False} for i in range(1, 5)]
    milestones = [
        {"id": "MA", "objective": "shared contract", "acceptance_criteria": ["C1"], "depends_on": [],
         "affected_paths": ["contract/"]},
        {"id": "MB", "objective": "server behavior", "acceptance_criteria": ["C2"], "depends_on": ["MA"],
         "affected_paths": ["server/"]},
        {"id": "MC", "objective": "client behavior", "acceptance_criteria": ["C3"], "depends_on": ["MA"],
         "affected_paths": ["client/"]},
        {"id": "MD", "objective": "integration", "acceptance_criteria": ["C4"], "depends_on": ["MB", "MC"],
         "affected_paths": ["integration/"]},
    ]
    draft = body()
    draft["acceptance_criteria"] = criteria
    draft["milestones"] = milestones
    return draft


class DagCase(t01.ApprovalCase):
    def start_diamond(self):
        draft = diamond()
        goals.install_draft(self.state, draft, origin="test")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.state["settings"]["milestone_checkpoints"] = copy.deepcopy(milestones.DEFAULTS)
        self.state["settings"]["orchestration"] = {"max_parallel": 2}
        self.oracle = ReadySetOracle(draft["milestones"])

    def decision(self, milestone, status="CONTINUE"):
        owned = set(next(m for m in self.state["goal_contract"]["body"]["milestones"]
                         if m["id"] == milestone)["acceptance_criteria"])
        # The decision restates every approved criterion; the task owns a subset.
        criteria = [{**c, "status": "unverified", "evidence": ""}
                    for c in self.state["acceptance_criteria"]]
        owned_paths = next(m for m in self.state["goal_contract"]["body"]["milestones"]
                           if m["id"] == milestone)["affected_paths"]
        return {**envelope(self.state), "status": status, "acceptance_criteria": criteria,
                "next_objective": f"Deliver {milestone}", "affected_paths": list(owned_paths),
                "plan": ["Complete the milestone"],
                "next_task": {"kind": "implement", "milestone_id": milestone,
                              "requirements": ["Real flow"], "validation_plan": ["Execute checks"],
                              "acceptance_criteria": sorted(owned), "findings": []},
                "evidence": ["Independent receipt"], "blocker": "", "agreed_limitations": [],
                "findings": [], "finding_dispositions": []}

    def assign(self, milestone):
        decision = self.decision(milestone)
        output = self.run / f"astra-{milestone}-{len(self.state['stages'])}.json"
        support.atomic_json(output, decision)
        runner.apply_result(self.state, "astra_review", decision,
                            {"output": str(output), "source_revision": support.snapshot(self.root)["revision"]},
                            self.root, self.run)

    def validate(self, passing):
        scope = milestones.scope(self.state)
        statuses = {cid: ("PASS" if cid in passing else "NOT_VERIFIED")
                    for cid in [c["id"] for c in self.state["acceptance_criteria"]]}
        passed = all(statuses[cid] == "PASS" for cid in scope["acceptance_criteria"])
        n = sum(1 for r in self.state["stages"] if r.get("role") == "sol")
        events = self.run / f"sol-{n}.jsonl"
        events.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "check", "type": "command_execution", "command": "execute-milestone",
            "exit_code": 0 if passed else 1, "aggregated_output": str(statuses)}}) + "\n")
        value = {**envelope(self.state), "verdict": "PASS" if passed else "FAIL",
                 "checks_run": ["execute-milestone"],
                 "checks": [{"command": "execute-milestone", "exit_code": 0 if passed else 1,
                             "evidence_ref": "event:check"}],
                 "findings": [], "unverified_criteria": [cid for cid, s in statuses.items() if s == "NOT_VERIFIED"],
                 "criterion_results": [{"id": cid, "status": s, "evidence_refs": ["event:check"]}
                                       for cid, s in statuses.items()],
                 "end_to_end_result": {"status": "PASS" if passed else "FAIL",
                                       "summary": "Executed current outcome", "evidence_refs": ["event:check"]}}
        runner.apply_result(self.state, "sol", value,
                            {"role": "sol", "stage": "sol", "events": str(events), "output": str(events),
                             "source_revision": support.snapshot(self.root)["revision"]},
                            self.root, self.run)

    def accepted(self):
        return set(milestones.summary(self.state)["accepted_milestones"])


class DagScenarios(DagCase):

    def test_dag01_diamond_executes_in_dependency_order(self):
        """DAG-01. Existing: test_milestone_checkpoints.test_dependent_milestone_waits..."""
        self.start_diamond()
        self.assign("MA")
        self.check("initial_task_is_the_root", "MA", self.state["current_task"]["milestone_id"])
        self.check("oracle_ready_after_nothing", {"MA"}, self.oracle.ready(set()))
        self.validate({"C1"})
        self.check("oracle_ready_after_ma", {"MB", "MC"}, self.oracle.ready({"MA"}))
        # D must not be admitted before B and C are accepted.
        before = copy.deepcopy(self.state["current_task"])
        self.expect_raises("premature_dependent_refused", ValueError, self.assign, "MD")
        self.check("current_task_unchanged_by_refusal", before, self.state["current_task"])
        # B and C are a safe overlapping wave under the oracle.
        self.check("oracle_wave_after_ma_contains_b_c", {"MB", "MC"}, set(self.oracle.wave("MB", {"MA"})))
        self.assign("MB")
        self.check("mb_admitted", "MB", self.state["current_task"]["milestone_id"])
        self.check("ma_accepted_once_reviewed", {"MA"}, self.accepted())
        self.validate({"C1", "C2"})
        self.assign("MC")
        self.validate({"C1", "C2", "C3"})
        self.assign("MD")
        self.check("accepted_b_c_before_d", {"MA", "MB", "MC"}, self.accepted())
        self.check("oracle_ready_for_d", {"MD"}, self.oracle.ready({"MA", "MB", "MC"}))
        self.check("md_admitted_after_prereqs", "MD", self.state["current_task"]["milestone_id"])
        self.check("not_complete_before_integration_verification", False,
                   self.state["status"] == "TASK_COMPLETE")
        self.finish(summary="COMPLETE-ordered: no consumer started before its accepted prerequisites")

    def test_dag02_cycles_rejected(self):
        """DAG-02. Existing: test_autopilot_torture cycle case."""
        for label, edges in (("two-node cycle", {"MB": ["MA"], "MA": ["MB"]}),
                             ("self-edge", {"MA": ["MA"]}),
                             ("longer cycle", {"MB": ["MD"], "MD": ["MC"], "MC": ["MB"]})):
            with self.subTest(variant=label):
                draft = diamond()
                for milestone in draft["milestones"]:
                    if milestone["id"] in edges:
                        milestone["depends_on"] = edges[milestone["id"]]
                self.expect_raises(f"[{label}] cyclic_graph_rejected", ValueError,
                                   goals.install_draft, self.state, draft, origin="test")
        self.check("no_builder_from_invalid_graph", 0, len(self.state.get("stages", [])))
        self.finish(summary="PAUSED_SAFE: cycles and self-edges cannot become executable")

    def test_dag03_unknown_prerequisite_rejected(self):
        """DAG-03. New explicit case (runtime prerequisite naming)."""
        draft = diamond()
        draft["milestones"][2]["depends_on"] = ["MZ"]  # C depends on a nonexistent task
        try:
            goals.install_draft(self.state, draft, origin="test")  # may pass draft validation...
        except ValueError:
            self.check_true("unknown_dependency_rejected_at_draft", True)
            self.finish(summary="PAUSED_SAFE: unknown prerequisite rejected at draft validation")
            return
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.state["settings"]["milestone_checkpoints"] = copy.deepcopy(milestones.DEFAULTS)
        self.assign("MA")
        self.validate({"C1"})
        # ...but the scheduler never treats the missing task as satisfied.
        self.expect_raises("unknown_prerequisite_named", ValueError, self.assign, "MC")
        self.check("current_task_unchanged", "MA", self.state["current_task"]["milestone_id"])
        self.finish(summary="PAUSED_SAFE: unknown prerequisite is named, never assumed accepted")

    def test_dag04_duplicate_ids_rejected(self):
        """DAG-04. Existing: validate_body uniqueness (test_goals schema cases)."""
        before = copy.deepcopy(self.state)
        for label, mutate in (("milestone id", lambda d: d["milestones"].append(
            {"id": "MA", "objective": "duplicate", "acceptance_criteria": ["C1"], "depends_on": [],
             "affected_paths": ["dup/"]})),
            ("acceptance id", lambda d: d["acceptance_criteria"].append(
                {**d["acceptance_criteria"][0], "criterion": "duplicate criterion"})),
            ("question id", lambda d: d["open_blocking_questions"].extend([
                {"id": "Q1", "question": "First", "why": "w", "options": ["a"], "proposed_default": "a"},
                {"id": "Q1", "question": "Second", "why": "w", "options": ["b"], "proposed_default": "b"}]))):
            with self.subTest(variant=label):
                draft = diamond()
                mutate(draft)
                self.expect_raises(f"[{label}] duplicate_ids_rejected", ValueError,
                                   goals.install_draft, self.state, draft, origin="test")
        self.check("draft_untouched_by_rejections", before, self.state)
        self.finish(summary="PAUSED_SAFE: duplicate milestone/criterion/question ids rejected")

    def test_dag05_initial_task_needs_finished_dependency(self):
        """DAG-05. Existing: covered inside test_dependent_milestone_waits... (explicit case)."""
        self.start_diamond()
        self.expect_raises("unfinished_prerequisite_refused", ValueError, self.assign, "MB")
        self.check("no_builder_started", 0, len(self.state.get("stages", [])))
        self.check("no_current_task", True, not self.state.get("current_task"))
        self.assign("MA")
        self.check("recovery_root_task_admitted", "MA", self.state["current_task"]["milestone_id"])
        self.finish(summary="PAUSED_SAFE: B refused as the initial task while A is unaccepted")

    def test_dag06_partial_milestones_are_not_whole_completion(self):
        """DAG-06. Existing: test_pass_advances_with_later_criteria_unverified and
        test_unverified_whole_flow_cannot_complete."""
        self.start_diamond()
        self.assign("MA")
        self.validate({"C1"})
        self.assign("MB")  # the next decision records A's acceptance checkpoint
        self.check("ma_scheduling_checkpoint", {"MA"}, self.accepted())
        complete = {**self.decision("MD", "COMPLETE")}
        complete["acceptance_criteria"] = [{**c, "status": "verified", "evidence": "event:check"}
                                           for c in self.state["acceptance_criteria"]]
        self.check_false("partial_progress_is_not_completion",
                         support.completion_ready(self.state, complete, support.snapshot(self.root)))
        self.check("project_still_running", False, self.state["status"] == "TASK_COMPLETE")
        self.check("downstream_progress_allowed", {"MB", "MC"}, self.oracle.ready({"MA"}))
        self.finish(summary="READY_BUILD: accepted A unlocks B/C; overall completion still blocked")

    def test_dag07_overlapping_ownership_serializes(self):
        """DAG-07. Existing: dispatch wave selection in test_assignment_scenarios."""
        draft = diamond()
        next(row for row in draft["milestones"] if row["id"] == "MC")["affected_paths"] = ["server/"]
        goals.install_draft(self.state, draft, origin="test")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.state["settings"]["milestone_checkpoints"] = copy.deepcopy(milestones.DEFAULTS)
        self.state["settings"]["orchestration"] = {"max_parallel": 2}
        self.oracle = ReadySetOracle(draft["milestones"])
        self.assign("MA")
        self.validate({"C1"})
        self.assign("MB")
        self.check("oracle_serializes_overlap", ["MB"], self.oracle.wave("MB", {"MA"}))
        self.check("scheduler_admits_only_safe_subset", [],
                   [row["id"] for row in dispatch.select(self.state)])
        self.check("no_second_writer_to_shared_path", True,
                   not any(batch for batch in [dispatch.select(self.state)]))
        # Ownership entirely absent is not eligible either; a revised plan
        # without MC ownership still cannot form a parallel wave.
        revised = diamond()
        next(row for row in revised["milestones"] if row["id"] == "MC").pop("affected_paths")
        goals.install_draft(self.state, revised, origin="test")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.oracle = ReadySetOracle(revised["milestones"])
        self.check("mc_never_ready_without_ownership", False,
                   any("MC" in self.oracle.ready(accepted)
                       for accepted in (set(), {"MA"}, {"MA", "MB"})))
        self.check("absent_ownership_not_parallel", [],
                   [row["id"] for row in dispatch.select(self.state)])
        self.finish(summary="SERIAL_PROGRESS: overlap or missing ownership never yields two writers")

    def test_dag08_units_stop_at_their_boundaries(self):
        """DAG-08. Existing: test_units.* (separate units, entrypoints, boundaries)."""
        self.start_diamond()
        planning_stages = len(self.state.get("stages", []))
        with self.forbid_real_launches(runner):
            self.assign("MA")
        self.check("planning_apply_dispatched_no_builder", True,
                   all(rec.get("stage") != "terra" for rec in self.state["stages"]))
        self.check("one_handoff_record_added", 1, len(self.state["stages"]) - planning_stages)
        self.check("next_unit_saved", "terra", self.state["next_stage"])
        review = {**self.decision("MA", "REWORK"), "findings": [
            {"severity": "high", "finding": "Contract schema incomplete", "evidence": "event:check",
             "blocking": True}]}
        support.atomic_json(self.run / "review-rework.json", review)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", review,
                                {"output": str(self.run / "review-rework.json"),
                                 "source_revision": support.snapshot(self.root)["revision"]},
                                self.root, self.run)
        self.check("review_routes_to_resolver_not_builder", "astra_resolve", self.state["next_stage"])
        self.check("no_builder_launched_by_review", True,
                   all(rec.get("stage") != "terra" for rec in self.state["stages"]))
        self.finish(summary="UNIT_BOUNDARY: each unit hands off without cross-unit dispatch")

    def test_dag09_modified_handoff_is_not_authorization(self):
        """DAG-09. Existing: test_units.test_changed_source_pauses_resolver..."""
        self.start_diamond()
        self.assign("MA")
        review = {**self.decision("MA", "REWORK"), "findings": [
            {"severity": "high", "finding": "Contract mismatch", "evidence": "event:check", "blocking": True}]}
        support.atomic_json(self.run / "review-rework.json", review)
        pinned_revision = support.snapshot(self.root)["revision"]
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", review,
                                {"output": str(self.run / "review-rework.json"),
                                 "source_revision": pinned_revision}, self.root, self.run)
        (self.root / "contract" / "schema.json").parent.mkdir(exist_ok=True)
        (self.root / "contract" / "schema.json").write_text('{"tampered": true}\n')
        diagnosis = {**self.decision("MA", "REWORK"), "findings": [], "finding_dispositions": [],
                     "diagnosis": "Handoff edited mid-flight", "evidence": ["event:check"]}
        diagnosis["acceptance_criteria"] = [{**c, "status": "verified", "evidence": "claim"}
                                            for c in self.state["acceptance_criteria"]]
        self.expect_raises("tampered_handoff_refused", support.Paused,
                           runner.apply_result, self.state, "astra_resolve", diagnosis,
                           {"output": str(self.run / "resolve.json"), "source_revision": "forged"},
                           self.root, self.run)
        self.check("no_writer_from_forged_handoff", True,
                   all(rec.get("stage") != "terra" for rec in self.state["stages"]))
        self.finish(summary="PAUSED_SAFE: edited source revision in a handoff refuses dispatch")

    def test_dag10_unanswered_planner_questions_preserved(self):
        """DAG-10. Existing: test_planning.test_requirements_handoff_is_separate... and
        test_goals answer-preservation tests."""
        self.draft(questions=True)
        goals.present(self.state)
        self.check("q1_tracked", ["Q1"],
                   [q["id"] for q in self.state.get("pending_questions", [])])
        self.expect_raises("approve_refused_with_open_question", ValueError,
                           goals.approve, self.state, goals.token(self.state["goal_contract"]))
        # While Q1 is unanswered the runner launches no planner at all, so the
        # planner cannot drop what it is never allowed to touch.
        support.atomic_json(self.run / "state.json", self.state)
        with patch.object(runner, "run_role", side_effect=AssertionError("planner must not launch")):
            import contextlib, io, sys as _sys
            argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run), "--resume-paused"]
            with patch.object(_sys, "argv", argv), patch.object(support, "assert_no_legacy_process"), \
                    patch.object(support, "local_settings", return_value=self.local), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    runner.main()
                except SystemExit as exit_code:
                    self.check("resume_launches_nothing_while_q1_open", 2, exit_code.code)
        self.state = support.read(self.run / "state.json")
        self.check("q1_retained_across_resume", ["Q1"],
                   [q["id"] for q in self.state.get("pending_questions", [])])
        # Answering Q1 and redrafting without it is the legitimate resolution.
        goals.answer(self.state, "Q1", "Use a CLI")
        self.draft()
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.check_true("answered_resolution_recovers", goals.approved(self.state))
        self.bundle.log("scoped_note", note="a direct install_draft call on an unapproved draft can "
                       "replace open questions (revision_guard bypasses unapproved-draft swaps); the "
                       "supported path is protected by the resume gate, which never launches the "
                       "planner while a blocking question is unanswered")
        self.finish(summary="PAUSED_SAFE: unanswered blocking question cannot disappear silently")


if __name__ == "__main__":
    unittest.main()
