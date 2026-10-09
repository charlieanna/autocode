#!/usr/bin/env python3
"""Live mutation driver: score one cart mutant against a real model Validator.

Unlike the offline suite, this does NOT install the fixture provider. The
Builder and Validator are live model sessions. Human gates are driven from
saved state (``--answer``, ``--approve-goal``) the same way live_trial.py does.

The experiment: pre-seed a mutant cart.py and a shallow green test suite, then
ask the workflow to validate it. Score whether the *Validator's own findings*
name the defect — not whether the Builder happened to fix the code.

Usage:
    python3 live_mutant.py --name offbyone_empty --code-file mutant.py \\
        --mode behavior --profile default --workdir /tmp/live-mut
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

AUTOCODE = Path.home() / ".local" / "bin" / "autocode"
if not AUTOCODE.is_file():
    AUTOCODE = REPO_ROOT / "tools" / "autocode.py"

SPEC = """# Shopping cart

cart.py is under review. Independently validate the existing implementation.

Spec:
- total(items) -- sum a list of numeric prices.
  - total([]) must return 0
  - total([1,2,3]) must return 6
  - a non-numeric entry must raise TypeError (not be swallowed)
- discount(amount) -- 10% off orders over 100.
  - discount(100) must return 100 (boundary is exclusive: over 100)
  - discount(101) must return 90.9
  - negative amounts must raise ValueError

Constraints:
- Do not modify cart.py or tests/. This is a validation of existing code.
- Write a validation report with findings that name any unmet criterion.

Acceptance: every spec clause above is proven or explicitly flagged unproven.
"""

SHALLOW_TESTS = """from cart import total, discount

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

STRONG_TESTS = """import pytest
from cart import total, discount

def test_total_empty():
    assert total([]) == 0

def test_total_sums():
    assert total([1, 2, 3]) == 6

def test_total_non_numeric_raises():
    with pytest.raises(TypeError):
        total([1, "2", 3])

def test_discount_below_threshold():
    assert discount(50) == 50

def test_discount_at_boundary():
    assert discount(100) == 100

def test_discount_above_threshold():
    assert discount(101) == 90.9

def test_discount_negative_raises():
    with pytest.raises(ValueError):
        discount(-1)
"""

CORRECT = """def total(items):
    for i in items:
        if not isinstance(i, (int, float)):
            raise TypeError("non-numeric")
    return sum(items)

def discount(amount):
    if amount < 0:
        raise ValueError("negative")
    return amount * 0.9 if amount > 100 else amount
"""

