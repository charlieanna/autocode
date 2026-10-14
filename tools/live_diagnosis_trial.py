#!/usr/bin/env python3
"""Live-model validation trial for the astra_diagnose operational-recovery route.

This is the deferred "live validation" step from issue #61's AutoResolver
recovery plan: at least one real model diagnosis, on a genuine failure, with
an independently verified outcome. It requires real provider credentials
this session did not have, so it is written to be run later -- on a machine
with ``opencode`` installed and authenticated -- rather than run here.

What it proves, and what it does not
-------------------------------------
The trial seeds one small, clearly labeled defective candidate (a real bug,
with a regression test proving the seed fails and the reference fix passes),
drives it through the real ``autocode`` CLI with a real model profile, and:

1. Lets the Builder make a genuine first attempt. If that attempt's report is
   accepted, astra_diagnose is never reached -- this is recorded as
   ``NOT_EXERCISED``, per the plan's own allowance ("if it never reaches
   AutoResolver, record NOT_EXERCISED... rather than counting it as resolver
   success or forcing a model to fail"). The trial does not manufacture a
   rejection to force the route; a well-formed first attempt is a valid,
   if unexercising, outcome.
2. If the first attempt's report is genuinely rejected (real evidence, a
   real defect in the report itself -- e.g. a bad evidence citation -- not
   fabricated), the trial captures that real record, then MECHANICALLY
   raises its repeat count to the policy's 3-occurrence threshold using that
   same real evidence. This step makes no model call and is logged plainly
   as bookkeeping, not as three organic live failures: forcing a real,
   well-behaved model to fail identically three times in a row is neither
   reliable nor useful (a first-attempt success or a differently-worded
   second failure would prove nothing about the diagnosis itself). What the
   trial actually puts under live test is the diagnosis call and the
   verified retry, both genuine.
3. Runs ``--resume-paused --diagnose-failed-stage`` for real: astra_diagnose
   receives the real rejected report and error, and a real model returns a
   diagnosis and a bounded retry-or-escalate recommendation.
4. On "retry": resumes normally, so the real Builder gets a real second
   attempt (its guidance is recorded only with the policy decision), and the real Reviewer/Validator
   independently check it. The trial records the actually-observed verdict;
   it does not accept the runner's own completion claim (see ``judge``).
   On "escalate": records that outcome. A correct escalate on a genuinely
   ambiguous defect is a valid, scoreable result, not a failure of the trial.

Diagnosis-quality scoring is deliberately not automatic. The evidence bundle
separates the seeded implementation bug from the actual diagnostic target:
the observed Builder report rejection. A human must compare that rejection's
evidence with the diagnosis. The trial checks persisted policy acceptance and
retry dispatch, and independently grades delivered conversion results.
Neither a working program nor NOT_EXERCISED is a diagnosis-quality PASS.

Usage
-----
    # Offline, free: proves the harness itself, and the negative controls
    # (see test_live_diagnosis_trial.py for the same controls as unit tests).
    python3 tools/live_diagnosis_trial.py --profile fixture

    # On a machine with opencode installed and `opencode auth login` done,
    # using the profile the issue's plan names:
    python3 tools/live_diagnosis_trial.py --profile glm53-openai \\
        --i-authorize-live-model-spend

Read the printed evidence directory afterward; ``diagnosis-comparison.json``
separates the actual report-rejection evidence from the seeded code defect.

Note on ``--profile fixture``: the scripted fixture provider is written for
the LIVE-01..LIVE-05 task-type scenarios, not this trial's celsius task, and
its scripted planning responses do not reproduce a genuine terra (Builder)
rejection for this task. Running it here is expected to print
``astra_diagnose: NOT_EXERCISED`` -- that proves the driving, escalation and
reporting code runs cleanly end to end and correctly declines to
misattribute a non-target pause, not that the fixture organically reaches
astra_diagnose. The admission -> astra_diagnose -> retry mechanic itself is
already proven offline, through the real CLI, by
tools/test_resolver_runtime.py's OperationalDiagnosisTests. Only a live
profile puts a real model in front of the actual report-rejection evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import autocode_grader_process as grader_process  # noqa: E402
import live_profiles as profiles  # noqa: E402
import live_trial as base  # noqa: E402
from autopilot_testkit import Bundle, source_revision  # noqa: E402

TrialError = base.TrialError

# --- the seeded defect ----------------------------------------------------
# A real, small, single-cause bug: the additive constant is 31, not 32. The
# test asserts both the freezing and boiling points, so a partial/incorrect
# fix (e.g. patching only one call site) still fails independent validation.
SEED_MODULE = '''"""Temperature conversion. Seeded defect: see convert() below."""


def celsius_to_fahrenheit(celsius):
    return celsius * 9 / 5 + 31  # BUG: should be + 32
'''

SEED_TEST = '''"""Regression tests: stdlib only, run as `python3 test_convert.py`.

