"""search WORDS: the notes whose text contains WORDS."""

import sys

from notes import store


def search(args) -> int:
    if not args:
        print("usage: python3 -m notes search WORDS", file=sys.stderr)
        return 2
    words = " ".join(args)
    for note in store.load():
        if words in note["text"]:
            print(f"{note['id']} {note['text']}")
    return 0


def register(table) -> None:
    table["search"] = search
