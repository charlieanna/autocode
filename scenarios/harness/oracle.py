"""Helpers for scenario oracles.

An oracle judges the delivered project from the outside: it runs the project's
commands, runs hidden tests against a scratch copy, and reads files. It never
imports AutoCode and never trusts the run's own reports or the model's tests.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# .fake-turns/ in a solution holds the scripted model's per-turn reports (fake_codex.turn_report): never
# part of a project, so it is never materialized or copied.
IGNORED = shutil.ignore_patterns(".git", ".autocode", "__pycache__", "*.pyc", ".fake-turns", "node_modules", ".vitest")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def run(
    cmd: list[str], cwd: Path, *, timeout: int = 120, input: str | None = None, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Capture command failures: timeout -1, missing binary 127, cannot execute 126.

    ``env`` is None for every existing caller, so children keep inheriting
    this process's environment unchanged; acceptance phases pass the declared
    phase environment built by ``harness.phase_env``.
    """
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=input, env=env)
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
        if (project / "node_modules").is_dir():
            (target / "node_modules").symlink_to((project / "node_modules").resolve(), target_is_directory=True)
        yield target


def python_tests(
    cwd: Path, start: str = "tests", timeout: int = 300, env: dict | None = None
) -> subprocess.CompletedProcess:
    return run([sys.executable, "-m", "unittest", "discover", "-s", start, "-t", "."], cwd, timeout=timeout, env=env)


def hidden_tests(copy: Path, hidden: Path, timeout: int = 300, env: dict | None = None) -> subprocess.CompletedProcess:
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
        names.update(
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test")
        )
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


def changed_since_seed(project: Path) -> list[str]:
    """Paths that differ from the seed commit (the repository's first), committed or not.

    ``changed_paths`` reads ``git status``, which is clean where the product was committed; this compares the
    working tree with the seed itself."""
    root = run(["git", "rev-list", "--max-parents=0", "HEAD"], project).stdout.split()
    if not root:
        return changed_paths(project)
    diff = run(["git", "diff", "--name-only", "--no-renames", root[-1], "--"], project).stdout.splitlines()
    new = run(["git", "ls-files", "--others", "--exclude-standard"], project).stdout.splitlines()
    return sorted(
        {path for path in [*diff, *new] if path and not path.startswith(".autocode/") and "__pycache__" not in path}
    )


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


def finding_matches(
    finding: dict, *, file: str, lines: tuple[int, int] | None = None, words: tuple[tuple[str, ...], ...] = ()
) -> bool:
    """Does a review finding point at a planted defect? It must name the file and
    either overlap the planted line span or describe the defect in words."""
    if not isinstance(finding, dict) or finding.get("file") != file:
        return False
    span = finding.get("lines")
    if (
        lines
        and isinstance(span, list)
        and len(span) == 2
        and all(isinstance(n, int) for n in span)
        and span[0] <= lines[1]
        and lines[0] <= span[1]
    ):
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
RUNNER_STAGES = ("orchestrator",)


