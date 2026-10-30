"""Offline contract fixtures used by regression and subprocess smoke tests."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState


GREETING_SOURCE = """import sys

def main():
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("A nonempty name is required", file=sys.stderr)
        return 2
    print("Hello, " + sys.argv[1])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""


GREETING_TESTS = """from pathlib import Path
import subprocess
import sys
import unittest

class GreetingTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run([sys.executable, str(Path(__file__).with_name("greet.py")), *args],
                              capture_output=True, text=True)

    def test_greets_a_valid_name(self):
        result = self.invoke("Ada")
        self.assertEqual(0, result.returncode)
        self.assertEqual("Hello, Ada\\n", result.stdout)
        self.assertEqual("", result.stderr)

    def test_rejects_empty_and_whitespace_names(self):
        for args in (("",), ("   ",), (), ("Ada", "Lovelace")):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertIn("nonempty name", result.stderr)
"""


GOODBYE_SOURCE = """import sys
print('Goodbye, ' + sys.argv[1])
"""

GOODBYE_TESTS = """from pathlib import Path
import subprocess
import sys
import unittest

class GoodbyeTests(unittest.TestCase):
    def test_says_goodbye_to_a_name(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("bye.py")), "Ada"],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode)
        self.assertEqual("Goodbye, Ada\\n", result.stdout)
        self.assertEqual("", result.stderr)
"""


def write_goodbye_source(workspace, *, revision=None):
    """The second milestone's CLI with its own fail-to-pass test; revision markers vary the source."""
    marker = f"# batch {revision}\n" if revision is not None else ""
    (Path(workspace) / "bye.py").write_text(GOODBYE_SOURCE + marker)
    (Path(workspace) / "test_goodbye.py").write_text(GOODBYE_TESTS)


def write_greeting_source(workspace, *, revision=None):
    """A real fixture artifact; revision markers vary source without breaking behavior."""
    marker = f"# Fixture revision: {revision}\n" if revision is not None else ""
    (Path(workspace) / "greet.py").write_text(marker + GREETING_SOURCE)


def seed_greeting_workspace(workspace):
    """Give check-replay a discoverable suite that exercises the fixture contract."""
    root = Path(workspace)
    write_greeting_source(root)
    (root / "test_greeting.py").write_text(GREETING_TESTS)


# The fake Tester's reasons for the lines the mutator cannot break, per fixture CLI: the exact source
# and tests they describe (comment lines aside, which are never obligations), and each line's reason.
FIXTURE_REASONS = {
    "greet.py": (
        GREETING_SOURCE,
        "test_greeting.py",
        GREETING_TESTS,
        {
            "import sys": "No supported import mutation. The tests execute sys.argv, stderr and process exits; removing this import breaks the observed CLI flows.",
            'print("A nonempty name is required", file=sys.stderr)': "No supported call mutation. test_rejects_empty_and_whitespace_names asserts the nonempty-name diagnostic on stderr for four invalid inputs; deleting or redirecting this call fails those assertions.",
            'print("Hello, " + sys.argv[1])': "No supported call mutation. test_greets_a_valid_name asserts exact Hello, Ada plus newline on stdout and empty stderr; suppressing or redirecting this call fails the test.",
            'if __name__ == "__main__":': "Entrypoint guards are excluded by the mutator. Both test methods invoke the CLI as a process and require its greeting or rejection; disabling this guard fails those assertions.",
            "raise SystemExit(main())": "No supported exit-adapter mutation. Both test methods check exit status, including exit 2 for empty, whitespace, missing and extra names; discarding main's result fails those checks.",
        },
    ),
    "bye.py": (
        GOODBYE_SOURCE,
        "test_goodbye.py",
        GOODBYE_TESTS,
        {
            "import sys": "No supported import mutation. test_says_goodbye_to_a_name runs the CLI with a name argument; removing this import breaks the observed flow.",
            "print('Goodbye, ' + sys.argv[1])": "No supported call mutation. test_says_goodbye_to_a_name asserts exact Goodbye, Ada plus newline on stdout and empty stderr; suppressing or redirecting this call fails the test.",
        },
    ),
}


