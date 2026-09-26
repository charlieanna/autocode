#!/usr/bin/env python3
"""Full autocode workflow + live models — remaining implementation mutants.

Fixes vs the first run:
- Split producer/verifier model families from the start (PAUSED_CROSS_MODEL).
- Brief forbids workspace writes so sol stays snapshot-stable (PAUSED_STALE_VALIDATION).
- --max-iterations 5 so abandon+retry is not immediately capped.
- Explicit recovery for CROSS_MODEL / STALE_VALIDATION / PROVIDER_UNCERTAIN /
  ITERATION_LIMIT / STAGE_ABANDONED.

Scores from VALIDATION.md + state.json: did the Validator name the defect,
and did the runner refuse COMPLETE on a mutant?
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

BUILDER = os.environ.get("LIVE_BUILDER", "xiaomi-token-plan-sgp/mimo-v2.6-pro")
VERIFIER = os.environ.get("LIVE_VERIFIER", "zai-coding-plan/glm-5.3")
WORKDIR = Path(os.environ.get("LIVE_WORKDIR", "/tmp/live-wf-mut2"))
AC = Path.home() / ".local" / "bin" / "autocode"
if not AC.is_file():
    AC = Path(__file__).resolve().parents[1] / "tools" / "autocode.py"

SPEC_BRIEF = """# Shopping cart — independent validation of existing code

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
- Do NOT create or write any file in the workspace (no VALIDATION.md, no
  scratch files). The validation report is the run artifact only.
- Independently execute each spec clause (python3 / pytest) and record
  exit-coded tool events as evidence.
- In the report, name every unmet criterion in plain language.

Acceptance: every clause is proven or explicitly flagged unproven with evidence.
"""

SHALLOW = """from cart import total, discount

def test_total_smoke():
    total([1, 2, 3])
    total([10])
    total([1])

def test_discount_smoke():
    discount(50)
    discount(100)
    discount(101)
    discount(200)

def test_no_crash():
    total([0])
    discount(1)
