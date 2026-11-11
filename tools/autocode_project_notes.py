"""Durable, evidence-cited notes about a repository, kept between runs (#2943).

A run that gets stuck often pays to discover a fact about the repository: how to reproduce a
failure, which test flakes, which quirk the build has. ``.autocode/project-notes.json`` keeps
those facts for the next task in the same project, so it does not pay for them again.

A note is advisory context, the same standing as documentation a person maintains: it never
authorizes a path, remote, permission or action, and the current brief, configuration and
repository win over it. Every note carries the receipt of the observation that produced it
(the run, the stage and what was observed); there is no note without one. A person sees the
notes in the status view and ``autocode notes list``, and corrects them through the same
interface (``retract``, ``correct``). A retracted note stays recorded, so the same fact
rediscovered later does not come back over the person's correction.

The file shares project memory's rules (autocode_project_memory): one bounded regular file in
the project's ``.autocode``, read without following links, replaced atomically under the same
lock. Credential-shaped values and local absolute or private paths are omitted whole, never
rewritten. Utility layer: no run state here; autocode_project_notes_lifecycle binds runs to it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

try:
    from . import autocode_project_memory as memory
except ImportError:
    import autocode_project_memory as memory

VERSION = 1
FILENAME = "project-notes.json"
PATH = ".autocode/" + FILENAME
MAX_BYTES = 131072
MAX_NOTES = 64
MAX_TEXT = 600
MAX_OBSERVATION = 1024
MAX_REASON = 600
MAX_SHORT = 160
# The bytes of active notes a stage prompt is given; the rest stay in the file and the list.
MAX_CONTEXT_BYTES = 8192
# What a note is about. "other" keeps a useful fact that fits none of the rest.
KINDS = ("reproduce_command", "test_command", "flaky_test", "build_quirk", "ownership", "other")
# The stages that may write a note: the Investigator with a passing probe, or a person's correction.
WRITERS = ("investigate_stuck", "person")
NOTE_FIELDS = frozenset({"id", "kind", "text", "receipt", "retraction"})
RECEIPT_FIELDS = frozenset({"run", "stage", "investigation", "observation", "source_revision", "recorded_at"})
RETRACTION_FIELDS = frozenset({"at", "reason", "replaced_by"})
ID_PATTERN = re.compile(r"note-[0-9a-f]{16}")
RUN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")


def _error(message="Project notes are invalid or unsafe; the existing file was preserved."):
    return ValueError(message)


def _text(value, limit, *, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _error()
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value):
        raise _error()


def note_id(kind: str, text: str) -> str:
    """The same fact, in any run, is the same note: kind and whitespace-normalized text."""
    normalized = " ".join(text.split()).casefold()
    return "note-" + hashlib.sha256(f"{kind}\0{normalized}".encode()).hexdigest()[:16]


def _check_receipt(receipt):
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS or receipt["stage"] not in WRITERS:
        raise _error()
    if receipt["run"] is not None and (
        not isinstance(receipt["run"], str) or RUN_PATTERN.fullmatch(receipt["run"]) is None
    ):
        raise _error()
    if (receipt["stage"] == "investigate_stuck") != (receipt["run"] is not None):
        raise _error()  # an Investigator's note names its run; a person's correction names none
    _text(receipt["investigation"], MAX_SHORT, nullable=True)
    _text(receipt["observation"], MAX_OBSERVATION)
    _text(receipt["source_revision"], MAX_SHORT, nullable=True)
    _text(receipt["recorded_at"], MAX_SHORT)


def _check_note(note):
    if not isinstance(note, dict) or set(note) != NOTE_FIELDS:
        raise _error()
    if note["kind"] not in KINDS:
        raise _error()
    _text(note["text"], MAX_TEXT)
    if note["id"] != note_id(note["kind"], note["text"]):
        raise _error()
    _check_receipt(note["receipt"])
    retraction = note["retraction"]  # None while the note is in force
    if retraction is not None:
        if not isinstance(retraction, dict) or set(retraction) != RETRACTION_FIELDS:
            raise _error()
        _text(retraction["at"], MAX_SHORT)
        _text(retraction["reason"], MAX_REASON, nullable=True)
        if retraction["replaced_by"] is not None and not (
            isinstance(retraction["replaced_by"], str) and ID_PATTERN.fullmatch(retraction["replaced_by"])
        ):
            raise _error()


def shareable(note) -> bool:
    """Whether every string a note holds is safe to keep and share (autocode_project_memory.safe)."""
    receipt, retraction = note["receipt"], note["retraction"] or {}
    values = [note["text"], *receipt.values(), *retraction.values()]
    return all(memory.safe(value) for value in values if isinstance(value, str))


def sanitize(document):
    """Validate the exact version-1 shape; an unshareable note is dropped whole, never rewritten."""
    if not isinstance(document, dict) or set(document) != {"version", "notes"} or document["version"] != VERSION:
        raise _error()
    notes = document["notes"]
    if type(document["version"]) is not int or not isinstance(notes, list) or len(notes) > MAX_NOTES:
        raise _error()
    seen = set()
    for note in notes:
        _check_note(note)
        if note["id"] in seen:
            raise _error()
        seen.add(note["id"])
    return {"version": VERSION, "notes": [copy.deepcopy(note) for note in notes if shareable(note)]}


def _empty():
    return {"version": VERSION, "notes": []}


def load(project) -> dict:
    """The project's notes as a snapshot; an invalid file is reported and ignored, never echoed."""
    try:
        document, pin = memory.read_document(project, FILENAME, sanitize, limit=MAX_BYTES)
    except (OSError, ValueError):
        return {
            "status": "invalid",
            "notes": [],
            "sha256": None,
            "note": "Project notes could not be safely loaded; they were ignored.",
        }
    if document is None:
        return {"status": "absent", "notes": [], "sha256": None, "note": "No project notes are saved."}
    return {
        "status": "loaded",
        "notes": document["notes"],
        "sha256": pin,
        "note": "Project notes are advisory; the task and the repository win over them.",
    }