The trial independently asserts numeric results in its parent process.
This candidate-visible test's output is never grading authority.
"""
import sys
import unittest

EXPECTED_TEST_COUNT = 3


class ConversionTests(unittest.TestCase):
    def test_freezing_point(self):
        from convert import celsius_to_fahrenheit
        self.assertEqual(32, celsius_to_fahrenheit(0))

    def test_boiling_point(self):
        from convert import celsius_to_fahrenheit
        self.assertEqual(212, celsius_to_fahrenheit(100))

    def test_body_temperature(self):
        from convert import celsius_to_fahrenheit
        self.assertAlmostEqual(98.6, celsius_to_fahrenheit(37), places=2)


if __name__ == "__main__":
    _suite = unittest.TestLoader().loadTestsFromTestCase(ConversionTests)
    _result = unittest.TextTestRunner(verbosity=0).run(_suite)
    _ok = (_result.wasSuccessful() and not _result.skipped
           and _result.testsRun == EXPECTED_TEST_COUNT)
    sys.exit(0 if _ok else 1)
'''

REFERENCE_MODULE = '''"""Temperature conversion."""


def celsius_to_fahrenheit(celsius):
    return celsius * 9 / 5 + 32
'''

GROUND_TRUTH = (
    "The additive constant in celsius_to_fahrenheit is 31 instead of 32 "
    "(a single-character typo: '+ 31' should read '+ 32'). All three "
    "conversions are off by exactly one degree Fahrenheit as a result."
)

TASK = (
    "Fix the bug in celsius_to_fahrenheit inside convert.py so that "
    "celsius_to_fahrenheit(0) == 32, celsius_to_fahrenheit(100) == 212, and "
    "celsius_to_fahrenheit(37) is within 0.01 of 98.6. Do not change "
    "test_convert.py or the function's name or signature. Python standard "
    "library only."
)


def prove_seed_and_reference(bundle: Bundle, deadline: float | None = None) -> None:
    """Regression proof, run before any model is involved: the seed fails
    its own test and the reference fix passes it -- exactly the labeled,
    reproducible defect the plan requires, not a claim taken on faith."""
    with tempfile.TemporaryDirectory(prefix="diagnosis-trial-proof-") as tmp:
        root = Path(tmp)
        (root / "convert.py").write_text(SEED_MODULE)
        (root / "test_convert.py").write_text(SEED_TEST)
        frozen = root / "frozen_test.py"
        frozen.write_text(SEED_TEST)
        seed_result = judge_final_verdict(root, root, frozen, deadline)
        (root / "convert.py").write_text(REFERENCE_MODULE)
        reference_result = judge_final_verdict(root, root, frozen, deadline)
    bundle.log("regression_proof", seed=seed_result, reference=reference_result)
    if verdict_result(seed_result) != "FAIL":
        raise TrialError("seeded defect does not actually fail its own test; fix the fixture")
    if verdict_result(reference_result) != "PASS":
        raise TrialError("reference fix does not pass the seed's own test; fix the fixture")


# --- driving the real CLI to a genuine first Builder verdict --------------


def output_text(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")


class TrialBudget:
    """One invocation count and absolute deadline, including failed calls."""

    def __init__(self, stages: int, deadline: float):
        self.stages = stages
        self.deadline = deadline
        self.used = 0

    def invoke(self, kind, cmd, env, root, bundle, allow_codes=(0, 2)):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TrialError("wall-clock budget exceeded")
        if self.used >= self.stages:
            raise TrialError(f"CLI invocation budget exceeded after {self.used} steps")
        self.used += 1
        bundle.log("cli_step", kind=kind, cmd=cmd, invocation=self.used)
        try:
            proc = base.invoke(cmd, env, root, remaining)
        except subprocess.TimeoutExpired as error:
            bundle.log(
                "cli_timeout",
                kind=kind,
                invocation=self.used,
                stdout_tail=output_text(error.stdout)[-800:],
                stderr_tail=output_text(error.stderr)[-800:],
            )
            raise TrialError(f"{kind} exceeded the shared wall-clock budget") from error
        bundle.log(
            "cli_result",
            kind=kind,
            returncode=proc.returncode,
            stdout_tail=output_text(proc.stdout)[-800:],
            stderr_tail=output_text(proc.stderr)[-800:],
        )
        if time.monotonic() >= self.deadline:
            raise TrialError(f"{kind} exceeded the shared wall-clock budget")
        if proc.returncode not in allow_codes:
            raise TrialError(f"{kind} exited {proc.returncode}: {output_text(proc.stderr or proc.stdout)[-500:]}")
        return proc


def drive_to_first_verdict(project: Path, root: Path, profile: dict, budget: TrialBudget, bundle: Bundle) -> dict:
    """Serve ordinary gates for real until the run reaches a terminal state,
    a genuine PAUSED_REPEATED_FAILURE, or a genuine rejected Builder report
    becomes visible. Unlike live_trial.drive, this stops at the first sign
    of the condition this trial needs, rather than trying to finish the run.

    ``budget.deadline`` is one absolute ``time.monotonic()`` value shared across
    every phase of the whole trial (driving, diagnosis and retry), not a
    per-phase timeout renewed at each call: a per-call budget that keeps
    resetting can, in total, run far longer than the value the operator
    actually asked for.
    """
    env = dict(os.environ, AUTOCODE_HOME=str(root / "registry"), PYTHONDONTWRITEBYTECODE="1")
    if profile["provider"] == "fixture":
        env.update(base.install_fixture_provider(root))
        env.pop("AUTOCODE_PROVIDER", None)

    steps: list[dict] = []

    def step(kind: str, cmd: list[str], *, allow_codes=(0, 2)) -> subprocess.CompletedProcess:
        proc = budget.invoke(kind, cmd, env, root, bundle, allow_codes)
        record = {
            "kind": kind,
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout_tail": output_text(proc.stdout)[-800:],
            "stderr_tail": output_text(proc.stderr)[-800:],
        }
        steps.append(record)
        return proc

    step("start", base.autocode_command(project, profile, TASK, None, []))
    run_dir = base._discover_run_dir(project)
    if run_dir is None:
        raise TrialError("first invocation did not create a run directory")
    bundle.log("run_dir", path=str(run_dir))

    def terra_report_repair_identity(state: RunState) -> dict | None:
        """The one condition this trial's astra_diagnose route accepts: a
        report-repair-eligible identity whose original stage is terra. A
        PAUSED_REPEATED_FAILURE or rejected row from any other stage (e.g. a
        planning-stage rejection) is real, but is not this trial's target and
        must not be misreported as astra_diagnose's trigger.
        """
        pending = state.get("pending_report_repair") or {}
        original = pending.get("original")
        if original and original.get("stage") == "terra" and original.get("failure_key"):
            return original
        for row in reversed(state.get("stages", [])):
            if row.get("stage") == "terra" and row.get("failure_key"):
                return row
        return None

    while True:
        state = base.load_state(run_dir)
        status = state.get("status", "")
        bundle.state(
            f"gate.{len(steps)}",
            {k: state.get(k) for k in ("status", "phase", "next_stage", "displayed_goal", "pending_questions")},
        )

        if terra_report_repair_identity(state):
            return {"state": state, "steps": steps, "run_dir": run_dir, "outcome": "rejected"}
        if status in ("TASK_COMPLETE", "COMPLETE"):
            return {"state": state, "steps": steps, "run_dir": run_dir, "outcome": "complete"}
        if status.startswith("PAUSED_") and not (state.get("pending_questions") or state.get("user_request")):
            # A genuine pause this trial does not know how to serve further
            # (e.g. a non-terra repeated failure, or an unrelated blocker) and
            # that is not this trial's target condition. Stop cleanly rather
            # than attempt an unproductive resume.
            return {"state": state, "steps": steps, "run_dir": run_dir, "outcome": "blocked"}

        if base._serve_gate(state, run_dir, project, profile, step):
            continue

        before_state = state
        step("resume", base.autocode_command(project, profile, None, run_dir, []))
        after = base.load_state(run_dir)
        if after.get("status", "") == before_state.get("status", "") and not base._progressed(before_state, after):
            raise TrialError(f"run is stuck at {after.get('status')!r} with no gate and no progress")


def escalate_to_repeat_threshold(run_dir: Path, bundle: Bundle) -> None:
    """Mechanically raise a real rejected report's repeat count to the
    policy's 3-occurrence threshold, from the one real occurrence already
    on disk. No model call happens here; this is bookkeeping so the live
    model call under test (the diagnosis itself) can proceed on real
    evidence without gambling on three organic identical failures.
    """
    state = base.load_state(run_dir)
    pending = state.get("pending_report_repair") or {}
    original = pending.get("original")
    # Scoped to terra exactly like admit_operational_diagnosis itself: a real
    # rejection at a different stage exists, but is not this trial's target
    # and must never be mistaken for it.
    failure_key = original.get("failure_key") if original and original.get("stage") == "terra" else None
    if not failure_key:
        original = next(
            (
                row
                for row in reversed(state.get("stages", []))
                if row.get("stage") == "terra" and row.get("failure_key")
            ),
            None,
        )
        failure_key = original.get("failure_key") if original else None
    if not failure_key or failure_key not in state.get("failure_history", {}):
        raise TrialError("no real rejected Builder (terra) report with a failure_key was found to escalate")
    entry = state["failure_history"][failure_key]
    bundle.log(
        "mechanical_escalation",
        failure_key=failure_key,
        real_count_before=entry.get("count"),
        real_last_error=entry.get("last_error"),
        note="labeled repeat-count fault injection; not organic repetition; no model call",
    )
    # The repeated-failure gate reads the consecutive streak; entries saved
    # before streaks existed fall back to count. Raise both to the threshold.
    entry["count"] = entry["streak"] = 3
    state["status"] = "PAUSED_REPEATED_FAILURE"
    (run_dir / "state.json").write_text(json.dumps(state, indent=2, default=str))


def diagnose_and_retry(
    project: Path, root: Path, profile: dict, run_dir: Path, budget: TrialBudget, bundle: Bundle
) -> dict:
    """The two genuine live-model steps: admit + run astra_diagnose, then,
    on an accepted retry, let the real Builder and Reviewer finish for real.

    ``budget`` is the SAME invocation count and deadline drive_to_first_verdict was
    given, not a fresh budget: this phase spends whatever wall-clock time
    that phase left, not another full timeout on top of it.
    """
    env = dict(os.environ, AUTOCODE_HOME=str(root / "registry"), PYTHONDONTWRITEBYTECODE="1")
    if profile["provider"] == "fixture":
        env.update(base.install_fixture_provider(root))
        env.pop("AUTOCODE_PROVIDER", None)

    def invoke_cli(extra: list[str], allow_codes=(0, 2)) -> subprocess.CompletedProcess:
        cmd = base.autocode_command(project, profile, None, run_dir, extra)
        return budget.invoke("diagnose", cmd, env, root, bundle, allow_codes)

    # Admit: real dispatch path (autocode.main --resume-paused --diagnose-failed-stage).
    invoke_cli(["--resume-paused", "--diagnose-failed-stage"])
    state = base.load_state(run_dir)
    if state.get("status") not in ("RUNNING",) or state.get("next_stage") != "astra_diagnose":
        return {"admitted": False, "state": state}
    request = state.get("diagnosis_request") or {}
    previous_rows = len(state.get("stages", []))

    # The real model call: run the admitted astra_diagnose stage.
    invoke_cli([])
    state = base.load_state(run_dir)
    new_rows = state.get("stages", [])[previous_rows:]
    diagnosis_record = next((row for row in reversed(new_rows) if row.get("stage") == "astra_diagnose"), None)
    if diagnosis_record is None:
        raise TrialError("astra_diagnose did not produce a recorded stage after the model call")
    try:
        diagnosis_value = json.loads(Path(diagnosis_record["output"]).read_text())
    except (KeyError, OSError, ValueError) as error:
        raise TrialError("astra_diagnose result is missing or unreadable") from error
    if not isinstance(diagnosis_value, dict) or not isinstance(diagnosis_value.get("recommendation"), dict):
        raise TrialError("astra_diagnose result has no recommendation object")
    recommendation = diagnosis_value.get("recommendation", {})
    bundle.log("diagnosis_received", diagnosis=diagnosis_value.get("diagnosis"), recommendation=recommendation)

    result = {
        "admitted": True,
        "diagnosis": diagnosis_value.get("diagnosis"),
        "recommendation": recommendation,
        "state": state,
        "policy_accepted_retry": False,
        "retry_dispatched": False,
    }
    receipt_row: dict | None = next(
        (
            row
            for row in reversed(new_rows)
            if row.get("stage") == "resolver"
            and row.get("runner_owned")
            and row.get("receipt", {}).get("blocker_id") == request.get("blocker_id")
        ),
        {},
    )
    result["policy_receipt"] = receipt_row
    accepted = (
        receipt_row is not None
        and bool(request.get("blocker_id"))
        and request.get("original_stage") == "terra"
        and recommendation.get("action") == "retry"
        and receipt_row.get("decision", {}).get("action") == "retry"
        and receipt_row.get("receipt", {}).get("action") == "retry"
        and receipt_row.get("receipt", {}).get("in_scope_reason") == "proposal within boundaries"
        and "diagnosis_request" not in state
        and request.get("failure_key") not in state.get("failure_history", {})
    )
    result["policy_accepted_retry"] = accepted
    if not accepted:
        return result

    # Accepted retry: the real Builder gets a genuine second attempt, then
    # real independent review. Drive normally (ordinary gates only) to a
    # terminal or blocked state; do not trust the runner's own completion
    # claim (see judge_final_verdict). _serve_gate expects a step(kind, cmd,
    # allow_codes=...) callback, so wrap invoke_cli's extra-args interface.
    def step(kind: str, cmd: list[str], *, allow_codes=(0, 2)) -> subprocess.CompletedProcess:
        return budget.invoke(kind, cmd, env, root, bundle, allow_codes)

    while True:
        state = base.load_state(run_dir)
        status = state.get("status", "")
        if status in ("TASK_COMPLETE", "COMPLETE") or (status.startswith("PAUSED_") and not base._resumable(state)):
            break
        if base._serve_gate(state, run_dir, project, profile, step):
            continue
        before = state
        step("resume", base.autocode_command(project, profile, None, run_dir, []))
        after = base.load_state(run_dir)
        if after.get("status") == before.get("status") and not base._progressed(before, after):
            break
    result["state"] = base.load_state(run_dir)
    rows = result["state"].get("stages", [])[previous_rows:]
    diagnosis_index = next(i for i, row in enumerate(rows) if row.get("stage") == "astra_diagnose")
    result["retry_dispatched"] = any(
        row.get("stage") == request.get("original_stage") and row.get("output") for row in rows[diagnosis_index + 1 :]
    )
    bundle.log("retry_observed", policy_accepted=True, dispatched=result["retry_dispatched"])
    return result


GRADING_SUBPROCESS_TIMEOUT = 30  # seconds; a delivered module that hangs must not stall grading indefinitely.
_ADAPTER = r"""
import decimal, importlib.util, inspect, json, math, numbers, socket, sys
channel = socket.socket(fileno=int(sys.argv[1]))
try:
    spec = importlib.util.spec_from_file_location("convert", "convert.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = module.celsius_to_fahrenheit
    if not inspect.isfunction(function):
        raise TypeError("celsius_to_fahrenheit must be a function")
    parameters = list(inspect.signature(function, follow_wrapped=False).parameters.values())
    if (len(parameters) != 1 or parameters[0].name != "celsius"
            or parameters[0].kind != inspect.Parameter.POSITIONAL_OR_KEYWORD
            or parameters[0].default is not inspect.Parameter.empty):
        raise TypeError("expected signature celsius_to_fahrenheit(celsius)")
    values = [function(value) for value in (0, 100, 37)]
    if any(isinstance(value, bool) or not isinstance(value, (numbers.Real, decimal.Decimal)) for value in values):
        raise TypeError("conversion results must be finite numbers")
    values = [float(value) for value in values]
    if not all(math.isfinite(value) for value in values):
        raise TypeError("conversion results must be finite numbers")
    payload = {"values": values, "contract_valid": True}
except BaseException as error:
    payload = {"error": type(error).__name__, "contract_valid": False}
channel.sendall(json.dumps(payload, allow_nan=False).encode("utf-8"))
channel.close()
"""


def verdict_result(verdict: dict) -> str:
    """PASS only when parent-owned numeric assertions passed AND the
    delivered test_convert.py is byte-identical to what was seeded -- the
    task explicitly prohibits editing it, so a modified or missing protected
    test is a FAIL even when the code fix itself is correct.
    """
    if verdict.get("protected_test_status") != "unmodified":
        return "FAIL"
    return "PASS" if verdict.get("verified_pass") else "FAIL"


def judge_final_verdict(project: Path, run_dir: Path, frozen_test_path: Path, deadline: float | None = None) -> dict:
    """Assert numeric results in the trusted parent, never candidate stdout.

    The isolated child only imports and invokes the candidate, returning JSON
    over a separate inherited socket. This is not a malicious-code sandbox:
    candidate Python still shares the adapter process and host permissions.
    Birth-identified cleanup waits for ordinary descendants before returning;
    this is not a sandbox against deliberate process escape. No unittest
    status, stdout marker or bare exit can grant PASS.
    """
    delivered = project / "convert.py"
    delivered_test = project / "test_convert.py"
    frozen_bytes = frozen_test_path.read_bytes()
    expected_hash = hashlib.sha256(SEED_TEST.encode()).hexdigest()
    frozen_hash = hashlib.sha256(frozen_bytes).hexdigest()
    protected_test_status = "missing"
    if delivered_test.is_file():
        protected_test_status = "unmodified" if delivered_test.read_bytes() == frozen_bytes else "modified"
    if frozen_hash != expected_hash:
        protected_test_status = "invalid_frozen_test"
    verdict = {
        "independent_test_exit": None,
        "verified_pass": False,
        "protected_test_status": protected_test_status,
        "frozen_test_sha256": frozen_hash,
        "expected_test_sha256": expected_hash,
        "independent_test_tail": "",
        "runner_status": base.load_state(run_dir).get("status"),
    }
    if not delivered.is_file():
        return dict(verdict, note="convert.py is missing from the delivered workspace")
    timeout: float = float(GRADING_SUBPROCESS_TIMEOUT)
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
    if timeout <= 0:
        raise TrialError("wall-clock budget exceeded before independent grading")
    with tempfile.TemporaryDirectory(prefix="diagnosis-trial-verdict-") as tmp:
        scoring_dir = Path(tmp)
        shutil.copy2(delivered, scoring_dir / "convert.py")
        # Disk-backed output avoids pipe inheritance hangs and unbounded RAM
        # capture. Only the final 800 bytes are read into the evidence bundle.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            parent, child = socket.socketpair()
            timed_out = False
            with parent, child:
                proc = subprocess.Popen(
                    [sys.executable, "-I", "-B", "-c", _ADAPTER, str(child.fileno())],
                    cwd=scoring_dir,
                    stdout=stdout,
                    stderr=stderr,
                    pass_fds=(child.fileno(),),
                    start_new_session=True,
                )
                child.close()
                try:
                    _, timed_out, cleanup = grader_process.wait(proc, timeout)
                except grader_process.processes.ProcessError as error:
                    raise TrialError(f"independent grader cleanup could not be verified: {error}") from error
                verdict["grader_cleanup"] = cleanup
                parent.setblocking(False)
                chunks = bytearray()
                while len(chunks) <= 65536:
                    try:
                        chunk = parent.recv(65537 - len(chunks))
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    chunks.extend(chunk)
            for key, stream in (("independent_test_tail", stdout), ("independent_test_stderr_tail", stderr)):
                stream.seek(max(0, stream.seek(0, os.SEEK_END) - 800))
                verdict[key] = output_text(stream.read(800))
    try:
        payload = json.loads(chunks) if len(chunks) <= 65536 else {}
    except (ValueError, UnicodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    values = payload.get("values")
    numeric_values = values if isinstance(values, list) and len(values) == 3 else []
    numeric = (
        isinstance(values, list)
        and len(values) == 3
        and all(type(value) in (int, float) and abs(value) < 1e6 and math.isfinite(value) for value in values)
    )
    # Match the protected unittest's places=2 comparison without executing its
    # assertions in the candidate-controlled interpreter.
    checks = (
        [numeric_values[0] == 32, numeric_values[1] == 212, round(abs(numeric_values[2] - 98.6), 2) == 0]
        if numeric
        else [False, False, False]
    )
    verified = not timed_out and proc.returncode == 0 and payload.get("contract_valid") is True and all(checks)
    verdict.update(
        independent_test_exit=proc.returncode,
        timed_out=timed_out,
        adapter_result=payload,
        checks=checks,
        verified_pass=verified,
    )
    return verdict


# --- entry point -----------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="fixture", help="model profile from live_profiles (default: fixture)")
    parser.add_argument("--workspace", type=Path, help="parent directory for the disposable project (default: temp)")
    parser.add_argument("--budget-stages", type=int, default=40)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--i-authorize-live-model-spend", action="store_true", help="required for any non-fixture profile"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    profile = profiles.resolve(args.profile)
    if profile["provider"] != "fixture" and not args.i_authorize_live_model_spend:
        print(
            f"refusing live spend: rerun with --i-authorize-live-model-spend "
            f"(profile={args.profile}, model={profiles.describe(profile)})",
            file=sys.stderr,
        )
        return 2

    bundle = Bundle("DIAGNOSIS-TRIAL")
    bundle.log(
        "trial_started",
        profile=args.profile,
        model=profiles.describe(profile),
        authorized=args.i_authorize_live_model_spend,
        seeded_implementation_bug=GROUND_TRUTH,
        diagnostic_target="actual Builder report rejection",
    )

    budget = TrialBudget(args.budget_stages, time.monotonic() + args.timeout)

    temp = None
    if args.workspace:
        root = Path(args.workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        temp = tempfile.TemporaryDirectory(prefix="autopilot-diagnosis-trial-")
        root = Path(temp.name).resolve()

    try:
        prove_seed_and_reference(bundle, budget.deadline)
        project = base.make_workspace(root)
        (project / "convert.py").write_text(SEED_MODULE)
        (project / "test_convert.py").write_text(SEED_TEST)
        subprocess.run(["git", "-C", str(project), "add", "-A"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "-c",
                "user.name=LiveTrial",
                "-c",
                "user.email=live@example.test",
                "commit",
                "-qm",
                "seed defective candidate",
            ],
            check=True,
        )
        bundle.log("seeded", files=["convert.py", "test_convert.py"])
        # Frozen before any Builder attempt, kept outside the workspace: the
        # candidate can edit or delete its own copy of test_convert.py, but
        # every independent verdict in this trial scores against this one.
        frozen_test_path = bundle.dir / "frozen_test_convert.py"
        frozen_test_path.write_text(SEED_TEST)

        # One deadline shared across driving, diagnosis and retry: --timeout
        # is the whole trial's wall-clock budget, not a per-phase allowance.
        first = drive_to_first_verdict(project, root, profile, budget, bundle)
        bundle.state("first_verdict", {k: first["state"].get(k) for k in ("status", "phase", "next_stage")})

        payload = {
            "profile": args.profile,
            "profile_detail": profile,
            "seeded_implementation_bug": GROUND_TRUTH,
            "diagnostic_target": "Actual Builder report rejection, not the seeded implementation bug",
            "diagnosis_quality": "HUMAN_ASSESSMENT_PENDING",
            "code_verdict": "NOT_GRADED",
            "source": source_revision(),
            "outcome": first["outcome"],
        }
        # Code correctness and diagnosis quality are separate. No automatic
        # diagnosis PASS is possible while human assessment is pending.
        result = "NEEDS_HUMAN_REVIEW"

        if first["outcome"] == "complete":
            verdict = judge_final_verdict(project, first["run_dir"], frozen_test_path, budget.deadline)
            payload.update(
                astra_diagnose="NOT_EXERCISED",
                reason="runner completed without a target rejection",
                final_verdict=verdict,
            )
            payload["code_verdict"] = verdict_result(verdict)
        elif first["outcome"] not in ("rejected",):
            payload.update(
                astra_diagnose="NOT_EXERCISED",
                reason=f"drive stopped at {first['outcome']!r} before any Builder rejection",
            )
        else:
            pending = first["state"].get("pending_report_repair") or {}
            original: dict | Any = pending.get("original") or next(
                (
                    row
                    for row in reversed(first["state"].get("stages", []))
                    if row.get("stage") == "terra" and row.get("failure_key")
                ),
                {},
            )
            payload["report_rejection_evidence"] = {
                "original": original,
                "failure": first["state"].get("failure_history", {}).get(original.get("failure_key")),
            }
            if first["state"].get("status") != "PAUSED_REPEATED_FAILURE":
                escalate_to_repeat_threshold(first["run_dir"], bundle)
                payload["repeat_count_mechanically_escalated"] = True
                payload["repeat_count_mode"] = "LABELED_FAULT_INJECTION_NOT_ORGANIC_REPETITION"
            else:
                payload["repeat_count_mechanically_escalated"] = False
                payload["repeat_count_mode"] = "OBSERVED_REPEATED_FAILURE"
            diagnosis = diagnose_and_retry(project, root, profile, first["run_dir"], budget, bundle)
            payload["astra_diagnose"] = "EXERCISED" if diagnosis["admitted"] else "ADMISSION_REFUSED"
            payload["diagnosis"] = diagnosis.get("diagnosis")
            payload["recommendation"] = diagnosis.get("recommendation")
            payload["policy_accepted_retry"] = diagnosis.get("policy_accepted_retry", False)
            payload["policy_receipt"] = diagnosis.get("policy_receipt")
            payload["retry_dispatched"] = diagnosis.get("retry_dispatched", False)
            if diagnosis.get("policy_accepted_retry") and diagnosis.get("retry_dispatched"):
                verdict = judge_final_verdict(project, first["run_dir"], frozen_test_path, budget.deadline)
                payload["final_verdict"] = verdict
                payload["code_verdict"] = verdict_result(verdict)

        if payload["code_verdict"] == "FAIL":
            result = "FAIL"
        payload["cli_invocations"] = budget.used
        payload["result"] = result
        report_path = bundle.dir / "diagnosis-comparison.json"
        report_path.write_text(json.dumps(payload, indent=2, default=str))
        status = base.scenarios.FAIL if result == "FAIL" else "RECORDED"
        summary = f"astra_diagnose={payload.get('astra_diagnose', 'NOT_EXERCISED')} result={result}"
        try:
            bundle.finish(status, summary)
        except AssertionError:
            # Bundle persists FAIL before raising; re-raise only for a real FAIL.
            if result != "FAIL":
                raise
        print(f"result: {result}")
        print(f"astra_diagnose: {payload.get('astra_diagnose', 'NOT_EXERCISED')}")
        print(f"evidence: {bundle.dir}")
        print(f"read {report_path} against the report-rejection evidence for human scoring")
        return 1 if result == "FAIL" else 3
    except (TrialError, subprocess.TimeoutExpired) as error:
        bundle.log(
            "trial_error",
            error=str(error),
            cli_invocations=budget.used,
            stdout_tail=output_text(getattr(error, "stdout", None))[-800:],
            stderr_tail=output_text(getattr(error, "stderr", None))[-800:],
        )
        bundle.finish(base.scenarios.ERROR, str(error))
        print(f"ERROR: {error}", file=sys.stderr)
        print(f"evidence: {bundle.dir}", file=sys.stderr)
        return 1
    finally:
        if temp is not None:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
