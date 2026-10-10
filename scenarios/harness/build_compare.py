"""Paired full-run comparison of fixed and adaptive planning, using the catalog oracle.

Both arms use the original brief, fresh seed copies, one resolved model profile
and identical caps. Each pair is sequential; the first arm alternates across
cases and repetitions to balance warm-cache/time effects. Every scheduled
attempt is reported, including failures, skips and missing evidence. Fake runs
prove plumbing and gates, not model quality or dollar savings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import api_cost, profiles, stats, verdict

VARIANTS = {"fixed": ("--no-adaptive-planning",), "adaptive": ("--adaptive-planning",)}


def schedule(ids: list[str], repeats: int) -> list[dict]:
    if repeats < 1 or len(ids) != len(set(ids)):
        raise ValueError("positive repeats and unique scenario ids required")
    return [
        {"scenario": id_, "repeat": repeat, "order": list(VARIANTS)[:: 1 if (index + repeat) % 2 else -1]}
        for repeat in range(1, repeats + 1)
        for index, id_ in enumerate(ids)
    ]


def passed(record: dict) -> bool:
    return (
        record.get("verdict") == verdict.PASS
        and record.get("oracle_passed") is True
        and not record.get("harness_error")
    )


def _total(values):
    """An unfinished arm's missing measurement is never zero consumption."""
    return sum(values) if all(type(value) in (int, float) for value in values) else None


def report(root: Path, protocol: dict, records: list[dict]) -> dict:
    expected = [(pair["scenario"], pair["repeat"], variant) for pair in protocol["pairs"] for variant in pair["order"]]
    indexed = {(row["scenario"], row["repeat"], row["variant"]): row for row in records}
    if len(indexed) != len(records) or set(indexed) - set(expected):
        raise ValueError("duplicate or unscheduled comparison attempts")
    missing = [list(key) for key in expected if key not in indexed]
    rows = [indexed[key] for key in expected if key in indexed]
    summary = {}
    for variant in VARIANTS:
        arm = [row for row in rows if row["variant"] == variant]
        successes = sum(passed(row) for row in arm)
        costs = [row.get("api_cost") or {} for row in arm]
        priced = (
            not protocol["fake"]
            and bool(arm)
            and len(arm) == len(protocol["pairs"])
            and all(cost.get("complete") and cost.get("usd") is not None for cost in costs)
        )
        total = round(sum(cost["usd"] for cost in costs), 8) if priced else None
        summary[variant] = {
            "scheduled": len(protocol["pairs"]),
            "attempts": len(arm),
            "passes": successes,
            "verdicts": dict(Counter(row["verdict"] for row in arm)),
            "api_usd": total,
            "api_usd_per_pass": round(total / successes, 8) if total is not None and successes else None,
            "wall_seconds": _total([row.get("wall_seconds", 0) for row in arm]),
            "model_calls": _total([row.get("metrics", {}).get("model_stages", 0) for row in arm]),
            "report_repairs": _total([row.get("metrics", {}).get("report_repairs", 0) for row in arm]),
            "rejected_model_calls": _total([row.get("rejected_model_calls", 0) for row in arm]),
            "questions": sum(len(row.get("answers", [])) for row in arm),
        }
    result = {
        "protocol": protocol,
        "summary": summary,
        "records": rows,
        "missing": missing,
        "all_passed": not missing and all(passed(row) for row in rows) and bool(rows),
    }
    (root / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    text = [
        "# Complete-run planning comparison",
        "",
        f"Mode: {'fake (no cost/quality proof)' if protocol['fake'] else protocol['profile_name']}",
        "API dollars are estimates from the frozen rate card; failed attempts are included.",
        "",
        "| Mode | Passed / scheduled | Model calls | Repairs | Estimated API $ | $ / pass |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for variant, arm in summary.items():
        usd = f"{arm['api_usd']:.6f}" if arm["api_usd"] is not None else "unknown"
        per_pass = f"{arm['api_usd_per_pass']:.6f}" if arm["api_usd_per_pass"] is not None else "unknown"
        text.append(
            f"| {variant} | {arm['passes']} / {arm['scheduled']} | {arm['model_calls']} | {arm['report_repairs']} | {usd} | {per_pass} |"
        )
    text += [
        "",
        "| Scenario | Repeat | Mode | Verdict | Oracle | Model calls |",
        "| --- | ---: | --- | --- | --- | ---: |",
    ]
    for row in rows:
        checks = row.get("checks", [])
        text.append(
            f"| {row['scenario']} | {row['repeat']} | {row['variant']} | {row['verdict']} | "
            f"{sum(bool(check['ok']) for check in checks)}/{len(checks)} | {row.get('metrics', {}).get('model_stages', 0)} |"
        )
    if missing:
        text += ["", f"Missing scheduled attempts: {missing}"]
    (root / "comparison.md").write_text("\n".join(text) + "\n")
    return result


def rebuild(root: Path) -> dict:
    protocol = json.loads((root / "protocol.json").read_text())
    records = [json.loads(path.read_text()) for path in sorted(root.glob("pair-*/attempt-*.json"))]
    indexed = {(row["scenario"], row["repeat"], row["variant"]) for row in records}
    # The scenario admission precedes its CLI call and the outer arm receipt.
    # Recover that admission for reporting only; rebuilding never launches work.
    for pair in sorted(root.glob("pair-*")):
        for row in stats.load_results(pair):
            key = (row.get("scenario"), row.get("repeat"), row.get("variant"))
            if key not in indexed and key[1] is not None and key[2] in VARIANTS:
                records.append(row)
                indexed.add(key)
    return report(root, protocol, records)


def prepare(scenarios, args, root: Path, *, revision: dict) -> dict:
    """Freeze a reviewable protocol without launching AutoCode or a provider."""
    if args.jobs < 1:
        raise ValueError("positive jobs required")
    card = json.loads(args.rate_card.read_text()) if args.rate_card else None
    profile = profiles.resolve(args.profile) if not args.fake else None
    if not args.fake and (card is None or profile.get("passthrough")):
        raise ValueError("live comparisons need a frozen rate card and explicit model profile")
    pairs = schedule([scenario.id for scenario in scenarios], args.repeats)
    protocol = {
        "revision": revision,
        "fake": args.fake,
        "profile_name": args.profile,
        "profile": profile,
        "rate_card": card,
        "pairs": pairs,
        "jobs": args.jobs,
        "autocode": args.autocode,
        "fake_solution": args.fake_solution,
        "caps": {
            key: getattr(args, key, None)
            for key in ("max_steps", "timeout_minutes", "max_seconds", "max_stage_seconds", "max_iterations")
        },
        "brief_sha256": {scenario.id: hashlib.sha256(scenario.brief.encode()).hexdigest() for scenario in scenarios},
        "briefs": {scenario.id: scenario.brief for scenario in scenarios},
        "seed_sha256": {
            scenario.id: {
                str(path.relative_to(scenario.seed)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(scenario.seed.rglob("*"))
                if path.is_file()
            }
            for scenario in scenarios
        },
        "scenario_budgets": {
            scenario.id: {
                "max_steps": args.max_steps or scenario.max_steps,
                "timeout_minutes": args.timeout_minutes or scenario.timeout_minutes,
            }
            for scenario in scenarios
        },
    }
    (root / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    return protocol


def run(scenarios, args, root: Path, *, run_one, revision: dict) -> dict:
    protocol = prepare(scenarios, args, root, revision=revision)
    pairs, card = protocol["pairs"], protocol["rate_card"]
    records = []
    by_id = {scenario.id: scenario for scenario in scenarios}

    def run_pair(pair):
        out = root / f"pair-{pair['scenario']}-{pair['repeat']}"
        out.mkdir()
        results = []
        for variant in pair["order"]:
            options = argparse.Namespace(
                **{**vars(args), "out": out, "attempt_context": {"repeat": pair["repeat"], "variant": variant}}
            )
            result = run_one(by_id[pair["scenario"]], options, extra_flags=VARIANTS[variant])
            result.update(repeat=pair["repeat"], variant=variant)
            if result["verdict"] == verdict.INTERRUPTED_UNGRADED:
                result["rejected_model_calls"] = None
                result["api_cost"] = {"usd": None, "complete": False, "issues": ["interrupted usage is unknown"]}
            else:
                state_path = Path(result["evidence"]) / "state.json"
                state = json.loads(state_path.read_text()) if state_path.is_file() else {}
                result["rejected_model_calls"] = sum(
                    bool(stage.get("rejected")) for stage in state.get("stages", []) if not stage.get("runner_owned")
                )
                if card is not None and not args.fake:
                    result["api_cost"] = api_cost.estimate(state, card)
            (out / f"attempt-{variant}.json").write_text(json.dumps(result, indent=2) + "\n")
            print(
                f"{pair['scenario']} repeat {pair['repeat']} {variant}: {result['verdict']} — {result['summary']}",
                flush=True,
            )
            results.append(result)
        return results

    # Reports are updated after each pair; an interrupted arm remains in attempt-*.json for rebuild.
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_pair, pair) for pair in pairs]
        for future in as_completed(futures):
            records.extend(future.result())
            report(root, protocol, records)
    return report(root, protocol, records)
