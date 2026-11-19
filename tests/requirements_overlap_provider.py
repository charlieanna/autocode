#!/usr/bin/env python3
"""Script the Requirements passes of the chat's no-assumption tests (#3233).

A test-local adapter over the scenario harness's scripted provider
(``scenarios/harness/fake_codex.py``), the same pattern as ``human_stop_provider``:
only model output is scripted. The tests drive the real CLI and controller.

With ``AUTOCODE_OVERLAP_MODE=questions``, until the question below is among the
call's saved answers, the Requirements pass asks it (a questions-only handoff on a
full-schema call, the triage contract's ``ready: False`` on a triage call). Once a
saved answer for it exists, the ordinary complete report is handed over.

Every report's ``summary`` records the exact saved answers the call ran with, so
a test can see which call's report the run adopted. Every provider invocation
appends one JSON line to ``AUTOCODE_OVERLAP_LOG`` when its prompt is read -
before any output exists - with the stage and the saved answers it was given,
and the question ids its packet shows as answered by the person. A call that
ran while the person was still reading would show the offered default as a saved
answer with no recorded answer from the person.
"""

from __future__ import annotations

import json
import os
import re
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

QUESTION_ID = "OVERLAP_FORM"
QUESTION_TEXT = "Should the greeting be a library function, a command line, or both?"
QUESTION_DEFAULT = "Command line"
DIFFERENT_ANSWER = "A web endpoint"

QUESTION = {
    "id": QUESTION_ID,
    "question": QUESTION_TEXT,
    "why": "The request does not say how the greeting is invoked, which changes what is built.",
    "options": [],
    "proposed_default": QUESTION_DEFAULT,
    "kind": "decision",
    "category": "requested_outcome",
    "delegable": False,
}


def _answer_text(row) -> str:
    if isinstance(row, dict):
        return str(row.get("text") or "")
    return str(row or "")


def _saved(data: dict) -> dict:
    return {str(qid): _answer_text(row) for qid, row in (data.get("saved_answers") or {}).items()}


def _answered(data: dict) -> list:
    return sorted(
        {
            str(event.get("question_id"))
            for event in data.get("user_events") or []
            if isinstance(event, dict) and event.get("question_id")
        }
    )


def _stamp(report: dict, saved: dict) -> dict:
    return {**report, "summary": f"{report.get('summary', '')} [overlap saved={json.dumps(saved, sort_keys=True)}]"}


def _log(entry: dict) -> None:
    path = os.environ.get("AUTOCODE_OVERLAP_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\n")


def _grounded_in_task(report: dict, data: dict) -> dict:
    """A full-schema Requirements report grounded in the packet's actual task text."""
    task = str(data.get("task") or "")
    if not task or "requirements" not in report:
        return report
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", task.strip()) if part.strip()]
    rows = [
        {"id": f"R{number}", "text": sentence, "source_quote": sentence}
        for number, sentence in enumerate(sentences, start=1)
    ]
    return {**report, "requirements": rows, "required_behaviors": [row["text"] for row in rows]}


class _PromptTap:
    """Record each provider invocation when its prompt is read, before any output exists."""

    def __init__(self, stream):
        self._stream = stream
        self._calls = 0

    def read(self, *args, **kwargs):
        data = self._stream.read(*args, **kwargs)
        if isinstance(data, str) and "CURRENT HANDOFF DATA\n" in data:
            try:
                packet = json.loads(data.split("CURRENT HANDOFF DATA\n", 1)[1])
            except ValueError:
                packet = {}
            self._calls += 1
            _log(
                {
                    "call": self._calls,
                    "stage": packet.get("stage"),
                    "saved": _saved(packet),
                    "answered": _answered(packet),
                }
            )
        return data

    def __getattr__(self, name):
        return getattr(self._stream, name)


def main() -> int:
    mode = os.environ.get("AUTOCODE_OVERLAP_MODE", "")
    provider = runpy.run_path(str(ROOT / "scenarios" / "harness" / "fake_codex.py"))
    original = provider["report_for"]

    def report_for(stage, data):
        report = original(stage, data)
        saved = _saved(data)
        questions = [QUESTION]
        if stage != "requirements_gather" or mode != "questions":
            return _stamp(report, saved)
        if "ready" in report:
            # The triage contract: blocking questions now, the full pass once they are answered.
            return _stamp(
                {
                    "summary": report.get("summary", ""),
                    "intended_outcome": report.get("intended_outcome", ""),
                    "ready": not all(saved.get(row["id"]) for row in questions),
                    "open_questions": [] if all(saved.get(row["id"]) for row in questions) else questions,
                },
                saved,
            )
        if not all(saved.get(row["id"]) for row in questions):
            report = {**report, "required_behaviors": [], "acceptance_tests": [], "open_questions": questions}
        # Builder-added correction (documented in the run report): the scripted provider quotes
        # its configured scenario brief, so a case that runs a longer brief (MEDIUM_BRIEF) would
        # hand over a report the Requirements handoff's coverage gate rightly refuses -- no line
        # of the actual brief quoted. Ground the full-schema report in the packet's own task with
        # the harness's own rule (fake_codex.requirements): one requirement per brief sentence,
        # quoted verbatim. Identical output for a case whose task is the scenario brief.
        report = _grounded_in_task(report, data)
        return _stamp(report, saved)

    original.__globals__["report_for"] = report_for
    sys.stdin = _PromptTap(sys.stdin)
    return provider["main"]()


if __name__ == "__main__":
    raise SystemExit(main())
