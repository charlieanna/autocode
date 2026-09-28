"""Runner-owned regression proof for bug-fix runs.

When the approved contract's ``task_kind`` is ``"bugfix"``, the runner proves the
fix itself, with no model call, right before the Validator runs: the candidate's
new or changed tests must fail on the run's base commit and pass on the current
source, and no test that passed on base may fail now (``autocode_verify``). The
proof is bound to the exact source revision. The Validator and the Completion
Owner receive it as evidence, and the completion gate refuses a bug fix whose
current source has no passing proof. Every workflow stage still runs.

When the Investigator wrote the regression tests in plain English (its
``test_cases``), the proof also requires each case to have its own test, named
after the case id, among the tests that fail on base and pass now
(``case_tests``, from autocode_test_cases.match_cases).

A small feature gets the same proof when its approved one-milestone plan marks
acceptance criteria as tests (``verification_method: "test: test_c2_..."``,
autocode_test_cases.contract_cases): each such test must pass with the change
and must not have passed without it (verify's ``new_behavior``).
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

try:
    from . import autocode_support as support, autocode_goals as goals, autocode_verify as verify
    from . import autocode_workspaces as workspaces
    from . import autocode_bug_job as bug_job, autocode_test_cases as test_cases
except ImportError:
    import autocode_bug_job as bug_job
    import autocode_test_cases as test_cases
    import autocode_support as support
    import autocode_goals as goals
    import autocode_verify as verify
    import autocode_workspaces as workspaces

STAGE = "regression_proof"
SUMMARY_KEYS = ("verdict", "failures", "unverified", "notes", "review_reasons", "fail_to_pass", "commands",
                "base", "source_revision", "test_files", "source_files", "case_tests")


def required(state):
    return goals.task_kind(state) == "bugfix" or bool(test_cases.contract_cases(state))


def cases(state):
    """The English cases this proof must cover: the bug's diagnosis, else the plan's test criteria."""
    return bug_job.test_cases(state) or test_cases.contract_cases(state)


def base_commit(state, workspace):
    """The revision the run started from: saved at run creation, else the task worktree's base."""
    if state.get("base_commit"):
        return state["base_commit"]
    try:
        return (workspaces.metadata(workspace) or {}).get("base_commit")
    except ValueError:
        return None


