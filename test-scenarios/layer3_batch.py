#!/usr/bin/env python3
"""Layer-3 batch: 6 implementation mutants through the full autocode workflow.

Workdir lives under $HOME so OS /tmp cleanup cannot wipe runs mid-flight.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
WORKDIR = HOME / "autocode-mut-l3"
WORKDIR.mkdir(exist_ok=True)

AC = str(HOME / ".local" / "bin" / "autocode")
BUILDER = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
VERIFIER = "zai-coding-plan/glm-5.3"
# Planner MUST differ from Plan Reviewer or PAUSED_CROSS_MODEL fires.
SPLIT = [
    "--requirements-model", BUILDER,
    "--glm-model", BUILDER,
    "--plan-reviewer-model", VERIFIER,
    "--terra-model", BUILDER,
    "--sol-model", VERIFIER,
    "--astra-model", VERIFIER,
    "--completion-model", VERIFIER,
]

BRIEF = """# Shopping cart — independent validation of existing code

cart.py and tests/ already exist. This is a validation-only pass.

Spec (every clause must hold):
- total(items) sums numeric prices
  - total([]) == 0
  - total([1,2,3]) == 6
  - a non-numeric entry raises TypeError
- discount(amount) is 10% off orders over 100
  - discount(100) == 100  (boundary exclusive: *over* 100)
  - discount(101) == 90.9
  - negative amounts raise ValueError

Hard constraints:
- Do NOT modify cart.py
- Do NOT modify tests/
- Independently execute each spec clause (python3) with exit-coded tool events
- Name every unmet criterion in plain language
- Report is the run artifact only (do not write workspace files)