def run_checks(
    run: dict | None,
    *,
    workflow: str,
    no_build: bool = False,
    after_requirements: str | None = None,
    no_plan_review: bool = False,
    plan_approved: bool = False,
    max_questions: int | None = None,
    max_model_stages: int | None = None,
) -> list[Check]:
    """Checks on how AutoCode worked, from the run record the harness passes to oracles.

    ``run`` is None in ``check`` mode (no AutoCode ran), and then there is nothing to
    judge. Otherwise it holds ``status``, the final status ``view`` (docs/task-run.md),
    ``stages`` (saved stage names in order), ``answers`` (questions the driver answered),
    ``approvals`` (the gates the driver passed for the person, as in result.json; a plan
    approval's row also holds ``plan_file``, harness.plan_round_trip) and ``cli_calls``
    (the kinds of CLI call the driver made; the round trip's `autocode plan` calls are not among them).

    ``workflow`` is the kind of job the run should have recognized; the harness
    reads it from the status view's ``workflow`` field (README, "Workflows").
    ``after_requirements`` is the job's own first stage: Requirements must be the first model
    stage, run once, and that stage must follow it (owner decision 2026-10-22, #1860).
    """
    if run is None:
        return []
    checks = []
    view = run.get("view") or {}
    stages = run.get("stages") or []
    checks.append(
        Check(
            "workflow_recognized",
            view.get("workflow") == workflow,
            f"status view reports workflow={view.get('workflow')!r}, wanted {workflow!r}",
        )
    )
    if no_build:
        built = [stage for stage in stages if stage in BUILD_STAGES]
        checks.append(Check("no_builder_dispatched", not built, f"build stages ran: {built}"))
        approved = "approve-plan" in run.get("cli_calls", [])
        checks.append(
            Check(
                "no_build_plan_approval_requested",
                not approved,
                "a build plan was put up for approval" if approved else "",
            )
        )
    if after_requirements:
        # Runner-owned records and report repairs are not the job's stages.
        model = [
            str(stage)
            for stage in (run.get("model_stages") if run.get("model_stages") is not None else stages)
            if stage not in RUNNER_STAGES and not str(stage).endswith("_report_repair")
        ]
        gathered = [stage for stage in model if stage in REQUIREMENTS_STAGES]
        checks.append(
            Check(
                "requirements_first",
                model[:1] == list(REQUIREMENTS_STAGES) and len(gathered) == 1,
                f"Requirements ran {len(gathered)} times; first stages: {model[:3]}",
            )
        )
        checks.append(
            Check(
                "job_starts_after_requirements",
                model[1:2] == [after_requirements],
                f"after Requirements came {model[1:2]}, wanted {after_requirements!r}",
            )
        )
    if no_plan_review:
        reviewed = [stage for stage in stages if stage in PLAN_REVIEW_STAGES]
        checks.append(Check("no_plan_review_rounds", not reviewed, f"ran {reviewed}"))
    if plan_approved:
        # The plan was challenged by the Plan Reviewer and put to the user, who approved it.
        reviewed = [stage for stage in stages if stage in PLAN_REVIEW_STAGES]
        checks.append(Check("plan_reviewed", bool(reviewed), f"plan review stages: {reviewed}"))
        approved = any(
            step.get("kind") == "approve-plan" and type(step.get("exit")) is int and step["exit"] == 0
            for step in run.get("steps", [])
        )
        checks.append(
            Check("plan_approved_by_user", approved, "" if approved else "no successful plan approval was recorded")
        )
    if max_questions is not None:
        asked = len(run.get("answers") or [])
        checks.append(Check("question_budget", asked <= max_questions, f"asked {asked}, allowed {max_questions}"))
    if max_model_stages is not None:
        model = run.get("model_stages")
        count = len(model) if model is not None else len([stage for stage in stages if stage != "orchestrator"])
        checks.append(
            Check("stage_budget", count <= max_model_stages, f"{count} model stages, allowed {max_model_stages}")
        )
    return checks


