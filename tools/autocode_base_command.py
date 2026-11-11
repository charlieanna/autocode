"""Run one planned command in a clean copy of the base revision (#3160).

The clean replay (autocode_check_replay) runs a planned check on the candidate in a copy that
autocode_verify.scratch_run makes; when that check fails, autocode_base_attribution compares it with the
same command on the base revision, run here the same way: a detached worktree of the base with the run's
dependencies, ignored launch inputs and joint inputs, the workspace's path replaced by the copy's, and a
review follow-up's reviewed change applied as the proof applies it. The copy is removed afterwards.
"""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_joint_inputs as joint_inputs
    from . import autocode_verify as verify
except ImportError:
    import autocode_joint_inputs as joint_inputs
    import autocode_verify as verify


def run(
    workspace,
    run_dir,
    *,
    base,
    command,
    timeout=verify.DEFAULT_TIMEOUT,
    base_patch=None,
    dependencies_from=None,
    ignored_inputs=None,
) -> dict:
    """``command``'s receipt from a clean copy of ``base``, naming that copy as ``tree`` so its output can
    be compared with the candidate's (autocode_base_attribution.judge)."""
    workspace, run_dir = Path(workspace), Path(run_dir)
    tree = verify.make_tree(
        workspace,
        base,
        run_dir / "scratch" / "base",
        workspace,
        {},
        dependencies_from=dependencies_from or workspace,
        patch=base_patch,
        ignored_inputs=ignored_inputs,
    )
    try:
        joint_inputs.copy(workspace, tree)
        for root in dict.fromkeys((str(workspace.resolve()), str(workspace))):
            command = command.replace(root, str(tree))
        receipt = verify.run_command(command, tree, run_dir / "base-command.log", timeout=timeout)
    finally:
        verify.remove_tree(workspace, tree)
    return {**receipt, "results": None, "error": receipt.get("error") or "", "tree": str(tree)}
