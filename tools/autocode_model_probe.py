"""Run the Requirements probe on the models the person chose (#3630 part 2).

Each request runs as an ordinary spec-first AutoCode task on a fresh copy of the bundled project
(``fixtures/requirements_probe``), with only the Requirements route set to the model and level
being measured. A spec-first run stops as soon as Requirements has answered: with its own
question, with the understanding it asks the person to confirm, or with the answer to a question
about the code. Nothing is planned or built. The probe then reads that stop through the public
task-run interface (``autocode_taskrun.TaskRun`` and its status view, AGENTS.md rule 5) and grades
it with ``autocode_requirements_probe.grade``:

- the reply is the run's last assistant chat message, and the open questions are the run's
  questions to the person, without the spec-first confirmation AutoCode itself adds;
- the job, the clarity and the handoff come from ``view.requirements``;
- the time, tool calls, tokens and report repairs come from the Requirements attempts in
  ``view.usage.accounting``, and the cost from ``view.run_metrics``.

Each run gets the probe's remaining money as its own ``--max-cost-usd``, so the probe never spends
past the cap the person approved. A model runs the three quick requests first, and the other six
only when it passes them (#3630 step 3). The results are recorded with
``autocode_measured_routes.record``, where selection and the quota fallback read them.

This layer starts task runs and reads their status; it imports nothing from the runner.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

try:
    from . import autocode_builder_probe as builder_probe
    from . import autocode_check_probe as check_probe
    from . import autocode_measured_routes as measured_routes
    from . import autocode_plan_reviewer_probe as reviewer_probe
    from . import autocode_planner_probe as planner_probe
    from . import autocode_prices as prices
    from . import autocode_requirements_probe as requirements_probe
    from . import autocode_taskrun as taskrun
    from . import autocode_verifier_independence as independence
    from . import probe_script_provider as probe_script
except ImportError:
    import autocode_builder_probe as builder_probe
    import autocode_check_probe as check_probe
    import autocode_measured_routes as measured_routes
    import autocode_plan_reviewer_probe as reviewer_probe
    import autocode_planner_probe as planner_probe
    import autocode_prices as prices
    import autocode_requirements_probe as requirements_probe
    import autocode_taskrun as taskrun
    import autocode_verifier_independence as independence
    import probe_script_provider as probe_script

FIXTURE = Path(__file__).with_name("fixtures") / "requirements_probe"
STAGE = "requirements_gather"
# Questions AutoCode itself adds, not the model: the spec-first confirmation after a clear handoff
# (autocode_requirements_review.PREFIX), left out entirely, and the requested-outcome
# question the runner asks when a report names no outcome (autocode_requirements_chat.OUTCOME_PREFIX),
# which stops the run for the person but is not the model's wording. tests.test_model_probe keeps
# both equal to the runner's.
REVIEW_QUESTION_PREFIX = "requirements-review-"
RUNNER_OUTCOME_PREFIX = "outcome-"
# What one request took of the measured stage's own model, used only to estimate a probe's cost
# before it runs: one Requirements reply on 2026-11-15, and one Planner draft with its revision on
# 2026-11-16. The run's own cap still binds what it spends.
ESTIMATE_TOKENS = {"input": 40_000, "output": 4_000}
# A Plan Reviewer request is two reviews (the plan as written and with its planted flaw), each about
# a third of a Planner request's tokens on 2026-11-16.
ESTIMATE_TOKENS_BY_STAGE = {
    "requirements": ESTIMATE_TOKENS,
    "planner": {"input": 120_000, "output": 12_000},
    "plan_reviewer": {"input": 80_000, "output": 8_000},
    # A Builder works with tools: it reads, edits and runs the tests, so its input is several times a
    # Planner draft's. An estimate before any Builder probe ran; the cap binds what it spends.
    "builder": {"input": 300_000, "output": 15_000},
    # A Tester runs the tests and reads the change, and checks a flawed request's change twice (the
    # flawed build, then the sound one); a Completion Reviewer reads more and runs less. Estimates
    # before any such probe ran; every other stage of these runs is scripted and costs nothing.
    "tester": {"input": 250_000, "output": 12_000},
    "completion": {"input": 200_000, "output": 10_000},
}
RUN_TIMEOUT_SECONDS = 1800
_RAN = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)
# The stages a probe measures: the role its results are recorded under, its requests and quick pass.
STAGES = {
    "requirements": requirements_probe,
    "planner": planner_probe,
    "plan_reviewer": reviewer_probe,
    "builder": builder_probe,
    "tester": check_probe,
    "completion": check_probe,
}
# The routes a Builder probe pins, so neither the measured Builder nor its checkers move to a stronger
# model mid-run (`--pin-model-role`; autocode_builder_policy escalates only an unpinned Builder).
BUILDER_PINNED_ROLES = ("builder", "tester", "completion")
TEST_TIMEOUT_SECONDS = 300
# The stages measured on scripted runs (``autocode_check_probe``), and the routes the script plays in
# them: every route but the one measured. Each is set on the bundled ``probe_script`` tool by
# ``--role-transport`` and pinned, so neither it nor the measured route moves mid-run.
CHECK_STAGES = ("tester", "completion")
SCRIPT_TOOL = "probe_script"
SCRIPT_ROUTES = ("requirements", "planner", "plan_reviewer", "task_planner", "builder", "tester", "completion")
SCRIPT_COMMAND = "autocode-probe-script"
# The route a Plan Reviewer candidate is set on in the run that wrote the plan (`--stage-model`).
REVIEWER_ROUTE = "plan_reviewer"


def estimate(models, stage="requirements") -> dict:
    """What measuring ``models`` (``[(model, level), ...]``) for ``stage`` can cost, before anything runs.

    ``calls`` is the most requests the probe runs: every request for every model. ``quick_calls`` is
    the first pass alone. ``max_usd`` prices each request's use of the measured model at
    ``ESTIMATE_TOKENS_BY_STAGE`` from the dated price table, None when a model has no rate. A Planner
    request also runs Requirements and the Plan Reviewer, which the estimate leaves out and the cap
    still covers.
    """
    probe = STAGES[stage]
    tokens = ESTIMATE_TOKENS_BY_STAGE[stage]
    rows = []
    for model, level in models:
        rate = prices.rate(model)
        per_reply = (tokens["input"] * rate["input"] + tokens["output"] * rate["output"]) / 1_000_000 if rate else None
        total = len(probe.REQUESTS)
        rows.append(
            {
                "model": model,
                "effort": level,
                "quick_calls": len(probe.QUICK),
                "calls": total,
                "max_usd": round(per_reply * total, 2) if per_reply is not None else None,
            }
        )
    priced = [row["max_usd"] for row in rows]
    return {
        "stage": stage,
        "models": rows,
        "quick_calls": sum(row["quick_calls"] for row in rows),
        "calls": sum(row["calls"] for row in rows),
        "max_usd": round(sum(priced), 2) if rows and None not in priced else None,
        "basis": (
            f"each request priced at {tokens['input']:,} input and {tokens['output']:,} output tokens of the "
            "measured model at the dated price table"
            + ("; Requirements and the Plan Reviewer are extra" if stage == "planner" else "")
            + (
                "; Requirements, the Planner, the Plan Reviewer, the Tester and the Completion Reviewer are extra"
                if stage == "builder"
                else ""
            )
            + ("; each request's plan, written once for every model, is extra" if stage == "plan_reviewer" else "")
            + (
                "; every other stage of the probe's runs is a script that calls no model"
                if stage in CHECK_STAGES
                else ""
            )
            + "; the cap you set still limits what the probe spends"
        ),
    }


def prepare_workspace(parent, *, fixed_tax=False) -> Path:
    """A fresh Git checkout of the bundled project under ``parent``, with its tax bug fixed when asked."""
    workspace = Path(parent) / "project"
    shutil.copytree(FIXTURE, workspace, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if fixed_tax:
        totals = workspace / "totals.py"
        broken, fixed = planner_probe.FIXED_TAX
        text = totals.read_text(encoding="utf-8")
        if broken not in text:
            raise RuntimeError("the bundled project no longer has the tax bug the Planner probe fixes")
        totals.write_text(text.replace(broken, fixed), encoding="utf-8")
    (workspace / ".gitignore").write_text(".autocode/\n__pycache__/\n", encoding="utf-8")
    identity = ["-c", "user.name=AutoCode probe", "-c", "user.email=probe@autocode.invalid"]
    for command in (["init", "-q"], ["add", "-A"], [*identity, "commit", "-qm", "probe project"]):
        subprocess.run(["git", "-C", str(workspace), *command], check=True, capture_output=True)
    return workspace


def report_from_view(view) -> tuple[dict | None, dict]:
    """The Requirements answer a stopped spec-first run shows, and what it cost.

    Returns ``(report, measurements)``. ``report`` is None when the run never produced an accepted
    Requirements report; ``measurements`` holds the keyword arguments ``grade`` takes.
    """
    attempts = [
        row
        for row in (((view.get("usage") or {}).get("accounting") or {}).get("attempts") or [])
        if isinstance(row, dict) and row.get("stage") == STAGE
    ]
    measurements = {
        "seconds": _total(row.get("duration_seconds") for row in attempts),
        "tool_calls": _total(((row.get("tool_activity") or {}).get("observed_calls") for row in attempts), int),
        "tokens": _total(
            (
                ((row.get("tokens") or {}).get("input_tokens") or 0)
                + ((row.get("tokens") or {}).get("output_tokens") or 0)
                for row in attempts
            ),
            int,
        ),
        "cost_usd": _requirements_cost(view),
        "report_repairs": max(0, len(attempts) - 1) if attempts else None,
    }
    handoff = view.get("requirements")
    reply = _last_assistant_text(view)
    if not isinstance(handoff, dict) and not reply:
        return None, measurements
    handoff = handoff if isinstance(handoff, dict) else {}
    needs = view.get("needs") or {}
    questions = [
        {
            "id": q.get("id"),
            "question": q.get("question"),
            "runner": str(q.get("id") or "").startswith(RUNNER_OUTCOME_PREFIX),
        }
        for q in (needs.get("questions") or [] if needs.get("kind") == "answer" else [])
        if isinstance(q, dict) and not str(q.get("id") or "").startswith(REVIEW_QUESTION_PREFIX)
    ]
    workflow = view.get("workflow")
    report = {
        "job": handoff.get("job") or (workflow if isinstance(workflow, str) else None),
        "clarity": handoff.get("clarity"),
        "assistant_reply": reply,
        "open_questions": questions,
        "required_behaviors": [row.get("text") for row in handoff.get("requirements") or [] if isinstance(row, dict)],
        "acceptance_tests": list(handoff.get("acceptance_tests") or []),
    }
    return report, measurements


def run_request(model, effort, request, *, options=(), env=None, command=taskrun.AUTOCODE, cap_usd=None):
    """Run one probe request on ``model`` at ``effort`` and grade where Requirements stopped."""
    request = requirements_probe.BY_ID[request] if isinstance(request, str) else request
    flags = ["--spec-first", "--joint-planning", "--requirements-model", model]
    if effort:
        flags += ["--requirements-reasoning-effort", effort]
    if cap_usd is not None:
        flags += ["--max-cost-usd", f"{max(cap_usd, 0.01):.2f}"]
    with tempfile.TemporaryDirectory(prefix="autocode-probe-") as parent:
        workspace = prepare_workspace(parent)
        error = None
        try:
            run = taskrun.TaskRun.start(
                workspace,
                request.text,
                options=(*options, *flags),
                command=command,
                env=env,
                timeout=RUN_TIMEOUT_SECONDS,
            )
            view = run.advance_until_input()
        except taskrun.TaskRunError as failure:
            error = str(failure)
            view = _status_after_failure(failure, workspace, options, flags, command, env)
        report, measurements = report_from_view(view or {})
    graded = requirements_probe.grade(request, report, **measurements)
    graded["run_status"] = (view or {}).get("status")
    graded["error"] = error
    return graded


# Stages of a Planner run, from usage.accounting attempts (a report repair is "<stage>_report_repair").
PLANNER_STAGES = frozenset({"draft_plan", "revise_plan"})
REVIEW_STAGE = "review_plan"
REPAIR_SUFFIX = "_report_repair"
# Requirements questions a Planner probe answers with Requirements' own proposed default before it
# gives up on reaching the plan: the probe measures the Planner, not how Requirements asks.
REQUIREMENTS_ANSWER_ROUNDS = 5
DEFAULT_ANSWER = "Use your proposed default."


def checker_for(model, checkers):
    """The first of ``checkers`` (``[(model, level)]``) that may review ``model``'s plans, or None.

    The Plan Reviewer is the probe's measuring instrument, so it stays the same for every candidate
    it may check; a candidate of its own family gets the next one (verifier independence).
    """
    for checker in checkers:
        if not independence.shares(model, checker[0]):
            return checker
    return None


def builder_checker_for(model, checkers, planner):
    """The first of ``checkers`` that may check both ``planner``'s plans and ``model``'s work, or None.

    One checker reviews the plan and tests and completes the candidate's work, so it must share a
    family with neither (verifier independence); it stays the same for every candidate it may check.
    """
    for checker in checkers:
        if not independence.shares(model, checker[0]) and not independence.shares(planner[0], checker[0]):
            return checker
    return None


def plan_from_view(view) -> tuple[dict | None, dict]:
    """The plan a stopped Planner run shows at approval, and what the probe measured about the run.

    Returns ``(plan, measurements)``: ``plan`` is ``view.displayed_plan`` when the run waits for plan
    approval, else None; ``measurements`` holds the keyword arguments ``planner_probe.grade`` takes,
    except the wall time, which the caller measures.
    """
    attempts = [
        row
        for row in (((view.get("usage") or {}).get("accounting") or {}).get("attempts") or [])
        if isinstance(row, dict) and isinstance(row.get("stage"), str)
    ]
    planner = [row for row in attempts if row["stage"].removesuffix(REPAIR_SUFFIX) in PLANNER_STAGES]
    drafts = sum(1 for row in planner if row["stage"] == "draft_plan")
    repairs = sum(1 for row in planner if row["stage"].endswith(REPAIR_SUFFIX) or row.get("rejected"))
    reached = ((view.get("needs") or {}).get("kind")) == "approve_plan"
    plan = view.get("displayed_plan") if reached and isinstance(view.get("displayed_plan"), dict) else None
    return plan, {
        "reached_approval": reached,
        "review_rounds": sum(1 for row in attempts if row["stage"] == REVIEW_STAGE),
        "report_repairs": repairs + max(0, drafts - 1) if planner else None,
        "cost_usd": _job_cost(view, "Planner"),
    }


def run_planner_request(
    model,
    effort,
    request,
    *,
    checker,
    requirements=None,
    options=(),
    env=None,
    command=taskrun.AUTOCODE,
    cap_usd=None,
    clock=time.monotonic,
):
    """Run one Planner probe request on ``model`` at ``effort`` and grade the plan it shows.

    ``checker`` is the Plan Reviewer's ``(model, level)``, ``requirements`` the pinned Requirements
    route (None: the person's saved choice). The run is a spec-first task: Requirements' own
    confirmation is answered "plan" and its questions with its proposed default, and the run stops
    at plan approval, which the probe never gives.
    """
    request = planner_probe.BY_ID[request] if isinstance(request, str) else request
    flags = _planner_flags((model, effort), checker, requirements, cap_usd)
    started = clock()
    with tempfile.TemporaryDirectory(prefix="autocode-probe-") as parent:
        workspace = prepare_workspace(parent, fixed_tax=not request.broken_tax)
        error = None
        try:
            run = taskrun.TaskRun.start(
                workspace,
                request.text,
                options=(*options, *flags),
                command=command,
                env=env,
                timeout=RUN_TIMEOUT_SECONDS,
            )
            view = _through_requirements(run, run.advance_until_input(), request.text)
        except taskrun.TaskRunError as failure:
            error = str(failure)
            view = _status_after_failure(failure, workspace, options, flags, command, env)
        plan, measurements = plan_from_view(view or {})
    graded = planner_probe.grade(request, plan, seconds=round(clock() - started, 1), **measurements)
    graded["run_cost_usd"] = _spent(view or {})
    graded["checker"] = list(checker)
    graded["run_status"] = (view or {}).get("status")
    graded["error"] = error
    return graded


def run_builder_request(
    model,
    effort,
    request,
    *,
    planner,
    checker,
    requirements=None,
    options=(),
    env=None,
    command=taskrun.AUTOCODE,
    cap_usd=None,
    clock=time.monotonic,
):
    """Run one Builder probe request to its end with ``model`` at ``effort`` building, and grade what it left.

    The run is a spec-first task on a fresh copy of the bundled project: Requirements' confirmation is
    answered "plan" and its questions with its proposed default, as in the Planner probe; the plan
    ``planner`` writes and ``checker`` reviews is approved by the probe on its exact token, since the
    person's approval of the probe and its cap covers this throwaway project and nothing else; the
    Builder, then ``checker`` as Tester and Completion Reviewer, run as in any task. When the run
    stops, the held-back tests and the project's own tests run on the files it left.
    """
    request = builder_probe.BY_ID[request] if isinstance(request, str) else request
    flags = _builder_flags((model, effort), planner, checker, requirements, cap_usd)
    with tempfile.TemporaryDirectory(prefix="autocode-probe-") as parent:
        workspace = prepare_workspace(parent, fixed_tax=not request.broken_tax)
        base = _git(workspace, "rev-parse", "HEAD").strip()
        error, view, built = None, None, workspace
        try:
            run = taskrun.TaskRun.start(
                workspace,
                request.text,
                options=(*options, *flags),
                command=command,
                env=env,
                timeout=RUN_TIMEOUT_SECONDS,
            )
            view = _through_requirements(run, run.advance_until_input(), request.text)
            needs = view.get("needs") or {}
            if needs.get("kind") == "approve_plan" and isinstance(needs.get("token"), str):
                run.approve_plan(needs["token"])
                view = run.advance_until_input()
            built = Path(getattr(run, "workspace", workspace))
        except taskrun.TaskRunError as failure:
            error = str(failure)
            view = _status_after_failure(failure, workspace, options, flags, command, env)
        view = view or {}
        held_back = run_tests(built, {"test_probe_held_back.py": request.tests}, parent)
        project = run_tests(built, None, parent)
        changed = changed_files(built, base)
        attempts = [
            row
            for row in (((view.get("usage") or {}).get("accounting") or {}).get("attempts") or [])
            if isinstance(row, dict) and str(row.get("stage") or "").removesuffix(REPAIR_SUFFIX) == "builder"
        ]
    graded = builder_probe.grade(
        request,
        run_status=view.get("status"),
        held_back=held_back,
        project_tests=project,
        changed_files=changed,
        builder_attempts=sum(1 for row in attempts if row.get("stage") == "builder") if attempts else 0,
        seconds=_total(row.get("duration_seconds") for row in attempts),
        cost_usd=_job_cost(view, "Builder"),
    )
    graded["run_cost_usd"] = _spent(view)
    graded["planner"] = list(planner)
    graded["checker"] = list(checker)
    graded["run_status"] = view.get("status")
    graded["error"] = error
    return graded


def run_check_request(
    model,
    effort,
    request,
    *,
    stage,
    options=(),
    env=None,
    command=taskrun.AUTOCODE,
    cap_usd=None,
    clock=time.monotonic,
):
    """Run one Tester or Completion Reviewer probe request to its end with ``model`` at ``effort`` in ``stage``.

    The run builds the greeting of ``autocode_check_probe`` in an empty project. Every route but the
    measured one is the bundled ``probe_script`` tool, which calls no model: it writes the plan, builds
    ``request``'s files (a flawed build first, when the request has a flaw) and passes the checks the
    probe does not measure. The probe approves the plan on its exact token, since the person's approval
    of the probe and its cap covers this throwaway project and nothing else. When the run stops, the
    plan's own tests run on the files it left, and the script's launch log shows which builds the
    measured stage sent back.
    """
    request = check_probe.BY_ID[request] if isinstance(request, str) else request
    measured = check_probe.MEASURED[stage]
    flags, start = _check_flags(stage, (model, effort), cap_usd)
    with tempfile.TemporaryDirectory(prefix="autocode-probe-") as parent:
        workspace = _empty_project(parent)
        log = Path(parent) / "launches.jsonl"
        script = Path(parent) / f"script-{uuid.uuid4().hex}.json"
        script.write_text(json.dumps({"log": str(log), "builds": request.builds()}), encoding="utf-8")
        run_env = {**(env or {}), **_script_env(parent, script, env)}
        error, view, built = None, None, workspace
        try:
            run = taskrun.TaskRun.start(
                workspace,
                check_probe.BRIEF,
                options=(*options, *flags),
                start_options=start,
                command=command,
                env=run_env,
                timeout=RUN_TIMEOUT_SECONDS,
            )
            view = run.status()
            needs = view.get("needs") or {}
            if needs.get("kind") == "approve_plan" and isinstance(needs.get("token"), str):
                run.approve_plan(needs["token"])
                view = run.advance_until_input()
            built = Path(getattr(run, "workspace", workspace))
        except taskrun.TaskRunError as failure:
            error = str(failure)
            view = _status_after_failure(failure, workspace, options, flags, command, run_env)
        view = view or {}
        result = run_tests(built, {"test_probe_plan.py": check_probe.ORACLE}, parent)
        launches = [json.loads(line) for line in log.read_text().splitlines() if line.strip()] if log.is_file() else []
        attempts = [
            row
            for row in (((view.get("usage") or {}).get("accounting") or {}).get("attempts") or [])
            if isinstance(row, dict) and str(row.get("stage") or "").removesuffix(REPAIR_SUFFIX) == measured["stage"]
        ]
    graded = check_probe.grade(
        request,
        stage,
        run_status=view.get("status"),
        launches=launches,
        result_tests=result,
        seconds=_total(row.get("duration_seconds") for row in attempts),
        cost_usd=_job_cost(view, "Tester" if stage == "tester" else "Completion Reviewer"),
    )
    graded["run_cost_usd"] = _spent(view)
    graded["run_status"] = view.get("status")
    graded["error"] = error
    return graded


def _check_flags(stage, measured, cap_usd):
    """A scripted run's flags (repeated on every advance) and its start-only tool bindings."""
    route = check_probe.MEASURED[stage]["route"]
    flags = ["--joint-planning", "--no-adaptive-planning", "--max-parallel-builders", "1"]
    start = []
    for role in SCRIPT_ROUTES:
        if role == route:
            continue
        flags += [f"--{role.replace('_', '-')}-model", probe_script.MODELS[role]]
        start += ["--role-transport", f"{role}={SCRIPT_TOOL}"]
    model, level = measured
    flags += [f"--{route}-model", model]
    if level:
        flags += [f"--{route}-reasoning-effort", level]
    for role in BUILDER_PINNED_ROLES + ("task_planner",):
        flags += ["--pin-model-role", role]
    if cap_usd is not None:
        flags += ["--max-cost-usd", f"{max(cap_usd, 0.01):.2f}"]
    return flags, start


def _empty_project(parent):
    """An empty Git project with one commit, where the run builds the greeting."""
    workspace = Path(parent) / "project"
    workspace.mkdir()
    (workspace / ".gitignore").write_text(".autocode/\n__pycache__/\n", encoding="utf-8")
    identity = ["-c", "user.name=AutoCode probe", "-c", "user.email=probe@autocode.invalid"]
    for git in (["init", "-q"], ["add", "-A"], [*identity, "commit", "-qm", "probe project"]):
        subprocess.run(["git", "-C", str(workspace), *git], check=True, capture_output=True)
    return workspace


def _script_env(parent, script, env):
    """``probe_script``'s command on PATH, running this Python on the bundled script, and the script it reads."""
    folder = Path(parent) / "bin"
    folder.mkdir()
    launcher = folder / SCRIPT_COMMAND
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{Path(probe_script.__file__).resolve()}" "$@"\n', encoding="utf-8"
    )
    launcher.chmod(0o755)
    path = (env or {}).get("PATH") or os.environ.get("PATH", "")
    return {"PATH": f"{folder}{os.pathsep}{path}", probe_script.SCRIPT_ENV: str(script)}


