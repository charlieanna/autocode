"""`autocode completion bash|zsh|fish`: a shell completion script read from the CLI's parsers (#714).

The script completes the commands typed first and every public option of the main command:
an option's choices after it, and file names after an option that takes a path. A subcommand
completes from the parser its module returns from parser() (autocode_subcommands): its own
options, then the commands it takes (`output read`, `arena run`) and each one's options, or the
choices of its first argument (`completion bash`). The subcommands without a parser to read
(autocode_subcommands.WITHOUT_PARSER) and the builtin ones complete --help. Internal options
(argparse.SUPPRESS) are left out.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from . import autocode_args as cli_args
    from . import autocode_configure as configure
    from . import autocode_subcommands as subcommands
except ImportError:
    import autocode_args as cli_args
    import autocode_configure as configure
    import autocode_subcommands as subcommands

SHELLS = ("bash", "zsh", "fish")
# Unattended passes ordinary run arguments through to the main parser (autocode_subcommands).
PASS_THROUGH = ("unattended",)
# Words are written into shell source, so only plain words are accepted.
WORD = re.compile(r"-{0,2}[A-Za-z0-9][A-Za-z0-9._:/+=@%,-]*")


@dataclass(frozen=True)
class Option:
    names: tuple[str, ...]
    takes_value: bool
    choices: tuple[str, ...]
    path: bool
    summary: str


HELP = Option(("-h", "--help"), False, (), False, "Show the command help")


@dataclass(frozen=True)
class Command:
    """What completes after a command that reads its own arguments."""

    options: tuple[Option, ...]  # its own options
    words: dict[str, tuple[Option, ...]]  # its commands, or its first argument's choices: the options after each


@dataclass(frozen=True)
class Inventory:
    commands: tuple[str, ...]  # typed first; --version among them
    own_parser: dict[str, Command]  # commands that read their own arguments, not the main command's
    extras: dict[str, tuple[str, ...]]  # unattended -> --analyze
    options: tuple[Option, ...]  # the main command's public options


def summary(text: str | None) -> str:
    """An option's help, cut to its first clause, for the shells that show one."""
    text = " ".join(re.sub(r"%\(\w+\)s", "...", text or "").replace("%%", "%").split())
    first = re.split(r"(?<=[.;])\s", text, maxsplit=1)[0].rstrip(".;")
    return first if len(first) <= 72 else first[:69].rstrip() + "..."


def options_of(parser: argparse.ArgumentParser) -> tuple[Option, ...]:
    """A parser's public options, each spelling, choice and kind of value it takes."""
    return tuple(
        Option(
            names=tuple(action.option_strings),
            takes_value=action.nargs != 0,
            choices=tuple(str(choice) for choice in action.choices or ()),
            path=action.type is Path,
            summary=summary(action.help),
        )
        for action in parser._actions
        if action.option_strings and action.help != argparse.SUPPRESS
    )


def command_of(parser: argparse.ArgumentParser) -> Command:
    """A subcommand's options, and the words that may follow it: its commands, else its first argument's choices."""
    own = options_of(parser)
    first = next((action for action in parser._actions if not action.option_strings), None)
    if isinstance(first, argparse._SubParsersAction):
        return Command(own, {name: options_of(child) for name, child in first.choices.items()})
    choices = first.choices if first is not None and first.choices else ()
    return Command(own, {str(choice): own for choice in choices})


def inventory() -> Inventory:
    options = options_of(cli_args.build_parser(None, configure.DEFAULT_ROLE_MODELS))
    commands = subcommands.command_names()
    extras: dict[str, tuple[str, ...]] = {}
    for path in subcommands.public_command_paths():
        if len(path) == 2 and path[1].startswith("-"):
            extras[path[0]] = (*extras.get(path[0], ()), path[1])
    own: dict[str, Command] = {}
    for name in commands:
        if name.startswith("-") or name in cli_args.COMMAND_WORDS or name in PASS_THROUGH:
            continue
        parsed = subcommands.command_parser(name)
        own[name] = command_of(parsed) if parsed is not None else Command((HELP,), {})
    scopes = [options, *(command.options for command in own.values())]
    scopes += [after for command in own.values() for after in command.words.values()]
    words = [*commands, *(word for command in own.values() for word in command.words)]
    words += [word for scope in scopes for option in scope for word in (*option.names, *option.choices)]
    words += [word for values in extras.values() for word in values]
    unsafe = sorted({word for word in words if not WORD.fullmatch(word)})
    if unsafe:
        raise ValueError(f"Completion words must be plain: {unsafe}")
    return Inventory(commands, own, extras, options)


