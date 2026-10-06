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

from functools import partial
try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope

import datetime as dt
import json
from pathlib import Path
import uuid

try:
    from . import autocode_verification_plan as verification_plan, autocode_test_quality as test_quality
    from . import autocode_acceptance_policy as acceptance_policy, autocode_protected_oracles as protected_oracles, autocode_brief_evidence as brief_evidence, autocode_risk_evidence as risk_evidence
    from . import autocode_util as util, autocode_verification_schedule as schedule
except ImportError:
    import autocode_verification_plan as verification_plan, autocode_test_quality as test_quality
    import autocode_acceptance_policy as acceptance_policy, autocode_protected_oracles as protected_oracles, autocode_brief_evidence as brief_evidence, autocode_risk_evidence as risk_evidence
    import autocode_util as util, autocode_verification_schedule as schedule

PASS, FAIL = "PASS", "FAIL"
# Told to the Validator with every request. A live Validator showed "fails without __init__.py" as a check
# exiting 1 inside a PASS report, which the runner refuses (parallel-diamond, 2026-09-29).
VALIDATOR_NOTE = """
CHECKS IN A PASS: every check in a PASS report must exit 0; the runner re-runs each one in a clean copy and
refuses the report otherwise. To show that something fails as it should (a negative control), write a check
that exits 0 exactly when the failure happens, for example sh -c '! python3 -m unittest tests/test_x.py' or a
test that asserts the error. Never cite a check that exits non-zero in a PASS.
The clean copy is the repository's source, including explicitly approved ignored deliverables; no .autocode/. A check that reads run files
(state.json, regression/proof-*/verification.json) cannot pass there. regression_proof in your handoff is the
runner's own executed evidence: cite its verdict and source_revision directly, never a command that reads it.
Replay uses a clean Git worktree: .git may be a file or a directory. Exclude .git in either form
from product-file inventories; filtering only directory names leaves its worktree pointer file behind.
Git metadata is not a delivered product file. Keep the actual source-file and behavioral assertions intact.
The runner also executes explicit commands from the approved verification methods and current_task.validation_plan;
another successful command cannot replace them. Empty Python test bodies cannot establish behavioral coverage.
An explicit planned exit-code expectation is replayed as an assertion: a usage-error probe expected to exit 2
must actually exit 2. Your reported checks in a PASS still need to exit 0 themselves.
In read-only contained stages, capture commands execute in a runner-prepared copy of the current source,
where build outputs are writable but existing source and tests remain protected. Receipts are still written
to the --output path you give capture. Use repository-relative paths for product files. Each reported check must
include its own setup (for example build and execute in the same command): clean replay does not retain
artifacts from earlier checks. An execution in the prepared copy is still subject to clean-source replay.
Keep every scratch copy and test artefact inside the workspace under .autocode/ (for example .autocode/scratch/,
or tool_containment.scratch when your handoff has one); the runner's changed-file measurement ignores .autocode/.
Capture receipts where your output contract's capture example says. A later repair re-verifies each pin, so
under .autocode/ cite only this run directory, .autocode/evidence/, your own tool_containment.scratch, or
runner-written design captures and inputs: never .autocode/scratch/ or another stage's tool-containment
scratch (the Builder's or an earlier attempt's). The runner refuses a report that cites them.
Never use /tmp, mktemp or any path outside the workspace: the provider sandbox denies external directories
and the whole attempt is lost (a live run paused after three such denials, 2026-10-01).
Probe mixed-type numeric interactions. For staged/transactional operations inject failures after work begins:
assert the public error contract, unchanged persistent state and complete cleanup across failure modes.
""" + acceptance_policy.DOMAIN + acceptance_policy.COVERAGE
# A Validator closed a proof-linked finding with a check that read the proof from .autocode/, twice
# (fix run B, 2026-09-29); each replay failed and the run paused. The rejection says why.
RUN_FILES_HINT = (" The clean copy has no .autocode/, so a check that reads run files cannot pass there: drop it, "
                  "and cite regression_proof from your handoff as the runner's evidence instead.")
TIMEOUT_SECONDS = 900
TAIL_CHARS = 600


def evidence_pins(result):
    """Bind accepted runner output to the existing completion evidence guard."""
    pins = {}
    for row in (result or {}).get("checks", []):
        if row.get("output") and row.get("output_sha256"):
            pins[row["output"]] = row["output_sha256"]
        receipt = row.get("scheduling") or {}
        if receipt.get("receipt") and receipt.get("receipt_sha256"):
            pins[receipt["receipt"]] = receipt["receipt_sha256"]
    pins.update(brief_evidence.evidence_pins((result or {}).get("brief_acceptance")))
    pins.update(risk_evidence.evidence_pins((result or {}).get("risk_acceptance")))
    return pins


