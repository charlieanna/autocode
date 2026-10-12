"""`autocode <subcommand>`: the entry points that live in their own modules, and --version.

A table rather than one ``if`` per subcommand in autocode.py, which is at its
recorded size limit (tests/test_architecture.py). Each module named here has a
``cli(argv) -> int``.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from importlib import metadata
from pathlib import Path

COMMAND_WORDS = ("resume", "status", "explain")
# These existing interface seams are handled by autocode itself.
BUILTIN_COMMANDS = ("capture", "registry", "intervention")
# Hidden replay plumbing is supplied by TaskRun and the replay driver.
# Unattended passes ordinary run arguments through the same root parser.
INTERNAL_FLAGS: dict[tuple[str, ...], frozenset[str]] = {
    (): frozenset({"--new-run-dir", "--replay-recording", "--replay-checks"}),
    ("unattended",): frozenset({"--new-run-dir", "--replay-recording", "--replay-checks"}),
}

SUBCOMMANDS = {
    "demo": "autocode_demo",
    "verify": "autocode_verify_task",
    "issue": "autocode_issue",
    "arena": "autocode_arena",
    "dashboard": "dashboard.agent_console",
    "unattended": "autocode_unattended",
    "checkpoint": "autocode_checkpoint_cli",
    "replay": "autocode_replay_cli",
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
    "prune-run": "autocode_prune_run",
    "merge": "autocode_merge",
    "models": "model_catalogue",
    "review-pr": "autocode_review_pr",
}
# A few existing entry points consume sys.argv rather than an argv parameter.
ENTRY_FUNCTIONS = {"issue": "main", "arena": "main", "dashboard": "main"}
SYS_ARGV_COMMANDS = frozenset({"dashboard", "unattended"})
DISTRIBUTION = "autocode-supervisor"

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util
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
    entry = getattr(module, ENTRY_FUNCTIONS.get(argv[0], "cli"))
    if argv[0] not in SYS_ARGV_COMMANDS:
        return entry(argv[1:])
    saved = sys.argv
    try:
        sys.argv = [f"autocode {argv[0]}", *argv[1:]]
        result = entry()
        return 0 if result is None else result
    finally:
        sys.argv = saved


def command_names() -> tuple[str, ...]:
    """Every command handled before the main task parser, in help order."""
    return ("--version", "doctor", *COMMAND_WORDS, *sorted((set(SUBCOMMANDS) | set(BUILTIN_COMMANDS)) - {"doctor"}))


def public_command_paths() -> tuple[tuple[str, ...], ...]:
    """Actual parser entry routes for documentation coverage; nested argparse parsers recurse themselves.

    Program dispatches manually, so its routes come from the same map its CLI uses.
    Unattended's normal arguments pass through to the main parser; --analyze has
    its own parser. Version has no parser and is displayed by command_names().
    """
    module = importlib.import_module(f"{__package__}.autocode_program" if __package__ else "autocode_program")
    roots = sorted(set(SUBCOMMANDS) | set(BUILTIN_COMMANDS))
    return (
        (),
        *((name,) for name in roots),
        *(("program", name) for name in module.COMMANDS),
        ("unattended", "--analyze"),
    )


def version_line() -> str:
    """``autocode 0.7.1 (commit 1a2b3c4)``; the commit is known only when running from a checkout."""
    commit = util.source_commit()
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


if __name__ == "__main__":
    raise SystemExit(dispatch(sys.argv[1:]) or 0)
