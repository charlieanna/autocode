#!/usr/bin/env python3
"""Per-step live token/cost sampler for a running AutoCode run.

Reads provider event logs (`*.jsonl`) under the run directory and extracts each
`step_finish` token block as a sample. Finer-grained than the per-stage rollup
in `score_autocode_run.py` — this is the stream you watch while a stage is live.

These are observed steps, not a complete run invoice. USD values use the
scorer's historical flat comparison rates; missing data stays unknown.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

try:
    from .token_cost import count_text, estimate_cost, known_sum, money, recorded_model, token_count
except ImportError:
    from token_cost import count_text, estimate_cost, known_sum, money, recorded_model, token_count
from typing import Any


def event_records(state: dict, run_dir: Path) -> dict:
    """Only recorded event paths inside this run; never scan copied evidence."""
    records: dict[Any, Any] = {}
    stages = list(state.get("stages") or [])
    if state.get("active_stage"):
        stages.append(state["active_stage"])
    for record in stages:
        path = record.get("events")
        if not isinstance(path, str) or not path:
            continue
        path = Path(path)
        path = path.resolve() if path.is_absolute() else (run_dir / path).resolve()
        if path.is_relative_to(run_dir.resolve()):
            records.setdefault(path, []).append(record)
    return records


def parse_step_samples(run_dir: Path, state: dict | None = None) -> list[dict]:
    """Last update of each (session, part) in the recorded stage event logs."""
    if state is None:
        state = json.loads((run_dir / "state.json").read_text())
    samples: list[dict] = []
    for jl, records in sorted(event_records(state, run_dir).items()):
        if not jl.is_file():
            continue
        stage_names = {r.get("stage") for r in records if r.get("stage")}
        stage = next(iter(stage_names)) if len(stage_names) == 1 else re.sub(r"-\d+$", "", jl.stem)
        parts = {}
        for line_no, line in enumerate(jl.read_text(errors="replace").splitlines(), 1):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            part = row.get("part") if isinstance(row.get("part"), dict) else row
            ptype = part.get("type") or row.get("type")
            if ptype not in ("step_finish", "step-finish"):
                continue
            session, ident = row.get("sessionID"), part.get("id")
            key = (
                (session, ident) if isinstance(session, str) and isinstance(ident, str) and ident else ("line", line_no)
            )
            parts[key] = (line_no, row, part)
        for step, (line_no, row, part) in enumerate(parts.values(), 1):
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            samples.append(
                {
                    "ts": row.get("timestamp"),
                    "log": str(jl.relative_to(run_dir.resolve())),
                    "stage": stage,
                    "step": step,
                    "line": line_no,
                    "reason": part.get("reason"),
                    "cost_reported": part.get("cost"),
                    "tokens": {
                        "input": token_count(tokens.get("input")),
                        "output": token_count(tokens.get("output")),
                        "reasoning": token_count(tokens.get("reasoning")),
                        "cache_read": token_count(cache.get("read")),
                        "cache_write": token_count(cache.get("write")),
                        "total": token_count(tokens.get("total")),
                    },
                }
            )
    return samples


def attach_cost(samples: list[dict], model_for_stage) -> list[dict]:
    for s in samples:
        model = model_for_stage(s.get("stage") or "", s.get("log"))
        # estimate_cost expects the provider token dict shape
        t = s.get("tokens") or {}
        flat = {
            "input": t.get("input"),
            "output": t.get("output"),
            "reasoning": t.get("reasoning"),
            "cache": {"read": t.get("cache_read"), "write": t.get("cache_write")},
        }
        s["model"] = model
        s["est_usd"] = estimate_cost(model, flat)
    return samples


def model_resolver(state: dict, run_dir: Path):
    records = event_records(state, run_dir)

    def resolve(stage: str, log: str | None = None) -> str:
        matches = (
            records.get((run_dir / log).resolve(), [])
            if log
            else [r for rows in records.values() for r in rows if r.get("stage") == stage]
        )
        models = {recorded_model(r) for r in matches}
        return next(iter(models)) if len(models) == 1 else ""

    return resolve


def summarize(samples: list[dict]) -> dict:
    def rollup(rows):
        return {
            "steps": len(rows),
            **{
                k: known_sum((r.get("tokens") or {}).get(k) for r in rows)
                for k in ("input", "output", "reasoning", "cache_read", "cache_write")
            },
            "est_usd": known_sum(r.get("est_usd") for r in rows),
            "known_est_usd": sum(r["est_usd"] for r in rows if r.get("est_usd") is not None),
            "unpriced_steps": sum(r.get("est_usd") is None for r in rows),
            "models": sorted({r.get("model") or "unknown" for r in rows}),
        }

    grouped: dict[str, list] = {}
    for sample in samples:
        grouped.setdefault(sample["stage"], []).append(sample)
    return {
        "scope": "observed_steps_only",
        "per_stage": {k: rollup(v) for k, v in grouped.items()},
        "total": rollup(samples),
    }


def render_stream(samples: list[dict]) -> str:
    lines = [
        "stage | step | reason | in | cache read | cache write | out | reason_tok | est_usd | model",
        "--- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---",
    ]
    for s in samples[-40:]:
        t = s.get("tokens") or {}
        lines.append(
            f"{s.get('stage')} | {s.get('step')} | {s.get('reason') or '—'} | {count_text(t.get('input'))} "
            f"| {count_text(t.get('cache_read'))} | {count_text(t.get('cache_write'))} "
            f"| {count_text(t.get('output'))} | {count_text(t.get('reasoning'))} "
            f"| {money(s.get('est_usd'))} | `{s.get('model') or 'unknown'}`"
        )
    return "\n".join(lines) + "\n"


def render_summary(summary: dict) -> str:
    lines = [
        "## Observed provider steps (partial coverage)",
        "",
        "| Stage | Steps | Input | Cache read | Cache write | Output | Reasoning | Est. USD | Models |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for stage, bag in summary["per_stage"].items():
        lines.append(
            f"| {stage} | {bag['steps']} | {count_text(bag['input'])} | {count_text(bag['cache_read'])} "
            f"| {count_text(bag['cache_write'])} | {count_text(bag['output'])} "
            f"| {count_text(bag['reasoning'])} | {money(bag['est_usd'])} | {', '.join(bag['models'])} |"
        )
    t = summary["total"]
    lines.append("")
    lines.append(
        f"**Observed:** {t['steps']} steps · estimate {money(t['est_usd'])}; "
        f"known subtotal {money(t['known_est_usd'])}, {t['unpriced_steps']} unpriced steps. "
        "Historical flat comparison rates, not current prices or actual billing. "
        "Unfinished calls and missing logs can consume additional tokens."
    )
    return "\n".join(lines) + "\n"


def sample_run(run_dir: Path) -> tuple[list[dict], dict]:
    state = json.loads((run_dir / "state.json").read_text())
    samples = attach_cost(parse_step_samples(run_dir, state), model_resolver(state, run_dir))
    return samples, summarize(samples)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live per-step token/cost sampler for an AutoCode run.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--follow", action="store_true", help="Poll and reprint as new steps land")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--out", type=Path, help="Write markdown (stream + rollup) here")
    parser.add_argument("--json-out", type=Path, help="Write full samples JSON here")
    args = parser.parse_args()
    run_dir = args.run_dir
    while True:
        samples, summary = sample_run(run_dir)
        text = render_stream(samples) + "\n" + render_summary(summary)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json.dumps({"samples": samples, "summary": summary}, indent=2))
        if not args.follow:
            print(text)
            break
        print(
            f"[sampler] steps={summary['total']['steps']} est_usd={summary['total']['est_usd']} "
            f"at {time.strftime('%H:%M:%S')}",
            flush=True,
        )
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