def escalation_checks(
    run: dict | None,
    *,
    kind: str,
    role: str,
    job: str,
    climbed: tuple[tuple[str, str], tuple[str, str]],
    attempts: int,
    outcome: str,
    basis: str,
    stamped: dict[str, int],
) -> list[Check]:
    """Checks on the one escalation a run made, from the final status view's ``escalations`` (docs/task-run.md:
    the rows ``autocode --status --json`` prints, #612): its ``kind``, ``role`` and screen ``job``, the routes it
    ``climbed`` from and to as (model, reasoning effort), its escalated ``attempts``, each finished and priced,
    their cost in all, and how it ended (``outcome``, ``basis``); then the runner's own stamps on it and on the
    Builder's retries, as the view's ``escalation_outcomes`` counts them (``stamped``), which must agree with that
    row. Empty in ``check`` mode (``run`` is None)."""
    if run is None:
        return []
    stamps = (run.get("view") or {}).get("escalation_outcomes")
    rows = (run.get("view") or {}).get("escalations")
    row = rows[0] if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) else {}
    sides = [row.get(side) if isinstance(row.get(side), dict) else {} for side in ("from", "to")]
    launched = [one for one in row.get("attempts") or [] if isinstance(one, dict)]
    spent = [one.get("cost_usd") for one in launched]
    cost = row.get("cost") if isinstance(row.get("cost"), dict) else {}
    priced = all(isinstance(usd, (int, float)) and usd > 0 for usd in spent)
    return [
        Check(
            "one_escalation",
            (row.get("kind"), row.get("role"), row.get("job")) == (kind, role, job),
            f"escalations: {rows}",
        ),
        Check(
            "escalation_routes",
            tuple((side.get("model"), side.get("reasoning_effort")) for side in sides) == climbed,
            f"from {sides[0]} to {sides[1]}",
        ),
        Check(
            "escalated_attempts_and_cost",
            len(launched) == attempts
            and all(one.get("active") is False for one in launched)
            and priced
            and cost.get("complete") is True
            and cost.get("usd") == round(sum(spent), 6),
            f"attempts {launched}; cost {cost}",
        ),
        Check(
            "escalation_outcome",
            (row.get("outcome"), row.get("outcome_basis")) == (outcome, basis),
            f"{row.get('outcome')!r} ({row.get('outcome_basis')!r}), wanted {outcome!r} ({basis!r})",
        ),
        Check("runner_stamps_agree", stamps == stamped, f"escalation_outcomes {stamps}, wanted {stamped}"),
    ]


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
        checks.append(
            Check(
                "new_tests_fail_on_original_code",
                against_seed.returncode != 0,
                "delivered tests fail on the original code"
                if against_seed.returncode
                else "delivered tests still pass on the original code",
            )
        )
    missing = sorted(test_names(scenario.seed / "tests") - test_names(project / "tests"))
    checks.append(Check("existing_tests_kept", not missing, f"removed: {missing}" if missing else ""))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks


def _native_change_checks(project: Path, scenario, package: str, runner: str) -> list[Check]:
    from .native_tests import execute

    checks = []
    with scratch_copy(project) as copy:
        suite = execute(copy, runner, run)
        checks.append(Check("project_tests_pass", suite.passed, tail(suite.process)))
        hidden = scenario.dir / "hidden"
        if runner == "go":
            shutil.copytree(hidden, copy, dirs_exist_ok=True, ignore=IGNORED)
        else:
            shutil.copytree(hidden, copy / "_oracle_hidden", ignore=IGNORED)
        hidden_suite = execute(copy, runner, run, hidden=True)
        checks.append(Check("hidden_tests_pass", hidden_suite.passed, tail(hidden_suite.process)))
    with scratch_copy(scenario.seed) as seed:
        if (project / "node_modules").is_dir() and not (seed / "node_modules").exists():
            (seed / "node_modules").symlink_to((project / "node_modules").resolve(), target_is_directory=True)
        original = execute(seed, runner, run)
    missing = sorted(original.names - suite.names)
    checks.append(Check("existing_tests_kept", not missing, f"removed or skipped: {missing}" if missing else ""))
    required = [match[1] for text in scenario.fake_criteria.values() if (match := re.search(r"test:\s*(\w+)", text))]
    observed = lambda name, rows: any(
        row == name or row.endswith("/" + name) or row.endswith(" " + name) for row in rows
    )
    missing_cases = [name for name in required if not observed(name, suite.names)]
    checks.append(
        Check("required_case_tests", not missing_cases, f"missing or skipped: {missing_cases}" if missing_cases else "")
    )
    with scratch_copy(project) as copy:
        target = copy / package
        if runner == "go":
            # Go tests live beside production files. Preserve delivered tests
            # while restoring only the original implementation.
            for source in target.rglob("*.go") if target.exists() else ():
                if not source.name.endswith("_test.go"):
                    source.unlink()
            for source in (scenario.seed / package).rglob("*.go"):
                if not source.name.endswith("_test.go"):
                    restored = target / source.relative_to(scenario.seed / package)
                    restored.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, restored)
        else:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            if (scenario.seed / package).is_dir():
                shutil.copytree(scenario.seed / package, target, ignore=IGNORED)
            elif (scenario.seed / package).is_file():
                shutil.copyfile(scenario.seed / package, target)
        if runner == "go":
            for name in ("go.mod", "go.sum"):
                (copy / name).unlink(missing_ok=True)
                if (scenario.seed / name).is_file():
                    shutil.copyfile(scenario.seed / name, copy / name)
        before = execute(copy, runner, run)
        # The first Go package has no runnable Go project in its committed base.
        # Production owns that no-project proof policy; current and hidden suites
        # above must still execute real cases, and run-mode checks require its receipt.
        first_go = (
            runner == "go"
            and not any((scenario.seed / name).exists() for name in ("go.mod", "go.work"))
            and not any(scenario.seed.rglob("*.go"))
        )
        demonstrated = (
            all(observed(name, before.failed - original.failed) for name in required)
            if required
            else bool(before.failed - original.failed)
        )
        no_project = (before.process.stdout + before.process.stderr).lower()
        demonstrated = demonstrated or (
            first_go
            and before.process.returncode == 1
            and any(
                message in no_project
                for message in (
                    "matched no packages",
                    "cannot find main module",
                    "does not contain main module",
                    "go.mod file not found",
                )
            )
        )
        checks.append(
            Check(
                "new_tests_fail_on_original_code",
                demonstrated,
                tail(before.process) if demonstrated else "no delivered regression fails on original source",
            )
        )
    return checks


