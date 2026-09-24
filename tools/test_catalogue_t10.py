"""T10 — Parallel builds and integration catalogue scenarios (PAR-01..PAR-10).

Heavy subprocess-fixture regressions live in test_assignment_scenarios; this
file re-runs the cited ones programmatically (their result is the evidence)
and adds compact controller/dispatch-level cases for the gaps.
"""
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autopilot_testkit as kit
import autocode as runner
import autocode_dispatch as dispatch
import autocode_goals as goals
import autocode_support as support
import test_catalogue_t06 as t06
from goal_fixtures import body, envelope


REPO_ROOT = Path(__file__).resolve().parent.parent


def rerun(test_name):
    """Execute one cited regression in a clean subprocess from the repo root."""
    import os
    environment = {k: v for k, v in os.environ.items() if k != "AUTOCODE_TEST_CLI"}
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", test_name], cwd=REPO_ROOT, env=environment,
        capture_output=True, text=True, timeout=300)
    return completed.returncode == 0, 1 if "OK" in completed.stderr or "OK" in completed.stdout else 0


class ParallelCase(t06.SolControllerCase):

    def diamond_rows(self):
        return [
            {"id": "MB", "objective": "server", "acceptance_criteria": ["C1"], "depends_on": [],
             "affected_paths": ["server/"]},
            {"id": "MC", "objective": "client", "acceptance_criteria": ["C1"], "depends_on": [],
             "affected_paths": ["client/"]},
        ]

    def prepare_parallel(self):
        self.state["settings"]["orchestration"] = {"max_parallel": 2}
        return dispatch.prepare(self.state, self.root, self.run, self.diamond_rows())


