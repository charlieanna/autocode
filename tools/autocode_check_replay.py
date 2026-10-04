"""The runner re-runs the Validator's checks before its PASS counts.

A Validator's checks are attested against the provider's own event log or a
capture receipt (``autocode_support.verify_checks``), which shows a command ran
and what it returned in the Validator's session, not that it passes on the code
as it is. Before a PASS validation is accepted, ``replay`` runs every cited check
again itself: from the repository root, in a scratch copy of the current source
(never the workspace), with the credential-free environment and a time limit per
check. Every check must exit 0 there. There are no exceptions a model can claim:
a check that needs a server or other setup starts and stops it itself.

A check that does not reproduce rejects the Validator's report with a ValueError
naming the command, the runner's exit code and the end of its output. That is the
runner's ordinary rejected-report path: a bounded report repair may drop the check
or cite another executed one (each is replayed again), then the run pauses, and
``--resume-paused`` asks for a fresh validation.

The result is saved with the validation (``validation["check_replay"]``), bound to
the source revision it was run on, and shown in the status view's evidence. The
caller passes the scratch runner (``autocode_verify.scratch_run``), so this module
imports nothing from the runner.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

try:
    from . import autocode_verification_plan as verification_plan, autocode_test_quality as test_quality
    from . import autocode_acceptance_policy as acceptance_policy, autocode_protected_oracles as protected_oracles
except ImportError:
    import autocode_verification_plan as verification_plan, autocode_test_quality as test_quality
    import autocode_acceptance_policy as acceptance_policy, autocode_protected_oracles as protected_oracles

PASS, FAIL = "PASS", "FAIL"
# Told to the Validator with every request. A live Validator showed "fails without __init__.py" as a check
# exiting 1 inside a PASS report, which the runner refuses (parallel-diamond, 2026-09-29).
VALIDATOR_NOTE = """
CHECKS IN A PASS: every check in a PASS report must exit 0; the runner re-runs each one in a clean copy and
refuses the report otherwise. To show that something fails as it should (a negative control), write a check
that exits 0 exactly when the failure happens, for example sh -c '! python3 -m unittest tests/test_x.py' or a
test that asserts the error. Never cite a check that exits non-zero in a PASS.
The clean copy is the repository's source only: no ignored files and no .autocode/. A check that reads run files
(state.json, regression/proof-*/verification.json) cannot pass there. regression_proof in your handoff is the
runner's own executed evidence: cite its verdict and source_revision directly, never a command that reads it.
The runner also executes explicit commands from the approved verification methods and current_task.validation_plan;
another successful command cannot replace them. Empty Python test bodies cannot establish behavioral coverage.
An explicit planned exit-code expectation is replayed as an assertion: a usage-error probe expected to exit 2
must actually exit 2. Your reported checks in a PASS still need to exit 0 themselves.
Keep every scratch copy and test artefact inside the workspace under .autocode/ (for example .autocode/scratch/);
the runner's changed-file measurement ignores .autocode/. Never use /tmp, mktemp or any path outside the
workspace: the provider sandbox denies external directories and the whole attempt is lost (a live run paused
after three such denials, 2026-10-01).
Probe mixed-type numeric interactions. For staged/transactional operations inject failures after work begins:
assert the public error contract, unchanged persistent state and complete cleanup across failure modes.
""" + acceptance_policy.DOMAIN + acceptance_policy.COVERAGE
# A Validator closed a proof-linked finding with a check that read the proof from .autocode/, twice
# (fix run B, 2026-09-29); each replay failed and the run paused. The rejection says why.
RUN_FILES_HINT = (" The clean copy has no .autocode/, so a check that reads run files cannot pass there: drop it, "
                  "and cite regression_proof from your handoff as the runner's evidence instead.")
TIMEOUT_SECONDS = 900
TAIL_CHARS = 600


def replay(checks, workspace, run_dir, record, scratch_run, *, timeout=TIMEOUT_SECONDS, approved_state=None,
           required_commands=None, progressive_context=None) -> dict:
    """Re-run each distinct check command; return the result or raise ValueError on the first that fails."""
    # Unique per call (issue #341): stage attempt stems restart at 1 each iteration, so a stem-only
    # directory would let a later iteration's replay overwrite an earlier cited receipt in place.
    out = Path(run_dir) / "check-replay" / (Path(record.get("output") or "validation").stem + "-" + uuid.uuid4().hex)
    protected = protected_oracles.replay(approved_state or {}, workspace, out, scratch_run, timeout=timeout)
    checks = list(checks)
    prescribed = verification_plan.approved_commands(approved_state or {}, progressive_context=progressive_context)
    if required_commands is not None:
        for command in required_commands:
            if not isinstance(command, str) or verification_plan.commands(command) != [command]:
                raise ValueError(f"Required replay command is not an explicit executable command: {command!r}")
            prescribed.append(command)
        prescribed = list(dict.fromkeys(prescribed))
    reported = {check["command"] for check in checks}
    checks += [{"command": command, "exit_code": 0, "evidence_ref": "approved-plan"}
               for command in prescribed if command not in reported]
    test_quality.require_behavioral_tests(workspace, [check["command"] for check in checks])
    rows, seen = [], {}
    for check in checks:
        command = check["command"]
        if command not in seen:
            receipt = scratch_run(workspace, out / f"check-{len(seen) + 1:02d}", command=command, timeout=timeout)
            seen[command] = {"command": command, "exit_code": receipt.get("exit_code"),
                             "timed_out": bool(receipt.get("timed_out")), "output": receipt.get("output"),
                             "output_sha256": receipt.get("output_sha256"),
                             "duration_seconds": receipt.get("duration_seconds"),
                             "error": receipt.get("error") or "", "tail": (receipt.get("tail") or "")[-TAIL_CHARS:]}
        rows.append({**seen[command], "reported_exit_code": check.get("exit_code"),
                     "evidence_ref": check.get("evidence_ref")})
    failed = [row for row in rows if row["error"] or row["timed_out"] or row["exit_code"] != 0]
    result = {"verdict": FAIL if failed else PASS, "checks": rows, "source_revision": record.get("source_revision"),
              "protected_tests": protected, "timeout_seconds": timeout, "replayed_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    out.mkdir(parents=True, exist_ok=True)
    (out / "replay.json").write_text(json.dumps(result, indent=2) + "\n")
    if failed:
        row = failed[0]
        if row["error"]:
            what = f"could not run ({row['error']})"
        elif row["timed_out"]:
            what = f"timed out after {timeout} seconds"
        else:
            what = f"exited {row['exit_code']}"
        raise ValueError(
            f"Check `{row['command']}` was reported as exit {row['reported_exit_code']}, but when the runner re-ran it "
            f"from the repository root in a clean copy of the current source it {what}"
            + (f"; its output ended: {row['tail'].strip()[-300:]}" if row["tail"].strip() else "")
            + f". Receipt: {out / 'replay.json'}. Cite only checks that pass from the repository root in a clean "
            "checkout of this source; a check that needs a server or other setup must start and stop it itself."
            + (RUN_FILES_HINT if ".autocode" in row["command"] else ""))
    return result
