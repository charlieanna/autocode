"""Prepare a disposable source copy with optional patch and retained overlays.

Tree operations are supplied by the verification runner; this module never
imports it. Review input admission happens before applying the explicit patch.
"""

import subprocess
from pathlib import Path

try:
    from . import autocode_joint_inputs as joint_inputs
    from . import autocode_review_proof as review_proof
    from . import autocode_scratch_overlay as scratch_overlay
except ImportError:
    import autocode_joint_inputs as joint_inputs
    import autocode_review_proof as review_proof
    import autocode_scratch_overlay as scratch_overlay


class ScratchPatchError(ValueError):
    """An unapplied patch has no execution receipt."""


def prepare(
    workspace,
    run_dir,
    *,
    patch=None,
    files=None,
    links=None,
    source_paths=(),
    dependencies_from=None,
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
    make_tree,
    changed_files,
    remove_tree,
    git,
    _before_patch=None,
    review_source=None,
):
    """Existing copy operations, supplied to the owned preparation worker."""
    workspace, run_dir = Path(workspace), Path(run_dir)
    head = git(workspace, "rev-parse", "HEAD").strip()
    tree = make_tree(
        workspace,
        head,
        run_dir / "scratch" / "tree",
        workspace,
        changed_files(workspace, head, source_paths=source_paths),
        dependencies_from=dependencies_from or workspace,
        generated_record=generated_record,
        generated_unrecorded=generated_unrecorded,
        ignored_inputs=ignored_inputs,
    )
    try:
        if review_source is not None:
            review_proof.verify_source(tree, review_source)
        if _before_patch is not None:
            _before_patch(tree)
        if patch:
            applied = subprocess.run(["git", "-C", str(tree), "apply", str(patch)], capture_output=True, text=True)
            if applied.returncode:
                raise ScratchPatchError(
                    f"git apply {patch} failed: {(applied.stderr or applied.stdout).strip()[-300:]}"
                )
        scratch_overlay.apply(tree, files, links)
        joint_inputs.copy(workspace, tree)
    except BaseException:
        remove_tree(workspace, tree)
        raise
    return tree
