#!/usr/bin/env python3
"""Script the reporter's unanswered questions through the current Requirements role.

The catalog fake only reads a negative diagnosis in the historical Investigator
stage. This test-local adapter supplies those same questions at the current
Requirements boundary, without changing dispatch, saved policy or run state.
"""

from __future__ import annotations

import json
import os
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    config = json.loads(Path(os.environ["SCENARIO_FAKE_CONFIG"]).read_text())
    note = json.loads((Path(config["reference"]) / "docs/bugs/none-cells.json").read_text())
    provider = runpy.run_path(str(ROOT / "scenarios/harness/fake_codex.py"))
    original = provider["report_for"]

    def report_for(stage: str, data: dict) -> dict:
        report = original(stage, data)
        if stage == "requirements_gather":
            report.update(
                summary=note["finding"],
                required_behaviors=[],
                acceptance_tests=[],
                open_questions=[
                    {
                        "id": f"REPORTER_{number}",
                        "question": question,
                        "why": "The reported behavior does not reproduce on this checkout; reporter evidence is missing.",
                        "options": [],
                        "proposed_default": "",
                        "kind": "decision",
                        "category": "requested_outcome",
                        "delegable": False,
                    }
                    for number, question in enumerate(note["questions"], start=1)
                ],
            )
        return report

    # runpy's functions retain their execution namespace; replace only model
    # output there, leaving the real CLI/controller and every gate untouched.
    original.__globals__["report_for"] = report_for
    return provider["main"]()


if __name__ == "__main__":
    raise SystemExit(main())
