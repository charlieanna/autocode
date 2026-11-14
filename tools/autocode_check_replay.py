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
naming the command, the runner's exit code and the end of its output. Later checks
and acceptance observations are not run after that failure; the saved FAIL receipt
lists them as not_run, never as passing evidence. That is the
runner's ordinary rejected-report path: a bounded report repair may drop the check
or cite another executed one (each is replayed again), then the run pauses, and
``--resume-paused`` asks for a fresh validation.

A mandatory lifecycle observation (``autocode_risk_evidence``) that fails while every
reported check reproduces is different: the report is accurate and the product is
wrong. The replay is saved with verdict FAIL. With ``keep_lifecycle_failure`` (the
Tester's report) it is returned, and the controller turns the observation into a
runner-owned finding for repair (``autocode_risk_findings``); otherwise it raises
``LifecycleFailure``, the ValueError every other caller always saw.

The result is saved with the validation (``validation["check_replay"]``), bound to
the source revision it was run on, and shown in the status view's evidence. The
caller passes the scratch runner (``autocode_verify.scratch_run``), so this module
imports nothing from the runner.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


from functools import partial

try:
    from . import autocode_native_check_execution as native_checks
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_native_check_execution as native_checks
    import autocode_source_scope as source_scope

import datetime as dt
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_policy as acceptance_policy
    from . import autocode_base_attribution as base_attribution
    from . import autocode_brief_evidence as brief_evidence
    from . import autocode_command_receipt as command_receipt
    from . import autocode_protected_oracles as protected_oracles
    from . import autocode_risk_evidence as risk_evidence
    from . import autocode_test_quality as test_quality
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_acceptance_policy as acceptance_policy
    import autocode_base_attribution as base_attribution
    import autocode_brief_evidence as brief_evidence
    import autocode_command_receipt as command_receipt
    import autocode_protected_oracles as protected_oracles
    import autocode_risk_evidence as risk_evidence
    import autocode_test_quality as test_quality
    import autocode_util as util
    import autocode_verification_plan as verification_plan
    import autocode_verification_schedule as schedule

PASS, FAIL = "PASS", "FAIL"
LifecycleFailure = risk_evidence.LifecycleFailure
PlannedCheckFailure = verification_plan.PlannedCheckFailure
ReportedCheckFailure = verification_plan.ReportedCheckFailure


# Told to the Validator with every request. A live Validator showed "fails without __init__.py" as a check
# exiting 1 inside a PASS report, which the runner refuses (parallel-diamond, 2026-09-29).
VALIDATOR_NOTE = (
    prompts.get("fragments/check-replay/validator-note.md") + acceptance_policy.DOMAIN + acceptance_policy.COVERAGE
)
# A Validator closed a proof-linked finding with a check that read the proof from .autocode/, twice
# (fix run B, 2026-09-29); each replay failed and the run paused. The rejection says why.
RUN_FILES_HINT = prompts.get("fragments/check-replay/run-files-hint.md")
# A live skeleton's check ended in a grep over git status: it passed while its files were uncommitted and failed
# once the program merged them, which undid a correct merge (2026-10-06).
WORKTREE_STATE = re.compile(r"\bgit\b[^|;&\n]*?\bstatus\b")
WORKTREE_STATE_HINT = prompts.get("fragments/check-replay/worktree-state-hint.md")
TIMEOUT_SECONDS = 900
TAIL_CHARS = 600


def evidence_pins(result):
    """Bind accepted runner output to the existing completion evidence guard."""
    if (result or {}).get("simulated"):
        return {}  # Archival observations never become current completion pins.
    pins = {}
    for row in (result or {}).get("checks", []):
        pins.update(command_receipt.pins(row))
        pins.update(row.get("native_check_pins") or {})
        if row.get("output") and row.get("output_sha256"):
            pins[row["output"]] = row["output_sha256"]
        receipt = row.get("scheduling") or {}
        if receipt.get("receipt") and receipt.get("receipt_sha256"):
            pins[receipt["receipt"]] = receipt["receipt_sha256"]
        on_base = row.get("base_attribution") or {}  # the base run a failure was compared with (#3160)
        if on_base.get("base_output") and on_base.get("base_output_sha256"):
            pins[on_base["base_output"]] = on_base["base_output_sha256"]
    protected = (result or {}).get("protected_tests") or {}
    for name in ("candidate", "original"):
        pins.update(command_receipt.pins(protected.get(name)))
        receipt = (protected.get(name) or {}).get("scheduling") or {}
        if receipt.get("receipt") and receipt.get("receipt_sha256"):
            pins[receipt["receipt"]] = receipt["receipt_sha256"]
    pins.update(brief_evidence.evidence_pins((result or {}).get("brief_acceptance")))
    pins.update(risk_evidence.evidence_pins((result or {}).get("risk_acceptance")))
    return pins


def executable_checks(checks, approved_state=None, required_commands=None, progressive_context=None):
    """One command inventory for ordinary execution and explicit simulation."""
    checks = list(checks)
    prescribed = verification_plan.approved_commands(approved_state or {}, progressive_context=progressive_context)
    if required_commands is not None:
        for command in required_commands:
            if not isinstance(command, str) or verification_plan.commands(command) != [command]:
                raise ValueError(f"Required replay command is not an explicit executable command: {command!r}")
            prescribed.append(command)
        prescribed = list(dict.fromkeys(prescribed))
    reported = {check["command"] for check in checks}
    checks += [
        {"command": command, "exit_code": 0, "evidence_ref": "approved-plan"}
        for command in prescribed
        if command not in reported
    ]
    checks += [
        {"command": command, "exit_code": 0, "evidence_ref": "approved-repeat", "repetition": repeat}
        for command, count in verification_plan.repetitions(
            approved_state or {}, progressive_context=progressive_context
        ).items()
        for repeat in range(2, count + 1)
    ]
    return checks, prescribed


def _protected_check(run_dir, obligation, measured, timeout, label, execute, *, policy, accepted_failure):
    """Schedule one half of the protected-test pair under the original Tester obligation (#3040).

    The identity binds the complete measured execution context (source, runtime, interpreter,
    dependencies, environment, ignored inputs), the original Tester execution, the protected
    binding and command, the overlay (candidate or original) and the approved supersedes policy.
    Only a completed, authenticated receipt of that exact identity is reused, after a fresh
    comparison; anything else executes. The original run's nonzero exit is reusable only when the
    current approved supersedes policy excuses exactly its failures; the gate reinterprets it anyway.
    """
    command = policy["command"]
    eligible = bool(schedule.collection_kind(command))

    def identity(*, refresh=False):
        return {
            "execution": measured(command, eligible, refresh=refresh),
            "obligation": obligation,
            "protected_tests": {**policy, "overlay": label},
            "timeout": timeout,
            "purpose": "protected_tests",
        }

    binding = identity()
    supported = eligible and binding["execution"].get("reuse_supported", False)

    def reusable(receipt, *, root=None):
        if not set(command_receipt.OWNERSHIP_FIELDS) <= set(receipt):
            return False  # an unowned command never stands in for the protected pair
        if schedule.reusable(receipt, root=root):
            return True
        return bool(
            label == "original"
            and accepted_failure(receipt)
            and schedule.complete_results(receipt)
            and schedule.intact(receipt, root=root)
        )

    return schedule.run(
        Path(run_dir) / "check-replay" / "obligations",
        binding,
        execute,
        reuse_allowed=supported,
        reason="no_current_receipt"
        if supported
        else "execution_identity_not_cacheable"
        if eligible
        else "command_has_no_supported_inventory",
        owned_preparation=True,
        current_identity=lambda: identity(refresh=True),
        reusable_result=reusable,
    )


def _failed_check(row):
    return bool(row["error"] or row["timed_out"] or row["exit_code"] != 0 or not command_receipt.completed(row))


def _attribute(row, on_base, base_run, workspace, out, timeout):
    """Run ``row``'s command once on the base revision (cached in ``on_base`` for a repeated command) and
    judge whose failure it is. A base copy that cannot be prepared leaves the failure the change's."""
    command = row["command"]
    if command not in on_base:
        try:
            on_base[command] = base_run(
                workspace, out / f"base-{len(on_base) + 1:02d}", command=command, timeout=timeout
            )
        except util.Paused:
            raise
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            on_base[command] = error
    base = on_base[command]
    if isinstance(base, BaseException):
        return base_attribution.unavailable(f"The base revision could not be prepared to compare ({base}).")
    return base_attribution.judge(row, base)


def replay(
    checks,
    workspace,
    run_dir,
    record,
    scratch_run,
    *,
    timeout=TIMEOUT_SECONDS,
    approved_state=None,
    required_commands=None,
    progressive_context=None,
    execution_identity=None,
    all_brief_observations=False,
    all_risk_observations=False,
    native_execution=None,
    keep_lifecycle_failure=False,
    base_run=None,
) -> dict:
    """Re-run each distinct check command; return the result or raise ValueError on the first that fails.

    keep_lifecycle_failure: when every check reproduces but a mandatory lifecycle observation fails,
    return the saved FAIL replay instead of raising LifecycleFailure (the Tester's accepted report).
    base_run: runs one command in a clean copy of the base revision (autocode_base_command.run,
    bound by the caller). With it, a planned check that fails is first run on the base, and a failure
    the base has identically is recorded as the base's and not counted (autocode_base_attribution).
    """
    try:
        from . import autocode_replay_checks as recorded_checks
    except ImportError:
        import autocode_replay_checks as recorded_checks
    checks = list(checks)
    if native_execution is not None:
        if recorded_checks.simulated((approved_state or {}).get("settings")):
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native replay requires fresh executed checks")
        if type(native_execution) is not native_checks.NativeCheckExecution:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Original concrete native check owner required")
        native_execution.require_replay(
            workspace, run_dir, approved_state, record, checks, execution_identity=execution_identity
        )
        ordinary_scratch = scratch_run

        def scratch_run(*args, **kwargs):
            before = native_execution.checkpoint()
            receipt = ordinary_scratch(*args, **kwargs, native_execution=native_execution)
            native_execution.authenticate(receipt, after=before)
            return receipt

    if recorded_checks.simulated((approved_state or {}).get("settings")):
        recorded_checks.require_supported(cast("RunState", approved_state or {}))
        selected_checks, _prescribed = executable_checks(checks, approved_state, required_commands, progressive_context)
        for check in selected_checks:
            if WORKTREE_STATE.search(str(check.get("command") or "")):
                raise ValueError(f"Check `{check['command']}` runs git status." + WORKTREE_STATE_HINT)
        test_quality.require_behavioral_tests(workspace, [check["command"] for check in selected_checks])
        return recorded_checks.replay(selected_checks, approved_state, workspace, record)
    selected = source_scope.paths(cast("RunState", approved_state or {}))
    if selected:
        scratch_run = partial(scratch_run, source_paths=selected)
        if execution_identity:
            execution_identity = partial(execution_identity, source_paths=selected)
    schedule.guard(Path(run_dir) / "check-replay" / "obligations")
    checks = list(checks)
    for check in checks:  # the Validator's own; an approved plan's commands are the planner's
        if WORKTREE_STATE.search(str(check.get("command") or "")):
            raise ValueError(f"Check `{check['command']}` runs git status." + WORKTREE_STATE_HINT)
    # Report stems repeat across iterations, repairs and retries of one attempt.
    # Allocate before any scratch/protected-test writes so old citations stay intact.
    stem = Path(record.get("output") or "validation").stem
    out = Path(run_dir) / "check-replay" / f"{stem}-{uuid.uuid4().hex}"
    out.mkdir(parents=True, exist_ok=False)
    state = approved_state or {}
    # The original provider execution, not its repaired JSON formatting, owns
    # this obligation. A later Validator or any final check is new work; the
    # protected-test pair is bound to it too, so a report-only repair of the
    # same Tester execution does not launch the pair again (#3040).
    events = Path(record.get("events") or "")
    obligation = (
        {
            "events": str(events.resolve()),
            "events_sha256": util.file_hash(events),
            "stage": record.get("stage"),
            "task_id": record.get("task_id"),
            "contract": state.get("goal_contract"),
            "task": state.get("current_task"),
            "settings_identity": util.digest(state.get("settings") or {}),
            "progressive": progressive_context,
            "purpose": "independent_clean_replay",
        }
        if (
            execution_identity
            and record.get("stage") in ("tester", "review_checkpoint")
            and events.is_file()
            and not events.is_symlink()
        )
        else None
    )
    contexts: dict[Any, Any] = {}

    def measured(command, eligible, *, refresh=False):
        # Shell syntax affects interpreter binding even when argv[0]
        # matches (for example a multiline -c script and a plain CLI).
        runtime = (command, eligible)
        if refresh or runtime not in contexts:
            contexts[runtime] = execution_identity(workspace, command=command, full=eligible)
        execution = contexts[runtime]
        if util.file_hash(events) != obligation["events_sha256"]:
            raise ValueError("Validator execution evidence changed during clean replay")
        if record.get("source_revision") and execution["source_revision"] != record["source_revision"]:
            raise ValueError("The source changed since this Validator obligation; fresh validation is required")
        return execution

    protected = protected_oracles.replay(
        cast("RunState", state),
        workspace,
        out,
        scratch_run,
        timeout=timeout,
        run_check=partial(_protected_check, run_dir, obligation, measured, timeout) if obligation else None,
    )
    checks, prescribed = executable_checks(checks, approved_state, required_commands, progressive_context)
    test_quality.require_behavioral_tests(workspace, [check["command"] for check in checks])
    rows: list[Any] = []
    failed: list[Any] = []
    seen: dict[Any, Any] = {}
    on_base: dict[str, Any] = {}
    for check in checks:
        command = check["command"]
        key = (command, check.get("repetition", 1))
        if key not in seen:
            if obligation:
                eligible = command not in prescribed and bool(schedule.collection_kind(command))

                def identity(*, refresh=False):
                    return {
                        "execution": measured(command, eligible, refresh=refresh),
                        "obligation": obligation,
                        "command": command,
                        "repetition": check.get("repetition", 1),
                        "timeout": timeout,
                    }

                binding = identity()
                reason = (
                    "mandatory_approved_execution"
                    if command in prescribed
                    else "command_has_no_supported_inventory"
                    if not eligible
                    else "execution_identity_not_cacheable"
                    if not binding["execution"].get("reuse_supported", False)
                    else "no_current_receipt"
                )
                receipt = schedule.run(
                    Path(run_dir) / "check-replay" / "obligations",
                    binding,
                    lambda directory: scratch_run(workspace, directory, command=command, timeout=timeout),
                    reuse_allowed=eligible and binding["execution"].get("reuse_supported", False),
                    reason=reason,
                    owned_preparation=True,
                    # Reuse the previous check's *post-execution* measurement
                    # only inside this invocation, never across restarts. Every
                    # execution still receives a fresh after-context check.
                    current_identity=lambda: identity(refresh=True),
                )
            else:
                receipt = scratch_run(workspace, out / f"check-{len(seen) + 1:02d}", command=command, timeout=timeout)
            seen[key] = {
                "command": command,
                "exit_code": receipt.get("exit_code"),
                "timed_out": bool(receipt.get("timed_out")),
                "output": receipt.get("output"),
                "output_sha256": receipt.get("output_sha256"),
                "duration_seconds": receipt.get("duration_seconds"),
                "results": receipt.get("results"),
                "purpose": "approved_execution" if command in prescribed else "independent_clean_replay",
                "scheduling": receipt.get("scheduling"),
                "error": receipt.get("error") or "",
                "tail": (receipt.get("tail") or "")[-TAIL_CHARS:],
                **command_receipt.project(receipt),
                **(
                    {
                        "native_check": receipt["native_check"],
                        "native_check_pins": native_execution.authenticate(receipt)["pins"],
                    }
                    if native_execution is not None
                    else {}
                ),
            }
        rows.append(
            {**seen[key], "reported_exit_code": check.get("exit_code"), "evidence_ref": check.get("evidence_ref")}
        )
        if (
            _failed_check(rows[-1])
            and base_run is not None
            and command in prescribed
            and not progressive_context
            and base_attribution.eligible(command)
        ):
            rows[-1]["base_attribution"] = _attribute(rows[-1], on_base, base_run, workspace, out, timeout)
            if rows[-1]["base_attribution"]["kind"] == base_attribution.BASE:
                continue  # the base's failure, recorded on the row; not the change's
        if _failed_check(rows[-1]):
            failed.append(rows[-1])
            break  # This report cannot pass; preserve the failure before spending on later gates.
    brief = risk = refuted = None
    if not failed:
        brief = brief_evidence.replay(
            cast("RunState", approved_state or {}),
            workspace,
            out,
            scratch_run,
            timeout=timeout,
            source_revision=record.get("source_revision"),
            progressive_context=progressive_context,
            all_observations=all_brief_observations,
        )
        try:
            risk, refuted = (
                risk_evidence.replay(
                    cast("RunState", approved_state or {}),
                    workspace,
                    out,
                    scratch_run,
                    timeout=timeout,
                    source_revision=record.get("source_revision"),
                    progressive_context=progressive_context,
                    all_observations=all_risk_observations,
                ),
                None,
            )
        except LifecycleFailure as error:
            risk, refuted = error.result, error
    result = {
        "verdict": FAIL if failed or refuted else PASS,
        "checks": rows,
        "source_revision": record.get("source_revision"),
        "protected_tests": protected,
        "brief_acceptance": brief,
        "risk_acceptance": risk,
        "timeout_seconds": timeout,
        "replayed_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    if failed:
        result["not_run"] = {
            "checks": checks[len(rows) :],
            "brief_acceptance": True,
            "risk_acceptance": True,
        }
    # Each decision is counted once: the protected pair's rows are their own obligations (#3040).
    decisions = [row for row in seen.values() if row.get("scheduling")] + [
        row
        for row in ((protected or {}).get(name) for name in ("candidate", "original"))
        if (row or {}).get("scheduling")
    ]
    result["scheduling"] = {
        "executed_count": sum(row["scheduling"]["action"] == "execute" for row in decisions),
        "reused_count": sum(row["scheduling"]["action"] == "reuse" for row in decisions),
        "executed_seconds": sum(
            row.get("duration_seconds") or 0 for row in decisions if row["scheduling"]["action"] == "execute"
        ),
        "avoided_seconds": sum(
            row.get("duration_seconds") or 0 for row in decisions if row["scheduling"]["action"] == "reuse"
        ),
        "duration_basis": "Original command subprocess seconds, not net wall-time savings or scheduling overhead",
    }
    result["receipt_path"] = str(out / "replay.json")
    (out / "replay.json").write_text(json.dumps(result, indent=2) + "\n")
    if failed:
        row = failed[0]
        if row["error"]:
            what = f"could not run ({row['error']})"
        elif row["timed_out"]:
            what = f"timed out after {timeout} seconds"
        elif not command_receipt.completed(row):
            what = "has no complete owned-command evidence"
        else:
            what = f"exited {row['exit_code']}"
        if row.get("base_attribution"):
            what += f". {row['base_attribution']['reason']}"
        planned = row.get("evidence_ref") in ("approved-plan", "approved-repeat")
        error_type = PlannedCheckFailure if planned else ReportedCheckFailure
        failure = error_type(
            (
                f"Check `{row['command']}` is required by the approved verification methods or the current task's "
                "validation plan (the Validator did not report it) and must exit 0, but when the runner ran it "
                if planned
                else f"Check `{row['command']}` was reported as exit {row['reported_exit_code']}, but when the runner re-ran it "
            )
            + f"from the repository root in a clean copy of the current source it {what}"
            + (f"; its output ended: {row['tail'].strip()[-300:]}" if row["tail"].strip() else "")
            + (f". Replay summary: {out / 'replay.json'}. " if planned else f". Receipt: {out / 'replay.json'}. ")
            + (
                "A Validator report cannot change it: if the command must fail, the plan's author (the Resolver, for "
                "a repair task) must reword that step. " + verification_plan.EXPECTED_FAILURE_RULE
                if planned
                else "Cite only checks that pass from the repository root in a clean checkout of this source; a check "
                "that needs a server or other setup must start and stop it itself."
            )
            + (RUN_FILES_HINT if ".autocode" in row["command"] else "")
            + " Later checks and acceptance observations were not run; they remain required for a passing replay."
        )
        # Read by autocode_planned_check_rework and autocode_reported_check_rerun.
        failure.command, failure.tail = row["command"], row["tail"]
        raise failure
    if refuted and not keep_lifecycle_failure:
        raise refuted
    # With keep_lifecycle_failure the reported checks reproduce and the product failed the runner's
    # own lifecycle observation: the FAIL replay is returned for autocode_risk_findings.
    return result
