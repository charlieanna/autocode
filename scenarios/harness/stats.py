"""Summarize saved scenario results: how often each ran, how often it passed, and its cost.

One passing run can be luck (issue #16), and a speed target needs a measured
baseline (issue #15), so this reads every ``result.json`` the runner saved and
reports, per scenario, mode and AutoCode revision, the run count, passes, the
current streak of consecutive passes, and median wall time and model stages.
Fake and live modes are always kept apart: fake passes prove the rules work, not
that real models do. Revisions are kept apart for the same reason: a pass
recorded against an older commit says nothing about the code in the checkout,
and a streak or median pooled across revisions describes no code that existed.

Its cost is the status view's ``cost`` block each result saves (#721): the median known spend
in USD (``spent_usd``: the provider's reported cost, else the stage's tokens at the dated price
table; a floor when some stage had no rate) and how many attempts' spend is unknown. Unknown is
never counted as zero: an attempt that priced nothing, was interrupted or is still pending, or a
result saved before the block was recorded, is left out of the median and counted as unknown.

For scenarios whose oracle scores a diagnosis (issue #59) it also counts, apart from
the run verdicts, the runs whose diagnosis was exercised (``diagnosed``: the stage ran
on the planted failure) and how many of those were CORRECT, INCORRECT and UNSCORED. Those
verdicts come from word lists, so a live CORRECT is read by a person before it is cited.

For the #455 campaign each row also carries the stop classes (``verdict.stop_class``) of its
attempts and the decisions the harness made for them (``decisions``): the person's own answers,
the defaults it served and the gates it approved, each apart, so an auto-served approval is
never read as zero-decision autonomy.
"""

from __future__ import annotations

import contextlib
import json
import math
import statistics
from pathlib import Path

from . import attempts, attribution
from .verdict import (
    CORRECT,
    INCORRECT,
    INTERRUPTED_UNGRADED,
    NOT_EXERCISED,
    PASS,
    PENDING_UNGRADED,
    STOP_CLASSES,
    UNSCORED,
    stop_class,
)

UNKNOWN_REVISION = "?"


def load_results(root: Path) -> list[dict]:
    """Every final result or admitted unfinished attempt, without launching work."""
    results = []
    for directory in sorted(Path(root).iterdir()) if Path(root).is_dir() else []:
        if directory.is_symlink() or not directory.is_dir():
            continue
        result = None
        with contextlib.suppress(OSError, ValueError):
            result = json.loads((directory / "result.json").read_text())
        if not (isinstance(result, dict) and result.get("scenario") and result.get("verdict")):
            result = attempts.unfinished(directory)
        if result is not None:
            results.append(attribution.project(directory, result))
    return results


def revision_label(recorded) -> str:
    """The label for a recorded AutoCode revision (``result["autocode"]``): the short commit, ``-dirty``
    when the checkout also carried uncommitted changes under ``tools/`` (then the commit is not the code
    that ran), or ``?`` when nothing was recorded."""
    commit = _commit(recorded)
    if not commit:
        return UNKNOWN_REVISION
    return commit[:8] + ("-dirty" if recorded.get("dirty") else "")


def matches_revision(recorded, wanted: str) -> bool:
    """Whether a recorded revision is the one a caller asked for, by commit prefix or by label."""
    return _commit(recorded).startswith(wanted) or revision_label(recorded).startswith(wanted)


def _commit(recorded) -> str:
    return str(recorded.get("commit") or "") if isinstance(recorded, dict) else ""


