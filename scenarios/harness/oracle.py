"""Helpers for scenario oracles.

An oracle judges the delivered project from the outside: it runs the project's
commands, runs hidden tests against a scratch copy, and reads files. It never
imports AutoCode and never trusts the run's own reports or the model's tests.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

IGNORED = shutil.ignore_patterns(".git", ".autocode", "__pycache__", "*.pyc")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def run(cmd: list[str], cwd: Path, *, timeout: int = 120, input: str | None = None,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """Capture command failures: timeout -1, missing binary 127, cannot execute 126.

    ``env`` is None for every existing caller, so children keep inheriting
    this process's environment unchanged; acceptance phases pass the declared
    phase environment built by ``harness.phase_env``.
    """
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, input=input, env=env)
    except subprocess.TimeoutExpired as error:
        out = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        return subprocess.CompletedProcess(cmd, -1, out, f"TIMEOUT after {timeout}s")
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(cmd, 127, "", str(error))
    except OSError as error:
        return subprocess.CompletedProcess(cmd, 126, "", str(error))


def tail(proc: subprocess.CompletedProcess, limit: int = 600) -> str:
    return f"exit {proc.returncode}: " + (proc.stdout + proc.stderr).strip()[-limit:]


@contextmanager
def scratch_copy(project: Path):
    """A throwaway copy of the delivered project, so checks cannot alter it."""
    with tempfile.TemporaryDirectory(prefix="oracle-") as tmp:
        target = Path(tmp) / "project"
        shutil.copytree(project, target, ignore=IGNORED)
        yield target


def python_tests(cwd: Path, start: str = "tests", timeout: int = 300,
                 env: dict | None = None) -> subprocess.CompletedProcess:
    return run([sys.executable, "-m", "unittest", "discover", "-s", start, "-t", "."],
               cwd, timeout=timeout, env=env)


def hidden_tests(copy: Path, hidden: Path, timeout: int = 300,
                 env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the scenario's hidden unittest files from the root of a scratch copy."""
    target = copy / "_oracle_hidden"
    shutil.copytree(hidden, target, ignore=IGNORED)
    (target / "__init__.py").touch()
    return python_tests(copy, start="_oracle_hidden", timeout=timeout, env=env)


def test_names(root: Path) -> set[str]:
    """Names of test functions defined under ``root``."""
    names = set()
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        names.update(node.name for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef) and node.name.startswith("test"))
    return names


def non_stdlib_imports(project: Path) -> list[str]:
    """Imports that are neither standard library nor part of the project."""
    # Top-level modules and directories (namespace packages included) are the project's own code.
    local = {path.stem for path in project.glob("*.py")} | {path.name for path in project.iterdir() if path.is_dir()}
    foreign = []
    for path in sorted(project.rglob("*.py")):
        if {".git", ".autocode", "_oracle_hidden"} & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as error:
            foreign.append(f"{path.relative_to(project)}: syntax error {error}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            else:
                continue
            for name in (name.split(".")[0] for name in names):
                if name not in sys.stdlib_module_names and name not in local:
                    foreign.append(f"{path.relative_to(project)}: imports {name}")
    return foreign


def changed_paths(project: Path) -> list[str]:
    """Paths the run added, changed or deleted, relative to the committed seed."""
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], project)
    paths = (line[3:].strip() for line in status.stdout.splitlines() if line.strip())
    # AutoCode's own run state lives in the workspace; it is not a change to the project.
    return sorted(path for path in paths if not path.startswith(".autocode/") and "__pycache__" not in path)


def only_changed_under(project: Path, *allowed: str) -> Check:
    """A read-only job may leave only its report behind. ``allowed`` are path prefixes."""
    stray = [path for path in changed_paths(project) if not path.startswith(allowed)]
    return Check("workspace_unchanged_except_report", not stray, f"also changed: {stray}" if stray else "")


def load_json(path: Path) -> tuple[object, str]:
    """(parsed value, error); the value is None when the file is missing or invalid."""
    try:
        return json.loads(path.read_text()), ""
    except (OSError, ValueError) as error:
        return None, str(error)


def apply_patch(copy: Path, patch: Path) -> subprocess.CompletedProcess:
    return run(["git", "apply", "--whitespace=nowarn", str(patch)], copy)


def mentions(text: object, *groups: tuple[str, ...]) -> bool:
    """True when the text contains a word from every group (case-insensitive)."""
    lowered = json.dumps(text).lower() if not isinstance(text, str) else text.lower()
    return all(any(word.lower() in lowered for word in group) for group in groups)