def _value_names(options: tuple[Option, ...]) -> tuple[list[str], list[str]]:
    """(options that take a path, options that take free text), every spelling of each."""
    paths = [name for option in options if option.path for name in option.names]
    free = [
        name
        for option in options
        if option.takes_value and not option.choices and not option.path
        for name in option.names
    ]
    return paths, free


def _grouped(words: dict[str, tuple[Option, ...]]) -> list[tuple[tuple[Option, ...], list[str]]]:
    """The words that take the same options together, so each set is written once."""
    groups: dict[tuple[Option, ...], list[str]] = {}
    for word, options in words.items():
        groups.setdefault(options, []).append(word)
    return list(groups.items())


def _plain(found: Inventory) -> str:
    return " ".join(name for name in found.commands if not name.startswith("-"))


def _bash_values(options: tuple[Option, ...], indent: str) -> list[str]:
    """What follows an option: its choices, file names after a path, nothing after free text."""
    arms = [
        f"{indent}    {'|'.join(option.names)}) COMPREPLY=($(compgen -W '{' '.join(option.choices)}' -- \"$cur\")); "
        "return 0 ;;"
        for option in options
        if option.choices
    ]
    paths, free = _value_names(options)
    if paths:
        arms.append(f'{indent}    {"|".join(paths)}) COMPREPLY=($(compgen -f -- "$cur")); return 0 ;;')
    if free:
        arms.append(f"{indent}    {'|'.join(free)}) return 0 ;;")
    return [f"{indent}case $prev in", *arms, f"{indent}esac"] if arms else []


def _bash_scope(options: tuple[Option, ...], otherwise: str, indent: str) -> list[str]:
    """One parser: a value after its option, its options after a dash, else ``otherwise``."""
    names = " ".join(name for option in options for name in option.names)
    lines = [
        *_bash_values(options, indent),
        f"{indent}if [[ $cur == -* ]]; then",
        f"{indent}    COMPREPLY=($(compgen -W '{names}' -- \"$cur\"))",
    ]
    if otherwise:
        lines += [f"{indent}else", f"{indent}    {otherwise}"]
    return [*lines, f"{indent}fi"]


def _bash_command(name: str, command: Command) -> list[str]:
    lines = [f"        {name})"]
    otherwise = ""
    if command.words:
        words = " ".join(command.words)
        lines += [
            "            local word",
            f"            word=$(_autocode_word '{words}')",
            "            case $word in",
        ]
        for options, group in _grouped(command.words):
            lines += [f"                {'|'.join(group)})", *_bash_scope(options, "", " " * 20)]
            lines.append("                    return 0 ;;")
        lines.append("            esac")
        otherwise = f"COMPREPLY=($(compgen -W '{words}' -- \"$cur\"))"
    return [*lines, *_bash_scope(command.options, otherwise, " " * 12), "            return 0 ;;"]