def head(workspace):
    result = subprocess.run(["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def settings(state):
    return state.get("settings", {}).get("regression") or {}


def _baseline(state, workspace, run_dir, base, framework, suite, dependencies):
    cached = state.get("regression_baseline") or {}
    if cached.get("base") == base and cached.get("command") == suite and Path(cached.get("path", "")).is_file():
        return support.read(cached["path"])
    result = verify.baseline(workspace, base, Path(run_dir) / "regression", framework=framework,
                             suite_command=suite, dependencies_from=dependencies,
                             timeout=settings(state).get("test_timeout", verify.DEFAULT_TIMEOUT))
    path = Path(run_dir) / "regression" / "baseline.json"
    support.atomic_json(path, result)
    state["regression_baseline"] = {"base": base, "command": suite, "path": str(path), "health": result["health"]}
    return result


def prove(state, workspace, run_dir):
    """Run (or reuse) the proof for the current source; return its summary. Launches no model."""
    workspace = Path(workspace)
    current = support.snapshot(workspace)["revision"]
    saved = state.get("regression_proof") or {}
    if saved.get("source_revision") == current:
        return saved
    started = time.monotonic()
    base = base_commit(state, workspace)
    options = settings(state)
    if not base:
        proof = {"verdict": verify.UNVERIFIED, "failures": [], "notes": [], "review_reasons": [],
                 "unverified": ["No base commit is recorded for this run, so the fix cannot be compared "
                                "with the original code"], "fail_to_pass": None, "commands": {},
                 "base": None, "source_revision": current, "test_files": [], "source_files": []}
        path = None
    else:
        dependencies = state.get("project_workspace") or str(workspace)
        # A task worktree has no virtualenv of its own; use the project's.
        framework = verify.detect_framework(workspace, python=options.get("python")
                                            or verify.python_for(dependencies))
        suite = options.get("test_command") or (framework.suite if framework else None)
        base_suite = _baseline(state, workspace, run_dir, base, framework, suite, dependencies) if suite else None
        number = len(state.get("regression_proofs", [])) + 1
        out = Path(run_dir) / "regression" / f"proof-{number:02d}"
        result = verify.verify(workspace, base, out, framework=framework, suite_command=options.get("test_command"),
                               regression_command=options.get("regression_command"),
                               reported=None, base_suite=base_suite, dependencies_from=dependencies,
                               timeout=options.get("test_timeout", verify.DEFAULT_TIMEOUT),
                               new_behavior=goals.task_kind(state) != "bugfix")
        path = out / "verification.json"
        support.atomic_json(path, result)
        proof = {key: result.get(key) for key in SUMMARY_KEYS}
        check_cases(proof, cases(state))
        proof["checks"] = {label: {"command": receipt["command"], "exit_code": receipt["exit_code"],
                                   "timed_out": receipt["timed_out"], "output": receipt["output"]}
                           for label, receipt in result["checks"].items()}
    proof.update(path=str(path) if path else None, proved_at=support.now(),
                 duration_seconds=round(time.monotonic() - started, 1))
    state["regression_proof"] = proof
    state.setdefault("regression_proofs", []).append(
        {k: proof.get(k) for k in ("verdict", "source_revision", "path", "proved_at", "duration_seconds")})
    # A visible, runner-owned step in the stage history: no provider, no tokens.
    record = {"stage": STAGE, "role": "runner", "iteration": state.get("iteration"), "finished_at": support.now(),
              "runner_owned": True, "engine": "runner", "duration_seconds": proof["duration_seconds"],
              "metrics": {"provider_tokens": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                                              "reasoning_output_tokens": 0}},
              "summary": f"Regression proof {proof['verdict']}", "output": proof["path"]}
    state.setdefault("stages", []).append(record)
    state.setdefault("history", []).append(record)
    print(f"{STAGE}: {proof['verdict']}" + "".join(f"\n  - {r}" for r in proof["failures"] + proof["unverified"]),
          flush=True)
    return proof


def check_cases(proof, cases):
    """Each English test case needs a test named after it that fails on base and passes now."""
    if not cases:
        return
    if proof.get("fail_to_pass") is None:
        # No fail-to-pass list: either the proof already failed for another reason, or the
        # runner reports exit codes only, which cannot tell which test proves which case.
        proof["case_tests"] = {case["id"]: [] for case in cases}
        if proof["verdict"] == verify.PASS:
            proof["unverified"] = list(proof.get("unverified") or []) + [
                "The English test cases could not be matched to tests: the test run reported no per-test results"]
            proof["verdict"] = verify.UNVERIFIED
        return
    proof["case_tests"] = test_cases.match_cases(cases, proof["fail_to_pass"])
    missing = [case for case in cases if not proof["case_tests"][case["id"]]]
    if missing:
        proof["failures"] = list(proof.get("failures") or []) + [
            f"Test case {test_cases.case_text(case)} has no test named {test_cases.case_test_name(case['id'])} "
            "that passes with the change and did not pass without it" for case in missing]
        proof["verdict"] = verify.FAIL


def before_review(state, stage, workspace, run_dir):
    """Called by both dispatch paths just before the Validator (or combined checkpoint) runs."""
    if stage in ("sol", "astra_checkpoint") and required(state):
        prove(state, workspace, run_dir)


def complete(state, current_revision):
    """True when the run needs no proof, or has a passing proof for exactly this source."""
    if not required(state):
        return True
    proof = state.get("regression_proof") or {}
    return proof.get("verdict") == verify.PASS and proof.get("source_revision") == current_revision


def handoff(state):
    """What the Validator and Completion Owner see: the runner's executed result, not a claim."""
    proof = state.get("regression_proof")
    if not required(state) or not proof:
        return None
    return {**{k: proof.get(k) for k in SUMMARY_KEYS}, "checks": proof.get("checks", {}), "path": proof.get("path"),
            "meaning": "Executed by the runner in clean worktrees against this run's base commit: the new or "
                       "changed tests must fail on the original code and pass on the current code, and no test "
                       "that passed on base may fail. PASS is required before a bug fix can complete."}
