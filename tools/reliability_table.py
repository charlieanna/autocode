"""Generate the dated reliability table from saved sweep rows (#694).

Fake-provider rows and live-model rows are never mixed (RELIABILITY.md). Each
row names the master commit the sweep ran on. The table is produced by a
command, never edited by hand.

    python tools/reliability_table.py docs/reliability-sweeps.json > docs/reliability-table.md
    python tools/reliability_table.py docs/reliability-sweeps.json --readme   # the README copy

A row may carry `interaction_timings` (first question, plan and Builder) and
`case_costs` (#721): per case, the money fields of that run's `autocode --status`
view, checked by the same rule as the view's own `cost` block. An unknown amount
is null and is shown as unknown, never as zero.
"""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal
from pathlib import Path
from statistics import median

try:
    from . import autocode_cost as cost
    from . import autocode_interaction_timing as interaction_timing
except ImportError:
    import autocode_cost as cost
    import autocode_interaction_timing as interaction_timing

# One sweep: a dated, named campaign on one profile and one master commit.
COLUMNS = (
    "date",
    "profile",
    "master",
    "prompts_hash",
    "mode",
    "runs",
    "passed",
    "false_completions",
    "median_minutes",
    "note",
    "interaction_timings",
    "case_costs",
)

# The money fields a case cost sample copies from the status view's `cost` block, in table order.
CASE_COST_MONEY = ("spent_usd", "estimated_usd", "reported_usd", "cap_usd")


def _case_costs(samples, runs) -> list[dict]:
    """Per-case cost samples, each amount checked as the status view's `cost` block is (#721)."""
    if not isinstance(samples, list):
        raise ValueError("case_costs must be a list of per-case cost samples")
    if len(samples) > runs:
        raise ValueError("case_costs lists more cases than the sweep's runs")
    checked = []
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get("case"), str) or not sample["case"].strip():
            raise ValueError("a case cost sample must name its case")
        run = sample.get("run")
        if run is not None and (not isinstance(run, str) or not run.strip()):
            raise ValueError("a case cost sample's run must name the run or be null")
        missing = [key for key in CASE_COST_MONEY if key not in sample]
        if missing:
            raise ValueError(f"a case cost sample must copy {missing} from the status view (null when unknown)")
        checked.append({"case": sample["case"], "run": run, **cost.check(sample)})
    return checked


def load_rows(path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError("reliability sweeps must be a JSON list of rows")
    rows = []
    for row in data:
        missing = [
            key
            for key in ("date", "profile", "master", "mode", "runs", "passed", "false_completions", "note")
            if key not in row
        ]
        if missing:
            raise ValueError(f"sweep row is missing {missing}: {row}")
        if row["mode"] not in ("live", "fake"):
            raise ValueError(f"mode must be live or fake, not {row['mode']!r}")
        if row["false_completions"] < 0 or row["runs"] < 0 or row["passed"] < 0:
            raise ValueError(f"counts must be non-negative: {row}")
        if row["passed"] + row["false_completions"] > row["runs"]:
            raise ValueError(f"passed + false_completions exceed runs: {row}")
        prompts_hash = row.get("prompts_hash")
        if prompts_hash is not None and (
            not isinstance(prompts_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", prompts_hash)
        ):
            raise ValueError("prompts_hash must be a SHA-256 digest or unknown")
        samples = row.get("interaction_timings")
        if samples is not None:
            if not isinstance(samples, list):
                raise ValueError("interaction_timings must be a list of public timing samples")
            for sample in samples:
                if not isinstance(sample, dict) or not isinstance(sample.get("run"), str) or not sample["run"].strip():
                    raise ValueError("an interaction timing sample must name its run")
                launch = sample.get("launched_at")
                if interaction_timing.elapsed(launch, launch) != 0:
                    raise ValueError("an interaction timing sample needs an aware launch timestamp")
                projected = interaction_timing.project({"interaction_timing": sample})
                for event in interaction_timing.EVENTS:
                    at, seconds = f"first_{event}_at", f"first_{event}_seconds"
                    duration = sample.get(seconds)
                    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))):
                        raise ValueError(f"{event} elapsed seconds must be numeric or unknown")
                    if at not in sample or seconds not in sample or sample[seconds] != projected[seconds]:
                        raise ValueError(f"{event} timing must preserve the public timestamp and elapsed seconds")
                    if sample[at] is not None and projected[seconds] is None:
                        raise ValueError(f"{event} timestamp must be aware and no earlier than launch")
        if row.get("case_costs") is not None:
            row = {**row, "case_costs": _case_costs(row["case_costs"], row["runs"])}
        rows.append({key: row.get(key) for key in COLUMNS})
    return rows


def _cell(text) -> str:
    return str(text).replace("|", "\\|")


def _amount(value) -> str:
    """A copied USD amount as written, without exponent notation. Null is unknown, never zero."""
    return "unknown" if value is None else format(Decimal(repr(value)), "f")


def _median_spent(row) -> str:
    """The sweep's median known spend: blank when no case cost was recorded, unknown when none is known."""
    samples = row.get("case_costs")
    if not samples:
        return ""
    known = [sample["spent_usd"] for sample in samples if sample["spent_usd"] is not None]
    return _amount(round(median(known), 6)) if known else "unknown"


def markdown(rows: list[dict]) -> str:
    """A dated table per mode, live first, then fake. Never mixes the two."""
    lines = [
        "# Reliability table",
        "",
        "Generated by `python tools/reliability_table.py docs/reliability-sweeps.json`.",
        "Do not edit by hand. See [RELIABILITY.md](../RELIABILITY.md) for what these",
        "numbers do and do not claim. A row names the master commit the sweep ran on.",
        "",
    ]
    for mode, title in (("live", "Live models"), ("fake", "Fake provider")):
        subset = [row for row in rows if row["mode"] == mode]
        if not subset:
            continue
        lines += [
            f"## {title}",
            "",
            "| Date | Profile | Master | Prompts (SHA-256) | Runs | Passed | False completions | Median (min) "
            "| Median spent (USD) | First question (s) | First plan (s) | First Builder (s) | Note |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for row in subset:
            completion_median = "" if row.get("median_minutes") is None else str(row["median_minutes"])
            first = []
            for event in interaction_timing.EVENTS:
                known = [
                    sample[f"first_{event}_seconds"]
                    for sample in row.get("interaction_timings") or []
                    if sample.get(f"first_{event}_seconds") is not None
                ]
                first.append("" if not known else str(median(known)))
            lines.append(
                f"| {row['date']} | {row['profile']} | `{row['master']}` | "
                f"{row.get('prompts_hash') or 'not recorded'} | {row['runs']} | {row['passed']} | {row['false_completions']} | "
                f"{completion_median} | {_median_spent(row)} | {' | '.join(first)} | {row['note']} |"
            )
        lines.append("")
        samples = [(row, sample) for row in subset for sample in row.get("interaction_timings") or []]
        if samples:
            lines += [
                "### First interaction samples",
                "",
                "Times start at CLI main entry before workspace setup. They include human wait; Builder is job dispatch, not first model token. Medians above use recorded values only. Blank cells mean unrecorded or unreached.",
                "",
                "| Profile | Master | Run | Launched at | First question shown at | First plan shown at | First Builder dispatched at |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for row, sample in samples:
                events = []
                for event in interaction_timing.EVENTS:
                    at, seconds = sample[f"first_{event}_at"], sample[f"first_{event}_seconds"]
                    events.append("" if at is None else f"{at} ({seconds}s)")
                lines.append(
                    f"| {row['profile']} | `{row['master']}` | {sample['run']} | {sample['launched_at']} | {' | '.join(events)} |"
                )
            lines.append("")
        costs = [(row, sample) for row in subset for sample in row.get("case_costs") or []]
        if costs:
            lines += [
                "### Cost per case",
                "",
                "Each amount is copied from that case's `autocode --status` JSON view (`cost`) and checked by the same rule. Unknown means the run could not price it and is never shown as zero. Spent is the known spend the cap is compared with: a floor when a stage had no rate. The median spent above uses known values only.",
                "",
                "| Profile | Master | Case | Run | Spent USD | Estimated USD | Reported USD | Cap USD |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
            ]
            for row, sample in costs:
                amounts = [_amount(sample[key]) for key in CASE_COST_MONEY[:-1]]
                cap = "no cap" if sample["cap_usd"] is None else _amount(sample["cap_usd"])
                run = "not recorded" if sample["run"] is None else _cell(sample["run"])
                lines.append(
                    f"| {row['profile']} | `{row['master']}` | {_cell(sample['case'])} | {run} | {' | '.join(amounts)} | {cap} |"
                )
            lines.append("")
    return "\n".join(lines)


def readme_excerpt(rows: list[dict]) -> str:
    """The short copy for the README: live rows only, one line each."""
    lines = [
        "",
        "## Reliability",
        "",
        "AutoCode does not report completion without evidence. Live sweeps on the",
        "three-case portfolio (small new application, feature in an existing project,",
        "bug fix) are recorded in [docs/reliability-table.md](docs/reliability-table.md)",
        "and [RELIABILITY.md](RELIABILITY.md). Fake-provider rows are kept separate",
        "and never mixed with these. The table is generated, never edited by hand:",
        "",
        "```sh",
        "python tools/reliability_table.py docs/reliability-sweeps.json",
        "```",
        "",
        "| Date | Profile | Runs | Passed | False completions |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        if row["mode"] != "live":
            continue
        lines.append(
            f"| {row['date']} | {row['profile']} | {row['runs']} | {row['passed']} | {row['false_completions']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sweeps", help="JSON list of sweep rows")
    parser.add_argument("--readme", action="store_true", help="print the README excerpt instead")
    args = parser.parse_args(argv)
    rows = load_rows(args.sweeps)
    print(readme_excerpt(rows) if args.readme else markdown(rows), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