def bash(found: Inventory) -> str:
    lines = [
        "_autocode_word() {",  # the first word after the command that is one of $1
        "    local word",
        '    for word in "${COMP_WORDS[@]:2:COMP_CWORD-2}"; do',
        '        if [[ " $1 " == *" $word "* ]]; then printf \'%s\\n\' "$word"; return; fi',
        "    done",
        "}",
        "_autocode() {",
        "    local cur=${COMP_WORDS[COMP_CWORD]} prev=${COMP_WORDS[COMP_CWORD-1]} first=${COMP_WORDS[1]}",
        '    if [[ $prev == "=" && $COMP_CWORD -ge 2 ]]; then prev=${COMP_WORDS[COMP_CWORD-2]}; fi',
        "    COMPREPLY=()",
        "    if [[ $COMP_CWORD -eq 1 && $cur != -* ]]; then",
        f"        COMPREPLY=($(compgen -W '{_plain(found)}' -- \"$cur\"))",
        "        return 0",
        "    fi",
        "    case $first in",
    ]
    for name, command in found.own_parser.items():
        lines += _bash_command(name, command)
    options = " ".join(name for option in found.options for name in option.names)
    lines += [
        "    esac",
        *_bash_values(found.options, "    "),
        "    if [[ $cur == -* ]]; then",
        f"        local options='{options}'",
        '        if [[ $COMP_CWORD -eq 1 ]]; then options="$options --version"; fi',
    ]
    for parent, extra in found.extras.items():
        lines.append(f'        if [[ $first == {parent} ]]; then options="$options {" ".join(extra)}"; fi')
    lines += [
        '        COMPREPLY=($(compgen -W "$options" -- "$cur"))',
        "    fi",
        "    return 0",
        "}",
        "complete -o default -F _autocode autocode",
    ]
    return "\n".join(lines) + "\n"


def _zsh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _zsh_values(options: tuple[Option, ...], indent: str) -> list[str]:
    arms = [
        f"{indent}  ({'|'.join(option.names)}) compadd -- {' '.join(option.choices)}; return ;;"
        for option in options
        if option.choices
    ]
    paths, free = _value_names(options)
    if paths:
        arms.append(f"{indent}  ({'|'.join(paths)}) _files; return ;;")
    if free:
        arms.append(f"{indent}  ({'|'.join(free)}) return ;;")
    return [f"{indent}case $prev in", *arms, f"{indent}esac"] if arms else []


def _zsh_described(options: tuple[Option, ...], indent: str) -> list[str]:
    return [f"{indent}  {_zsh_quote(name + ':' + option.summary)}" for option in options for name in option.names]


def _zsh_scope(options: tuple[Option, ...], otherwise: str, indent: str) -> list[str]:
    return [
        *_zsh_values(options, indent),
        f"{indent}if [[ $PREFIX == -* ]]; then",
        f"{indent}  options=(",
        *_zsh_described(options, indent + "  "),
        f"{indent}  )",
        f"{indent}  _describe -t options 'autocode option' options",
        f"{indent}else",
        f"{indent}  {otherwise}",
        f"{indent}fi",
    ]


def _zsh_command(name: str, command: Command) -> list[str]:
    lines = [f"    ({name})"]
    otherwise = "_files"
    if command.words:
        words = " ".join(command.words)
        lines += [f"      local word=$(_autocode_word '{words}')", "      case $word in"]
        for options, group in _grouped(command.words):
            lines += [f"        ({'|'.join(group)})", *_zsh_scope(options, "_files", " " * 10), "          return ;;"]
        lines.append("      esac")
        otherwise = f"compadd -- {words}"
    return [*lines, *_zsh_scope(command.options, otherwise, " " * 6), "      return ;;"]


def zsh(found: Inventory) -> str:
    lines = [
        "_autocode_word() {",  # the first word after the command that is one of $1
        "  local word",
        "  for word in ${words[3,CURRENT-1]}; do",
        '    if [[ " $1 " == *" $word "* ]]; then print -r -- $word; return; fi',
        "  done",
        "}",
        "_autocode() {",
        "  local first=${words[2]} prev=${words[CURRENT-1]}",
        "  local -a options",
        "  if (( CURRENT == 2 )) && [[ $PREFIX != -* ]]; then",
        f"    compadd -- {_plain(found)}",
        "    return",
        "  fi",
        "  case $first in",
    ]
    for name, command in found.own_parser.items():
        lines += _zsh_command(name, command)
    lines += [
        "  esac",
        *_zsh_values(found.options, "  "),
        "  if [[ $PREFIX != -* ]]; then",
        "    _files",
        "    return",
        "  fi",
        "  options=(",
        *_zsh_described(found.options, "  "),
        "  )",
        "  (( CURRENT == 2 )) && options+=('--version:Print the version and source commit')",
    ]
    for parent, extra in found.extras.items():
        lines.append(f"  [[ $first == {parent} ]] && options+=({' '.join(extra)})")
    lines += [
        "  _describe -t options 'autocode option' options",
        "}",
        'if [[ "${funcstack[1]}" == _autocode ]]; then',
        '  _autocode "$@"',
        "else",
        "  compdef _autocode autocode",
        "fi",
    ]
    return "\n".join(lines) + "\n"