def finding_matches(finding: dict, *, file: str, lines: tuple[int, int] | None = None,
                    words: tuple[tuple[str, ...], ...] = ()) -> bool:
    """Does a review finding point at a planted defect? It must name the file and
    either overlap the planted line span or describe the defect in words."""
    if not isinstance(finding, dict) or finding.get("file") != file:
        return False
    span = finding.get("lines")
    if lines and isinstance(span, list) and len(span) == 2 and all(isinstance(n, int) for n in span):
        if span[0] <= lines[1] and lines[0] <= span[1]:
            return True
    return bool(words) and mentions(finding, *words)


def findings_of(report: object, severity: str) -> list[dict]:
    if not isinstance(report, dict) or not isinstance(report.get("findings"), list):
        return []
    return [f for f in report["findings"] if isinstance(f, dict) and f.get("severity") == severity]


# Stage names AutoCode saves today (AGENTS.md, "Names"); oracles use them only through the sets below.
REQUIREMENTS_STAGES = ("requirements_gather",)
PLAN_REVIEW_STAGES = ("astra_challenge", "glm_revise")
BUILD_STAGES = ("orchestrator", "astra_plan", "terra")


def run_checks(run: dict | None, *, workflow: str, no_build: bool = False, no_requirements: bool = False,
               no_plan_review: bool = False, plan_approved: bool = False, max_questions: int | None = None,
               max_model_stages: int | None = None) -> list[Check]:
    """Checks on how AutoCode worked, from the run record the harness passes to oracles.

    ``run`` is None in ``check`` mode (no AutoCode ran), and then there is nothing to
    judge. Otherwise it holds ``status``, the final status ``view`` (docs/task-run.md),
    ``stages`` (saved stage names in order), ``answers`` (questions the driver answered)
    and ``cli_calls`` (the kinds of CLI call the driver made).

    ``workflow`` is the kind of job the run should have recognized; the harness
    reads it from the status view's ``workflow`` field (README, "Workflows").
    """
    if run is None:
        return []
    checks = []
    view = run.get("view") or {}
    stages = run.get("stages") or []
    checks.append(Check("workflow_recognized", view.get("workflow") == workflow,
                        f"status view reports workflow={view.get('workflow')!r}, wanted {workflow!r}"))
    if no_build:
        built = [stage for stage in stages if stage in BUILD_STAGES]
        checks.append(Check("no_builder_dispatched", not built, f"build stages ran: {built}"))
        approved = "approve-plan" in run.get("cli_calls", [])
        checks.append(Check("no_build_plan_approval_requested", not approved,
                            "a build plan was put up for approval" if approved else ""))
    if no_requirements:
        gathered = [stage for stage in stages if stage in REQUIREMENTS_STAGES]
        checks.append(Check("no_requirements_gathering", not gathered, f"ran {gathered}"))
    if no_plan_review:
        reviewed = [stage for stage in stages if stage in PLAN_REVIEW_STAGES]
        checks.append(Check("no_plan_review_rounds", not reviewed, f"ran {reviewed}"))
    if plan_approved:
        # The plan was challenged by the Plan Reviewer and put to the user, who approved it.
        reviewed = [stage for stage in stages if stage in PLAN_REVIEW_STAGES]
        checks.append(Check("plan_reviewed", bool(reviewed), f"plan review stages: {reviewed}"))
        approved = "approve-plan" in run.get("cli_calls", [])
        checks.append(Check("plan_approved_by_user", approved, "" if approved else "no plan was put up for approval"))
    if max_questions is not None:
        asked = len(run.get("answers") or [])
        checks.append(Check("question_budget", asked <= max_questions, f"asked {asked}, allowed {max_questions}"))
    if max_model_stages is not None:
        model = run.get("model_stages")
        count = len(model) if model is not None else len([stage for stage in stages if stage != "orchestrator"])
        checks.append(Check("stage_budget", count <= max_model_stages, f"{count} model stages, allowed {max_model_stages}"))
    return checks


def python_change_checks(project: Path, scenario, package: str) -> list[Check]:
    """Standard checks for a change to an existing Python project with a seed and hidden tests.

    The project's tests pass, the hidden tests pass, no existing test was removed,
    only the standard library is used, and the delivered tests fail when run
    against the seed's version of ``package`` — so they actually cover the change.
    """
    checks = []
    with scratch_copy(project) as copy:
        suite = python_tests(copy)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    with scratch_copy(project) as copy:
        shutil.rmtree(copy / package, ignore_errors=True)
        shutil.copytree(scenario.seed / package, copy / package, ignore=IGNORED)
        against_seed = python_tests(copy)
        checks.append(Check("new_tests_fail_on_original_code", against_seed.returncode != 0,
                            "delivered tests fail on the original code" if against_seed.returncode
                            else "delivered tests still pass on the original code"))
    missing = sorted(test_names(scenario.seed / "tests") - test_names(project / "tests"))
    checks.append(Check("existing_tests_kept", not missing, f"removed: {missing}" if missing else ""))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks
