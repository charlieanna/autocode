"""Compact root help; accepted options remain in the ordinary argparse parser."""

from __future__ import annotations

import argparse
import os
import sys
import textwrap


def program_name(default: str = "autocode") -> str:
    """The program argparse should name: ``sys.argv[0]`` when it is a script, else ``default``.

    Python 3.14's argparse ignores ``sys.argv[0]`` when the process started as
    ``python -m module`` and reconstructs ``python -m module`` from ``__main__.__spec__``
    (``_prog_name``); ``os.path.basename("python -m unittest")`` keeps that whole string.
    A usage line must name the command that was run (#2387).
    """
    argv0 = os.path.basename(sys.argv[0]) if sys.argv else ""
    if argv0 and " -m " not in argv0 and not argv0.startswith("-m") and argv0 != "-":
        return argv0
    return default


# Advanced options stay public and are shown by --help-all, never SUPPRESSed.
COMMON_OPTIONS = (
    ("--workspace", "Project directory"),
    ("--run-dir", "Use a saved run"),
    ("--workflow", "Choose the kind of job"),
    ("--engine", "Coding agent: opencode, codex or claude"),
    ("--status", "Read saved status without launching an agent"),
    ("--explain", "Explain a saved stop without launching an agent"),
    ("--no-chat", "Return when the run needs your decision"),
)


class FullHelp(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        parser.print_full_help()
        parser.exit()


class CompactParser(argparse.ArgumentParser):
    """Short discovery help without changing parsing or defaults."""

    def format_usage(self):
        # parser.error() prints this before the message. The generated form lists
        # every accepted option, which buries the actual error for a person and
        # for a client showing it in chat.
        return f"usage: {self.prog} [TASK] [OPTIONS]\n"

    def format_help(self):
        lines = ["usage: autocode [TASK] [OPTIONS]", "", "Plan, build and verify a task, or act on a saved run.", ""]
        lines.extend(textwrap.wrap(self.epilog or "", width=79, break_on_hyphens=False))
        lines += ["", "Common options:"]
        accepted = {flag for action in self._actions for flag in action.option_strings}
        for flag, description in COMMON_OPTIONS:
            if flag in accepted:
                lines.append(f"  {flag:<16} {description}")
        lines += [
            "  -h, --help       Show this overview",
            "  --help-all       Show every accepted option",
            "Subcommands: autocode COMMAND --help.",
            "Full reference: https://github.com/charlieanna/autocode/blob/master/docs/cli.md",
        ]
        return "\n".join(lines) + "\n"

    def error(self, message):
        # `autocode Add a hello script`: argparse reads "Add" as the task and refuses the rest as
        # "unrecognized arguments". When none of them is an option, the person left the quotes out.
        extra = message.removeprefix("unrecognized arguments: ")
        if extra != message and not any(word.startswith("-") for word in extra.split()):
            message += '. If this was the task, put all of it in quotes: autocode "your whole task"'
        super().error(message)

    def print_full_help(self):
        self._print_message(argparse.ArgumentParser.format_help(self), sys.stdout)