def _fish_quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _fish_options(options: tuple[Option, ...], condition: str) -> list[str]:
    lines = []
    for option in options:
        names = " ".join(
            f"-l {name[2:]}" if name.startswith("--") else f"-s {name[1:]}" if len(name) == 2 else f"-o {name[1:]}"
            for name in option.names
        )
        value = ""
        if option.choices:
            value = f" -x -a {_fish_quote(' '.join(option.choices))}"
        elif option.path:
            value = " -r -F"
        elif option.takes_value:
            value = " -x"
        lines.append(
            f"complete -c autocode -n {_fish_quote(condition)} {names}{value} -d {_fish_quote(option.summary)}"
        )
    return lines


def _fish_command(name: str, command: Command) -> list[str]:
    seen = f"__autocode_first {name}"
    if not command.words:
        return [*_fish_options(command.options, seen), f"complete -c autocode -n {_fish_quote(seen)} -F"]
    # The command's words and options first; once a word is given, that word's options and files.
    words = " ".join(command.words)
    before = f"{seen}; and not __fish_seen_subcommand_from {words}"
    lines = [f"complete -c autocode -n {_fish_quote(before)} -a {_fish_quote(words)}"]
    lines += _fish_options(command.options, before)
    for options, group in _grouped(command.words):
        lines += _fish_options(options, f"{seen}; and __fish_seen_subcommand_from {' '.join(group)}")
    return [*lines, f"complete -c autocode -n {_fish_quote(f'{seen}; and __fish_seen_subcommand_from {words}')} -F"]


def fish(found: Inventory) -> str:
    lines = [
        # Whether the command typed first is one of $argv: `output read` is not a `read` command.
        "function __autocode_first",
        "    set -l words (commandline -opc)",
        "    set -q words[2]; and contains -- $words[2] $argv",
        "end",
        "complete -c autocode -f",
        f"complete -c autocode -n __fish_use_subcommand -a {_fish_quote(_plain(found))}",
        "complete -c autocode -n __fish_use_subcommand -l version -d 'Print the version and source commit'",
    ]
    for name, command in found.own_parser.items():
        lines += _fish_command(name, command)
    lines += _fish_options(found.options, f"not __autocode_first {' '.join(found.own_parser)}")
    for parent, extra in found.extras.items():
        lines += [f"complete -c autocode -n '__autocode_first {parent}' -l {name[2:]}" for name in extra]
    return "\n".join(lines) + "\n"


LOAD = {
    "bash": "source <(autocode completion bash)",
    "zsh": "source <(autocode completion zsh)",
    "fish": "autocode completion fish | source",
}
SAVE = {
    "bash": "Or save it as ~/.local/share/bash-completion/completions/autocode",
    "zsh": "Load it after compinit, or save it as _autocode in a directory on $fpath before compinit runs.",
    "fish": "Or save it as ~/.config/fish/completions/autocode.fish",
}
RENDER = {"bash": bash, "zsh": zsh, "fish": fish}


def script(shell: str) -> str:
    """The completion script for ``shell``, headed by the version it was generated from."""
    header = [
        f"# {shell} completion for autocode {subcommands.package_version()}, written by `autocode completion {shell}`.",
        f"# Load it in this shell:  {LOAD[shell]}",
        f"# {SAVE[shell]}",
    ]
    # zsh reads #compdef only from a completion file's first line.
    first = ["#compdef autocode"] if shell == "zsh" else []
    return "\n".join([*first, *header]) + "\n" + RENDER[shell](inventory())


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode completion` reads; its own script completes them too."""
    result = argparse.ArgumentParser(
        prog="autocode completion",
        description="Print a shell completion script for autocode, generated from its parser.",
        epilog="Load it in this shell:\n" + "\n".join(f"  {LOAD[shell]}" for shell in SHELLS),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    result.add_argument("shell", choices=SHELLS, help="The shell to generate the script for")
    return result


def cli(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    sys.stdout.write(script(args.shell))
    return 0