def run_tests(workspace, held_back, scratch):
    """Run the project's tests (``held_back`` None) or the held-back test files on ``workspace``.

    Returns ``{"ok", "ran", "output"}``: ``ok`` is unittest's own verdict, ``ran`` the number of tests it
    reports. Held-back files are written outside the project, so the run never saw them and they leave
    nothing behind; the project folder is the working directory and on the import path.
    """
    if held_back is None:
        argv = ["-m", "unittest", "discover", "-s", str(workspace), "-t", str(workspace), "-p", "test*.py"]
    else:
        folder = Path(tempfile.mkdtemp(prefix="held-back-", dir=scratch))
        for name, text in held_back.items():
            (folder / name).write_text(text, encoding="utf-8")
        argv = ["-m", "unittest", "discover", "-s", str(folder), "-t", str(folder), "-p", "test*.py"]
    environment = {**os.environ, "PYTHONPATH": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        result = subprocess.run(
            [sys.executable, *argv],
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        return {"ok": False, "ran": 0, "output": f"the tests did not finish: {failure}"}
    ran = _RAN.search(result.stderr)
    return {
        "ok": result.returncode == 0,
        "ran": int(ran.group(1)) if ran else 0,
        "output": (result.stdout + result.stderr)[-4000:],
    }


def changed_files(workspace, base):
    """The project's files that differ from ``base`` or are new, leaving AutoCode's own folder out."""
    tracked = _git(workspace, "diff", "--name-only", base).splitlines()
    untracked = _git(workspace, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({name for name in (*tracked, *untracked) if name and not name.startswith(".autocode/")})


def _git(workspace, *args):
    result = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, text=True, timeout=60)
    return result.stdout if result.returncode == 0 else ""


def _builder_flags(builder, planner, checker, requirements, cap_usd):
    """A spec-first run's flags: the measured Builder, the Planner, and one checker for the Plan Reviewer,
    the Tester and the Completion Reviewer, all pinned; Requirements and the cap as for the Planner probe."""
    flags = _planner_flags(planner, checker, requirements, cap_usd)
    for flag, (model, level) in (("--builder", builder), ("--tester", checker), ("--completion", checker)):
        flags += [f"{flag}-model", model]
        if level:
            flags += [f"{flag}-reasoning-effort", level]
    for role in BUILDER_PINNED_ROLES:
        flags += ["--pin-model-role", role]
    return flags


def run_reviewer_request(
    request,
    candidates,
    *,
    planner,
    requirements=None,
    options=(),
    env=None,
    command=taskrun.AUTOCODE,
    cap_usd=None,
    clock=time.monotonic,
    source=None,
):
    """One Plan Reviewer probe request: a plan to approval, then each candidate reviews it twice.

    ``planner`` writes the plan, checked in that run by the first candidate. Then, in the same run
    (a plan cites that run's own answers, so it cannot be moved to another), each candidate in turn is
    set as the Plan Reviewer and reviews the plan as written and with its flaw planted
    (``reviewer_probe.plant``). Each review is one advance under a planning call cap one above the
    calls made so far, so the run stops right after the review, before any revision, whatever the
    verdict. Returns ``{"request", "source", "grades": {(model, level): grade}, "spent_usd"}``;
    ``grades`` is empty when the plan never reached approval. ``source`` stands in for starting the
    run that writes the plan (``(workspace, request) -> (run, view)``) in tests.
    """
    request = reviewer_probe.BY_ID[request] if isinstance(request, str) else request
    flaw = reviewer_probe.BY_REQUEST[request.id]
    flags = _planner_flags(planner, candidates[0], requirements, cap_usd)
    grades = {}
    with tempfile.TemporaryDirectory(prefix="autocode-probe-") as parent:
        workspace = prepare_workspace(parent, fixed_tax=not request.broken_tax)
        error, run = None, None
        try:
            if source is not None:
                run, view = source(workspace, request)
            else:
                run = taskrun.TaskRun.start(
                    workspace,
                    request.text,
                    options=(*options, *flags),
                    command=command,
                    env=env,
                    timeout=RUN_TIMEOUT_SECONDS,
                )
                view = _through_requirements(run, run.advance_until_input(), request.text)
        except taskrun.TaskRunError as failure:
            error = str(failure)
            view = _status_after_failure(failure, workspace, options, flags, command, env) or {}
        reached = ((view.get("needs") or {}).get("kind")) == "approve_plan"
        source = {"reached_approval": reached, "run_status": view.get("status"), "error": error}
        if run is None or not reached:
            return {"request": request.id, "source": source, "grades": grades, "spent_usd": _spent(view)}
        exported = Path(parent) / "source-plan.md"
        run.export_plan(exported)
        front = _front_matter(exported.read_text(encoding="utf-8"))
        files = {
            "sound": _plan_file(parent, front, reviewer_probe.sound(front["contract"]), command, env),
            "flawed": _plan_file(parent, front, reviewer_probe.plant(front["contract"], flaw), command, env),
        }
        for model, level in candidates:
            reviews, seconds, cost, failure_text = {}, [], 0.0, None
            try:
                _use_reviewer(run, model, level)
                for variant in ("sound", "flawed"):
                    before = _spent(run.status())
                    run.load_plan_file(files[variant])
                    used = (run.status().get("planning_calls") or {}).get("used")
                    started = clock()
                    after_view = run.advance_with_planning_cap((used if type(used) is int else 0) + 1)
                    seconds.append(round(clock() - started, 1))
                    reviews[variant] = after_view.get("plan_review")
                    after = _spent(after_view)
                    cost = cost + after - before if cost is not None and None not in (before, after) else None
            except taskrun.TaskRunError as failure:
                failure_text = str(failure)
            graded = reviewer_probe.grade(
                request,
                reviews.get("sound"),
                reviews.get("flawed"),
                seconds=seconds,
                cost_usd=round(cost, 4) if cost is not None else None,
            )
            graded["planner"] = list(planner)
            graded["reviewed_by"] = sorted({row.get("model") for row in reviews.values() if isinstance(row, dict)})
            graded["review_stages"] = [(reviews.get(variant) or {}).get("stage") for variant in ("sound", "flawed")]
            graded["error"] = failure_text
            grades[(model, level)] = graded
        spent = _spent(run.status())
    return {"request": request.id, "source": source, "grades": grades, "spent_usd": spent}


def run_reviewer_probe(
    models,
    *,
    cap_usd,
    planner,
    requirements=None,
    options=(),
    env=None,
    command=taskrun.AUTOCODE,
    record=True,
    runner=None,
):
    """Measure each ``(model, level)`` as Plan Reviewer on ``planner``'s plans, within ``cap_usd``.

    Requests run one at a time, the quick ones first, and each request's plan is written once and
    reviewed by every candidate still in the probe; a candidate that fails a quick request reviews
    no more. A candidate of the Planner's family is not measured (verifier independence). A request
    whose plan never reached approval is listed in ``skipped``, and leaves every candidate short of
    the full set, so none qualifies on that run. ``runner`` stands in for ``run_reviewer_request`` in
    tests.
    """
    if not isinstance(cap_usd, (int, float)) or isinstance(cap_usd, bool) or cap_usd <= 0:
        raise ValueError("the probe needs a positive cost cap in USD")
    runner = runner or run_reviewer_request
    eligible = [(model, level) for model, level in models if not independence.shares(planner[0], model)]
    unmeasured = [
        {"model": model, "effort": level, "reason": "the same family as the Planner whose plans it would review"}
        for model, level in models
        if (model, level) not in eligible
    ]
    grades: dict = {key: [] for key in eligible}
    spent, unpriced, stopped, skipped = 0.0, 0, None, []
    for request in _order(reviewer_probe):
        active = [
            key for key in eligible if request.id in reviewer_probe.QUICK or reviewer_probe.passes_quick(grades[key])
        ]
        if not active:
            break
        remaining = cap_usd - spent
        if remaining <= 0:
            stopped = f"the probe reached its ${cap_usd:.2f} cap"
            break
        outcome = runner(
            request,
            active,
            planner=planner,
            requirements=requirements,
            options=options,
            env=env,
            command=command,
            cap_usd=remaining,
        )
        if isinstance(outcome.get("spent_usd"), (int, float)):
            spent += outcome["spent_usd"]
        else:
            unpriced += 1
        if not outcome["source"].get("reached_approval"):
            skipped.append({"request": request.id, **outcome["source"]})
        for key, graded in outcome["grades"].items():
            grades[key].append(graded)
    results = []
    for model, level in eligible:
        graded = grades[(model, level)]
        if stopped and len(graded) < len(reviewer_probe.REQUESTS) and reviewer_probe.passes_quick(graded):
            unmeasured.append({"model": model, "effort": level})  # cut off in the middle of its full pass
        elif graded:
            results.append(measured_routes.result(model, level, graded))
    if record and results:
        measured_routes.record("plan_reviewer", results)
    return {
        "stage": "plan_reviewer",
        "results": results,
        "ranking": measured_routes.rank(results, "plan_reviewer"),
        "spent_usd": round(spent, 4),
        "unpriced_replies": unpriced,
        "stopped": stopped,
        "unmeasured": unmeasured,
        "skipped": skipped,
    }


def run_probe(
    models,
    *,
    cap_usd,
    stage="requirements",
    checkers=(),
    requirements=None,
    planner=None,
    options=(),
    env=None,
    command=taskrun.AUTOCODE,
    record=True,
    runner=None,
):
    """Measure each ``(model, level)`` for ``stage`` in turn, within ``cap_usd``; record and return the results.

    ``runner`` stands in for ``run_request``, ``run_planner_request`` or ``run_builder_request`` in
    tests. A model that fails the quick pass is recorded with its quick grades only, so it does not
    qualify. When the money runs out the probe stops and names the models it did not measure; their
    earlier records, if any, are kept. A Planner or Builder candidate no checker may check is not
    measured, and the result says why. A Builder probe needs ``planner``, the Planner that plans every
    task, and ``checkers``, which review its plans and check the candidate's work.
    """
    if not isinstance(cap_usd, (int, float)) or isinstance(cap_usd, bool) or cap_usd <= 0:
        raise ValueError("the probe needs a positive cost cap in USD")
    probe = STAGES[stage]
    if stage == "planner" and not checkers:
        raise ValueError("the Planner probe needs at least one Plan Reviewer to check its plans")
    if stage == "builder" and (not checkers or not planner):
        raise ValueError("the Builder probe needs a Planner and at least one checker of another family")
    runner = runner or {
        "planner": run_planner_request,
        "builder": run_builder_request,
        "tester": run_check_request,
        "completion": run_check_request,
    }.get(stage, run_request)
    spent = 0.0
    unpriced = 0
    results = []
    stopped = None
    unmeasured = []
    for index, (model, level) in enumerate(models):
        if stopped:
            unmeasured.append({"model": model, "effort": level})
            continue
        extra = {}
        if stage == "planner":
            checker = checker_for(model, checkers)
            if checker is None:
                unmeasured.append({"model": model, "effort": level, "reason": "no Plan Reviewer of another family"})
                continue
            extra = {"checker": checker, "requirements": requirements}
        if stage == "builder":
            checker = builder_checker_for(model, checkers, planner)
            if checker is None:
                unmeasured.append(
                    {"model": model, "effort": level, "reason": "no checker of another family than it and the Planner"}
                )
                continue
            extra = {"checker": checker, "planner": planner, "requirements": requirements}
        if stage in CHECK_STAGES:
            extra = {"stage": stage}
        grades = []
        for request in _order(probe):
            if request.id not in probe.QUICK and not probe.passes_quick(grades):
                break
            remaining = cap_usd - spent
            if remaining <= 0:
                stopped = f"the probe reached its ${cap_usd:.2f} cap"
                break
            graded = runner(
                model, level, request, options=options, env=env, command=command, cap_usd=remaining, **extra
            )
            grades.append(graded)
            cost = graded.get("run_cost_usd", graded.get("cost_usd"))
            if isinstance(cost, (int, float)):
                spent += cost
            else:
                unpriced += 1
        if stopped and len(grades) < len(probe.REQUESTS) and probe.passes_quick(grades):
            # Cut off in the middle of its full pass: an incomplete measurement is not recorded.
            unmeasured.append({"model": model, "effort": level})
            continue
        if grades:
            results.append(measured_routes.result(model, level, grades))
        if stopped:
            unmeasured.extend({"model": m, "effort": e} for m, e in models[index + 1 :])
            break
    if record and results:
        measured_routes.record(stage, results)
    return {
        "stage": stage,
        "results": results,
        "ranking": measured_routes.rank(results, stage),
        "spent_usd": round(spent, 4),
        "unpriced_replies": unpriced,
        "stopped": stopped,
        "unmeasured": unmeasured,
    }


def _planner_flags(planner, checker, requirements, cap_usd):
    """A spec-first run's flags with the Planner, its Plan Reviewer, Requirements and the cap pinned."""
    flags = ["--spec-first", "--joint-planning", "--planner-model", planner[0]]
    if planner[1]:
        flags += ["--planner-reasoning-effort", planner[1]]
    flags += ["--plan-reviewer-model", checker[0]]
    if checker[1]:
        flags += ["--plan-reviewer-reasoning-effort", checker[1]]
    if requirements:
        flags += ["--requirements-model", requirements[0]]
        if requirements[1]:
            flags += ["--requirements-reasoning-effort", requirements[1]]
    if cap_usd is not None:
        flags += ["--max-cost-usd", f"{max(cap_usd, 0.01):.2f}"]
    return flags


def _front_matter(text):
    """A plan file's front matter (``autocode plan --export``): the JSON between its ``---`` lines."""
    if not text.startswith("---\n"):
        raise taskrun.TaskRunError("the exported plan file has no front matter")
    front = json.loads(text[4:].partition("\n---\n")[0])
    if not isinstance(front, dict) or not isinstance(front.get("contract"), dict):
        raise taskrun.TaskRunError("the exported plan file holds no plan")
    return front


def _plan_file(parent, front, contract, command, env):
    """Write ``contract`` as a plan file the run can load, with its text rendered by `autocode plan --render`.

    The file has a neutral name: the run records the path it loaded, and the reviewer could read it.
    """
    folder = Path(parent) / "plans"
    folder.mkdir(exist_ok=True)
    path = folder / f"plan-{uuid.uuid4().hex}.md"
    text = json.dumps({**front, "contract": contract}, indent=2, ensure_ascii=False)
    path.write_text(f"---\n{text}\n---\n", encoding="utf-8")
    rendered = subprocess.run(
        [*command, "plan", "--render", str(path)],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    if rendered.returncode != 0:
        raise taskrun.TaskRunError(f"plan --render exited {rendered.returncode}: {rendered.stderr.strip()[-500:]}")
    return path


def _use_reviewer(run, model, level):
    """Set the run's Plan Reviewer to ``model`` at ``level``, unless it already runs it."""
    route = (run.status().get("routes") or {}).get(REVIEWER_ROUTE) or {}
    if route.get("model") == model and (route.get("reasoning_effort") or None) == (level or None):
        return
    run.set_stage_model(REVIEWER_ROUTE, model, level or "default")


def _order(probe=requirements_probe):
    """The quick requests first, then the rest in their listed order."""
    quick = [probe.BY_ID[name] for name in probe.QUICK]
    return quick + [request for request in probe.REQUESTS if request.id not in probe.QUICK]


def _through_requirements(run, view, request_text):
    """Past Requirements to the Planner: confirm the understanding, and take Requirements' own defaults.

    The runner's own requested-outcome question (``RUNNER_OUTCOME_PREFIX``) is answered with the
    request itself, which is the outcome the person asked for.
    """
    for _round in range(REQUIREMENTS_ANSWER_ROUNDS):
        needs = view.get("needs") or {}
        questions = [q for q in needs.get("questions") or () if isinstance(q, dict) and q.get("id")]
        if needs.get("kind") != "answer" or not questions:
            return view
        review = [q for q in questions if str(q["id"]).startswith(REVIEW_QUESTION_PREFIX)]
        if review and len(review) == len(questions):
            run.answer(review[0]["id"], "plan")
        elif _last_stage(view).startswith(STAGE) and needs.get("resolver_scope") in (None, "clarification"):
            run.answer_many(
                {str(q["id"]): _default_answer(q, request_text) for q in questions},
                resolver_token=needs.get("resolver_token"),
            )
        else:
            return view  # the Planner or the Plan Reviewer asked: that stop is the Planner's to grade
        view = run.advance_until_input()
    return view


def _default_answer(question, request_text):
    if str(question["id"]).startswith(REVIEW_QUESTION_PREFIX):
        return "plan"
    if str(question["id"]).startswith(RUNNER_OUTCOME_PREFIX):
        return request_text
    for value in (question.get("proposed_default"), *(question.get("options") or ())[:1]):
        if isinstance(value, str) and value.strip():
            return value
    return DEFAULT_ANSWER


def _last_stage(view):
    attempts = ((view.get("usage") or {}).get("accounting") or {}).get("attempts") or []
    last = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    return str(last.get("stage") or "")


def _job_cost(view, job):
    for row in (view.get("run_metrics") or {}).get("by_job") or []:
        if isinstance(row, dict) and row.get("job") == job:
            value = row.get("spent_usd")
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return None


def _spent(view):
    value = (view.get("cost") or {}).get("spent_usd")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _status_after_failure(failure, workspace, options, flags, command, env):
    """The stopped run's status after a refused or failed start, or None when there is no run."""
    run_dir = getattr(failure, "run_dir", None)
    if run_dir is None:
        return None
    try:
        return taskrun.TaskRun(
            workspace,
            Path(run_dir),
            command=tuple(command),
            options=(*options, *flags),
            env=env,
            timeout=RUN_TIMEOUT_SECONDS,
        ).status()
    except taskrun.TaskRunError:
        return None


def _last_assistant_text(view):
    messages = ((view.get("chat") or {}).get("messages")) or []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant" and isinstance(message.get("text"), str):
            return message["text"]
    return ""


def _requirements_cost(view):
    metrics = view.get("run_metrics") or {}
    for row in metrics.get("by_job") or []:
        if isinstance(row, dict) and row.get("job") == "Requirements" and row.get("complete"):
            value = row.get("spent_usd")
            return float(value) if isinstance(value, (int, float)) else None
    return None


def _total(values, kind=float):
    numbers = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not numbers or any(isinstance(value, float) and math.isnan(value) for value in numbers):
        return None
    total = sum(numbers)
    return kind(total) if kind is int else round(float(total), 3)
