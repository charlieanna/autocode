"""The note store: one JSON file, named by NOTES_FILE or notes.json in the current directory.

load() returns every note as {"id", "text"} in the order the notes were added; add(text) appends one.
"""
import json
import os
from pathlib import Path


def path() -> Path:
    return Path(os.environ.get("NOTES_FILE") or "notes.json")


def load() -> list[dict]:
    file = path()
    return json.loads(file.read_text()) if file.is_file() else []


def add(text: str) -> dict:
    notes = load()
    note = {"id": len(notes) + 1, "text": text}
    path().write_text(json.dumps([*notes, note], indent=2) + "\n")
    return note
