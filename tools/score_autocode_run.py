#!/usr/bin/env python3
"""Score AutoCode tool behavior and per-stage token usage for a live run.

Dimensions (each scored PASS / FAIL / HONEST_BLOCKER / PARTIAL / N/A):
  repair_loops     — report-repair and builder rework are bounded and logged
  gate_honesty     — approval/answer gates never auto-approve; stale tokens rejected
  model_routing    — recorded models retain independent verification routes
  pause_recovery   — pauses state a reason and a recovery command; no silent COMPLETE
  evidence         — COMPLETE/ACCEPT requires evidence, not just claims
  token_discipline — per-stage token usage recorded

Cost note: USD values use historical comparison rates, not verified current
prices or invoices. Inclusive input/output totals are priced once at those flat
rates (no cache discount). Missing usage or rates remain unknown. Subscription
fees and quota consumption are not measured by this estimate.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path

try:
    from .autocode_usage import REFERENCE_PRICES
except ImportError:
    from autocode_usage import REFERENCE_PRICES

# docs/models.md ladder entry points + user independence rule (2026-09-26):
# verifier never equals producer. OpenAI GPT checks GLM work and GLM checks GPT work.
# Start at the ladder's medium rung; shift to higher reasoning in-stage when needed.
LADDER = {
    "requirements": {"model": "zai-coding-plan/glm-5.3", "effort": "medium"},
    "glm": {"model": "zai-coding-plan/glm-5.3", "effort": "high"},
    "plan_reviewer": {"model": "openai/gpt-6-sol", "effort": "high"},
    "terra": {"model": "zai-coding-plan/glm-5.3", "effort": "medium"},
    "sol": {"model": "openai/gpt-6-sol", "effort": "high"},
    "completion": {"model": "openai/gpt-6-sol", "effort": "medium"},
    "astra": {"model": "openai/gpt-6-astra", "effort": "high"},
    "resolver": {"model": "openai/gpt-6-astra", "effort": "high"},
}

# producer role -> verifier role(s) that must use a different model family
INDEPENDENCE_PAIRS = (
    ("glm", "plan_reviewer", "Planner/Plan Reviewer"),
    ("terra", "sol", "Builder/Validator"),
    ("terra", "completion", "Builder/Completion Owner"),
)


def _family(model: str) -> str:
    if not model:
        return ""
    name = model.rsplit("/", 1)[-1].lower()
    if model.startswith("zai-coding-plan/") or name.startswith("glm-"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/") or name.startswith("mimo-"):
        return "mimo"
    return model


def token_count(value):
    return value if type(value) is int and value >= 0 else None


def known_sum(values):
    values = list(values)
    return sum(values) if values and all(v is not None for v in values) else None


def normalized_tokens(tokens: dict) -> dict:
    """Runner totals include cached input and reasoning; raw OpenCode does not.

    Match providers/opencode.py normalization, including cache writes. Missing
    fields cannot be inferred to be zero, and explicit zero must survive.
    """
    tokens = tokens if isinstance(tokens, dict) else {}
    if any(k in tokens for k in ("input_tokens", "output_tokens",
                                 "cached_input_tokens", "reasoning_output_tokens")):
        result = {k: token_count(tokens.get(k)) for k in (
            "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")}
        for subset, total in (("cached_input_tokens", "input_tokens"),
                              ("reasoning_output_tokens", "output_tokens")):
            if tokens.get(subset) is not None and (
                    result[subset] is None or (result[total] is not None and result[subset] > result[total])):
                result[total] = None
        return result
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    cached, written = token_count(cache.get("read")), token_count(cache.get("write"))
    reasoning = token_count(tokens.get("reasoning"))
    return {"input_tokens": known_sum([token_count(tokens.get("input")), cached, written]),
            "cached_input_tokens": cached,
            "output_tokens": known_sum([token_count(tokens.get("output")), reasoning]),
            "reasoning_output_tokens": reasoning}


def estimate_cost(model: str, tokens: dict) -> float | None:
    prices = REFERENCE_PRICES.get(model)
    usage = normalized_tokens(tokens)
    inp, out = usage["input_tokens"], usage["output_tokens"]
    if prices is None or inp is None or out is None:
        return None
    return (inp * prices["input"] + out * prices["output"]) / 1e6


def recorded_model(record: dict) -> str:
    """The launch command wins; mutable current role settings are not history."""
    command = record.get("command")
    if isinstance(command, list):
        for i, arg in enumerate(command):
            if arg in ("--model", "-m") and i + 1 < len(command):
                value = command[i + 1]
                if isinstance(value, str) and value and not value.startswith("-"):
                    return value
            if isinstance(arg, str) and arg.startswith("--model="):
                return arg.partition("=")[2]
    model = record.get("model")
    return model if isinstance(model, str) else ""


def load_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text())


def stage_token_rows(state: dict) -> list[dict]:
    # stage id -> role key used in settings
    stage_role = {
        "recognize_workflow": "requirements", "review_change": "sol", "investigate_bug": "investigator", "review_design": "architect", "answer_question": "analyst", "check_design": "architect", "investigate_stuck": "stuck_investigator",
        "requirements_gather": "requirements", "requirements_gather_report_repair": "requirements",
        "astra_discovery": "astra", "astra_challenge": "plan_reviewer",
        "glm_revise": "glm", "astra_finalize": "plan_reviewer",
        "terra": "terra", "sol": "sol", "astra_review": "astra",
        "astra_checkpoint": "astra", "astra_resolve": "resolver",
        "astra_plan": "astra",
    }
    rows = []
    records = [(st, False) for st in state.get("stages") or []]
    active = state.get("active_stage") or {}
    if active.get("stage"):
        records.append((active, True))
    for st, is_active in records:
        metrics = st.get("metrics") or {}
        tokens = metrics.get("provider_tokens") or {}
        stage = st.get("stage") or ""
        role = st.get("role") or stage_role.get(stage) or ""
        model = recorded_model(st)
        runner_owned = st.get("runner_owned") is True and st.get("engine") == "runner"
        rows.append({
            "stage": stage,
            "name": st.get("name"),
            "role": role,
            "model": model,
            "tokens": normalized_tokens(tokens),
            "estimated_usd": 0.0 if runner_owned else estimate_cost(model, tokens),
            "finished_at": st.get("finished_at"),
            "active": is_active,
            "runner_owned": runner_owned,
        })
    return rows


def usage_summary(state: dict) -> dict:
    """Serializable accounting snapshot; does not inspect files or run checks."""
    rows = stage_token_rows(state)
    return {
        "per_stage": rows,
        "totals": {k: known_sum(r["tokens"].get(k) for r in rows) for k in (
            "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")},
        "estimated_api_equivalent_usd": None if any(r["active"] for r in rows)
            else known_sum(r["estimated_usd"] for r in rows),
        "known_estimated_api_equivalent_usd": sum(
            r["estimated_usd"] for r in rows if r["estimated_usd"] is not None),
        "unpriced_stages": sum(r["estimated_usd"] is None for r in rows),
        "pricing_basis": {"kind": "historical_flat_comparison_rates", "usd_per_million": REFERENCE_PRICES},
        "billed_note": "Historical flat comparison rates, not verified current prices or actual billing; "
                       "inclusive input/output priced once, without cache discounts. Unknown is not zero.",
    }


MODEL_ID_RE = re.compile(
    r"(zai-coding-plan/[\w.-]+|xiaomi-token-plan-sgp/[\w.-]+|mimo-token-plan/[\w.-]+"
    r"|opencode/mimo-[\w.-]+|openai/[\w.-]+|cursor-acp/[\w.-]+"
    r"|gpt-[\w.-]+|glm-[\w.-]+|mimo-v[\w.-]+)"
)


def model_route_checks(state: dict, run_dir: Path) -> dict:
    launched = []
    # Prefer explicit --model values from recorded commands; those are what ran.
    def take_command(cmd):
        if not isinstance(cmd, list):
            return
        model = recorded_model({"command": cmd})
        if model:
            launched.append(model)

    for st in state.get("stages") or []:
        take_command(st.get("command"))
    take_command((state.get("active_stage") or {}).get("command"))
    # Permission/config blobs may embed the model string too.
    for path in run_dir.rglob("*.opencode.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        text = json.dumps(data)
        for m in MODEL_ID_RE.findall(text):
            if "/" in m or m.startswith("gpt-") or m.startswith("glm-") or m.startswith("mimo-"):
                launched.append(m)

    roles = (state.get("settings") or {}).get("roles") or {}
    pinned = {role: (cfg if isinstance(cfg, dict) else {"model": cfg}) for role, cfg in roles.items()}
    for cfg in pinned.values():
        m = (cfg or {}).get("model")
        if m:
            launched.append(m)

    uniq = sorted(set(launched))
    # Legacy fields remain available to report consumers; billing is descriptive.
    subscription_only = bool(uniq) and all(m.startswith(("zai-coding-plan/", "github-copilot/")) for m in uniq)
    return {"launched_models": uniq, "pinned_roles": pinned, "forbidden_seen": [],
            "all_subscription_only": subscription_only}


def ladder_alignment(pinned: dict) -> dict:
    """Compare saved role routes against docs/models.md ladders + independence."""
    mismatches = []
    aligned = []

    def model_effort(role):
        cfg = pinned.get(role) or {}
        if not cfg and role == "resolver":
            cfg = pinned.get("astra") or {}
        if not cfg:
            return "", ""
        if isinstance(cfg, str):
            return cfg, ""
        return cfg.get("model") or "", cfg.get("reasoning_effort") or ""

    for role, expect in LADDER.items():
        model, effort = model_effort(role)
        if not model:
            continue
        if model != expect["model"]:
            mismatches.append(f"{role}: model {model!r} != ladder {expect['model']!r}")
        if effort and effort != expect["effort"]:
            mismatches.append(f"{role}: effort {effort!r} != ladder {expect['effort']!r}")
        if model == expect["model"] and (not effort or effort == expect["effort"]):
            aligned.append(role)

    independence_ok = True
    for producer, verifier, label in INDEPENDENCE_PAIRS:
        pm, _ = model_effort(producer)
        vm, _ = model_effort(verifier)
        if pm and vm and (pm == vm or (_family(pm) in ("glm", "mimo") and _family(pm) == _family(vm))):
            independence_ok = False
            mismatches.append(f"independence {label}: both {_family(pm)} ({pm} / {vm})")
    return {"aligned": aligned, "mismatches": mismatches,
            "independence_ok": independence_ok, "on_ladder": not mismatches}


def score_run(run_dir: Path) -> dict:
    state = load_state(run_dir)
    status = state.get("status") or ""
    stages = state.get("stages") or []
    stage_names = [s.get("stage") for s in stages]
    usage = usage_summary(state)
    rows = usage["per_stage"]
    routing = model_route_checks(state, run_dir)

    # --- repair loops ---
    repairs = [s for s in stages if "report_repair" in (s.get("stage") or "")]
    reworks = [s for s in stages if s.get("stage") in ("terra", "sol", "builder") and s.get("rework")]
    repair_score = "PASS"
    repair_notes = []
    if repairs:
        repair_notes.append(f"{len(repairs)} report-repair stage(s) recorded and bounded by the runner")
    # unlimited repair would show many repeats of same stage name
    counts = defaultdict(int)
    for n in stage_names:
        counts[n] += 1
    loops = {k: v for k, v in counts.items() if v >= 4 and "repair" in k}
    if loops:
        repair_score = "FAIL"
        repair_notes.append(f"unbounded repair loop: {loops}")
    elif repairs and repair_score == "PASS":
        repair_notes.append("repair present but bounded")

    # --- gate honesty ---
    gate_score = "PASS"
    gate_notes = []
    if status == "WAITING_FOR_USER" or status == "AWAITING_GOAL_APPROVAL":
        gate_notes.append(f"run currently parked at honest gate: {status}")
    stale = "show the goal again" in (state.get("error") or "").lower()
    if state.get("displayed_goal"):
        gate_notes.append("displayed_goal token present; approval must use the current token")
    # if COMPLETE without acceptance evidence -> FAIL
    if status in ("TASK_COMPLETE", "COMPLETE"):
        body = (state.get("contract") or {}).get("body") or {}
        if not (stages and any(s.get("stage") == "sol" for s in stages)):
            gate_score = "FAIL"
            gate_notes.append("COMPLETE without an independent validator stage")

    # --- model routing ---
    route_score = "PASS" if routing["launched_models"] else "FAIL"
    ladder = ladder_alignment(routing.get("pinned_roles") or {})
    if not ladder["independence_ok"]:
        route_score = "FAIL"

    # --- pause recovery ---
    pause_score = "PASS"
    pause_notes = []
    paused = [p for p in (state.get("progress") or []) if "Pause" in (p.get("text") or "") or (p.get("kind") == "transition" and "Paused" in (p.get("text") or ""))]
    if status.startswith("PAUSED"):
        pause_notes.append(f"paused at {status}; error={state.get('error')}")
        if not state.get("error") and not paused:
            pause_score = "PARTIAL"
            pause_notes.append("pause without a recorded reason")
    elif paused:
        pause_notes.append(f"{len(paused)} pause transition(s) observed; run continued or is parked")

    # --- evidence ---
    evidence_score = "N/A"
    evidence_notes = ["no terminal COMPLETE/ACCEPT yet"]
    if status in ("TASK_COMPLETE", "COMPLETE"):
        evidence_score = "PASS" if stages else "FAIL"
        evidence_notes = [f"{len(stages)} staged artifacts recorded"]

    # --- token discipline ---
    token_score = "PASS" if rows and all(
        r["tokens"][k] is not None for r in rows for k in ("input_tokens", "output_tokens")) else "PARTIAL"
    token_notes = []
    total_usd = usage["estimated_api_equivalent_usd"]
    known_usd = usage["known_estimated_api_equivalent_usd"]
    unknown_rows = usage["unpriced_stages"]
    step_summary = {"per_stage": {}, "total": {}}
    try:
        try:
            from . import live_token_sampler as sampler
        except ImportError:
            import live_token_sampler as sampler
        _, step_summary = sampler.sample_run(run_dir)
        token_notes.append(f"per-step sampler: {step_summary['total'].get('steps', 0)} provider steps")
    except Exception as exc:
        token_notes.append(f"per-step sampler unavailable: {exc}")
    if rows:
        token_notes.append(f"{len(rows)} stage metric rows")
        token_notes.append(f"historical-rate estimate: {money(total_usd)}; "
                           f"known subtotal {money(known_usd)}, {unknown_rows} unpriced stage(s)")

    return {
        "run_dir": str(run_dir),
        "status": status,
        "phase": state.get("phase"),
        "scored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scores": {
            "repair_loops": {"score": repair_score, "notes": repair_notes},
            "gate_honesty": {"score": gate_score, "notes": gate_notes},
            "model_routing": {"score": route_score, "notes": [
                f"launched={routing['launched_models']}",
                f"pinned={routing['pinned_roles']}",
                f"forbidden={routing['forbidden_seen']}",
                f"ladder_aligned={ladder['aligned']}",
                f"ladder_mismatches={ladder['mismatches']}",
            ]},
            "pause_recovery": {"score": pause_score, "notes": pause_notes},
            "evidence": {"score": evidence_score, "notes": evidence_notes},
            "token_discipline": {"score": token_score, "notes": token_notes},
        },
        "token_usage": {
            **usage,
            "per_step": step_summary,
        },
    }


def money(value) -> str:
    return "unknown" if value is None else f"${value:.6f}"


def count_text(value) -> str:
    return "unknown" if value is None else str(value)


def render(report: dict) -> str:
    lines = [f"# AutoCode tool-behavior score — {report['status']}", ""]
    lines.append(f"Run: `{report['run_dir']}`")
    lines.append(f"Scored: {report['scored_at']}")
    lines.append("")
    lines.append("## Dimension scores")
    lines.append("")
    lines.append("| Dimension | Score | Notes |")
    lines.append("| --- | --- | --- |")
    for name, item in report["scores"].items():
        notes = "; ".join(item["notes"]) or "—"
        lines.append(f"| {name} | **{item['score']}** | {notes} |")
    lines.append("")
    lines.append("## Token usage per stage")
    lines.append("")
    lines.append("| Stage | Name | Model | Input | Cached in | Output | Reasoning | Est. USD (API-equiv) |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for r in report["token_usage"]["per_stage"]:
        t = r.get("tokens") or {}
        lines.append(
            f"| {r.get('stage')} | {r.get('name') or '—'} | `{r.get('model') or 'unknown'}` | {count_text(t.get('input_tokens'))} "
            f"| {count_text(t.get('cached_input_tokens'))} | {count_text(t.get('output_tokens'))} "
            f"| {count_text(t.get('reasoning_output_tokens'))} | {money(r.get('estimated_usd'))} |"
        )
    tot = report["token_usage"]["totals"]
    lines.append("")
    lines.append(f"**Recorded totals:** input={count_text(tot.get('input_tokens'))} cached={count_text(tot.get('cached_input_tokens'))} "
                 f"output={count_text(tot.get('output_tokens'))} reasoning={count_text(tot.get('reasoning_output_tokens'))}")
    lines.append("")
    lines.append(f"**Estimated API-equivalent cost:** {money(report['token_usage']['estimated_api_equivalent_usd'])} "
                 f"({report['token_usage']['billed_note']})")
    lines.append(f"**Known subtotal:** {money(report['token_usage']['known_estimated_api_equivalent_usd'])}; "
                 f"unpriced stages={report['token_usage']['unpriced_stages']}")
    per_step = (report.get("token_usage") or {}).get("per_step") or {}
    if per_step.get("per_stage"):
        try:
            try:
                from . import live_token_sampler as sampler
            except ImportError:
                import live_token_sampler as sampler
            lines.append("")
            lines.append(sampler.render_summary(per_step))
        except Exception:
            pass
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Score AutoCode tool behavior + tokens for a run.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="Write markdown report here")
    parser.add_argument("--json-out", type=Path, help="Write JSON report here")
    args = parser.parse_args()
    report = score_run(args.run_dir)
    text = render(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2))
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
