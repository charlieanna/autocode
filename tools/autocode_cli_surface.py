"""Compact root help; accepted options remain in the ordinary argparse parser."""

from __future__ import annotations

import argparse
import sys
import textwrap

# Advanced options stay public and are shown by --help-all, never SUPPRESSed.
COMMON_OPTIONS = (
    ("--workspace", "Project directory"),
    ("--run-dir", "Use a saved run"),
    ("--workflow", "Choose the kind of job"),
    ("--provider", "Choose the coding agent"),
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

    def print_full_help(self):
        self._print_message(argparse.ArgumentParser.format_help(self), sys.stdout)
