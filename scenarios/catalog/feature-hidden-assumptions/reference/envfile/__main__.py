"""Command line: ``python3 -m envfile COMMAND ...``."""

import argparse
import sys

from .errors import EnvFileError, fail
from .parse import dump, load


def show(args):
    entries = load(args.file)
    if not entries:
        fail(f"{args.file}: no entries", code=1)
    sys.stdout.write(dump(entries, sort=args.sorted))


def get(args):
    entries = load(args.file)
    if args.key in entries:
        print(entries[args.key])
    elif args.default is not None:
        print(args.default)
    else:
        fail(f"{args.key}: not set in {args.file}", code=1)


def merge(args):
    # The person's answers: a later file's value wins (the key keeps the place it
    # first appeared), and inputs with no entries at all merge to empty output, exit 0.
    merged = {}
    for path in args.files:
        merged.update(load(path))
    sys.stdout.write(dump(merged, sort=args.sorted))


def parser():
    top = argparse.ArgumentParser(prog="envfile", description="Inspect KEY=VALUE env files.")
    commands = top.add_subparsers(dest="command", required=True)

    show_cmd = commands.add_parser("show", help="print a file's entries")
    show_cmd.add_argument("file")
    show_cmd.add_argument("--sorted", action="store_true", help="print keys in alphabetical order")
    show_cmd.set_defaults(run=show)

    get_cmd = commands.add_parser("get", help="print one key's value")
    get_cmd.add_argument("file")
    get_cmd.add_argument("key")
    get_cmd.add_argument("--default", metavar="VALUE", help="print VALUE when the key is not set")
    get_cmd.set_defaults(run=get)

    merge_cmd = commands.add_parser("merge", help="combine several files into one")
    merge_cmd.add_argument("files", nargs="+", metavar="FILE")
    merge_cmd.add_argument("--sorted", action="store_true", help="print keys in alphabetical order")
    merge_cmd.set_defaults(run=merge)
    return top


def main(argv=None):
    top = parser()
    args = top.parse_args(argv)
    if args.command == "merge" and len(args.files) < 2:
        fail("merge needs at least two files")
    try:
        args.run(args)
    except EnvFileError as error:
        fail(str(error))


if __name__ == "__main__":
    main()
