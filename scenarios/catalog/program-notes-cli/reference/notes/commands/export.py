"""export: every note as a JSON array of {"id", "text"} objects."""
import json

from notes import store


def export(args) -> int:
    print(json.dumps(store.load()))
    return 0


def register(table) -> None:
    table["export"] = export
