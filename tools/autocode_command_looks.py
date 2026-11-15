"""One command run on a scratch tree of a run's original code, to see what it does.

AutoCode finds some things about a project by running a command and reading its exit code, where a rule
over names would only guess: which of a plan's commands runs the project's tests
(autocode_plan_command_roles.settle), and which files are tests such a command runs (``find_tests``
there). The regression proof looks, and so does the check of a drafted plan (autocode_plan_test_suite),
so the one way to look lives here, below both.

A look proves nothing about a change. It runs in a tree of its own, made as the proof's trees are, and
the tree is removed afterwards.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_verify as verify
except ImportError:
    import autocode_launch_inputs as launch_inputs
    import autocode_verify as verify

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def looker(state: RunState, workspace, run_dir, base, say, *, limit):
    """``look(command, label, changes, edits, strict=False)``: one command on a tree of the code at
    ``base`` with ``changes`` ({path: status}, read from ``workspace``) applied and each file of ``edits``
    ({path: text}) overwritten. The command's exit code, or None when the tree could not be made or the
    command did not finish within ``limit`` seconds. A path of ``edits`` that is no regular file there is
    passed over, or with ``strict`` ends the look with None. ``say(message, command=, output=)`` reports
    each run."""
    out = Path(run_dir).resolve() / "regression" / ("roles-" + uuid.uuid4().hex)
    dependencies = state.get("project_workspace") or str(workspace)
    runs: list[str] = []

    def look(command, label, changes, edits, strict=False):
        runs.append(label)
        folder = out / f"{len(runs):02d}-{label}"
        say("Running the plan's commands on the original code to see what they do", command=command, output=folder)
        try:
            tree = verify.make_tree(
                workspace,
                base,
                Path(run_dir) / "scratch" / f"roles-{out.name[-8:]}-{len(runs):02d}",
                workspace,
                changes,
                dependencies_from=dependencies,
                ignored_inputs=launch_inputs.supply(state, workspace, run_dir),
            )
        except (OSError, ValueError):
            return None
        try:
            for path, text in edits.items():
                file = tree / path
                if file.is_file() and not file.is_symlink():
                    file.write_text(text)
                elif strict:
                    return None
            receipt = verify.run_suite(None, command, tree, folder, label, timeout=limit)
        except (OSError, ValueError):
            return None
        finally:
            verify.remove_tree(workspace, tree)
        finished = verify.command_receipt.completed(receipt) and not receipt.get("timed_out")
        return receipt.get("exit_code") if finished else None

    return look
