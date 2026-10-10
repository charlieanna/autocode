"""`autocode <subcommand>`: the entry points that live in their own modules, and --version.

A table rather than one ``if`` per subcommand in autocode.py, which is at its
recorded size limit (tests/test_architecture.py). Each module named here has a
``cli(argv) -> int``.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

SUBCOMMANDS = {
    "checkpoint": "autocode_checkpoint_cli",
    "visual-capture": "autocode_visual_capture",
    "output": "autocode_output",
    "tasks": "autocode_tasks",
    "components": "autocode_components",
    "ui": "autocode_ui",
    "program": "autocode_program",
    "compare-baseline": "autocode_baseline",
    "visual-check": "autocode_visual_check",
    "doctor": "autocode_doctor",
    "clean-worktrees": "autocode_worktrees",
    "merge": "autocode_merge",
    "models": "model_catalogue",
}
DISTRIBUTION = "autocode-supervisor"
HERE = Path(__file__).resolve().parent


def dispatch(argv: list[str]) -> int | None:
    """Run the subcommand ``argv`` starts with; None when it is an ordinary task invocation."""
    if argv[:1] == ["--version"]:
        print(version_line())
        return 0
    name = SUBCOMMANDS.get(argv[0]) if argv else None
    if name is None:
        return None
    module = importlib.import_module(f"{__package__}.{name}" if __package__ else name)
    return module.cli(argv[1:])


def version_line() -> str:
    """``autocode 0.7.1 (commit 1a2b3c4)``; the commit is known only when running from a checkout."""
    commit = source_commit()
    return f"autocode {package_version()} ({'commit ' + commit if commit else 'commit unknown'})"


def package_version() -> str:
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        pyproject = HERE.parent / "pyproject.toml"
        try:
            return tomllib.loads(pyproject.read_text())["project"]["version"]
        except (OSError, KeyError, ValueError):
            return "unknown"


def source_commit() -> str | None:
    """The AutoCode checkout's commit, with ``+modified`` when its tools/ has uncommitted changes.

    A commit is reported only when ``HERE`` sits in a Git work tree whose top level
    is this project's root (``HERE.parent`` with AutoCode's ``pyproject.toml``).
    An installed package inside some other repository (a venv under a user project)
    must print ``commit unknown``, never that project's HEAD (#339).
    """

    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(HERE), *args], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None

    top = git("rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0 or not top.stdout.strip():
        return None
    try:
        top_path = Path(top.stdout.strip()).resolve()
    except OSError:
        return None
    if top_path != HERE.parent.resolve() or not _is_autocode_checkout(top_path):
        return None
    head = git("rev-parse", "--short", "HEAD")
    if head is None or head.returncode != 0 or not head.stdout.strip():
        return None
    dirty = git("status", "--porcelain", "--", str(HERE))
    return head.stdout.strip() + ("+modified" if dirty and dirty.stdout.strip() else "")


def _is_autocode_checkout(root: Path) -> bool:
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text()).get("project") or {}
    except (OSError, ValueError):
        return False
    return project.get("name") == DISTRIBUTION and bool(project.get("version"))


if __name__ == "__main__":
    raise SystemExit(dispatch(sys.argv[1:]) or 0)