"""

IMPL = {
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
        r"concatenat|string|type coerc|\"\\+\"|join|str",
        "def total(items):\n    out = 0\n    for i in items:\n"
        "        if isinstance(out, (int, float)) and isinstance(i, (int, float)):\n"
        "            out += i\n        else:\n            out = str(out) + str(i)\n"
        "    return out\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount > 100 else amount\n",
    ),
}

CORRECT = (
    "def total(items):\n    for i in items:\n"
    "        if not isinstance(i, (int, float)):\n            raise TypeError(\"non-numeric\")\n"
    "    return sum(items)\n\ndef discount(amount):\n"
    "    if amount < 0:\n        raise ValueError(\"negative\")\n"
    "    return amount * 0.9 if amount > 100 else amount\n"
)

SPLIT_MODELS = [
    "--requirements-model", BUILDER,
    "--glm-model", BUILDER,
    "--plan-reviewer-model", VERIFIER,
    "--terra-model", BUILDER,
    "--sol-model", VERIFIER,
    "--astra-model", VERIFIER,
    "--completion-model", VERIFIER,
]


def log(msg: str) -> None:
    print(msg, flush=True)


def new_repo(name: str) -> Path:
    repo = WORKDIR / name
    if repo.exists():
        subprocess.run(["rm", "-rf", str(repo)], check=False)
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "live@example.test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Live"], check=True)
    (repo / "README.md").write_text("# cart\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_cart.py").write_text(SHALLOW)
    (repo / "cart.py").write_text(CORRECT)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)
    return repo


def load_state(run_dir: Path) -> dict:
    p = run_dir / "state.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def discover_run(project: Path) -> Path | None:
    runs = project / ".autocode" / "runs"
    if not runs.is_dir():
        return None
    c = sorted((p for p in runs.iterdir() if (p / "state.json").is_file()),
               key=lambda p: p.stat().st_mtime)
    return c[-1] if c else None


def attempt_id_for(state: dict, stage_prefix: str) -> str | None:
    active = state.get("active_stage") or {}
    out = Path(active.get("output") or "")
    if out.name:
        stem = out.name.replace(".json", "")
        parent = out.parent.name
        if parent.isdigit():
            return f"{parent}/{stem}"
        m = re.search(rf"({stage_prefix}-\d+)", stem)
        if m:
            return f"001/{m.group(1)}"
    for s in reversed(state.get("stages") or []):
        if str(s.get("stage", "")).startswith(stage_prefix):
            out = Path(s.get("output") or "")
            stem = out.name.replace(".json", "")
            m = re.search(rf"({stage_prefix}-\d+)", stem)
            if m:
                return f"001/{m.group(1)}"
    return None


def call(cmd: list[str], env: dict, timeout: int) -> int:
    log("  $ " + " ".join(cmd[:10]) + f" ... t={timeout}")
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("  TIMEOUT")
        return 124
    log(f"  exit={p.returncode}")
    if p.returncode not in (0, 2):
        tail = (p.stderr or p.stdout or "")[-350:]
        log("  " + tail.replace("\n", "\n  "))
    return p.returncode


def base_cmd(project: Path, run_dir: Path | None) -> list[str]:
    cmd = [str(AC), "--workspace", str(project), "--no-chat"]
    if run_dir is not None:
        cmd += ["--run-dir", str(run_dir)]
    return cmd


def drive(project: Path, env: dict, max_steps: int = 40) -> Path | None:
    cmd = base_cmd(project, None) + [
        SPEC_BRIEF, "--in-place",
        "--provider", "opencode", "--joint-planning",
        *SPLIT_MODELS,
        "--max-iterations", "5",
        "--max-idle-seconds", "600",
        "--max-tool-seconds", "900",
    ]
    call(cmd, env, timeout=1800)

    run_dir = discover_run(project)
    if run_dir is None:
        log("  no run dir")
        return None
    log(f"  run_dir={run_dir.name}")

    for step in range(max_steps):
        d = load_state(run_dir)
        status = d.get("status") or ""
        phase = d.get("phase") or ""
        log(f"[{step}] status={status!r} phase={phase!r} next={d.get('next_stage')!r}")
        if phase == "COMPLETE" or status in ("TASK_COMPLETE", "COMPLETE"):
            log("  TERMINAL")
            break

        # --- pause recovery -------------------------------------------
        if status == "PAUSED_CROSS_MODEL":
            log("  recover CROSS_MODEL with split routes")
            call(base_cmd(project, run_dir) + ["--resume-paused", *SPLIT_MODELS], env, 1800)
            continue
        if status == "PAUSED_PROVIDER_UNCERTAIN":
            att = attempt_id_for(d, "astra") or attempt_id_for(d, "sol") or "001/astra_discovery-01"
            log(f"  recover PROVIDER_UNCERTAIN via --abandon-stage {att}")
            call(base_cmd(project, run_dir) + ["--abandon-stage", att], env, 180)
            call(base_cmd(project, run_dir) + ["--resume-paused", *SPLIT_MODELS], env, 1800)
            continue
        if status == "PAUSED_STALE_VALIDATION":
            att = attempt_id_for(d, "sol") or "001/sol-01"
            log(f"  recover STALE_VALIDATION via --abandon-stage {att}")
            call(base_cmd(project, run_dir) + ["--abandon-stage", att], env, 180)
            call(base_cmd(project, run_dir) + ["--resume-paused", "--max-iterations", "5", *SPLIT_MODELS], env, 1800)
            continue
        if status == "PAUSED_ITERATION_LIMIT":
            log("  recover ITERATION_LIMIT")
            call(base_cmd(project, run_dir) + ["--resume-paused", "--max-iterations", "5", *SPLIT_MODELS], env, 1800)
            continue
        if status == "PAUSED_STAGE_ABANDONED":
            call(base_cmd(project, run_dir) + ["--resume-paused", *SPLIT_MODELS], env, 1800)
            continue
        if status == "AWAITING_GOAL_APPROVAL":
            token = d.get("displayed_goal") or ""
            if not token:
                log("  no goal token")
                break
            rc = call(base_cmd(project, run_dir) + ["--approve-goal", token, *SPLIT_MODELS], env, 1800)
            if rc not in (0, 2):
                log("  approve-goal unexpected rc")
            # If still unapproved, send planning feedback about milestone shape.
            d2 = load_state(run_dir)
            if d2.get("status") == "AWAITING_GOAL_APPROVAL" or d2.get("status") == "PAUSED_GOAL_UNAPPROVED":
                log("  approve rejected — sending milestone-shape feedback")
                call(base_cmd(project, run_dir) + [
                    "--feedback",
                    "Every implement/validate task needs a named milestone_id "
                    "(e.g. M1) and explicit affected_paths (e.g. [\"cart.py\"]). "
                    "Resubmit the brief with that structure.",
                    *SPLIT_MODELS,
                ], env, 600)
            continue

        if status == "PAUSED_GOAL_UNAPPROVED":
            # Recover the token via --show-goal, then approve.
            try:
                p = subprocess.run(base_cmd(project, run_dir) + ["--show-goal"],
                                   env=env, capture_output=True, text=True, timeout=120)
                show = p.stdout + p.stderr
            except subprocess.TimeoutExpired:
                show = ""
            m = re.search(r"\b(r\d+:[0-9a-f]{16,})", show)
            token = (d.get("displayed_goal") or "") or (m.group(1) if m else "")
            log(f"  GOAL_UNAPPROVED token={token[:40]!r}")
            if token:
                call(base_cmd(project, run_dir) + ["--approve-goal", token, *SPLIT_MODELS], env, 1800)
            else:
                call(base_cmd(project, run_dir) + [
                    "--feedback",
                    "Resubmit: every task needs a named milestone_id and explicit "
                    "affected_paths so the brief can be approved.",
                    *SPLIT_MODELS,
                ], env, 600)
            continue

        qs = d.get("pending_questions") or []
        if qs and status in ("DISCOVERING", "RUNNING", "WAITING_FOR_USER", "EXECUTING"):
            for q in qs:
                qid = q.get("id") or "Q1"
                opts = q.get("options") or []
                default = q.get("proposed_default") or (opts[0] if opts else "no")
                call(base_cmd(project, run_dir) + ["--answer", f"{qid}={default}", *SPLIT_MODELS], env, 600)
            continue

        if status.startswith("PAUSED_"):
            call(base_cmd(project, run_dir) + ["--resume-paused", "--max-iterations", "5", *SPLIT_MODELS], env, 1800)
            continue

        call(base_cmd(project, run_dir) + ["--max-iterations", "5", *SPLIT_MODELS], env, 1800)
    return run_dir


def score(run_dir: Path, project: Path, name: str, symptom: str, code: str) -> dict:
    d = load_state(run_dir)
    v = d.get("validation") or {}
    verdict = str(v.get("verdict") or "")
    findings = [str(f.get("finding") or "") for f in v.get("findings") or []]
    blob_state = "\n".join(findings)

    # Live Validator work product (written despite the "no workspace writes"
    # instruction when the plan required it; treat as primary evidence).
    md = ""
    for cand in (project / "VALIDATION.md", project / "validation.md"):
        if cand.is_file():
            md += cand.read_text() + "\n"

    # Prefer structured findings; fall back to the report text.
    blob = blob_state + "\n" + md
    cart = (project / "cart.py").read_text() if (project / "cart.py").is_file() else ""
    mutant_present = code.strip()[:50] in cart or cart.strip() == code.strip()

    claimed_complete = d.get("phase") == "COMPLETE" or d.get("status") in ("TASK_COMPLETE", "COMPLETE")
    rejected = verdict == "FAIL" or (not claimed_complete and (
        "UNMET" in md or re.search(r"unmet|not (hold|met)|failed|defect|wrong", md, re.I)))

    diagnosed = bool(re.search(symptom, blob, re.I))

    if claimed_complete and verdict != "FAIL" and mutant_present:
        result = "MISSED"
    elif not mutant_present and not findings and not md:
        result = "NO-VALIDATION"
    elif rejected and diagnosed:
        result = "CAUGHT+DIAGNOSED"
    elif rejected:
        result = "CAUGHT-VAGUE"
    elif claimed_complete:
        result = "MISSED"
    else:
        result = "NO-VALIDATION"

    return {
        "name": name,
        "result": result,
        "status": d.get("status"),
        "phase": d.get("phase"),
        "verdict": verdict,
        "findings": findings,
        "report_excerpt": md[:2500],
        "mutant_present": mutant_present,
        "claimed_complete": claimed_complete,
        "diagnosed": diagnosed,
    }


def main() -> int:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["AUTOCODE_HOME"] = str(WORKDIR / "registry")
    env["AUTOCODE_LIVE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep) if "fixture-bin" not in p
    )
    log(f"builder={BUILDER} verifier={VERIFIER} workdir={WORKDIR}")

    only = os.environ.get("ONLY_NAME", "")
    rows = []
    for name, (bug, symptom, code) in IMPL.items():
        if only and name != only:
            continue
        log(f"\n======== {name}: {bug} ========")
        repo = new_repo(name)
        (repo / "cart.py").write_text(code)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", f"mutant {name}"], check=True)
        run_dir = drive(repo, env)
        if run_dir is None:
            rows.append({"name": name, "result": "NO-RUN"})
            continue
        s = score(run_dir, repo, name, symptom, code)
        log(f"  => {s['result']}  verdict={s['verdict']!r} phase={s['phase']!r} "
            f"mutant_present={s['mutant_present']} diagnosed={s['diagnosed']}")
        if s["report_excerpt"]:
            for line in s["report_excerpt"].splitlines()[:8]:
                if line.strip():
                    log("    " + line[:160])
        rows.append(s)
        (WORKDIR / f"{name}-result.json").write_text(json.dumps(s, indent=2))

    if not only:
        log("\n======== control (correct code) ========")
        repo = new_repo("control")
        run_dir = drive(repo, env)
        if run_dir:
            s = score(run_dir, repo, "control", r"unmet|defect|wrong", CORRECT)
            ok = s["verdict"] != "FAIL" and not s["diagnosed"]
            rows.append({"name": "control", "result": "ACCEPTED-OK" if ok else "FALSE-ALARM",
                         **{k: v for k, v in s.items() if k != "result"}})
            (WORKDIR / "control-result.json").write_text(json.dumps(rows[-1], indent=2))

    log("\n======== WORKFLOW SUMMARY ========")
    for r in rows:
        log(f"  {r['name']:22s} {r.get('result'):18s} verdict={r.get('verdict')!r} "
            f"phase={r.get('phase')!r} diagnosed={r.get('diagnosed')}")
    (WORKDIR / "workflow-summary.json").write_text(json.dumps(rows, indent=2))
    log(f"wrote {WORKDIR}/workflow-summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