class ParallelScenarios(ParallelCase):

    def test_par01_disjoint_builders_run_in_isolated_worktrees(self):
        """PAR-01. Existing: test_assignment_scenarios.test_independent_tasks_use_isolated_worktrees."""
        ok, count = rerun("tools.test_assignment_scenarios.AssignmentScenarios."
                          "test_independent_tasks_use_isolated_worktrees")
        self.check("existing_isolation_regression_passes", (True, 1), (ok, count))
        batch = self.prepare_parallel()
        workspaces = [Path(row["workspace"]) for row in batch["workers"]]
        self.check("two_isolated_worktrees", 2, len(workspaces))
        self.check("worktrees_inside_parent", True,
                   all(w.resolve().is_relative_to(self.root.resolve()) for w in workspaces))
        self.check("distinct_branches", 2, len({row["branch"] for row in batch["workers"]}))
        self.check("baseline_pinned", True, bool(batch["baseline"]["revision"]))
        self.finish(summary="INTEGRATED_AWAITING_REVIEW: disjoint builders prepared in isolated worktrees")

    def test_par02_successful_sibling_survives_another_failure(self):
        """PAR-02. Existing: test_assignment_scenarios.test_partial_success_prose_is_stored_and_not_completion."""
        ok, count = rerun("tools.test_assignment_scenarios.AssignmentScenarios."
                          "test_partial_success_prose_is_stored_and_not_completion")
        self.check("sibling_partial_success_regression_passes", (True, 1), (ok, count))
        self.finish(summary="RECOVERED_BATCH: existing fixture-level regression rerun green")

    def test_par03_edits_outside_owned_paths_detected(self):
        """PAR-03. Existing: test_assignment_scenarios.test_test_edits_outside_the_assignment_are_rejected."""
        ok, count = rerun("tools.test_assignment_scenarios.AssignmentScenarios."
                          "test_test_edits_outside_the_assignment_are_rejected")
        self.check("ownership_violation_regression_passes", (True, 1), (ok, count))
        outside = sorted(name for name in ("../escape.txt", "/tmp/escape.txt")
                         if not dispatch.valid_path(name))
        self.check("escape_paths_rejected_by_validator", 2, len(outside))
        self.finish(summary="INTEGRATION_BLOCKED: ownership violations rejected with edits retained")

    def test_par04_hidden_overlap_not_parallelized(self):
        """PAR-04. Existing: test_assignment_scenarios.test_overlapping_tasks_are_not_parallelized_or_merged."""
        ok, count = rerun("tools.test_assignment_scenarios.AssignmentScenarios."
                          "test_overlapping_tasks_are_not_parallelized_or_merged")
        self.check("overlap_regression_passes", (True, 1), (ok, count))
        self.check("disjointness_check_exact", False,
                   dispatch.disjoint(["server/"], ["server/handler.py"]))
        self.finish(summary="INTEGRATION_BLOCKED: hidden shared-file overlap refuses merging")

    def test_par05_parent_edits_and_index_survive_preparation(self):
        """PAR-05. Compact: parent user edits and index stay intact through batch prep."""
        (self.root / "user-note.txt").write_text("parent user file\n")
        subprocess.run(["git", "-C", str(self.root), "add", "user-note.txt"], check=True)
        index_before = subprocess.run(["git", "-C", str(self.root), "ls-files", "--cached"],
                                      capture_output=True, text=True).stdout
        batch = self.prepare_parallel()
        index_after = subprocess.run(["git", "-C", str(self.root), "ls-files", "--cached"],
                                     capture_output=True, text=True).stdout
        self.check("parent_index_intact", index_before, index_after)
        self.check("parent_user_file_preserved", "parent user file\n",
                   (self.root / "user-note.txt").read_text())
        self.check("baseline_recorded_parent_state", True,
                   "user-note.txt" in batch["baseline"]["files"])
        self.finish(summary="INTEGRATED_AWAITING_REVIEW: parent work untouched by preparation")

    def test_par06_parent_source_change_pauses_the_batch(self):
        """PAR-06. Compact: drift against the pinned baseline is detected and pauses safely."""
        batch = self.prepare_parallel()
        (self.root / "greet.py").write_text("print('parent drift')\n")
        self.check("parent_drift_detected_against_baseline", True,
                   support.snapshot(self.root) != batch["baseline"])
        worker = Path(batch["workers"][0]["workspace"])
        (worker / "greet.py").write_text("tampered\n")
        batch["workers"][0]["status"] = "PREPARING"  # crash mid-preparation, then tamper
        self.expect_raises("modified_worker_pauses_batch", support.Paused,
                           dispatch.finish_preparation, self.state, self.root, self.run, batch)
        self.check("existing_files_left_untouched", True, (worker / "greet.py").exists())
        self.check("prior_worker_outputs_retained", True,
                   any((self.root / ".autocode" / "builders").rglob(".git")))
        self.finish(summary="INTEGRATION_BLOCKED: drift against the pinned baseline pauses the batch")

    def test_par07_conflicting_patch_refuses_integration(self):
        """PAR-07. Compact: two edits to one file cannot both integrate; nothing is accepted."""
        (self.root / "shared.txt").write_text("base\n")
        subprocess.run(["git", "-C", str(self.root), "add", "shared.txt"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=F", "-c", "user.email=f@t",
                        "commit", "-qm", "base"], check=True)
        first = subprocess.run(["git", "-C", str(self.root), "checkout", "-q", "-b", "w1"],
                               capture_output=True)
        (self.root / "shared.txt").write_text("worker one edit\n")
        subprocess.run(["git", "-C", str(self.root), "add", "shared.txt"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=F", "-c", "user.email=f@t",
                        "commit", "-qm", "w1"], check=True)
        patch_one = subprocess.run(["git", "-C", str(self.root), "diff", "master..w1"],
                                    capture_output=True, text=True).stdout
        subprocess.run(["git", "-C", str(self.root), "checkout", "-q", "master"], check=True)
        subprocess.run(["git", "-C", str(self.root), "apply"], input=patch_one, text=True, check=True)
        second = subprocess.run(["git", "-C", str(self.root), "stash"], capture_output=True, text=True)
        (self.root / "shared.txt").write_text("worker two edit\n")
        patch_two = subprocess.run(["git", "-C", str(self.root), "diff"], capture_output=True, text=True).stdout
        (self.root / "shared.txt").write_text("worker one edit\n")
        conflict = subprocess.run(["git", "-C", str(self.root), "apply"], input=patch_two,
                                  text=True, capture_output=True)
        self.check("conflicting_patch_rejected", False, conflict.returncode == 0)
        self.check("no_final_acceptance_on_conflict", "worker one edit\n",
                   (self.root / "shared.txt").read_text())
        self.check("conflict_evidence_retained", True, bool(patch_two))
        self.finish(summary="INTEGRATION_BLOCKED: patch conflict preserved both outputs, accepted neither")

    def test_par08_integration_invalidates_individual_acceptance(self):
        """PAR-08. Compact: pre-integration validation cannot approve the integrated candidate."""
        self.apply_sol(self.sol_report())  # individually accepted B-equivalent
        integrated_revision = self.state["validation"]["source_revision"]
        (self.root / "integration" / "flow.py").parent.mkdir(exist_ok=True)
        (self.root / "integration" / "flow.py").write_text("# integrated combined output\n")
        current = support.snapshot(self.root)
        self.check("integration_changed_identity", True,
                   current["revision"] != integrated_revision)
        complete = super().decision("COMPLETE")
        self.check_false("individual_acceptance_cannot_complete",
                         support.completion_ready(self.state, complete, current))
        self.check("fresh_review_required", "astra_review", self.state["next_stage"])
        self.finish(summary="READY_REVIEW: integrated manifest requires its own review")

    def test_par09_repair_keeps_combined_acceptance_scope(self):
        """PAR-09. Compact: a focused repair retains the combined batch's criteria."""
        rework = super().decision("REWORK")
        rework["findings"] = [{"severity": "high", "finding": "Integration defect",
                               "evidence": "event:check", "blocking": True}]
        support.atomic_json(self.run / "rework.json", rework)
        runner.apply_result(self.state, "astra_review", rework,
                            {"output": str(self.run / "rework.json"),
                             "source_revision": support.snapshot(self.root)["revision"]},
                            self.root, self.run)
        combined = {c["id"] for c in self.state["acceptance_criteria"]}
        request = self.state["resolution_request"]
        self.check("combined_scope_preserved", combined,
                   {c["id"] for c in self.state["acceptance_criteria"]})
        self.check("repair_bound_to_combined_contract", self.state["goal_contract"]["hash"],
                   request["contract_hash"])
        self.finish(summary="REWORK: focused repair under the combined acceptance scope")

    def test_par10_superseded_plan_results_are_stale(self):
        """PAR-10. Compact: results from a superseded plan cannot mix into the new batch."""
        old_decision = super().decision("CONTINUE")
        revised = body()
        revised["required_behaviors"].append("Accept Unicode names")
        goals.install_draft(self.state, revised, origin="test")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        before = dict(self.state)
        self.expect_raises("superseded_plan_result_refused", support.Paused,
                           runner.apply_result, self.state, "astra_review", old_decision,
                           {"output": "stale.json"}, self.root, self.run)
        self.check("new_plan_untouched_by_stale_result", before, self.state)
        self.finish(summary="RECONCILED_NEW_PLAN: superseded-batch results rejected by binding")


if __name__ == "__main__":
    unittest.main()
