"""A continuation restored from a checkpoint records whether its project checkout was worked in.

The regression proof reads ignored code from the run's original checkout
(project_workspace) and trusts it only when no Builder of the run worked there. A restore of
an --in-place run continues in a new worktree whose original checkout is the one that run's
Builder edited, so the continuation carries project_worked_in_place, and every later restore
keeps it (docs/bugs/2026-10-06-regression-proof-scaffold-base.md).
"""

import unittest
from pathlib import Path

import autocode_checkpoint_continuation as continuation
import autocode_contract_identity as identity
import autocode_cost as cost
import autocode_regression as regression
import autocode_util as util
from goal_fixtures import body

PROJECT = Path("/fixture/project")


def approved_run(workspace, **extra):
    """An original run with an authenticated approved plan, as continuation.create requires."""
    contract = {"task_id": "run-task", "revision": 1, "body": body()}
    contract["hash"] = util.digest(contract)
    approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract), "at": util.now()}
    contract.update(approval_status="approved", approval_event=approval, origin="astra_discovery")
    return {
        "version": 3,
        "task_id": "run-task",
        "task": "Build fixture",
        "workspace": str(workspace),
        "run_dir": str(Path(workspace) / ".autocode/runs/original"),
        "status": "PAUSED_REQUESTED",
        "goal_contract": contract,
        "user_events": [approval],
        "base_commit": "b" * 40,
        **extra,
    }


def restore(original, project, name):
    """continuation.create as autocode_checkpoint_cli.restore calls it."""
    workspace = PROJECT / ".autocode/worktrees" / name
    checkpoint = {"id": "checkpoint-" + name, "commit": "c" * 40}
    return continuation.create(
        original,
        checkpoint,
        workspace,
        workspace / ".autocode/runs" / name,
        "autocode/" + name,
        project,
        "operation-" + name,
    )


class ProjectWorkedInPlace(unittest.TestCase):
    def test_a_restore_of_an_in_place_run_marks_its_project_as_worked_in(self):
        # No task worktree metadata: the restore names the original's own workspace as the project.
        child = restore(approved_run(PROJECT), PROJECT, "first")
        self.assertEqual(str(PROJECT), child["project_workspace"])
        self.assertIs(True, child["project_worked_in_place"])

    def test_a_later_restore_keeps_the_mark(self):
        child = restore(approved_run(PROJECT), PROJECT, "first")
        # The child works in a worktree whose metadata names the project, so the second restore
        # alone could not tell that the first run's Builder edited the project checkout.
        grandchild = restore(child, PROJECT, "second")
        self.assertNotEqual(child["workspace"], grandchild["project_workspace"])
        self.assertIs(True, grandchild["project_worked_in_place"])

    def test_a_restore_of_a_worktree_run_does_not_mark_its_project(self):
        original = approved_run(PROJECT / ".autocode/worktrees/task-1", project_workspace=str(PROJECT))
        child = restore(original, PROJECT, "first")
        self.assertEqual(str(PROJECT), child["project_workspace"])
        self.assertNotIn("project_worked_in_place", child)
        self.assertNotIn("project_worked_in_place", restore(child, PROJECT, "second"))


class RegressionProofDependencies(unittest.TestCase):
    """autocode_regression.proof_dependencies is what _prove passes to verify.verify."""

    def test_a_continuation_of_an_in_place_run_reads_a_checkout_that_is_not_independent(self):
        grandchild = restore(restore(approved_run(PROJECT), PROJECT, "first"), PROJECT, "second")
        self.assertEqual(
            {"dependencies_from": str(PROJECT), "independent_dependencies": False},
            regression.proof_dependencies(grandchild, grandchild["workspace"]),
        )

    def test_other_runs_leave_independence_to_verify(self):
        worktree = PROJECT / ".autocode/worktrees/task-1"
        child = restore(approved_run(worktree, project_workspace=str(PROJECT)), PROJECT, "first")
        self.assertEqual(
            {"dependencies_from": str(PROJECT), "independent_dependencies": None},
            regression.proof_dependencies(child, child["workspace"]),
        )
        self.assertEqual(
            {"dependencies_from": str(PROJECT), "independent_dependencies": None},
            regression.proof_dependencies(approved_run(PROJECT), PROJECT),
        )


if __name__ == "__main__":
    unittest.main()


class SpendCarriesOver(unittest.TestCase):
    """Like active time, a restore never renews the money cap: the original's spend carries over (#721)."""

    TOKENS = {"input_tokens": 1000, "output_tokens": 200}

    def stage(self, model="gpt-5.6-terra", tokens=None):
        return {"stage": "terra", "model": model, "metrics": {"provider_tokens": tokens or self.TOKENS}}

    def test_a_restore_keeps_counting_what_the_original_spent(self):
        original = approved_run(PROJECT, stages=[self.stage()], settings={"limits": {"max_cost_usd": 5}})
        spent = cost.spent(original)
        self.assertGreater(spent, 0)
        child = restore(original, PROJECT, "first")
        self.assertEqual([], child["stages"])
        self.assertEqual({"usd": round(spent, 6), "unpriced_stages": 0, "partial_stages": 0}, child["inherited_spend"])
        view = cost.view(child)
        self.assertAlmostEqual(spent, view["spent_usd"])
        self.assertAlmostEqual(spent, view["inherited_usd"])
        self.assertTrue(view["complete"])
        self.assertIn("before the restored checkpoint", cost.line({**view, "estimated_usd": 0.1}))
        grandchild = restore({**child, "stages": [self.stage()]}, PROJECT, "second")
        self.assertAlmostEqual(2 * spent, grandchild["inherited_spend"]["usd"])

    def test_an_unpriced_original_keeps_the_continuations_total_a_floor(self):
        original = approved_run(PROJECT, stages=[self.stage(), self.stage("mystery-model")])
        child = restore(original, PROJECT, "first")
        self.assertEqual(1, child["inherited_spend"]["unpriced_stages"])
        view = cost.view(child)
        self.assertEqual((1, False), (view["unpriced_stages"], view["complete"]))
        self.assertTrue(cost.line(view).startswith("Cost: at least $"))
        only_unknown = restore(approved_run(PROJECT, stages=[self.stage("mystery-model")]), PROJECT, "second")
        self.assertEqual({"usd": 0.0, "unpriced_stages": 1, "partial_stages": 0}, only_unknown["inherited_spend"])
        self.assertIsNone(cost.view(only_unknown)["spent_usd"])

    def test_a_run_that_spent_nothing_carries_nothing(self):
        self.assertNotIn("inherited_spend", restore(approved_run(PROJECT), PROJECT, "first"))
