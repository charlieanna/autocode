"""`autocode <subcommand>`: the entry points that live in their own modules, and --version.

A table rather than one ``if`` per subcommand in autocode.py, which is at its
recorded size limit (tests/test_architecture.py). Each module named here has a
``cli(argv) -> int`` and, unless it is in WITHOUT_PARSER, a ``parser()`` that returns
the parser its cli() reads; `autocode completion` completes the command's options from it.
"""

from __future__ import annotations

import argparse
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
    (): frozenset({"--new-run-dir", "--replay-recording", "--replay-checks", "--worker-receipt"}),
    ("unattended",): frozenset({"--new-run-dir", "--replay-recording", "--replay-checks", "--worker-receipt"}),
    ("worker",): frozenset({"--receipt"}),
}

SUBCOMMANDS = {
    "connections": "autocode_connections",
    "modes": "autocode_run_modes",
    "demo": "autocode_demo",
    "conversation": "autocode_conversation_cli",
    "chat": "autocode_chat_client",
    "worker": "autocode_worker_cli",
    "attention": "autocode_attention_cli",
    "verify": "autocode_verify_task",
    "issue": "autocode_issue",
    "bridge": "autocode_bridge_cli",
    "changeset": "autocode_changeset",
    "coordinate": "autocode_coordinate",
    "arena": "autocode_arena",
    "dashboard": "dashboard.agent_console",
    "unattended": "autocode_unattended",
    "checkpoint": "autocode_checkpoint_cli",
    "replay": "autocode_replay_cli",
    "bundle": "autocode_bundle",
    "visual-capture": "autocode_visual_capture",
    "output": "autocode_output",
    "tasks": "autocode_tasks",
    "runs": "autocode_runs_command",
    "events": "autocode_events_command",
    "plan": "autocode_plan_command",
    "wait": "autocode_wait_command",
    "components": "autocode_components",
    "ui": "autocode_ui",
    "compare-baseline": "autocode_baseline",
    "visual-check": "autocode_visual_check",
    "doctor": "autocode_doctor",
    "forget": "autocode_forget",
    "clean-worktrees": "autocode_worktrees",
    "prune-run": "autocode_prune_run",
    "merge": "autocode_merge",
    "models": "model_catalogue",
    "review-pr": "autocode_review_pr",
    "review": "autocode_review_cli",
    "serve": "autocode_serve_cli",
    "completion": "autocode_shell_completion",
}
# A few existing entry points consume sys.argv rather than an argv parameter.
ENTRY_FUNCTIONS = {"issue": "main", "arena": "main", "dashboard": "main"}
SYS_ARGV_COMMANDS = frozenset({"dashboard", "unattended"})
# No parser() to read: the dashboard reads sys.argv itself; unattended passes run arguments through to
# the main parser.
WITHOUT_PARSER = frozenset({"dashboard", "unattended"})
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
        return as_program(argv[0], lambda: entry(argv[1:]), argv[1:])
    return as_program(argv[0], lambda: entry(), argv[1:], none_is=0)


def as_program(name: str, call, argv: list[str], *, none_is=None):
    """Run ``call`` with ``sys.argv`` naming the program `autocode NAME` followed by ``argv``.

    A parser with no prog of its own takes it from sys.argv[0], so its usage and error lines then
    name `autocode NAME`, not a bare `autocode` whose arguments do not exist (#2387). ``sys.argv`` is
    restored afterwards; ``none_is`` stands in for a None result.
    """
    saved = sys.argv
    try:
        sys.argv = [f"autocode {name}", *argv]
        result = call()
        return none_is if result is None else result
    finally:
        sys.argv = saved


def command_parser(name: str) -> argparse.ArgumentParser | None:
    """The parser `autocode NAME` reads its arguments with; None for a builtin or WITHOUT_PARSER."""
    if name not in SUBCOMMANDS or name in WITHOUT_PARSER:
        return None
    module = SUBCOMMANDS[name]
    return importlib.import_module(f"{__package__}.{module}" if __package__ else module).parser()


def option_typed_as_task(parser, argv, task) -> str | None:
    """`autocode approve-goal`: a one-word task that names a main-run option without its dashes is a
    typo, not a task to start a worktree and a run for. The refusal, else None. After ``--`` the word
    is task text as typed."""
    word = (task or "").strip()
    if "--" in argv or not word or " " in word or word.startswith("-"):
        return None
    if f"--{word}" not in parser._option_string_actions:
        return None
    return f"'{word}' is the option --{word}, not a task. Use --{word}, or write the task in quotes: autocode \"your task\""


def command_names() -> tuple[str, ...]:
    """Every command handled before the main task parser, in help order."""
    return ("--version", "doctor", *COMMAND_WORDS, *sorted((set(SUBCOMMANDS) | set(BUILTIN_COMMANDS)) - {"doctor"}))


def public_command_paths() -> tuple[tuple[str, ...], ...]:
    """Actual parser entry routes for documentation coverage; nested argparse parsers recurse themselves.

    Unattended's normal arguments pass through to the main parser; --analyze has
    its own parser. Version has no parser and is displayed by command_names().
    """
    roots = sorted(set(SUBCOMMANDS) | set(BUILTIN_COMMANDS))
    return (
        (),
        *((name,) for name in roots),
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
