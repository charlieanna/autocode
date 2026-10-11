"""Public CLI options have explicit entries in the CLI reference (#691)."""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import autocode
import autocode_args
import autocode_configure
import autocode_subcommands as subcommands

REFERENCE = Path(__file__).resolve().parents[1] / "docs" / "cli.md"
HELP_FLAGS = {"-h", "--help"}
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")


def documented_options(text):
    """Read option-entry columns, excluding prose and table descriptions."""
    scopes = {(): set(HELP_FLAGS)}
    scoped = False
    for line in text.splitlines():
        if line.startswith("## "):
            scoped = line == "## Subcommand options"
        if not line.startswith("|"):
            continue
        cells = re.split(r"(?<!\\)\|", line)[1:-1]
        if scoped:
            if len(cells) < 3:
                continue
            command = cells[0].strip().strip("`").split()
            if command[:1] != ["autocode"]:
                continue
            path = tuple(command[1:])
            scopes.setdefault(path, set()).update(FLAG.findall(cells[1]))
        elif cells:
            scopes[()].update(FLAG.findall(cells[0]))
    return scopes


def parser_actions(parser):
    for action in parser._actions:
        yield action
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from parser_actions(child)


def option_sets(parser):
    public, hidden = set(), set()
    for action in parser_actions(parser):
        target = hidden if action.help == argparse.SUPPRESS else public
        target.update(action.option_strings)
    return public, hidden


class ParserCaptured(BaseException):
    """Stop at the parser boundary, before a CLI can inspect or change a project."""

    def __init__(self, parser):
        self.parser = parser


def capture_parser(path):
    def capture(parser, *args, **kwargs):
        raise ParserCaptured(parser)

    try:
        with (
            mock.patch.object(sys, "argv", ["autocode", *path, "--help"]),
            mock.patch.object(argparse.ArgumentParser, "parse_args", capture),
            mock.patch.object(argparse.ArgumentParser, "parse_known_args", capture),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            autocode.main()
    except ParserCaptured as captured:
        return captured.parser
    return None


def scope_options(scopes, path):
    if not path or path[0] in autocode_args.COMMAND_WORDS or path == ("unattended",):
        return scopes[()]
    flags = set(HELP_FLAGS)
    for size in range(1, len(path) + 1):
        flags.update(scopes.get(path[:size], ()))
    return flags


class CliDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = subcommands.public_command_paths()
        cls.parsers = {path: capture_parser(path) for path in cls.paths}
        # The unattended overview describes a pass-through wrapper; its ordinary
        # accepted run flags come from the main parser, its analysis flags have
        # their own registered path. Program's manual overview has child paths.
        if ("unattended",) in cls.parsers and cls.parsers[("unattended",)] is None:
            cls.parsers[("unattended",)] = autocode_args.build_parser(None, autocode_configure.DEFAULT_ROLE_MODELS)
        cls.scopes = documented_options(REFERENCE.read_text())

    def test_every_public_option_has_a_reference_entry(self):
        for path, parser in self.parsers.items():
            with self.subTest(command="autocode " + " ".join(path)):
                if parser is None:
                    self.assertTrue(
                        any(child[: len(path)] == path and len(child) > len(path) for child in self.paths),
                        "A public command with no parser needs inventoried child parsers",
                    )
                    continue
                public, _ = option_sets(parser)
                if not path:
                    public.update(name for name in subcommands.command_names() if name.startswith("--"))
                missing = public - scope_options(self.scopes, path)
                self.assertFalse(missing, "Undocumented public options: " + ", ".join(sorted(missing)))

    def test_hidden_options_match_the_explicit_internal_set(self):
        observed = {}
        for path, parser in self.parsers.items():
            if parser is not None:
                _, hidden = option_sets(parser)
                if hidden:
                    observed[path] = frozenset(hidden)
        self.assertEqual(subcommands.INTERNAL_FLAGS, observed)

    def test_mentions_in_prose_or_descriptions_do_not_document_an_option(self):
        scopes = documented_options(
            "Mention --missing in prose.\n| Flag | Meaning |\n| `--present` | Compare with `--missing`. |\n"
        )
        self.assertIn("--present", scopes[()])
        self.assertNotIn("--missing", scopes[()])

    def test_negative_and_nested_options_need_their_own_entries(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--chat", action=argparse.BooleanOptionalAction)
        parser.add_subparsers().add_parser("child").add_argument("--new-public-flag")
        public, _ = option_sets(parser)
        self.assertEqual(
            {"--no-chat", "--new-public-flag"},
            public - {"--chat", *HELP_FLAGS},
        )
