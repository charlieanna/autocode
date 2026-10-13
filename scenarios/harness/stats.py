"""Summarize saved scenario results: how often each ran, how often it passed, and its cost.

One passing run can be luck (issue #16), and a speed target needs a measured
baseline (issue #15), so this reads every ``result.json`` the runner saved and
reports, per scenario, mode and AutoCode revision, the run count, passes, the
current streak of consecutive passes, and median wall time and model stages.
Fake and live modes are always kept apart: fake passes prove the rules work, not
that real models do. Revisions are kept apart for the same reason: a pass
recorded against an older commit says nothing about the code in the checkout,
and a streak or median pooled across revisions describes no code that existed.

For scenarios whose oracle scores a diagnosis (issue #59) it also counts, apart from
the run verdicts, the runs whose diagnosis was exercised (``diagnosed``: the stage ran
on the planted failure) and how many of those were CORRECT, INCORRECT and UNSCORED. Those
verdicts come from word lists, so a live CORRECT is read by a person before it is cited.
"""

from __future__ import annotations

import contextlib
import json
import statistics
from pathlib import Path

from . import attempts
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
            results.append(result)
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
        )
        for row in rows
    ]
    widths = [max(len(line[column]) for line in lines) for column in range(len(header))]
    text = ["  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=False)).rstrip() for line in lines]
    return "\n".join(
        [
            text[0],
            "(one row per scenario, mode and AutoCode revision, `?` being a result saved before the revision was "
            "recorded; medians exclude pending and skipped attempts; wall time is only recorded since 2026-09-28)",
            *text[1:],
        ]
    )


def _count(diagnoses: list, *verdicts: str):
    return sum(found in verdicts for found in diagnoses) if diagnoses else None


def _model_stages(result: dict):
    metrics = result.get("metrics") or {}
    if "model_stages" in metrics:
        return metrics["model_stages"]
    names = metrics.get("model_stage_names")
    return len(names) if names is not None else None


def _median(values, scale: float = 1):
    present = [value / scale for value in values if isinstance(value, (int, float))]
    return round(statistics.median(present), 1) if present else None


def _show(value) -> str:
    return "-" if value is None else f"{value:g}"
