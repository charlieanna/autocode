#!/usr/bin/env python3
"""Per-step live token/cost sampler for a running AutoCode run.

Reads provider event logs (`*.jsonl`) under the run directory and extracts each
`step_finish` token block as a sample. Finer-grained than the per-stage rollup
in `score_autocode_run.py` — this is the stream you watch while a stage is live.

Subscription note: OpenCode reports `cost: 0` on Z.AI Coding Plan and Xiaomi
Token Plan. We still emit an API-equivalent estimate so steps and stages are
comparable across models and runs.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from score_autocode_run import estimate_cost


def parse_step_samples(run_dir: Path) -> list[dict]:
    """One sample per provider step_finish across every stage log in the run."""
    samples: list[dict] = []
    for jl in sorted(run_dir.rglob("*.jsonl")):
        # keep only stage event logs (skip nested fixture state under evidence/)
        if "evidence" in jl.parts or "fixture-state" in jl.parts:
            continue
        stage = jl.name.split("-")[0] if "-" in jl.name else jl.stem
        # stem like requirements_gather-01.jsonl -> requirements_gather
        stem = jl.stem
        stage = stem.rsplit("-", 1)[0] if stem[-2:].isdigit() or stem[-3:-2] == "-" else stem
        step = 0
        for line_no, line in enumerate(jl.open(), 1):
            try:
                row = json.loads(line)
            except Exception:
                continue
            part = row.get("part") if isinstance(row.get("part"), dict) else row
            if not isinstance(part, dict):
                continue
            ptype = part.get("type") or row.get("type")
            if ptype not in ("step_finish", "step-finish"):
                continue
            tokens = part.get("tokens") or {}
            if not isinstance(tokens, dict):
                continue
            step += 1
            samples.append({
                "ts": time.time(),
                "log": str(jl.relative_to(run_dir)),
                "stage": stage,
                "step": step,
                "line": line_no,
                "reason": part.get("reason"),
                "cost_reported": part.get("cost"),
                "tokens": {
                    "input": tokens.get("input"),
                    "output": tokens.get("output"),
                    "reasoning": tokens.get("reasoning"),
                    "cache_read": (tokens.get("cache") or {}).get("read"),
                    "cache_write": (tokens.get("cache") or {}).get("write"),
                    "total": tokens.get("total"),
                },
            })
    return samples


def attach_cost(samples: list[dict], model_for_stage) -> list[dict]:
    for s in samples:
        model = model_for_stage(s.get("stage") or "")
        # estimate_cost expects the provider token dict shape
        t = s.get("tokens") or {}
        flat = {
            "input": t.get("input") or 0,
            "output": t.get("output") or 0,
            "reasoning": t.get("reasoning") or 0,
            "cache": {"read": t.get("cache_read") or 0, "write": t.get("cache_write") or 0},
        }
        s["model"] = model
        s["est_usd"] = estimate_cost(model, flat)
    return samples


def model_resolver(state: dict):
    roles = (state.get("settings") or {}).get("roles") or {}
    role_model = {k: (v or {}).get("model") for k, v in roles.items()}
    stage_role = {
        "requirements_gather": "requirements",
        "requirements_gather_report_repair": "requirements",
        "astra_discovery": "astra", "astra_challenge": "plan_reviewer",
        "glm_revise": "glm", "astra_finalize": "plan_reviewer",
        "terra": "terra", "sol": "sol", "astra_review": "astra",
        "astra_checkpoint": "astra", "astra_resolve": "resolver",
        "astra_plan": "astra",
    }

    def resolve(stage: str) -> str:
        return role_model.get(stage_role.get(stage, ""), "") or ""

    return resolve


def summarize(samples: list[dict]) -> dict:
    by_stage: dict[str, dict] = {}
    for s in samples:
        bag = by_stage.setdefault(s["stage"], {
            "steps": 0, "input": 0, "output": 0, "reasoning": 0,
            "cache_read": 0, "est_usd": 0.0, "model": s.get("model"),
        })
        bag["steps"] += 1
        t = s.get("tokens") or {}
        for k in ("input", "output", "reasoning", "cache_read"):
            bag[k] += t.get(k) or 0
        bag["est_usd"] = round(bag["est_usd"] + (s.get("est_usd") or 0.0), 4)
    total = {
        "steps": sum(b["steps"] for b in by_stage.values()),
        "input": sum(b["input"] for b in by_stage.values()),
        "output": sum(b["output"] for b in by_stage.values()),
        "reasoning": sum(b["reasoning"] for b in by_stage.values()),
        "cache_read": sum(b["cache_read"] for b in by_stage.values()),
        "est_usd": round(sum(b["est_usd"] for b in by_stage.values()), 4),
    }
    return {"per_stage": by_stage, "total": total}


def render_stream(samples: list[dict]) -> str:
    lines = ["stage | step | reason | in | cache | out | reason_tok | est_usd | model",
             "--- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---"]
    for s in samples[-40:]:
        t = s.get("tokens") or {}
        lines.append(
            f"{s.get('stage')} | {s.get('step')} | {s.get('reason') or '—'} | {t.get('input') or 0} "
            f"| {t.get('cache_read') or 0} | {t.get('output') or 0} | {t.get('reasoning') or 0} "
            f"| {s.get('est_usd') or 0:.4f} | `{s.get('model') or '—'}`"
        )
    return "\n".join(lines) + "\n"


def render_summary(summary: dict) -> str:
    lines = ["## Per-step sampler rollup", "",
             "| Stage | Steps | Input | Cached | Output | Reasoning | Est. USD | Model |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for stage, bag in summary["per_stage"].items():
        lines.append(
            f"| {stage} | {bag['steps']} | {bag['input']} | {bag['cache_read']} | {bag['output']} "
            f"| {bag['reasoning']} | {bag['est_usd']:.4f} | `{bag.get('model') or '—'}` |"
        )
    t = summary["total"]
    lines.append("")
    lines.append(f"**Total:** {t['steps']} steps · in={t['input']} cached={t['cache_read']} "
                 f"out={t['output']} reason={t['reasoning']} · est. ${t['est_usd']:.4f} API-equivalent "
                 f"(subscription routes report cost=0 per call)")
    return "\n".join(lines) + "\n"


def sample_run(run_dir: Path) -> tuple[list[dict], dict]:
    state = json.loads((run_dir / "state.json").read_text())
    samples = attach_cost(parse_step_samples(run_dir), model_resolver(state))
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
        print(f"[sampler] steps={summary['total']['steps']} est_usd={summary['total']['est_usd']} "
              f"at {time.strftime('%H:%M:%S')}", flush=True)
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