def active(notes) -> list[dict]:
    return [note for note in notes or [] if isinstance(note, dict) and note.get("retraction") is None]


def _bounded(notes):
    """Keep the newest MAX_NOTES; retracted notes outlive active ones so a correction is not lost."""
    rows = list(notes)
    while len(rows) > MAX_NOTES:
        oldest_active = next((i for i, row in enumerate(rows) if row["retraction"] is None), 0)
        rows.pop(oldest_active)
    return rows


def _note(kind, text, receipt):
    return {
        "id": note_id(kind, text),
        "kind": kind,
        "text": text,
        "receipt": copy.deepcopy(receipt),
        "retraction": None,
    }


def candidate(kind, text, receipt) -> dict | None:
    """One proposed note with its receipt, or None when it is malformed or not shareable."""
    if not isinstance(text, str) or kind not in KINDS:
        return None
    try:
        note = _note(kind, text.strip(), receipt)
        _check_note(note)
    except ValueError:
        return None
    return note if shareable(note) else None


def add(project, notes: list[dict]) -> list[str]:
    """Record receipted notes; return their IDs. A note already recorded, active or retracted, is kept as it is."""
    for note in notes:
        _check_note(note)

    def change(previous):
        document = previous or _empty()
        known = {row["id"] for row in document["notes"]}
        document["notes"] = _bounded([*document["notes"], *(n for n in notes if n["id"] not in known)])
        return document

    memory.update_document(project, FILENAME, sanitize, change, limit=MAX_BYTES)
    return [note["id"] for note in notes]


def _find(document, ident):
    note = next((row for row in (document or _empty())["notes"] if row["id"] == ident), None)
    if note is None:
        raise KeyError(f"No project note {ident}; `autocode notes list` shows them.")
    return note


def retract(project, ident: str, *, reason: str | None, at: str) -> dict:
    """A person says a note is wrong. It stays recorded as retracted, so it is not learned again."""
    if reason is not None and not memory.safe(reason):
        raise _error("The reason must not hold a secret or a local path.")
    result: dict[str, Any] = {}

    def change(previous):
        note = _find(previous, ident)
        if note["retraction"] is None:
            note.update(retraction={"at": at, "reason": reason or None, "replaced_by": None})
        result.update(note)
        return previous

    memory.update_document(project, FILENAME, sanitize, change, limit=MAX_BYTES)
    return result


def correct(project, ident: str, text: str, *, at: str) -> dict:
    """A person replaces a note's text: the old note is retracted and the new one is the person's own."""
    result: dict[str, Any] = {}

    def change(previous):
        old = _find(previous, ident)
        if old["retraction"] is not None:
            raise _error(f"{ident} is retracted; correct the note in force instead.")
        receipt = {
            "run": None,
            "stage": "person",
            "investigation": None,
            "observation": f"A person corrected {ident}.",
            "source_revision": None,
            "recorded_at": at,
        }
        new = candidate(old["kind"], text, receipt)
        if new is None:
            raise _error(
                "The corrected note must be plain text of at most 600 characters, with no secret or local path."
            )
        if new["id"] == old["id"]:
            raise _error("The corrected text is the note's current text.")
        old.update(retraction={"at": at, "reason": "Corrected by a person.", "replaced_by": new["id"]})
        existing = next((row for row in previous["notes"] if row["id"] == new["id"]), None)
        if existing is None:
            previous["notes"] = _bounded([*previous["notes"], new])
        else:  # the person's text names a known note: it is theirs, and in force, from now on
            existing.update(retraction=None, receipt=new["receipt"])
        result.update(new)
        return previous

    memory.update_document(project, FILENAME, sanitize, change, limit=MAX_BYTES)
    return result


def context(notes) -> list[dict]:
    """The active notes a stage prompt is given, newest first, within MAX_CONTEXT_BYTES."""
    rows: list[dict] = []
    for note in reversed(active(notes)):
        receipt = note["receipt"]
        row = {
            "id": note["id"],
            "kind": note["kind"],
            "text": note["text"],
            "learned_from": receipt["observation"],
            "learned_in": receipt["run"] or "a person's correction",
        }
        if len(json.dumps([*rows, row]).encode()) > MAX_CONTEXT_BYTES:
            break
        rows.append(row)
    return rows
