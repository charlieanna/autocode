"""`autocode completion bash|zsh|fish`: a shell completion script read from the CLI's parser (#714).

The script completes the commands typed first, `program`'s commands, and every public option
of the main command: an option's choices after it, and file names after an option that takes
a path. Internal options (argparse.SUPPRESS) are left out. A subcommand's own options are not
completed yet, only --help: each subcommand builds its parser inside its cli(), so there is no
parser to read without running the command.
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


@dataclass(frozen=True)
class Inventory:
    commands: tuple[str, ...]  # typed first; --version among them
    own_parser: tuple[str, ...]  # commands that parse their own options
    children: dict[str, tuple[str, ...]]  # program -> its commands
    extras: dict[str, tuple[str, ...]]  # unattended -> --analyze
    options: tuple[Option, ...]  # the main command's public options


def summary(text: str | None) -> str:
    """An option's help, cut to its first clause, for the shells that show one."""
    text = " ".join(re.sub(r"%\(\w+\)s", "...", text or "").replace("%%", "%").split())
    first = re.split(r"(?<=[.;])\s", text, maxsplit=1)[0].rstrip(".;")
    return first if len(first) <= 72 else first[:69].rstrip() + "..."


def inventory() -> Inventory:
    parser = cli_args.build_parser(None, configure.DEFAULT_ROLE_MODELS)
    options = tuple(
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
    commands = subcommands.command_names()
    children: dict[str, tuple[str, ...]] = {}
    extras: dict[str, tuple[str, ...]] = {}
    for path in subcommands.public_command_paths():
        if len(path) == 2:
            table = extras if path[1].startswith("-") else children
            table[path[0]] = (*table.get(path[0], ()), path[1])
    own = tuple(
        name
        for name in commands
        if not name.startswith("-") and name not in cli_args.COMMAND_WORDS and name not in PASS_THROUGH
    )
    words = [*commands, *(name for option in options for name in option.names)]
    words += [word for option in options for word in option.choices]
    words += [word for values in (*children.values(), *extras.values()) for word in values]
    unsafe = sorted({word for word in words if not WORD.fullmatch(word)})
    if unsafe:
        raise ValueError(f"Completion words must be plain: {unsafe}")
    return Inventory(commands, own, children, extras, options)


def _value_names(found: Inventory) -> tuple[list[str], list[str]]:
    """(options that take a path, options that take free text), every spelling of each."""
    paths = [name for option in found.options if option.path for name in option.names]
    free = [
        name
        for option in found.options
        if option.takes_value and not option.choices and not option.path
        for name in option.names
    ]
    return paths, free


def bash(found: Inventory) -> str:
    plain = " ".join(name for name in found.commands if not name.startswith("-"))
    lines = [
        "_autocode() {",
        "    local cur=${COMP_WORDS[COMP_CWORD]} prev=${COMP_WORDS[COMP_CWORD-1]} first=${COMP_WORDS[1]}",
        '    if [[ $prev == "=" && $COMP_CWORD -ge 2 ]]; then prev=${COMP_WORDS[COMP_CWORD-2]}; fi',
        "    COMPREPLY=()",
        "    if [[ $COMP_CWORD -eq 1 && $cur != -* ]]; then",
        f"        COMPREPLY=($(compgen -W '{plain}' -- \"$cur\"))",
        "        return 0",
        "    fi",
        "    case $first in",
    ]
    for parent, children in found.children.items():
        lines += [
            f"        {parent})",
            "            if [[ $COMP_CWORD -eq 2 && $cur != -* ]]; then",
            f"                COMPREPLY=($(compgen -W '{' '.join(children)}' -- \"$cur\"))",
            "            elif [[ $cur == -* ]]; then",
            "                COMPREPLY=($(compgen -W '--help' -- \"$cur\"))",
            "            fi",
            "            return 0 ;;",
        ]
    rest = [name for name in found.own_parser if name not in found.children]
    lines += [
        f"        {'|'.join(rest)})",
        "            if [[ $cur == -* ]]; then COMPREPLY=($(compgen -W '--help' -- \"$cur\")); fi",
        "            return 0 ;;",
        "    esac",
        "    case $prev in",
    ]
    for option in found.options:
        if option.choices:
            words = " ".join(option.choices)
            lines.append(
                f"        {'|'.join(option.names)}) COMPREPLY=($(compgen -W '{words}' -- \"$cur\")); return 0 ;;"
            )
    paths, free = _value_names(found)
    options = " ".join(name for option in found.options for name in option.names)
    lines += [
        f'        {"|".join(paths)}) COMPREPLY=($(compgen -f -- "$cur")); return 0 ;;',
        f"        {'|'.join(free)}) return 0 ;;",
        "    esac",
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


def zsh(found: Inventory) -> str:
    plain = " ".join(name for name in found.commands if not name.startswith("-"))
    lines = [
        "_autocode() {",
        "  local first=${words[2]} prev=${words[CURRENT-1]}",
        "  local -a options",
        "  if (( CURRENT == 2 )) && [[ $PREFIX != -* ]]; then",
        f"    compadd -- {plain}",
        "    return",
        "  fi",
        "  case $first in",
    ]
    for parent, children in found.children.items():
        lines += [
            f"    ({parent})",
            "      if (( CURRENT == 3 )) && [[ $PREFIX != -* ]]; then",
            f"        compadd -- {' '.join(children)}",
            "      elif [[ $PREFIX == -* ]]; then",
            "        compadd -- --help",
            "      else",
            "        _files",
            "      fi",
            "      return ;;",
        ]
    rest = [name for name in found.own_parser if name not in found.children]
    lines += [
        f"    ({'|'.join(rest)})",
        "      if [[ $PREFIX == -* ]]; then compadd -- --help; else _files; fi",
        "      return ;;",
        "  esac",
        "  case $prev in",
    ]
    for option in found.options:
        if option.choices:
            lines.append(f"    ({'|'.join(option.names)}) compadd -- {' '.join(option.choices)}; return ;;")
    paths, free = _value_names(found)
    lines += [
        f"    ({'|'.join(paths)}) _files; return ;;",
        f"    ({'|'.join(free)}) return ;;",
        "  esac",
        "  if [[ $PREFIX != -* ]]; then",
        "    _files",
        "    return",
        "  fi",
        "  options=(",
    ]
    lines += [f"    {_zsh_quote(name + ':' + option.summary)}" for option in found.options for name in option.names]
    lines += [
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


def fish(found: Inventory) -> str:
    plain = " ".join(name for name in found.commands if not name.startswith("-"))
    own = " ".join(found.own_parser)
    main = f"'not __fish_seen_subcommand_from {own}'"
    lines = [
        "complete -c autocode -f",
        f"complete -c autocode -n __fish_use_subcommand -a {_fish_quote(plain)}",
        "complete -c autocode -n __fish_use_subcommand -l version -d 'Print the version and source commit'",
    ]
    rest = " ".join(name for name in found.own_parser if name not in found.children)
    lines += [
        f"complete -c autocode -n '__fish_seen_subcommand_from {own}' -s h -l help -d 'Show the command help'",
        f"complete -c autocode -n '__fish_seen_subcommand_from {rest}' -F",
    ]
    for parent, children in found.children.items():
        # The parent's commands first; files only once one of them is given.
        seen = f"__fish_seen_subcommand_from {parent}; and"
        words = " ".join(children)
        lines += [
            f"complete -c autocode -n '{seen} not __fish_seen_subcommand_from {words}' -a {_fish_quote(words)}",
            f"complete -c autocode -n '{seen} __fish_seen_subcommand_from {words}' -F",
        ]
    for option in found.options:
        names = " ".join(f"-l {name[2:]}" if name.startswith("--") else f"-s {name[1:]}" for name in option.names)
        value = ""
        if option.choices:
            value = f" -x -a {_fish_quote(' '.join(option.choices))}"
        elif option.path:
            value = " -r -F"
        elif option.takes_value:
            value = " -x"
        lines.append(f"complete -c autocode -n {main} {names}{value} -d {_fish_quote(option.summary)}")
    for parent, extra in found.extras.items():
        lines += [f"complete -c autocode -n '__fish_seen_subcommand_from {parent}' -l {name[2:]}" for name in extra]
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


def cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="autocode completion",
        description="Print a shell completion script for autocode, generated from its parser.",
        epilog="Load it in this shell:\n" + "\n".join(f"  {LOAD[shell]}" for shell in SHELLS),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("shell", choices=SHELLS)
    args = parser.parse_args(argv)
    sys.stdout.write(script(args.shell))
    return 0
