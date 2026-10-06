"""Go test names the user's brief asks for, and the runner's check that the plan proves each one by that name.

A live Go run (#498, 2026-10-05) was told to add the native tests TestFixedReturnsTwo,
TestFixedPreservesExisting and TestFixedPreservesCrash. Its draft declared test_ac1_fixed_returns_two
and so on after test:/guard:, and a revision added prose saying each one "resolves to" the requested
test. The runner proves a criterion only by the identifier right after the mark
(autocode_test_cases.declared_test_name and match_cases), so none of the three criteria could bind a
test, yet the final Plan Reviewer offered the draft for approval. Prose is not a binding, and the
matcher is not widened to read it: the plan must declare the requested name itself.

A name is requested (``requested``) when all of these hold:

- It has the form of a Go test function (Test, then an upper-case letter, digit or underscore). TestMain
  is not a test: it is Go's hook for the test binary.
- The user wrote it, introduced as a test: within a few words after "test", "tests", "testing" or
  "func", or after another such name in the same list ("add Go tests TestA, TestB and TestC"). The
  sources are the user's own words, as for the brief-literal check (autocode_requirement_cues.scan_texts):
  the task, brief feedback and the user's answers, never a delegated default or model-written text. A
  Test-prefixed word used otherwise ("deploy to TestNet") asks for no test.
- The runner's regression proof will run Go tests: an explicit ``go test`` regression command, else the
  framework autocode_verify detects in the workspace, chosen as autocode_regression.prove chooses it.
  Elsewhere TestParser is a Python or Java class, not a test the proof reports.
- The project's Go files do not already contain it. A brief that mentions an existing test or helper
  ("the test TestRetention fails", "keep the test TestX passing", "remove the test TestOld", a
  TestServer helper) names the suite's own code, which the proof already protects; it is not a new case,
  and a removed test could never be declared.

Nothing is requested in a design-only job, or when a reproduced bug's diagnosis drives the proof (its
cases are named after their own ids, autocode_test_cases.diagnosis_cases).

The check (``problems``): each requested name is bound, as the proof binds it (``match_cases`` with
framework go), by the test name some test: or guard: criterion declares; and a marked criterion whose
verification method mentions a requested name declares that name, never a different identifier with
prose mapping one to the other. Criteria without a requested name keep the default test_<criterion
id>_... convention. The goal lifecycle calls ``check`` from validate_body, so a refused draft is never
installed and a saved one cannot be approved, whatever a review accepted. Planning stages get ``rule``.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

try:
    from . import autocode_requirement_cues as cues, autocode_test_cases as test_cases, autocode_verify as verify
except ImportError:
    import autocode_requirement_cues as cues
    import autocode_test_cases as test_cases
    import autocode_verify as verify

IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])Test[A-Z0-9_][A-Za-z0-9_]*")
NOT_A_TEST = frozenset({"TestMain"})
_WORD = re.compile(r"[A-Za-z0-9_]+")
# Words that introduce a test name, and how many words later a name still counts as introduced.
CUES = frozenset({"test", "tests", "testing", "func"})
WINDOW = 8
_SKIPPED_DIRECTORIES = frozenset({"node_modules"})


def named(texts) -> list[str]:
    """Go test names ``texts`` introduce as tests, in order, without repeats."""
    found = []
    for text in texts:
        last = None
        for index, match in enumerate(_WORD.finditer(text or "")):
            word = match.group()
            if word.lower() in CUES:
                last = index
            elif (IDENTIFIER.fullmatch(word) and word not in NOT_A_TEST
                  and last is not None and index - last <= WINDOW):
                last = index
                if word not in found:
                    found.append(word)
    return found


def go_identifiers(workspace) -> set[str]:
    """Every Test-prefixed identifier already written in the workspace's Go files."""
    found = set()
    for directory, subdirectories, files in os.walk(workspace):
        subdirectories[:] = [name for name in subdirectories
                             if not name.startswith(".") and name not in _SKIPPED_DIRECTORIES]
        for name in files:
            if name.endswith(".go"):
                try:
                    found.update(IDENTIFIER.findall(Path(directory, name).read_text(errors="replace")))
                except OSError:
                    continue
    return found