# Implementation mutants (same as scenario-09).
MUTANTS = {
    "offbyone_empty": (
        "total([]) returns 1 instead of 0",
        r"empty|off.by.one|sum\(items, *[1-9]|default|total\(\[\]\)|returns? 1",
        "def total(items):\n    return sum(items, 1)\n\ndef discount(amount):\n"
        "    if amount < 0:\n        raise ValueError(\"negative\")\n"
        "    return amount * 0.9 if amount > 100 else amount\n",
    ),
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

# Test-suite mutants for scenario-11 (code stays CORRECT; tests weaken).
# name -> (description, regex-or-marker gap expectation, transform)
def _drop(src: str, name: str) -> str:
    return re.sub(r"\n?def " + re.escape(name) + r"\(.*?(?=\ndef |\Z)", "\n", src, flags=re.S)

def _rewrite(src: str, name: str, body: str) -> str:
    return re.sub(
        r"(\ndef " + re.escape(name) + r"\(.*?:\n)(.*?)(?=\ndef |\Z)",
        lambda m: m.group(1) + body, src, flags=re.S,
    )

def _keep_only(src: str, name: str) -> str:
    for h in re.findall(r"\ndef (\w+)\(", src):
        if h != name:
            src = _drop(src, h)
    return src

TEST_MUTANTS = {
    "drop_empty_case": ("deleted test_total_empty", {"R1"}, lambda s: _drop(s, "test_total_empty")),
    "drop_boundary_case": ("deleted test_discount_at_boundary", {"R4"}, lambda s: _drop(s, "test_discount_at_boundary")),
    "drop_negative_case": ("deleted test_discount_negative_raises", {"R6"}, lambda s: _drop(s, "test_discount_negative_raises")),
    "weaken_type_check": ("TypeError check replaced with a plain call", {"R3"},
                          lambda s: _rewrite(s, "test_total_non_numeric_raises", "    total([1, 2, 3])\n")),
    "weaken_assertion": ("assert discount(101)==90.9 -> is not None", {"R5"},
                         lambda s: _rewrite(s, "test_discount_above_threshold", "    assert discount(101) is not None\n")),
    "vacuous_placeholder": ("test body replaced with assert True", {"R1"},
                            lambda s: _rewrite(s, "test_total_empty", "    assert True\n")),
    "keep_only_one": ("all tests dropped except test_total_sums",
                      {"R1", "R3", "R4", "R5", "R6"},
                      lambda s: _keep_only(s, "test_total_sums")),
}

REQ_HINT = {
    "R1": r"empty|total\(\[\]\)|sum.*default",
    "R2": r"1, *2, *3|sums",
    "R3": r"TypeError|non.numeric",
    "R4": r"boundar|exactly 100|discount\(100",
    "R5": r"90\.9|discount\(101|round|precision|above threshold",
    "R6": r"ValueError|negative",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def new_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "live@example.test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "LiveMutant"], check=True)
    (repo / "README.md").write_text(f"# {name}\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def commit_tree(repo: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=False)


def load_state(run_dir: Path) -> dict:
    path = run_dir / "state.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def discover_run(project: Path) -> Path | None:
    runs = project / ".autocode" / "runs"
    if not runs.is_dir():
        return None
    candidates = sorted(
        (p for p in runs.iterdir() if (p / "state.json").is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def invoke(cmd: list[str], env: dict, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)


def drive(project: Path, env: dict, workdir: Path, tag: str,
          extra: list[str], profile: dict, max_steps: int = 24) -> Path | None:
    """Start a run and serve human gates until terminal/blocked."""
    task = SPEC
    cmd = [str(AUTOCODE), task, "--workspace", str(project), "--in-place",
           "--no-chat", "--max-iterations", "1"] + extra
    if profile.get("provider") and profile.get("provider") != "fixture":
        cmd += ["--provider", profile["provider"]]
    if profile.get("joint_planning", True):
        cmd += ["--joint-planning"]
    log(f"  $ {' '.join(cmd[:8])} ... ({len(cmd)} argv)")
    try:
        proc = invoke(cmd, env, timeout=600)
    except subprocess.TimeoutExpired:
        log("  start timed out")
        return None
    log(f"  start exit={proc.returncode}")
    (workdir / f"{tag}-start.log").write_text(proc.stdout + "\n--- STDERR ---\n" + proc.stderr)

    run_dir = discover_run(project)
    if run_dir is None:
        log("  no run dir created")
        return None

    for step in range(max_steps):
        state = load_state(run_dir)
        status = state.get("status", "")
        phase = state.get("phase", "")
        log(f"  [{step}] status={status!r} phase={phase!r} next={state.get('next_stage')!r}")
        if status in ("TASK_COMPLETE", "COMPLETE") or phase == "COMPLETE":
            break
        if status.startswith("PAUSED_") and status not in (
            "PAUSED_REQUESTED", "PAUSED_WORKSPACE_BUSY",
        ):
            # Terminal-ish pause (budget, iteration limit, repeated failure).
            if status.startswith("PAUSED_ITERATION") or status.startswith("PAUSED_REPEATED") \
               or status.startswith("PAUSED_BUDGET") or status.startswith("PAUSED_BUILDER") \
               or status.startswith("PAUSED_INVALID") or status.startswith("PAUSED_WORKSPACE"):
                break

        served = False
        if status == "AWAITING_GOAL_APPROVAL":
            token = state.get("displayed_goal") or ""
            if token:
                proc = invoke(
                    [str(AUTOCODE), "--workspace", str(project), "--run-dir", str(run_dir),
                     "--approve-goal", token],
                    env, timeout=600)
                log(f"  approve-goal exit={proc.returncode}")
                (workdir / f"{tag}-approve-{step}.log").write_text(proc.stdout + proc.stderr)
                served = True
        questions = state.get("pending_questions") or []
        if questions and status in ("DISCOVERING", "RUNNING", "WAITING_FOR_USER", "EXECUTING"):
            for q in questions:
                qid = q.get("id") or "Q1"
                options = q.get("options") or []
                default = q.get("proposed_default") or (options[0] if options else "no")
                proc = invoke(
                    [str(AUTOCODE), "--workspace", str(project), "--run-dir", str(run_dir),
                     "--answer", f"{qid}={default}"],
                    env, timeout=600)
                log(f"  answer {qid}={default!r} exit={proc.returncode}")
                (workdir / f"{tag}-answer-{qid}-{step}.log").write_text(proc.stdout + proc.stderr)
            served = True
        if not served:
            proc = invoke(
                [str(AUTOCODE), "--workspace", str(project), "--run-dir", str(run_dir)],
                env, timeout=600)
            log(f"  resume exit={proc.returncode}")
            (workdir / f"{tag}-resume-{step}.log").write_text(proc.stdout + proc.stderr)
            if proc.returncode not in (0, 2):
                log(f"  resume stderr: {(proc.stderr or '')[-300:]}")
    return run_dir


def validation_findings(run_dir: Path) -> tuple[str, str, list[str]]:
    state = load_state(run_dir)
    v = state.get("validation") or {}
    verdict = str(v.get("verdict") or "")
    phase = str(state.get("phase") or state.get("status") or "")
    findings = [str(f.get("finding") or "") for f in v.get("findings") or []]
    return verdict, phase, findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--kind", choices=["impl", "tests", "control"], required=True)
    ap.add_argument("--mode", choices=["behavior", "trace", "strict"], default="behavior")
    ap.add_argument("--profile", default="default")
    ap.add_argument("--workdir", default="")
    ap.add_argument("--i-authorize-live-model-spend", action="store_true")
    args = ap.parse_args()

    if not args.i_authorize_live_model_spend:
        log("refusing live model spend without --i-authorize-live-model-spend")
        return 2

    import live_profiles as profiles
    profile = profiles.resolve(args.profile)
    overrides = profiles.cli_overrides(profile)

    workdir = Path(args.workdir or tempfile.mkdtemp(prefix="live-mut-"))
    workdir.mkdir(parents=True, exist_ok=True)
    log(f"workdir={workdir}  profile={args.profile} ({profile.get('provider')}/{profile.get('base')})")

    env = dict(os.environ)
    env["AUTOCODE_HOME"] = str(workdir / "registry")
    env["AUTOCODE_LIVE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Make sure the fixture is NOT on PATH.
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep) if "fixture-bin" not in p
    )

    repo = new_repo(workdir, f"mut-{args.name}")
    (repo / "cart.py").write_text(CORRECT)
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)

    if args.kind == "control":
        (repo / "cart.py").write_text(CORRECT)
        (tests_dir / "test_cart.py").write_text(STRONG_TESTS if args.mode != "behavior" else SHALLOW_TESTS)
    elif args.kind == "impl":
        bug, symptom, code = MUTANTS[args.name]
        (repo / "cart.py").write_text(code)
        (tests_dir / "test_cart.py").write_text(SHALLOW_TESTS)
        log(f"  mutant: {bug}")
    else:
        _desc, gaps, fn = TEST_MUTANTS[args.name]
        (repo / "cart.py").write_text(CORRECT)
        (tests_dir / "test_cart.py").write_text(fn(STRONG_TESTS))
        log(f"  test-mutant gaps={sorted(gaps)}")

    commit_tree(repo, f"seed {args.name}")

    # Sanity: shallow/strong suite must be green against the tree.
    probe = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
                           cwd=repo, capture_output=True, text=True)
    if probe.returncode != 0:
        log(f"  suite not green — invalid trap\n{probe.stdout[-300:]}")
        print(json.dumps({"name": args.name, "result": "INVALID", "detail": "suite not green"}))
        return 1
    log("  suite: GREEN (trap armed)")

    run_dir = drive(repo, env, workdir, args.name, overrides, profile)
    if run_dir is None:
        print(json.dumps({"name": args.name, "result": "NO-RUN", "detail": "no run dir"}))
        return 1

    verdict, phase, findings = validation_findings(run_dir)
    blob = "\n".join(findings)
    (workdir / f"{args.name}-findings.txt").write_text(blob)
    (workdir / f"{args.name}-state.json").write_text(
        json.dumps(load_state(run_dir), indent=2)[:200000])
    log(f"  verdict={verdict!r} phase={phase!r} findings={len(findings)}")
    for line in findings:
        log(f"    - {line[:200]}")

    claimed_complete = phase == "COMPLETE" or load_state(run_dir).get("status") in (
        "TASK_COMPLETE", "COMPLETE")
    rejected = verdict == "FAIL" or (not claimed_complete and verdict != "PASS")

    result = "MISSED"
    diagnosed = False
    if args.kind == "control":
        result = "ACCEPTED-OK" if (verdict == "PASS" or claimed_complete) and verdict != "FAIL" \
            else "FALSE-ALARM"
    elif args.kind == "impl":
        _, symptom, _ = MUTANTS[args.name]
        if claimed_complete and verdict != "FAIL":
            result = "MISSED"
        elif rejected:
            diagnosed = bool(re.search(symptom, blob, re.I))
            result = "CAUGHT+DIAGNOSED" if diagnosed else "CAUGHT-VAGUE"
        else:
            result = "MISSED"
    else:
        _desc, gaps, _ = TEST_MUTANTS[args.name]
        if claimed_complete and verdict != "FAIL":
            result = "MISSED"
        elif not rejected:
            result = "MISSED"
        else:
            named = [g for g in sorted(gaps) if re.search(REQ_HINT[g], blob, re.I)]
            if not named:
                result = "REJECT-VAGUE"
            elif set(named) >= set(gaps):
                result = "CAUGHT+PRECISE"
            else:
                result = "CAUGHT-PARTIAL"

    row = {
        "name": args.name,
        "kind": args.kind,
        "result": result,
        "verdict": verdict,
        "phase": phase,
        "findings": findings,
        "claimed_complete": claimed_complete,
        "workdir": str(workdir),
    }
    print(json.dumps(row, indent=2))
    (workdir / f"{args.name}-score.json").write_text(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