def summarize(
    results: list[dict], *, ids: set[str] = frozenset(), mode: str | None = None, revision: str | None = None
) -> list[dict]:
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for result in results:
        if (ids and result["scenario"] not in ids) or (mode and result.get("mode") != mode):
            continue
        recorded = result.get("autocode")
        if revision and not matches_revision(recorded, revision):
            continue
        groups.setdefault((result["scenario"], result.get("mode", "?"), revision_label(recorded)), []).append(result)
    rows = []
    for (scenario, run_mode, run_revision), runs in sorted(groups.items()):
        runs.sort(key=lambda result: result.get("started_at", ""))
        streak = 0
        for result in reversed(runs):
            if result["verdict"] != PASS:
                break
            streak += 1
        measured = [result for result in runs if result.get("verdict") not in ("SKIPPED", PENDING_UNGRADED)]
        diagnoses = [
            result["diagnosis"].get("verdict") for result in measured if isinstance(result.get("diagnosis"), dict)
        ]
        classes = [
            result.get("stop_class")
            or stop_class(
                result.get("verdict", ""),
                result.get("status") or result.get("runner_status") or "",
                result.get("summary") or "",
                pause_origin=result.get("pause_origin") or "",
            )
            for result in runs
        ]
        rows.append(
            {
                "scenario": scenario,
                "mode": run_mode,
                # Part of the row's identity, like the mode: see the module docstring.
                "revision": run_revision,
                "runs": len(measured),
                "attempts": len(runs),
                "passes": sum(result["verdict"] == PASS for result in measured),
                "streak": streak,
                "interrupted": sum(result["verdict"] == INTERRUPTED_UNGRADED for result in runs),
                "pending": sum(result["verdict"] == PENDING_UNGRADED for result in runs),
                "usage_unknown": sum(
                    result.get("usage_status") == "unknown"
                    or result["verdict"] in (INTERRUPTED_UNGRADED, PENDING_UNGRADED)
                    for result in runs
                ),
                # Runs that never reached the stage the scenario exists to test (issue #59).
                "not_exercised": sum(result["verdict"] == NOT_EXERCISED for result in measured),
                "last": runs[-1]["verdict"],
                # Why each attempt is not a pass (issue #455), beside the verdict.
                "stop_classes": {name: classes.count(name) for name in STOP_CLASSES if classes.count(name)},
                # The decisions the harness made across these attempts, apart from each other and from the
                # stops above (#455): a served default or an approval is the harness deciding, never
                # zero-decision autonomy; only a person's own answer ([fake] answers) is the person's.
                **decisions(runs),
                # Diagnosis verdicts, never mixed into the run verdicts above; None when no run was scored for one.
                "diagnosed": _count(diagnoses, CORRECT, INCORRECT, UNSCORED),
                "correct": _count(diagnoses, CORRECT),
                "incorrect": _count(diagnoses, INCORRECT),
                "unscored": _count(diagnoses, UNSCORED),
                "median_wall_minutes": _median([result.get("wall_seconds") for result in measured], scale=60),
                "median_model_stages": _median([_model_stages(result) for result in measured]),
                "median_model_minutes": _median(
                    [(result.get("metrics") or {}).get("model_seconds") for result in measured], scale=60
                ),
                # Spend (#721): the median of the known amounts only, and the attempts whose spend is unknown
                # (a skipped attempt spent nothing and is neither).
                "median_spent_usd": _median([spent_usd(result) for result in measured], digits=4),
                "cost_unknown": sum(spent_usd(result) is None for result in runs if result["verdict"] != "SKIPPED"),
            }
        )
    return rows


