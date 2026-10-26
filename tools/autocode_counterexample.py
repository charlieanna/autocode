"""Run a probe against its deliberately constructed counterexample (#1769).

A probe that exits 0 on the code shows its claim only if it would fail were the
claim false. A probed row names that case as one edit (``file``, ``find``,
``replace``, autocode_test_cases.COUNTEREXAMPLE); ``runner`` makes the edit in a
scratch copy, never in the workspace, and re-runs the probe there. Deciding what
the exit codes mean is autocode_test_cases.run_probes's job.

The caller passes the scratch runner (autocode_verify.scratch_run, or the launch
inputs' wrapper of it), so this module imports nothing from the runner.
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

try:
    from . import autocode_test_cases as test_cases
    from .autocode_protected_paths import relative_name
except ImportError:
    import autocode_test_cases as test_cases
    from autocode_protected_paths import relative_name


def edited(name: str, counterexample: dict, workspace, files=None) -> str:
    """The counterexample's file content after its edit; raises ValueError when it cannot be made.

    ``files`` is the scratch overlay the probe already runs with ({name: source path}); a
    counterexample to one of those files edits that copy, any other edits the repository's file."""
    try:
        relative_name(name)
    except ValueError:
        raise ValueError(f"the counterexample's file {name!r} is not a repository-relative path") from None
    if files and name in files:
        source = Path(files[name])
    else:
        root = Path(workspace).resolve()
        source = Path(workspace) / name
        if not source.resolve().is_relative_to(root):
            raise ValueError(f"the counterexample's file {name!r} is outside the repository")
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"the counterexample's file {name!r} is not a file in the repository")
    try:
        text = source.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"the counterexample's file {name!r} is not UTF-8 text") from None
    return test_cases.apply_counterexample(text, {**counterexample, "file": name})


def runner(scratch_run, workspace, out_dir, *, timeout, files=None):
    """``run(command, counterexample)``: the probe's receipt in a scratch copy with the edit made.

    A counterexample that cannot be made returns ``{"error": ...}`` without running anything."""
    count = itertools.count(1)

    def run(command, counterexample):
        name = str(counterexample.get("file") or "").strip()
        try:
            text = edited(name, counterexample, workspace, files)
        except ValueError as error:
            return {"error": str(error)}
        directory = Path(out_dir) / f"counterexample-{next(count):02d}"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "edited"
        target.write_bytes(text.encode("utf-8"))
        original = Path(files[name]) if files and name in files else Path(workspace) / name
        shutil.copymode(original, target)
        return scratch_run(
            workspace, directory, command=command, files={**(files or {}), name: str(target)}, timeout=timeout
        )

    return run