def replay(checks, workspace, run_dir, record, scratch_run, *, timeout=TIMEOUT_SECONDS, approved_state=None,
            required_commands=None, progressive_context=None, execution_identity=None, all_brief_observations=False, all_risk_observations=False) -> dict:
    """Re-run each distinct check command; return the result or raise ValueError on the first that fails."""
    selected = source_scope.paths(approved_state or {})
    if selected:
        scratch_run = partial(scratch_run, source_paths=selected)
        if execution_identity:
            execution_identity = partial(execution_identity, source_paths=selected)
    schedule.guard(Path(run_dir) / "check-replay" / "obligations")
    # Report stems repeat across iterations, repairs and retries of one attempt.
    # Allocate before any scratch/protected-test writes so old citations stay intact.
    stem = Path(record.get("output") or "validation").stem
    out = Path(run_dir) / "check-replay" / f"{stem}-{uuid.uuid4().hex}"
    out.mkdir(parents=True, exist_ok=False)
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
    checks += [{"command": command, "exit_code": 0, "evidence_ref": "approved-repeat", "repetition": repeat}
               for command, count in verification_plan.repetitions(
                   approved_state or {}, progressive_context=progressive_context).items()
               for repeat in range(2, count + 1)]
    test_quality.require_behavioral_tests(workspace, [check["command"] for check in checks])
    state = approved_state or {}
    # The original provider execution, not its repaired JSON formatting, owns
    # this obligation. A later Validator or any final/protected check is new work.
    events = Path(record.get("events") or "")
    obligation = ({"events": str(events.resolve()), "events_sha256": util.file_hash(events),
                   "stage": record.get("stage"), "task_id": record.get("task_id"),
                   "contract": state.get("goal_contract"), "task": state.get("current_task"),
                   "settings_identity": util.digest(state.get("settings") or {}),
                   "progressive": progressive_context, "purpose": "independent_clean_replay"}
                  if (execution_identity and record.get("stage") in ("sol", "astra_checkpoint")
                      and events.is_file() and not events.is_symlink()) else None)
    rows, seen, contexts = [], {}, {}
    for check in checks:
        command = check["command"]
        key = (command, check.get("repetition", 1))
        if key not in seen:
            if obligation:
                eligible = command not in prescribed and bool(schedule.collection_kind(command))
                # Shell syntax affects interpreter binding even when argv[0]
                # matches (for example a multiline -c script and a plain CLI).
                runtime = (command, eligible)
                def identity(*, refresh=False):
                    if refresh or runtime not in contexts:
                        contexts[runtime] = execution_identity(workspace, command=command, full=eligible)
                    execution = contexts[runtime]
                    if util.file_hash(events) != obligation["events_sha256"]:
                        raise ValueError("Validator execution evidence changed during clean replay")
                    if record.get("source_revision") and execution["source_revision"] != record["source_revision"]:
                        raise ValueError("The source changed since this Validator obligation; fresh validation is required")
                    return {"execution": execution, "obligation": obligation, "command": command,
                            "repetition": check.get("repetition", 1), "timeout": timeout}
                binding = identity()
                reason = ("mandatory_approved_execution" if command in prescribed else
                          "command_has_no_supported_inventory" if not eligible else
                          "execution_identity_not_cacheable" if not binding["execution"].get("reuse_supported", False)
                          else "no_current_receipt")
                receipt = schedule.run(Path(run_dir) / "check-replay" / "obligations", binding,
                    lambda directory: scratch_run(workspace, directory, command=command, timeout=timeout),
                    reuse_allowed=eligible and binding["execution"].get("reuse_supported", False),
                    reason=reason,
                    # Reuse the previous check's *post-execution* measurement
                    # only inside this invocation, never across restarts. Every
                    # execution still receives a fresh after-context check.
                    current_identity=lambda: identity(refresh=True))
            else:
                receipt = scratch_run(workspace, out / f"check-{len(seen) + 1:02d}",
                                      command=command, timeout=timeout)
            seen[key] = {"command": command, "exit_code": receipt.get("exit_code"),
                             "timed_out": bool(receipt.get("timed_out")), "output": receipt.get("output"),
                             "output_sha256": receipt.get("output_sha256"),
                              "duration_seconds": receipt.get("duration_seconds"), "results": receipt.get("results"),
                              "purpose": "approved_execution" if command in prescribed else "independent_clean_replay",
                              "scheduling": receipt.get("scheduling"),
                             "error": receipt.get("error") or "", "tail": (receipt.get("tail") or "")[-TAIL_CHARS:]}
        rows.append({**seen[key], "reported_exit_code": check.get("exit_code"),
                     "evidence_ref": check.get("evidence_ref")})
    brief = brief_evidence.replay(approved_state or {}, workspace, out, scratch_run, timeout=timeout,
                                  source_revision=record.get("source_revision"), progressive_context=progressive_context,
                                  all_observations=all_brief_observations)
    risk = risk_evidence.replay(approved_state or {}, workspace, out, scratch_run, timeout=timeout,
                               source_revision=record.get("source_revision"), progressive_context=progressive_context,
                               all_observations=all_risk_observations)
    failed = [row for row in rows if row["error"] or row["timed_out"] or row["exit_code"] != 0]
    result = {"verdict": FAIL if failed else PASS, "checks": rows, "source_revision": record.get("source_revision"),
              "protected_tests": protected, "brief_acceptance": brief, "risk_acceptance": risk, "timeout_seconds": timeout, "replayed_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    decisions = [row for row in seen.values() if row.get("scheduling")]
    result["scheduling"] = {
        "executed_count": sum(row["scheduling"]["action"] == "execute" for row in decisions),
        "reused_count": sum(row["scheduling"]["action"] == "reuse" for row in decisions),
        "executed_seconds": sum(row.get("duration_seconds") or 0 for row in decisions
                                if row["scheduling"]["action"] == "execute"),
        "avoided_seconds": sum(row.get("duration_seconds") or 0 for row in decisions
                               if row["scheduling"]["action"] == "reuse"),
        "duration_basis": "Original command subprocess seconds, not net wall-time savings or scheduling overhead"}
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
