"""Offline contract fixtures used by regression and subprocess smoke tests."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def _review_data(data):
    """Read the runner's pinned full rows before judging summarized proof detail."""
    if not isinstance(data, dict):
        return None
    fields = {
        "regression_proof_mutation_files": ("mutation_check", "files"),
        "regression_proof_obligations": ("changed_code_evidence", "obligations"),
    }
    artifact = data.get("context_artifact")
    if artifact is None:
        artifact = {}
    if not isinstance(artifact, dict):
        return None
    declared = artifact.get("fields")
    if declared is None:
        declared = {}
    if not isinstance(declared, dict):
        return None
    # Whole objects the runner summarizes by key (autocode_proof_detail.SURVIVORS and INPUTS).
    objects = {
        "regression_proof_mutation_survivors": ("mutation_check", "survivors"),
        "regression_proof_changed_code_inputs": (None, "changed_code_inputs"),
    }
    requested = (fields.keys() | objects.keys() | {"regression_proof"}) & declared.keys()
    if requested:
        try:
            raw = Path(artifact["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
                return None
            saved = json.loads(raw)
            if not isinstance(saved, dict):
                return None
            result = copy.deepcopy(data)
            if "regression_proof" in requested:
                whole_proof = saved["regression_proof"]
                if not isinstance(whole_proof, dict) or declared["regression_proof"].get("total") != len(whole_proof):
                    return None
                proof = copy.deepcopy(whole_proof)
            else:
                proof = result.get("regression_proof") or {}
                if not isinstance(proof, dict):
                    return None
            for name in fields.keys() & requested:
                rows = saved[name]
                if (
                    not isinstance(rows, list)
                    or not all(isinstance(row, dict) for row in rows)
                    or declared[name].get("total") != len(rows)
                ):
                    return None
                holder, key = fields[name]
                section = proof.get(holder)
                if not isinstance(section, dict):
                    section = {}
                    proof[holder] = section
                section[key] = rows
            for name in objects.keys() & requested:
                whole = saved[name]
                if not isinstance(whole, dict):
                    return None
                holder, key = objects[name]
                section = proof if holder is None else proof.setdefault(holder, {})
                if not isinstance(section, dict):
                    return None
                section[key] = whole
            result["regression_proof"] = proof
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
    else:
        result = data
    proof = result.get("regression_proof") or {}
    if not isinstance(proof, dict) or "details_artifact" in proof:
        return None
    for row in (proof.get("mutation_check") or {}).get("files") or []:
        if not isinstance(row, dict) or not isinstance(row.get("mutants", []), list):
            return None
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        if not isinstance(row, dict) or "gaps" in row or "tested_lines" in row:
            return None
    return result


def quote_changed_lines(data, rows):
    """Each decision with its gap's source_line from the handoff, as a Tester quotes the line it decides (#2977)."""
    resolved = _review_data(data)
    source = resolved if resolved is not None else data
    texts = {}
    evidence = ((source.get("regression_proof") or {}).get("changed_code_evidence") or {}).get("obligations") or []
    for row in evidence if isinstance(evidence, list) else []:
        for gap in [row, *(row.get("gaps") or [])] if isinstance(row, dict) else []:
            for key in ("gap_id", "handle"):
                if isinstance(gap, dict) and isinstance(gap.get(key), str):
                    texts[gap[key]] = gap.get("source_line", "")
    return [{**row, "source_line": texts.get(row.get("gap_id"), "")} for row in rows]


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


# A greeting variant whose two redundant guards each carry a surviving bound mutant: a valid name has
# already passed the nonempty check, so neither guard ever fires and a bound mutation of it changes nothing
# the tests observe. Unlike a no-site line (which the runner settles itself, #4218), a survived line keeps a
# mutation outcome and stays reason_required until the independent Tester accepts or rejects it. The
# report-repair diagnostics fixture scripts this exact source so its open gaps survive no-site settlement.
GREETING_REDUNDANT_SOURCE = """import sys

def main():
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("A nonempty name is required", file=sys.stderr)
        return 2
    name = sys.argv[1]
    if len(name) < 1: return 2
    greeting = "Hello, " + name
    if len(greeting) < 7: return 2
    print(greeting)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""
# The exact redundant-guard lines whose surviving mutants the scripted Tester reviews.
GREETING_REDUNDANT_GUARDS = ("if len(name) < 1: return 2", "if len(greeting) < 7: return 2")


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
    data = _review_data(data)
    if data is None:
        return []
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


def scripted_files_changed_code_rejections(data, files):
    """Reject undecided lines in a failed proof, bound to the Builder's exact scripted bytes."""
    data = _review_data(data)
    if data is None:
        return []
    proof = data.get("regression_proof") or {}
    if not files or not proof.get("path") or proof.get("verdict") not in ("FAIL", "UNVERIFIED"):
        return []
    decisions = []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        path, line = row.get("path"), row.get("line")
        source = files.get(path)
        if (
            source is None
            or row.get("status") != "reason_required"
            or row.get("sha256") != hashlib.sha256(source.encode()).hexdigest()
            or type(line) is not int
            or not 0 < line <= len(source.splitlines())
            or row.get("mutation_outcomes")
        ):
            continue
        decisions.append(
            {
                "gap_id": row["gap_id"],
                "decision": "reject",
                "reason_kind": "instrumentation_unavailable",
                "reason": (
                    f"The failed fixture proof does not establish behavioral evidence for {path}:{line}. "
                    "Add a regression test for the scripted behavior, run it against the original and candidate "
                    "source, and include this file in the verified suite before accepting the line."
                ),
                "evidence_refs": [proof["path"], path],
            }
        )
    return decisions


def scripted_files_changed_code_reasons(data, files):
    """The fake Tester's reasons for exact scripted lines without complete mutation evidence.

    Ordinary reasons bind the gap, scripted bytes and line, and only where the proof's collected
    mutation check read those bytes and found no mutation site on the line: never for a file it
    did not script, other bytes than it wrote, or a line a mutant reached or could have reached.
    The protected-link fixture additionally reviews one redundant assertion's exact surviving
    mutant, pinned to its scripted source and both independently observed oracle tests.
    """
    data = _review_data(data)
    if data is None:
        return []
    proof = data.get("regression_proof") or {}
    mutation = proof.get("mutation_check") or {}
    if not proof.get("path") or not files or mutation.get("collected") is not True:
        return []
    scripted = {name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()}
    sites = {}
    for entry in mutation.get("files") or []:
        if scripted.get(entry.get("path")) == entry.get("sha256"):
            sites[entry["path"]] = [
                (mutant.get("line"), mutant.get("end_line") or mutant.get("line"))
                for mutant in entry.get("mutants") or []
            ]
    decisions = []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        path, line = row.get("path"), row.get("line")
        if (
            row.get("status") != "reason_required"
            or path not in sites
            or row.get("sha256") != scripted[path]
            or type(line) is not int
            or row.get("mutation_outcomes")
            or any(start <= line <= end for start, end in sites[path])
        ):
            continue
        decisions.append(
            {
                "gap_id": row["gap_id"],
                "decision": "accept",
                "reason_kind": "instrumentation_unavailable",
                "reason": (
                    f"Scripted fixture: {path}:{line} is the exact scripted content of this delivery, and the "
                    "proof's mutation check read these bytes and found no mutation site on the line. The "
                    "delivery's tests, run independently by the proof, exercise the behavior it takes part in."
                ),
                "evidence_refs": [proof["path"], path],
            }
        )
    return (
        decisions
        + _scripted_oracle_equivalence(proof, files, mutation)
        + _scripted_greeting_equivalence(proof, files, mutation)
    )


def _scripted_oracle_equivalence(proof, files, mutation):
    """Review the exact protected-link fixture's redundant assertion, never arbitrary survivors."""
    path = "shared/oracle.py"
    oracle = (
        "import unittest\nfrom layout import height\nclass Layout(unittest.TestCase):\n"
        "    def test_required_height(self):\n        self.assertEqual(height(), 40)\n\n"
        "    def test_positive_height(self):\n        self.assertGreater(height(), 0)\n"
    )
    if (
        proof.get("verdict") != "PASS"
        or files.get(path) != oracle
        or files.get("test_layout.py") != oracle
        or files.get("layout.py") != "def height():\n    return 40\n"
    ):
        return []
    digest = hashlib.sha256(oracle.encode()).hexdigest()
    required_tests = {"test_layout.Layout.test_required_height", "test_layout.Layout.test_positive_height"}
    observed = any(
        entry.get("path") == path
        and entry.get("sha256") == digest
        and any(
            mutant.get("line") == mutant.get("end_line") == 8
            and mutant.get("operator") == "drop_call"
            and mutant.get("before") == "self.assertGreater(height(), 0)"
            and mutant.get("after") == "None"
            and mutant.get("outcome") == "survived"
            and mutant.get("source_sha256") == digest
            and mutant.get("failed_tests") == []
            and required_tests.issubset(mutant.get("survived_tests") or [])
            for mutant in entry.get("mutants") or []
        )
        for entry in mutation.get("files") or []
    )
    if not observed:
        return []
    return [
        {
            "gap_id": row["gap_id"],
            "decision": "accept",
            "reason_kind": "equivalent_mutant",
            "reason": (
                "The exact scripted protected oracle already asserts height() == 40 in "
                "test_required_height, which implies height() > 0. The drop_call mutant removes "
                "only this redundant positive-height assertion, not production behavior. Both "
                "named test_layout tests passed independently under that exact mutant."
            ),
            "evidence_refs": [proof["path"], path],
        }
        for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []
        if row.get("path") == path
        and row.get("sha256") == digest
        and row.get("line") == 8
        and row.get("status") == "reason_required"
        and row.get("mutation_outcomes") == ["survived"]
    ]


def _scripted_greeting_equivalence(proof, files, mutation):
    """Review the exact redundant-guard greeting's surviving bound mutants, never arbitrary survivors.

    A survived line keeps a mutation outcome, so the runner's no-site settlement (#4218) never closes it:
    only the independent Tester's accept/reject does. This scripts accept for the two redundant guards of
    the exact scripted greeting, each pinned to its observed surviving mutant and both greeting tests.
    """
    path = "greet.py"
    if (
        proof.get("verdict") != "PASS"
        or files.get(path) != GREETING_REDUNDANT_SOURCE
        or files.get("test_greeting.py") != GREETING_TESTS
        or mutation.get("collected") is not True
    ):
        return []
    digest = hashlib.sha256(GREETING_REDUNDANT_SOURCE.encode()).hexdigest()
    lines = GREETING_REDUNDANT_SOURCE.splitlines()
    guard_lines = {number for number, text in enumerate(lines, 1) if text.strip() in GREETING_REDUNDANT_GUARDS}
    required_tests = {
        "test_greeting.GreetingTests.test_greets_a_valid_name",
        "test_greeting.GreetingTests.test_rejects_empty_and_whitespace_names",
    }
    survived_lines = {
        mutant.get("line")
        for entry in mutation.get("files") or []
        if entry.get("path") == path and entry.get("sha256") == digest
        for mutant in entry.get("mutants") or []
        if mutant.get("line") in guard_lines
        and mutant.get("outcome") == "survived"
        and mutant.get("source_sha256") == digest
        and mutant.get("failed_tests") == []
        and required_tests.issubset(mutant.get("survived_tests") or [])
    }
    if not survived_lines:
        return []
    return [
        {
            "gap_id": row["gap_id"],
            "decision": "accept",
            "reason_kind": "equivalent_mutant",
            "reason": (
                f"The exact scripted greeting guard at {path}:{row['line']} is redundant: a valid name already "
                "passed the nonempty check, so this guard never fires. Its surviving bound mutant changes no "
                "greeting output or exit code, and both test_greeting tests passed independently under that "
                "exact mutant."
            ),
            "evidence_refs": [proof["path"], path],
        }
        for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []
        if row.get("path") == path
        and row.get("sha256") == digest
        and row.get("line") in survived_lines
        and row.get("status") == "reason_required"
        and "survived" in (row.get("mutation_outcomes") or [])
    ]


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
                "convention_ref": "",
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
    state.update({"next_stage": "builder", "phase": "EXECUTING"})
