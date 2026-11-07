"""An opt-in Requirements convention question for public project-memory controls.

Only model output is scripted. The test answers the real issued request through
AutoCode's public CLI; this provider never reads or writes AutoCode run state.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

QUESTION_ID = "PROJECT_TEST_NAMES"
QUESTION = "Which naming convention should future repository tests follow?"
ANSWER = "Use descriptive behavior-based unittest test method names."


def requirements(report: dict, data: dict) -> dict:
    """Ask only when neither this run's human answer nor historical memory supplies it."""
    saved = (data.get("saved_answers") or {}).get(QUESTION_ID) or {}
    answered = isinstance(saved, dict) and saved.get("kind") == "answer" and saved.get("text") == ANSWER
    memory = data.get("project_memory") or {}
    remembered = any(
        isinstance(row, dict) and row.get("question") == QUESTION and row.get("answer") == ANSWER
        for row in memory.get("conventions") or []
    )
    asked = not answered and not remembered
    questions = (
        [
            {
                "id": QUESTION_ID,
                "question": QUESTION,
                "why": "The request does not state the team's reusable naming preference for future repository tests.",
                "options": [],
                "proposed_default": "",
                "kind": "decision",
                "category": "technical",
                "delegable": False,
            }
        ]
        if asked
        else []
    )
    trace = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("project-memory-requests.jsonl")
    with trace.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "stage": "requirements_gather",
                    "asked": asked,
                    "saved_answer_used": answered,
                    "memory_convention_used": remembered,
                    "memory_test_command": memory.get("test_command"),
                }
            )
            + "\n"
        )
    return {
        **report,
        "open_questions": questions,
        # A tag names an actually issued question, never a manufactured answer.
        # Completion may persist it only after the person's matching public answer.
        "project_conventions": [QUESTION_ID] if asked or answered else [],
    }


# These tests belong only to the memory qualification's scripted Builder. The
# stock scenario reference tests exercise report functions but never the CLI.
CLI_TESTS = """import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from timesheet.__main__ import main


class MemoryFixtureCliTests(unittest.TestCase):
    def render(self, *options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.csv"
            path.write_text("date,hours,project\\n2025-03-03,2,core\\n2025-03-04,3,ops\\n")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, main(["report", str(path), *options]))
            return output.getvalue()

    def test_by_project_cli_prints_exact_rows(self):
        self.assertEqual(
            "2025-W10 core     2.00h\\n2025-W10 ops      3.00h\\ntotal             5.00h\\n",
            self.render("--by-project"),
        )

    def test_unflagged_cli_keeps_weekly_output(self):
        self.assertEqual("2025-W10     5.00h\\ntotal        5.00h\\n", self.render())

    def test_by_project_cli_combines_project_filter(self):
        self.assertEqual(
            "2025-W10 ops     3.00h\\ntotal            3.00h\\n",
            self.render("--by-project", "--project", "ops"),
        )
"""
CLI_TEST_PATH = "tests/test_project_memory_cli.py"
FIXTURE_REASONS = {
    "timesheet/__main__.py": (
        "5a365c43e8d82dabee0935a562b903bbc7e3a704300d8937c556d1a85808b246",
        {
            10: "The import has no supported mutation. MemoryFixtureCliTests imports main and asserts exact "
            "flagged, unflagged and filtered CLI output; removing the weekly_project_totals import "
            "breaks test_by_project_cli_prints_exact_rows.",
        },
    ),
    "timesheet/report.py": (
        "fbb35625fffc8d27534aef844e0bdd10c7a3f58cdb68f1d1a5a18e6dee60d757",
        {
            21: "The function declaration has no supported mutation. MemoryFixtureCliTests invokes the "
            "default unfiltered --by-project path and the explicit project filter and asserts exact "
            "weekly/project rows; removing or renaming this declaration breaks both CLI cases.",
        },
    ),
}


def prepare_fixture(root: Path, reference: Path) -> Path:
    """Give only this qualification its missing CLI behavior checks."""
    import shutil

    # The generic provider locates scripted faults relative to the catalog's
    # scenario.toml. Preserve that layout in this task-local reference copy.
    selected = root / "scenarios/catalog/project-memory/reference"
    shutil.copytree(reference, selected)
    shutil.copy2(reference.parent / "scenario.toml", selected.parent / "scenario.toml")
    harness = root / "scenarios/harness"
    harness.mkdir(parents=True)
    shutil.copy2(Path(__file__), harness / "project_memory_provider.py")
    (selected / CLI_TEST_PATH).write_text(CLI_TESTS)
    return selected


def install_fixture_reviewer(root: Path) -> None:
    """Compose a task-local reviewer; never edit the shared scripted provider."""
    import shutil

    bindir = root / "bin"
    (bindir / "fake_changed_code.py").rename(bindir / "project_memory_base_evidence.py")
    shutil.copy2(Path(__file__), bindir / "project_memory_provider.py")
    (bindir / "fake_changed_code.py").write_text(
        "from project_memory_base_evidence import *\n"
        "from project_memory_base_evidence import judgments as base_judgments\n"
        "from project_memory_provider import fixture_judgments\n\n"
        "def judgments(data, *, producer=None):\n"
        "    return [*base_judgments(data, producer=producer), *fixture_judgments(data, producer=producer)]\n"
    )


def fixture_judgments(data: dict, *, producer=None) -> list[dict]:
    """Explain only exact unsupported lines this task's Builder actually scripts."""
    import hashlib

    proof = data.get("regression_proof") or {}
    producer = producer or {}
    if not proof.get("path") or not producer.get("reference") or producer.get("fault") != "project_memory":
        return []
    reference = Path(producer["reference"])
    workspace = Path.cwd()
    try:
        if any((root / CLI_TEST_PATH).read_bytes() != CLI_TESTS.encode() for root in (workspace, reference)):
            return []
    except OSError:
        return []
    result = []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        path = row.get("path")
        pin, reasons = FIXTURE_REASONS.get(path, (None, {}))
        reason = reasons.get(row.get("line"))
        if (
            not reason
            or path not in producer.get("paths", [])
            or row.get("status") != "reason_required"
            or row.get("sha256") != pin
            or row.get("mutation_outcomes")
        ):
            continue
        try:
            if any(hashlib.sha256((root / path).read_bytes()).hexdigest() != pin for root in (workspace, reference)):
                continue
        except OSError:
            continue
        result.append(
            {
                "gap_id": row["gap_id"],
                "decision": "accept",
                "reason_kind": "instrumentation_unavailable",
                "reason": reason,
                "evidence_refs": [proof["path"], path, CLI_TEST_PATH],
            }
        )
    return result