def format_table(rows: list[dict]) -> str:
    header = (
        "scenario",
        "mode",
        "revision",
        "runs",
        "passes",
        "streak",
        "not exercised",
        "interrupted",
        "pending",
        "usage unknown",
        "last",
        "wall min",
        "model stages",
        "model min",
        "diagnosed",
        "correct",
        "incorrect",
        "unscored",
        "spent usd",
        "cost unknown",
        "stop classes",
        "person answers",
        "default answers",
        "approvals",
    )
    lines = [header] + [
        (
            row["scenario"],
            row["mode"],
            row.get("revision", UNKNOWN_REVISION),
            str(row["runs"]),
            str(row["passes"]),
            str(row["streak"]),
            str(row["not_exercised"]),
            *(str(row.get(key, 0)) for key in ("interrupted", "pending", "usage_unknown")),
            row["last"],
            _show(row["median_wall_minutes"]),
            _show(row["median_model_stages"]),
            _show(row["median_model_minutes"]),
            *(_show(row.get(key)) for key in ("diagnosed", "correct", "incorrect", "unscored")),
            _show(row.get("median_spent_usd")),
            str(row.get("cost_unknown", 0)),
            format_stop_classes(row.get("stop_classes") or {}),
            *(str(row.get(key, 0)) for key in ("person_answers", "default_answers", "approvals")),
        )
        for row in rows
    ]
    widths = [max(len(line[column]) for line in lines) for column in range(len(header))]
    text = ["  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=False)).rstrip() for line in lines]
    return "\n".join(
        [
            text[0],
            "(one row per scenario, mode and AutoCode revision, `?` being a result saved before the revision was "
            "recorded; medians exclude pending and skipped attempts; wall time is only recorded since 2026-09-28; "
            "spent usd is the median known spend and cost unknown counts the attempts without one; "
            "stop classes count every attempt; person answers are the scenario's own [fake] answers, default "
            "answers and approvals are decisions the harness made for the run)",
            *text[1:],
        ]
    )


def format_stop_classes(counts: dict) -> str:
    """``false_completion=1 provider_failure=2`` in the documented order; ``-`` when nothing was classed."""
    return " ".join(f"{name}={counts[name]}" for name in STOP_CLASSES if counts.get(name)) or "-"


def decisions(results: list[dict]) -> dict:
    """How many decisions the harness made for these attempts, by who decided (Driver.serve records them).

    ``person_answers`` are the scenario's explicit ``[fake] answers`` (a person's own decision);
    ``default_answers`` are questions answered with AutoCode's proposed default or first concrete option;
    ``approvals`` are the gates the harness passed for the person: plan, human-review criteria, program
    agreements, the canned feedback that grants more planning review calls (``planning_budget``), a budget
    pause continued with the flags of the scenario's ``[fake] acknowledge`` (``acknowledge``), and the
    ``--auto-approve`` of a components run (one row per CLI call, the product answering its gates inside it).

    A result saved before ``source`` and ``approvals`` were recorded keeps the answers rows it has: one with
    ``explicit`` is the person's, one that is a bare ``approved`` is a review acceptance and counts as an
    approval, and any other is a default. Its plan approvals and program agreements were never recorded, so
    its approvals column is a lower bound; compare it only with rows of the same revision.
    """
    person = default = approvals = 0
    for result in results:
        for row in result.get("answers") or []:
            if not isinstance(row, dict):
                continue
            source = row.get("source")
            if source == "approval":
                continue  # also in "approvals", where it is counted
            if source == "person" or (source is None and row.get("explicit")):
                person += 1
            elif source is None and row.get("answer") == "approved":
                approvals += 1  # an old review acceptance: the answer was "approved" and no source was kept
            else:
                default += 1
        approvals += sum(isinstance(row, dict) for row in result.get("approvals") or [])
    return {"person_answers": person, "default_answers": default, "approvals": approvals}


def _count(diagnoses: list, *verdicts: str):
    return sum(found in verdicts for found in diagnoses) if diagnoses else None


def _model_stages(result: dict):
    metrics = result.get("metrics") or {}
    if "model_stages" in metrics:
        return metrics["model_stages"]
    names = metrics.get("model_stage_names")
    return len(names) if names is not None else None


def spent_usd(result: dict):
    """A result's known spend in USD (its ``cost.spent_usd``), or None when it is unknown or absent."""
    cost = result.get("cost")
    value = cost.get("spent_usd") if isinstance(cost, dict) else None
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _median(values, scale: float = 1, digits: int = 1):
    present = [value / scale for value in values if isinstance(value, (int, float))]
    return round(statistics.median(present), digits) if present else None


def _show(value) -> str:
    return "-" if value is None else f"{value:g}"