def proof_framework(state) -> str | None:
    """The framework the regression proof will run, chosen as autocode_regression.prove chooses it."""
    options = (state.get("settings") or {}).get("regression") or {}
    command = options.get("test_command")
    framework = verify.command_framework(command) if command else None
    workspace = state.get("workspace")
    if framework is None and workspace and Path(workspace).is_dir():
        python = options.get("python") or verify.python_for(state.get("project_workspace") or workspace)
        framework = verify.detect_framework(workspace, python=python)
    return framework.name if framework else None


def requested(state) -> list[str]:
    """The Go test names the user asked for that the plan must declare (see the module docstring)."""
    if test_cases.design_only(state) or test_cases.diagnosis_cases(state):
        return []
    names = named(cues.scan_texts(state))
    if not names or proof_framework(state) != "go":
        return []
    workspace = state.get("workspace")
    existing = go_identifiers(workspace) if workspace and Path(workspace).is_dir() else set()
    return [name for name in names if name not in existing]


def _binds(declared: str | None, name: str) -> bool:
    """Whether the proof matches a test named ``name`` to a criterion declaring ``declared``; a declared
    Go subtest binds its own test function."""
    if not declared:
        return False
    function = declared.split("/", 1)[0]
    return bool(test_cases.match_cases([{"id": "case", "test_name": function}], [name], framework="go")["case"])


def problems(body, names: list[str]) -> list[str]:
    """Why ``body`` does not prove the requested Go tests ``names`` by name, one entry per fault; [] when it does."""
    if not names:
        return []
    errors, declared, reported = [], [], set()
    for row in (body.get("acceptance_criteria") if isinstance(body, dict) else None) or []:
        found = test_cases.mark(row.get("verification_method")) if isinstance(row, dict) else None
        if not found or not row.get("id"):
            continue
        rest = str(row["verification_method"]).strip()[len(found):].strip()
        name = test_cases.declared_test_name(rest)
        declared.append(name)
        mentioned = [wanted for wanted in names
                     if re.search(rf"(?<![A-Za-z0-9_]){re.escape(wanted)}(?![A-Za-z0-9_])", rest)]
        if mentioned and not any(_binds(name, wanted) for wanted in mentioned):
            errors.append(f"{row['id']} declares {name or 'no test name'} but refers to {', '.join(mentioned)} "
                          f"(write \"{found} {mentioned[0]}\")")
            reported.update(mentioned)
    unbound = [wanted for wanted in names
               if wanted not in reported and not any(_binds(name, wanted) for name in declared)]
    if unbound:
        errors.append(f"no criterion declares {', '.join(unbound)}")
    return errors


def check(state, body) -> None:
    """Refuse a contract body that does not declare the Go tests the user asked for."""
    errors = problems(body, requested(state))
    if errors:
        raise ValueError(
            "The plan does not prove the Go tests the user asked for by name: " + "; ".join(errors) + ". The runner "
            "proves a criterion only by the identifier right after test: or guard:; text saying one name maps to, "
            "resolves to or stands for another binds nothing. Put each requested name right there on the criterion "
            "it proves: test: for new or fixed behavior, guard: for behavior that must keep working")


def rule(names: list[str]) -> str:
    """The planning instruction naming the Go tests the runner will require, so a draft need not be sent back."""
    listed = ", ".join(names)
    return ("\nNATIVE TEST NAMES: the user asked for the Go tests " + listed + ". The runner proves a criterion only "
            "by the identifier right after test: or guard:, so on the criterion each one proves write that exact "
            f"name there (\"test: {names[0]}\" for new or fixed behavior, \"guard: {names[0]}\" for behavior that "
            "must keep working), keeping the user's spelling; an explanation may follow after \" — \". This replaces "
            "the test_<criterion id>_... name for those criteria only; other criteria keep it. Never declare another "
            "identifier (such as test_ac1_...) and say in prose that it maps to, resolves to or stands for a "
            "requested name: the runner does not read that text and refuses such a draft, whatever a review "
            "says.\n")