def go_change_checks(project: Path, scenario, package: str) -> list[Check]:
    """Check native Go suites, hidden behavior, retained guards and original-source regression."""
    return _native_change_checks(project, scenario, package, "go")


def node_change_checks(project: Path, scenario, package: str, *, runner: str = "node") -> list[Check]:
    """The same independent checks for node:test or pinned TypeScript/Vitest."""
    if runner not in {"node", "vitest"}:
        raise ValueError("node_change_checks runner must be node or vitest")
    return _native_change_checks(project, scenario, package, runner)


def named_proof_checks(record: dict | None, scenario=None) -> list[Check]:
    """Require the CLI's public current-source named proof in run mode."""
    if record is None:
        return []
    view = record.get("view") or {}
    proof = (view.get("evidence") or {}).get("regression_proof") or {}
    cases = proof.get("case_tests") or {}
    expected = [
        match[1]
        for text in (scenario.fake_criteria.values() if scenario else ())
        if (match := re.search(r"test:\s*(\w+)", text))
    ]
    identities = (
        [name for names in cases.values() if isinstance(names, list) for name in names if isinstance(name, str)]
        if isinstance(cases, dict)
        else []
    )
    covered = all(
        any(name == test or name.endswith("::" + test) or name.endswith("/" + test) for name in identities)
        for test in expected
    )
    evidence = view.get("evidence") or {}
    source = proof.get("source_revision")
    current = (
        isinstance(source, str)
        and bool(source)
        and source == evidence.get("validator_source_revision")
        and source == (evidence.get("check_replay") or {}).get("source_revision")
    )
    concrete = (
        isinstance(cases, dict)
        and len(cases) >= 2
        and all(
            isinstance(names, list) and names and all(isinstance(name, str) and name for name in names)
            for names in cases.values()
        )
    )
    return [
        Check(
            "named_regression_proof",
            proof.get("verdict") == "PASS" and concrete and covered and current,
            f"verdict={proof.get('verdict')}, named cases={sorted(cases) if isinstance(cases, dict) else []}, "
            f"required tests covered={covered}, source current={current}",
        )
    ]
