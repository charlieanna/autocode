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

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    from . import autocode_measured_routes as measured_routes
    from . import autocode_prices as prices
    from . import autocode_requirements_probe as requirements_probe
    from . import autocode_taskrun as taskrun
except ImportError:
    import autocode_measured_routes as measured_routes
    import autocode_prices as prices
    import autocode_requirements_probe as requirements_probe
    import autocode_taskrun as taskrun

FIXTURE = Path(__file__).with_name("fixtures") / "requirements_probe"
STAGE = "requirements_gather"
# Questions AutoCode itself adds, not the model: the spec-first confirmation after a clear handoff
# (autocode_requirements_review.PREFIX), left out entirely, and the requested-outcome
# question the runner asks when a report names no outcome (autocode_requirements_chat.OUTCOME_PREFIX),
# which stops the run for the person but is not the model's wording. tests.test_model_probe keeps
# both equal to the runner's.
REVIEW_QUESTION_PREFIX = "requirements-review-"
RUNNER_OUTCOME_PREFIX = "outcome-"
# What one Requirements reply took on 2026-11-15 across the measured models, used only to estimate
# a probe's cost before it runs: the run's own cap still binds what it spends.
ESTIMATE_TOKENS = {"input": 40_000, "output": 4_000}
RUN_TIMEOUT_SECONDS = 1800


def estimate(models) -> dict:
    """What measuring ``models`` (``[(model, level), ...]``) can cost, before anything runs.

    ``calls`` is the most Requirements replies the probe makes: every request for every model.
    ``quick_calls`` is the first pass alone. ``max_usd`` prices each reply at ``ESTIMATE_TOKENS``
    from the dated price table, None when a model has no rate.
    """
    rows = []
    for model, level in models:
        rate = prices.rate(model)
        per_reply = (
            (ESTIMATE_TOKENS["input"] * rate["input"] + ESTIMATE_TOKENS["output"] * rate["output"]) / 1_000_000
            if rate
            else None
        )
        total = len(requirements_probe.REQUESTS)
        rows.append(
            {
                "model": model,
                "effort": level,
                "quick_calls": len(requirements_probe.QUICK),
                "calls": total,
                "max_usd": round(per_reply * total, 2) if per_reply is not None else None,
            }
        )
    priced = [row["max_usd"] for row in rows]
    return {
        "models": rows,
        "quick_calls": sum(row["quick_calls"] for row in rows),
        "calls": sum(row["calls"] for row in rows),
        "max_usd": round(sum(priced), 2) if rows and None not in priced else None,
        "basis": (
            f"each reply priced at {ESTIMATE_TOKENS['input']:,} input and {ESTIMATE_TOKENS['output']:,} output "
            "tokens at the dated price table; the cap you set still limits what the probe spends"
        ),
    }


def prepare_workspace(parent) -> Path:
    """A fresh Git checkout of the bundled project under ``parent``."""
    workspace = Path(parent) / "project"
    shutil.copytree(FIXTURE, workspace, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
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


def run_probe(models, *, cap_usd, options=(), env=None, command=taskrun.AUTOCODE, record=True, runner=None):
    """Measure each ``(model, level)`` in turn, within ``cap_usd``; record and return the results.

    ``runner`` stands in for ``run_request`` in tests. A model that fails the quick pass is recorded
    with its quick grades only, so it does not qualify. When the money runs out the probe stops and
    names the models it did not measure; their earlier records, if any, are kept.
    """
    if not isinstance(cap_usd, (int, float)) or isinstance(cap_usd, bool) or cap_usd <= 0:
        raise ValueError("the probe needs a positive cost cap in USD")
    runner = runner or run_request
    spent = 0.0
    unpriced = 0
    results = []
    stopped = None
    unmeasured = []
    for index, (model, level) in enumerate(models):
        if stopped:
            unmeasured.append({"model": model, "effort": level})
            continue
        grades = []
        for request in _order():
            if request.id not in requirements_probe.QUICK and not requirements_probe.passes_quick(grades):
                break
            remaining = cap_usd - spent
            if remaining <= 0:
                stopped = f"the probe reached its ${cap_usd:.2f} cap"
                break
            graded = runner(model, level, request, options=options, env=env, command=command, cap_usd=remaining)
            grades.append(graded)
            if isinstance(graded.get("cost_usd"), (int, float)):
                spent += graded["cost_usd"]
            else:
                unpriced += 1
        if stopped and len(grades) < len(requirements_probe.REQUESTS) and requirements_probe.passes_quick(grades):
            # Cut off in the middle of its full pass: an incomplete measurement is not recorded.
            unmeasured.append({"model": model, "effort": level})
            continue
        if grades:
            results.append(measured_routes.result(model, level, grades))
        if stopped:
            unmeasured.extend({"model": m, "effort": e} for m, e in models[index + 1 :])
            break
    if record and results:
        measured_routes.record("requirements", results)
    return {
        "results": results,
        "ranking": measured_routes.rank(results),
        "spent_usd": round(spent, 4),
        "unpriced_replies": unpriced,
        "stopped": stopped,
        "unmeasured": unmeasured,
    }


def _order():
    """The quick requests first, then the rest in their listed order."""
    quick = [requirements_probe.BY_ID[name] for name in requirements_probe.QUICK]
    return quick + [request for request in requirements_probe.REQUESTS if request.id not in requirements_probe.QUICK]


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
