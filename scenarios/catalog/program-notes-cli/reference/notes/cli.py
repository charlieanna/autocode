"""The notes command line: add and list, plus the commands each module in notes/commands registers."""

import importlib
import pkgutil
import sys

from notes import commands, store


def usage(table) -> int:
    print("usage: python3 -m notes {" + ",".join(sorted(table)) + "} ...", file=sys.stderr)
    return 2


def add(args) -> int:
    if not args:
        print("usage: python3 -m notes add TEXT", file=sys.stderr)
        return 2
    print(f"added {store.add(' '.join(args))['id']}")
    return 0


def list_notes(args) -> int:
    for note in store.load():
        print(f"{note['id']} {note['text']}")
    return 0


def registry() -> dict:
    table = {"add": add, "list": list_notes}
    for module in pkgutil.iter_modules(commands.__path__):
        importlib.import_module(f"notes.commands.{module.name}").register(table)
    return table


def main(argv) -> int:
    table = registry()
    if not argv or argv[0] not in table:
        return usage(table)
    return table[argv[0]](argv[1:])
