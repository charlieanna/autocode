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
import autocode_regression as regression
import autocode_util as util
from goal_fixtures import body

PROJECT = Path('/fixture/project')


def approved_run(workspace, **extra):
    """An original run with an authenticated approved plan, as continuation.create requires."""
    contract = {'task_id': 'run-task', 'revision': 1, 'body': body()}
    contract['hash'] = util.digest(contract)
    approval = {'kind': 'goal_approval', 'actor': 'user_cli', 'token': identity.token(contract), 'at': util.now()}
    contract.update(approval_status='approved', approval_event=approval, origin='astra_discovery')
    return {'version': 3, 'task_id': 'run-task', 'task': 'Build fixture', 'workspace': str(workspace),
            'run_dir': str(Path(workspace) / '.autocode/runs/original'), 'status': 'PAUSED_REQUESTED',
            'goal_contract': contract, 'user_events': [approval], 'base_commit': 'b' * 40, **extra}


def restore(original, project, name):
    """continuation.create as autocode_checkpoint_cli.restore calls it."""
    workspace = PROJECT / '.autocode/worktrees' / name
    checkpoint = {'id': 'checkpoint-' + name, 'commit': 'c' * 40}
    return continuation.create(original, checkpoint, workspace, workspace / '.autocode/runs' / name,
                               'autocode/' + name, project, 'operation-' + name)


class ProjectWorkedInPlace(unittest.TestCase):
    def test_a_restore_of_an_in_place_run_marks_its_project_as_worked_in(self):
        # No task worktree metadata: the restore names the original's own workspace as the project.
        child = restore(approved_run(PROJECT), PROJECT, 'first')
        self.assertEqual(str(PROJECT), child['project_workspace'])
        self.assertIs(True, child['project_worked_in_place'])

    def test_a_later_restore_keeps_the_mark(self):
        child = restore(approved_run(PROJECT), PROJECT, 'first')
        # The child works in a worktree whose metadata names the project, so the second restore
        # alone could not tell that the first run's Builder edited the project checkout.
        grandchild = restore(child, PROJECT, 'second')
        self.assertNotEqual(child['workspace'], grandchild['project_workspace'])
        self.assertIs(True, grandchild['project_worked_in_place'])

    def test_a_restore_of_a_worktree_run_does_not_mark_its_project(self):
        original = approved_run(PROJECT / '.autocode/worktrees/task-1', project_workspace=str(PROJECT))
        child = restore(original, PROJECT, 'first')
        self.assertEqual(str(PROJECT), child['project_workspace'])
        self.assertNotIn('project_worked_in_place', child)
        self.assertNotIn('project_worked_in_place', restore(child, PROJECT, 'second'))


class RegressionProofDependencies(unittest.TestCase):
    """autocode_regression.proof_dependencies is what _prove passes to verify.verify."""

    def test_a_continuation_of_an_in_place_run_reads_a_checkout_that_is_not_independent(self):
        grandchild = restore(restore(approved_run(PROJECT), PROJECT, 'first'), PROJECT, 'second')
        self.assertEqual({'dependencies_from': str(PROJECT), 'independent_dependencies': False},
                         regression.proof_dependencies(grandchild, grandchild['workspace']))

    def test_other_runs_leave_independence_to_verify(self):
        worktree = PROJECT / '.autocode/worktrees/task-1'
        child = restore(approved_run(worktree, project_workspace=str(PROJECT)), PROJECT, 'first')
        self.assertEqual({'dependencies_from': str(PROJECT), 'independent_dependencies': None},
                         regression.proof_dependencies(child, child['workspace']))
        self.assertEqual({'dependencies_from': str(PROJECT), 'independent_dependencies': None},
                         regression.proof_dependencies(approved_run(PROJECT), PROJECT))


if __name__ == '__main__':
    unittest.main()
