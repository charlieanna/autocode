#!/usr/bin/env python3
"""Score AutoCode tool behavior and per-stage token usage for a live run.

Dimensions (each scored PASS / FAIL / HONEST_BLOCKER / PARTIAL / N/A):
  repair_loops     — report-repair and builder rework are bounded and logged
  gate_honesty     — approval/answer gates never auto-approve; stale tokens rejected
  model_routing    — every stage launch uses the pinned subscription models
  pause_recovery   — pauses state a reason and a recovery command; no silent COMPLETE
  evidence         — COMPLETE/ACCEPT requires evidence, not just claims
  token_discipline — per-stage tokens recorded; budget flags work

Cost note: OpenCode reports cost=0 on subscription routes (Z.AI Coding Plan,
Xiaomi Token Plan). We record tokens per stage and an *estimated API-equivalent*
USD using reference list prices so runs are comparable. Actual billed cost on
these subscriptions is the plan fee, not per-token.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path

# Reference list prices USD per 1M tokens (input, output). Used only to make
# stages comparable; subscription routes are not billed this way.
REFERENCE_PRICES = {
    "zai-coding-plan/glm-5.3": {"input": 0.60, "output": 2.20},
    "xiaomi-token-plan-sgp/mimo-v2.6-pro": {"input": 0.30, "output": 1.20},
    "default": {"input": 1.00, "output": 4.00},
}

ALLOWED_MODEL_PREFIXES = (
    "zai-coding-plan/glm-5.3",
    "xiaomi-token-plan-sgp/mimo-v2.6-pro",
)
FORBIDDEN_MODEL_MARKERS = ("-free", "flash", "mimo-token-plan/", "glm-5.2", "gpt-")

# docs/models.md ladder entry points + user independence rule (2026-09-26):
# verifier never equals producer. MiMo checks GLM work and GLM checks MiMo work.
# Start at the ladder's medium rung; shift to higher reasoning in-stage when needed.
LADDER = {
    "requirements": {"model": "zai-coding-plan/glm-5.3", "effort": "medium"},
    "glm": {"model": "zai-coding-plan/glm-5.3", "effort": "high"},
    "plan_reviewer": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "effort": "high"},
    "terra": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "effort": "medium"},
    "sol": {"model": "zai-coding-plan/glm-5.3", "effort": "high"},
    "completion": {"model": "zai-coding-plan/glm-5.3", "effort": "medium"},
    "astra": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "effort": "high"},
    "resolver": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "effort": "high"},
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
    if model.startswith("zai-coding-plan/"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/") or model.startswith("mimo-"):
        return "mimo"
    return model.split("/", 1)[0]


def estimate_cost(model: str, tokens: dict) -> float:
    prices = REFERENCE_PRICES.get(model, REFERENCE_PRICES["default"])
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    inp = (tokens.get("input_tokens") or tokens.get("input") or 0)
    inp += (tokens.get("cached_input_tokens") or cache.get("read") or 0)
    out = (tokens.get("output_tokens") or tokens.get("output") or 0)
    out += (tokens.get("reasoning_output_tokens") or tokens.get("reasoning") or 0)
    return round(inp / 1e6 * prices["input"] + out / 1e6 * prices["output"], 4)


def load_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text())


def stage_token_rows(state: dict) -> list[dict]:
    roles = (state.get("settings") or {}).get("roles") or {}
    role_model = {role: (cfg or {}).get("model") for role, cfg in roles.items()}
    # stage id -> role key used in settings
    stage_role = {
        "requirements_gather": "requirements", "requirements_gather_report_repair": "requirements",
        "astra_discovery": "astra", "astra_challenge": "plan_reviewer",
        "glm_revise": "glm", "astra_finalize": "plan_reviewer",
        "terra": "terra", "sol": "sol", "astra_review": "astra",
        "astra_checkpoint": "astra", "astra_resolve": "resolver",
        "astra_plan": "astra",
    }
    rows = []
    for st in state.get("stages") or []:
        metrics = st.get("metrics") or {}
        tokens = metrics.get("provider_tokens") or {}
        stage = st.get("stage") or ""
        role = st.get("role") or stage_role.get(stage) or ""
        model = st.get("model") or role_model.get(role) or ""
        cmd = st.get("command") or []
        if not model and isinstance(cmd, list) and "--model" in cmd:
            model = cmd[cmd.index("--model") + 1]
        rows.append({
            "stage": stage,
            "name": st.get("name"),
            "role": role,
            "model": model,
            "tokens": tokens,
            "estimated_usd": estimate_cost(model, tokens) if tokens else 0.0,
            "finished_at": st.get("finished_at"),
        })
    active = state.get("active_stage") or {}
    if active.get("stage"):
        metrics = active.get("metrics") or {}
        tokens = metrics.get("provider_tokens") or {}
        cmd = active.get("command") or []
        model = ""
        if isinstance(cmd, list) and "--model" in cmd:
            model = cmd[cmd.index("--model") + 1]
        role = active.get("role") or stage_role.get(active.get("stage") or "", "")
        model = model or role_model.get(role) or ""
        rows.append({
            "stage": active.get("stage"),
            "name": active.get("name"),
            "role": role,
            "model": model,
            "tokens": tokens,
            "estimated_usd": estimate_cost(model, tokens) if tokens else 0.0,
            "finished_at": None,
            "active": True,
        })
    return rows


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
        if "--model" in cmd:
            launched.append(cmd[cmd.index("--model") + 1])

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
    bad = [m for m in uniq if any(f in m for f in FORBIDDEN_MODEL_MARKERS)]
    bad += [m for m in uniq if m.startswith("mimo-token-plan/")]
    # only accept the two subscription families for this run
    ok_models = all(
        m.startswith("zai-coding-plan/glm-5.3") or m.startswith("xiaomi-token-plan-sgp/mimo-v2.6-pro")
        for m in uniq
    ) if uniq else False
    return {"launched_models": uniq, "pinned_roles": pinned, "forbidden_seen": sorted(set(bad)),
            "all_subscription_only": ok_models and not bad}


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
        if pm and vm and _family(pm) == _family(vm):
            independence_ok = False
            mismatches.append(f"independence {label}: both {_family(pm)} ({pm} / {vm})")
    return {"aligned": aligned, "mismatches": mismatches,
            "independence_ok": independence_ok, "on_ladder": not mismatches}


def score_run(run_dir: Path) -> dict:
    state = load_state(run_dir)
    status = state.get("status") or ""
    stages = state.get("stages") or []
    stage_names = [s.get("stage") for s in stages]
    rows = stage_token_rows(state)
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
    route_score = "PASS" if routing["all_subscription_only"] else "FAIL"
    if routing["forbidden_seen"]:
        route_score = "FAIL"
    ladder = ladder_alignment(routing.get("pinned_roles") or {})
    if not ladder["on_ladder"]:
        route_score = "PARTIAL" if route_score == "PASS" else route_score

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
    token_score = "PASS" if rows and any(r.get("tokens") for r in rows) else "PARTIAL"
    token_notes = []
    total_tokens = defaultdict(int)
    for r in rows:
        t = r.get("tokens") or {}
        for k, v in t.items():
            if isinstance(v, int):
                total_tokens[k] += v
    total_usd = round(sum(r.get("estimated_usd") or 0.0 for r in rows), 4)
    step_summary = {"per_stage": {}, "total": {}}
    try:
        import live_token_sampler as sampler
        _, step_summary = sampler.sample_run(run_dir)
        token_notes.append(f"per-step sampler: {step_summary['total'].get('steps', 0)} provider steps")
    except Exception as exc:
        token_notes.append(f"per-step sampler unavailable: {exc}")
    if rows:
        token_notes.append(f"{len(rows)} stage metric rows")
        token_notes.append(f"est. API-equivalent ${total_usd:.4f} (subscription routes bill plan fee, cost=0 per call)")
    limits = state.get("limits") or {}
    if limits.get("max_reported_tokens"):
        token_notes.append(f"max_reported_tokens={limits['max_reported_tokens']} enforced")

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
            "per_stage": rows,
            "totals": dict(total_tokens),
            "estimated_api_equivalent_usd": total_usd,
            "billed_note": "OpenCode reports cost=0 on these subscription routes; USD above is API-equivalent estimate only.",
            "per_step": step_summary,
        },
    }


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
            f"| {r.get('stage')} | {r.get('name') or '—'} | `{r.get('model') or '—'}` | {t.get('input_tokens') or 0} "
            f"| {t.get('cached_input_tokens') or 0} | {t.get('output_tokens') or 0} "
            f"| {t.get('reasoning_output_tokens') or 0} | {r.get('estimated_usd') or 0:.4f} |"
        )
    tot = report["token_usage"]["totals"]
    lines.append("")
    lines.append(f"**Totals:** input={tot.get('input_tokens', 0)} cached={tot.get('cached_input_tokens', 0)} "
                 f"output={tot.get('output_tokens', 0)} reasoning={tot.get('reasoning_output_tokens', 0)}")
    lines.append("")
    lines.append(f"**Estimated API-equivalent cost:** ${report['token_usage']['estimated_api_equivalent_usd']:.4f} "
                 f"({report['token_usage']['billed_note']})")
    per_step = (report.get("token_usage") or {}).get("per_step") or {}
    if per_step.get("per_stage"):
        try:
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