def _code_lines(text):
    return [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def greeting_changed_code_reasons(data):
    """Specific independent judgments for the exact tested fixture CLIs only."""
    proof = data.get("regression_proof") or {}
    if not proof.get("path"):
        return []
    decisions = []
    for path, (expected_source, test_name, expected_tests, reasons) in FIXTURE_REASONS.items():
        try:
            source = Path(path).read_text()
            tests = Path(test_name).read_text()
        except OSError:
            continue
        if _code_lines(source) != _code_lines(expected_source) or tests != expected_tests:
            continue
        decisions.extend(_line_reasons(proof, path, source, reasons))
    return decisions


def _line_reasons(proof, path, source, reasons):
    digest, lines = hashlib.sha256(source.encode()).hexdigest(), source.splitlines()
    decisions = []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        line = row.get("line")
        if (
            row.get("status") != "reason_required"
            or row.get("path") != path
            or row.get("sha256") != digest
            or type(line) is not int
            or not 0 < line <= len(lines)
            or row.get("mutation_outcomes")
        ):
            continue
        source_line = lines[line - 1].strip()
        reason = reasons.get(source_line)
        if reason is None and line == 1 and source.startswith("# Fixture revision: "):
            # This exact first-line comment is the fixture's source-revision nonce;
            # it cannot affect execution and is outside the behavioral mutation surface.
            reason = "This exact fixture revision comment carries no executable behavior. The pinned CLI tests exercise the implementation below it; removing the comment changes only fixture identity."
        if reason:
            decisions.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], path],
                }
            )
    # The multi-milestone visual CLI fixture has one additional, deliberately
    # tiny deliverable. Its Validator executes bye.py Ada and checks the exact
    # stdout and exit code. Accept only the two exact lines in that fixture;
    # ordinary or altered files still receive no exception.
    try:
        goodbye = Path("bye.py").read_text()
    except OSError:
        goodbye = ""
    goodbye_lines = goodbye.splitlines()
    if (
        len(goodbye_lines) == 3
        and goodbye_lines[:2] == ["import sys", "print('Goodbye, ' + sys.argv[1])"]
        and re.fullmatch(r"# batch [0-9a-f-]{36}", goodbye_lines[2])
    ):
        goodbye_digest = hashlib.sha256(goodbye.encode()).hexdigest()
        goodbye_reasons = {
            1: "The exact offline milestone fixture executes bye.py with Ada and asserts its output. The import supplies sys.argv used by the checked CLI; there is no other behavior on this line.",
            2: "The exact offline milestone fixture executes bye.py with Ada and asserts exact Goodbye, Ada output, empty stderr and exit zero; changing or removing this call fails that check.",
        }
        for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
            line = row.get("line")
            if (
                row.get("status") == "reason_required"
                and row.get("path") == "bye.py"
                and row.get("sha256") == goodbye_digest
                and type(line) is int
                and line in goodbye_reasons
                and not row.get("mutation_outcomes")
            ):
                decisions.append(
                    {
                        "gap_id": row["gap_id"],
                        "decision": "accept",
                        "reason_kind": "instrumentation_unavailable",
                        "reason": goodbye_reasons[line],
                        "evidence_refs": [proof["path"], "bye.py"],
                    }
                )
    return decisions


def assert_operational_wait(test, state: RunState, pause_status):
    from tools import autocode_resolver_human as human

    test.assertEqual("WAITING_FOR_USER", state["status"])
    test.assertEqual("WAITING_FOR_USER", state["phase"])
    public = human.current(state)
    test.assertIsNotNone(public)
    test.assertEqual("operational_exhaustion", public["scope"])
    proposal = state["resolver"]["human_escalations"][public["request_id"]]["identity"]["proposal"]
    test.assertEqual(pause_status, proposal["origin"]["pause_status"])
    return public


def body(*, questions=False, human=False, task_kind="build") -> dict[str, Any]:
    return {
        "task_kind": task_kind,
        "intended_outcome": "Provide a deterministic greeting CLI",
        "intended_user": "A local developer",
        "end_to_end_flow": ["Run the CLI with a name", "Read the greeting or an invalid-input error"],
        "technical_approach": ["A standard-library Python CLI using sys.argv"],
        "milestones": [
            {
                "id": "M1",
                "objective": "Deliver and verify the greeting flow",
                "acceptance_criteria": ["C1"],
                "depends_on": [],
                "affected_paths": ["greet.py", "test_greeting.py"],
            }
        ],
        "deliverables": ["greet.py", "CLI regression tests"],
        "required_behaviors": ["Print Hello, NAME for a nonempty name"],
        "important_failure_cases": ["Reject an empty name with nonzero exit status"],
        "scope_exclusions": ["Web service", "Deployment"],
        "constraints": ["Python standard library only"],
        "permission_boundaries": ["Read and edit only this fixture Git workspace; no external writes"],
        "accepted_assumptions": [{"text": "CLI invocation is sufficient", "basis": "agent_proposed", "answer_id": ""}],
        "delegated_decisions": [],
        "acceptance_criteria": [
            {
                "id": "C1",
                "criterion": "Contract holds",
                "verification_method": "Execute greeting and invalid-input regression checks",
                "human_review": human,
            }
        ],
        "open_blocking_questions": [
            {
                "id": "Q1",
                "question": "Should the greeting be a CLI or web endpoint?",
                "why": "This determines the delivered interface",
                "options": ["CLI", "Web"],
                "proposed_default": "CLI: local use without a server",
                "kind": "decision",
                "category": "behavior",
                "delegable": True,
            }
        ]
        if questions
        else [],
    }


def envelope(state: RunState):
    contract = cast(dict, state["goal_contract"])
    return {
        "contract_revision": contract["revision"],
        "contract_hash": contract["hash"],
        "task_id": cast(dict, state.get("current_task", {})).get("id", ""),
        "deferred_backlog": [],
        "user_request": {
            "kind": "none",
            "discovered": "",
            "impact": "",
            "decision_needed": "",
            "options": [],
            "proposed_delta": "",
        },
    }


def approve_fixture(state: RunState, goals):
    # The lifecycle and Resolver modules are imported the way `goals` was (plain, tools. or
    # autocode_cli.), so the fixture acts on the same module instances as the caller.
    import importlib

    prefix = goals.__name__[: -len("autocode_goals")]
    lifecycle = importlib.import_module(prefix + "autocode_goal_lifecycle")
    human = importlib.import_module(prefix + "autocode_resolver_human")
    lifecycle.migrate(state)
    lifecycle.install_draft(state, body(), origin="fixture")
    # The fixture simulates the writer boundary, never a presentation-side grant.
    human.evaluate(state)
    lifecycle.present(state)
    lifecycle.approve(state, goals.token(state["goal_contract"]))
    state.update({"next_stage": "terra", "phase": "EXECUTING"})