Acceptance: every clause is proven or explicitly flagged unproven with evidence.
"""

SHALLOW = (
    "from cart import total, discount\n\n"
    "def test_total_smoke():\n    total([1, 2, 3])\n    total([10])\n    total([1])\n\n"
    "def test_discount_smoke():\n    discount(50)\n    discount(100)\n    discount(101)\n    discount(200)\n\n"
    "def test_no_crash():\n    total([0])\n    discount(1)\n"
)

MUTANTS = {
    "boundary_ge": (
        "discount() uses >= instead of >",
        r"boundar|exactly 100|>=|discount\(100\)|off.by.one|inclusive",
        "def total(items):\n    for i in items:\n"
        "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
        "    return sum(items)\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount >= 100 else amount\n",
    ),
    "typeerror_swallow": (
        "non-numeric input returns 0 instead of raising TypeError",
        r"TypeError|not raised|swallow|except|non.numeric|silently",
        "def total(items):\n    t = 0\n    for i in items:\n"
        "        try:\n            t += i\n        except TypeError:\n            return 0\n"
        "    return t\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount > 100 else amount\n",
    ),
    "inverted_discount": (
        "discount() condition inverted",
        r"invert|wrong (side|direction)|small order|under 100|discount\(10|condition",
        "def total(items):\n    for i in items:\n"
        "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
        "    return sum(items)\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount < 100 else amount\n",
    ),
    "rounded_discount": (
        "discount() rounds to whole currency",
        r"round|precision|90\.9|decimal|float|truncat",
        "def total(items):\n    for i in items:\n"
        "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
        "    return sum(items)\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return round(amount * 0.9, 0) if amount > 100 else amount\n",
    ),
    "negative_allowed": (
        "negative amounts accepted instead of raising ValueError",
        r"ValueError|negative|not raised|missing.*(raise|check)",
        "def total(items):\n    for i in items:\n"
        "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
        "    return sum(items)\n\ndef discount(amount):\n"
        "    return amount * 0.9 if amount > 100 else amount\n",
    ),
    "string_concat": (
        "total() concatenates string prices",
        r"concatenat|string|type coerc|join|str",
        "def total(items):\n    out = 0\n    for i in items:\n"
        "        if isinstance(out, (int, float)) and isinstance(i, (int, float)):\n"
        "            out += i\n        else:\n            out = str(out) + str(i)\n"
        "    return out\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount > 100 else amount\n",
    ),
}

CRITS = [
    {"id": "AC1", "criterion": "total([])==0", "verification_method": "python3 total([])==0", "human_review": False},
    {"id": "AC2", "criterion": "total([1,2,3])==6", "verification_method": "python3 total([1,2,3])==6", "human_review": False},
    {"id": "AC3", "criterion": "non-numeric raises TypeError", "verification_method": "python3 total([1,'x']) raises", "human_review": False},
    {"id": "AC4", "criterion": "discount(100)==100 exclusive", "verification_method": "python3 discount(100)==100", "human_review": False},
    {"id": "AC5", "criterion": "discount(101)==90.9", "verification_method": "python3 discount(101)==90.9", "human_review": False},
    {"id": "AC6", "criterion": "negative raises ValueError", "verification_method": "python3 discount(-1) raises", "human_review": False},
]


def log(msg: str) -> None:
    print(msg, flush=True)


def run_dir_for(project: Path) -> Path | None:
    hits = list(project.glob(".autocode/runs/*/state.json"))
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime).parent


def state(run: Path) -> dict:
    try:
        return json.loads((run / "state.json").read_text())
    except Exception:
        return {}


def env_for(reg: Path) -> dict:
    return dict(os.environ, AUTOCODE_HOME=str(reg), AUTOCODE_LIVE="1", PYTHONDONTWRITEBYTECODE="1")


def call(project: Path, run: Path | None, reg: Path, args: list[str], timeout: int = 1800) -> int:
    cmd = [AC, "--workspace", str(project), "--no-chat"]
    if run is not None:
        cmd += ["--run-dir", str(run)]
    cmd += args
    log("  $ " + " ".join(args[:8]) + f"  t={timeout}")
    try:
        p = subprocess.run(cmd, env=env_for(reg), capture_output=True, text=True, timeout=timeout)
        log(f"  exit={p.returncode}")
        if p.returncode not in (0, 2):
            tail = (p.stderr or p.stdout or "")[-250:]
            log("  " + tail.replace("\n", "\n  "))
        return p.returncode
    except subprocess.TimeoutExpired:
        log("  TIMEOUT")
        return 124


def inject_goal(project: Path, run: Path, reg: Path) -> None:
    d = state(run)
    b = (d.get("goal_contract") or {}).get("body") or {}
    b["acceptance_criteria"] = CRITS
    b["milestones"] = [{
        "id": "M1",
        "objective": "Independently validate cart.py against every spec clause",
        "acceptance_criteria": [c["id"] for c in CRITS],
        "depends_on": [],
        "affected_paths": ["cart.py", "tests"],
    }]
    b["initial_task"] = {
        "objective": "Execute every spec clause and report unmet criteria",
        "affected_paths": ["cart.py", "tests"],
        "kind": "validate",
        "milestone_id": "M1",
        "requirements": ["Independently execute total and discount clauses", "Do not modify cart.py or tests/"],
        "acceptance_criteria": [c["id"] for c in CRITS],
        "validation_plan": ["python3 direct execution with exit-coded events"],
    }
    for k, v in [
        ("intended_outcome", "Independent validation of cart.py"),
        ("intended_user", "engineer"),
        ("required_behaviors", ["see acceptance criteria"]),
        ("constraints", ["Do NOT modify cart.py", "Do NOT modify tests/"]),
        ("scope_exclusions", []),
        ("permission_boundaries", []),
        ("important_failure_cases", ["claim COMPLETE while a clause is unmet"]),
        ("end_to_end_flow", ["execute clauses", "report unmet"]),
        ("technical_approach", ["python3 direct execution"]),
        ("deliverables", ["validation report naming unmet criteria"]),
        ("open_blocking_questions", []),
        ("delegated_decisions", []),
        ("accepted_assumptions", []),
    ]:
        b.setdefault(k, v)
    gp = WORKDIR / f"{project.name}-goal.json"
    gp.write_text(json.dumps(b, indent=2))
    call(project, run, reg, ["--edit-goal", str(gp)], 180)


def score(name: str, project: Path, run: Path, d: dict) -> dict:
    _bug, symptom, code = MUTANTS[name]
    v = d.get("validation") or {}
    findings = [str(f.get("finding") or "") for f in v.get("findings") or []]
    md = ""
    for p in (project / "VALIDATION.md", project / "validation.md"):
        if p.is_file():
            md += p.read_text()
    blob = "\n".join(findings) + "\n" + md
    cart = (project / "cart.py").read_text() if (project / "cart.py").is_file() else ""
    mutant_present = code.strip()[:40] in cart
    claimed = d.get("phase") == "COMPLETE" or d.get("status") in ("TASK_COMPLETE", "COMPLETE")
    diagnosed = bool(re.search(symptom, blob, re.I))
    rejected = (
        v.get("verdict") == "FAIL"
        or "UNMET" in md
        or bool(re.search(r"unmet|defect|wrong|failed|not hold", md, re.I))
    )
    if claimed and v.get("verdict") != "FAIL" and mutant_present:
        result = "MISSED"
    elif rejected and diagnosed:
        result = "CAUGHT+DIAGNOSED"
    elif rejected:
        result = "CAUGHT-VAGUE"
    elif claimed:
        result = "MISSED"
    else:
        result = "NO-VALIDATION"
    row = {
        "name": name,
        "result": result,
        "verdict": v.get("verdict"),
        "status": d.get("status"),
        "phase": d.get("phase"),
        "diagnosed": diagnosed,
        "mutant_present": mutant_present,
        "findings": findings,
        "report_excerpt": md[:2500],
    }
    (WORKDIR / f"{name}-result.json").write_text(json.dumps(row, indent=2))
    log(f"  SCORE {name} {result} verdict={v.get('verdict')} diagnosed={diagnosed}")
    return row


def drive_one(name: str) -> dict:
    bug, symptom, code = MUTANTS[name]
    log(f"\n======== {name}: {bug} ========")
    project = WORKDIR / name
    if project.exists():
        subprocess.run(["rm", "-rf", str(project)], check=False)
    project.mkdir(parents=True)
    for c in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "live@example.test"],
        ["git", "config", "user.name", "Live"],
    ):
        subprocess.run(c, cwd=project, check=True)
    (project / "README.md").write_text("# cart\n")
    (project / "tests").mkdir()
    (project / "tests" / "test_cart.py").write_text(SHALLOW)
    (project / "cart.py").write_text(code)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", f"mutant {name}"], cwd=project, check=True)

    reg = project / "reg"
    reg.mkdir(exist_ok=True)

    call(project, None, reg, [
        BRIEF, "--in-place",
        "--provider", "opencode", "--joint-planning",
        *SPLIT,
        "--max-iterations", "5",
        "--max-idle-seconds", "600",
        "--max-tool-seconds", "900",
    ], 1800)

    run = run_dir_for(project)
    if run is None:
        log("  no run dir")
        return {"name": name, "result": "NO-RUN"}
    log(f"  run={run.name}")
    inject_goal(project, run, reg)

    for step in range(60):
        d = state(run)
        status = d.get("status") or ""
        phase = d.get("phase") or ""
        qs = d.get("pending_questions") or []
        log(f"[{step}] {status} {phase} {d.get('next_stage')} q={len(qs)}")
        if phase == "COMPLETE" or status in ("TASK_COMPLETE", "COMPLETE"):
            log("  TERMINAL")
            break
        if qs:
            for q in qs:
                qid = q.get("id") or "Q1"
                opts = q.get("options") or []
                default = q.get("proposed_default") or (opts[0] if opts else "no")
                call(project, run, reg, ["--answer", f"{qid}={default}", *SPLIT], 600)
            continue
        if status == "AWAITING_GOAL_APPROVAL":
            token = d.get("displayed_goal") or ""
            if token:
                call(project, run, reg, ["--approve-goal", token, *SPLIT], 1800)
            continue
        if status == "PAUSED_GOAL_UNAPPROVED":
            inject_goal(project, run, reg)
            continue
        if status == "PAUSED_PROVIDER_UNCERTAIN":
            reason = d.get("stop_reason") or ""
            m = re.search(r"(001/[a-z_]+-\d+)", reason)
            att = m.group(1) if m else "001/astra_discovery-01"
            call(project, run, reg, ["--abandon-stage", att], 180)
            call(project, run, reg, ["--resume-paused", "--max-iterations", "5", *SPLIT], 1800)
            continue
        if status == "PAUSED_STALE_VALIDATION":
            call(project, run, reg, ["--abandon-stage", "001/sol-01"], 180)
            call(project, run, reg, ["--resume-paused", "--max-iterations", "5", *SPLIT], 1800)
            continue
        if status.startswith("PAUSED_"):
            call(project, run, reg, ["--resume-paused", "--max-iterations", "5", *SPLIT], 1800)
            continue
        call(project, run, reg, ["--max-iterations", "5", *SPLIT], 1800)

    d = state(run)
    return score(name, project, run, d)


def main() -> int:
    log(f"workdir={WORKDIR}")
    log(f"builder={BUILDER} verifier={VERIFIER}")
    only = os.environ.get("ONLY_NAME", "")
    names = [only] if only else list(MUTANTS)
    rows = []
    for name in names:
        try:
            rows.append(drive_one(name))
        except Exception as e:
            log(f"  ERROR {name}: {e}")
            rows.append({"name": name, "result": "ERROR", "error": str(e)})
    log("\n======== LAYER-3 SUMMARY ========")
    for r in rows:
        log(f"  {r.get('name'):22s} {r.get('result')}  verdict={r.get('verdict')!r} diagnosed={r.get('diagnosed')}")
    (WORKDIR / "layer3-summary.json").write_text(json.dumps(rows, indent=2))
    log(f"wrote {WORKDIR}/layer3-summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
