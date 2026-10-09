"""Summarize AutoCode against a plain-agent baseline on the same scenarios.

Both sides are scored by the same oracle on what they delivered ("deliverable":
the oracle with no run record, as in ``check`` mode, so AutoCode-only process
checks such as workflow recognition do not count against the baseline). Each
side also gets a verdict, which adds whether it claimed completion. Pure
functions over the per-scenario rows that scenarios/run.py records.
"""

from __future__ import annotations

from statistics import median

from . import verdict

SIDES = ("autocode", "baseline")


def outcome(row: dict) -> str:
    """Which side delivered work the oracle accepts."""
    a, b = (row[side]["deliverable_passed"] for side in SIDES)
    return {
        (True, True): "both",
        (True, False): "autocode only",
        (False, True): "baseline only",
        (False, False): "neither",
    }[(a, b)]


def summarize(rows: list[dict]) -> dict:
    compared = [row for row in rows if not row.get("skipped")]
    result = {"scenarios": len(rows), "compared": len(compared), "skipped": len(rows) - len(compared), "outcomes": {}}
    for row in compared:
        result["outcomes"][outcome(row)] = result["outcomes"].get(outcome(row), 0) + 1
    for side in SIDES:
        cells = [row[side] for row in compared]
        seconds = [cell["seconds"] for cell in cells if cell.get("seconds") is not None]
        verdicts = {}
        for cell in cells:
            verdicts[cell["verdict"]] = verdicts.get(cell["verdict"], 0) + 1
        result[side] = {
            "deliverable_passed": sum(cell["deliverable_passed"] for cell in cells),
            "verdicts": verdicts,
            "false_completions": verdicts.get(verdict.FALSE_COMPLETE, 0),
            "total_seconds": round(sum(seconds), 1),
            "median_seconds": round(median(seconds), 1) if seconds else None,
        }
    return result


def markdown(meta: dict, rows: list[dict], summary: dict) -> str:
    a, b = summary["autocode"], summary["baseline"]
    n = summary["compared"]
    lines = [
        f"# AutoCode vs {meta['baseline']}",
        "",
        f"Mode: {meta['mode']}. AutoCode {meta['autocode']['commit'][:12]}"
        + (" (dirty)" if meta["autocode"].get("dirty") else "")
        + f". Started {meta['started_at']}.",
        "",
        "Both sides get the same seed and brief; the same oracle judges what each delivered.",
        "",
        "| | AutoCode | Baseline |",
        "| --- | ---: | ---: |",
        f"| Deliverable accepted by the oracle | {a['deliverable_passed']}/{n} | {b['deliverable_passed']}/{n} |",
        f"| False completions (claimed done, oracle disagrees) | {a['false_completions']} | {b['false_completions']} |",
        f"| Median seconds per scenario | {a['median_seconds']} | {b['median_seconds']} |",
        f"| Total seconds | {a['total_seconds']} | {b['total_seconds']} |",
        "",
        "Outcomes: "
        + (", ".join(f"{count} {name}" for name, count in sorted(summary["outcomes"].items())) or "none")
        + (f"; {summary['skipped']} skipped" if summary["skipped"] else "")
        + ".",
        "",
        "| Scenario | AutoCode | Baseline | AutoCode s | Baseline s | AutoCode stages | Deliverable |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        if row.get("skipped"):
            lines.append(f"| {row['scenario']} | skipped: {row['skipped']} | | | | | |")
            continue
        auto, base = row["autocode"], row["baseline"]
        lines.append(
            f"| {row['scenario']} | {auto['verdict']} | {base['verdict']} | {auto['seconds']} "
            f"| {base['seconds']} | {auto.get('model_stages', '')} | {outcome(row)} |"
        )
    lines += [
        "",
        "Verdicts: PASS, FALSE_COMPLETE (claimed done; the oracle found failures, or it should have "
        "stopped), HONEST_BLOCKER (stopped without claiming done), ERROR. A baseline agent claims done by "
        "exiting 0. Per-scenario evidence: " + meta["out"],
    ]
    return "\n".join(lines) + "\n"
