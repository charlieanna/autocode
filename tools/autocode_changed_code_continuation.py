"""Continue an authenticated, incomplete changed-line study without repeating its proof.

Each window keeps the existing mutation deadline. An invocation gets at most the
saved stage allowance (and remaining run time), stops when no new site ran, and
publishes every completed window as a new immutable proof artifact. A subsequent
review can continue that artifact. Failed or timed-out commands are never retried.
No provider is launched and no completion decision is made here.
"""

from __future__ import annotations

import copy
import math
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_budget_state as budget_state
    from . import autocode_changed_code_proof as changed_code
    from . import autocode_mutation_check as mutation
    from . import autocode_mutation_checkpoint as mutation_checkpoint
    from . import autocode_runner_check as runner_check
    from . import autocode_source_scope as source_scope
    from . import autocode_status as status
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
    from . import autocode_verify as verify
except ImportError:
    import autocode_budget_state as budget_state
    import autocode_changed_code_proof as changed_code
    import autocode_mutation_check as mutation
    import autocode_mutation_checkpoint as mutation_checkpoint
    import autocode_runner_check as runner_check
    import autocode_source_scope as source_scope
    import autocode_status as status
    import autocode_util as util
    import autocode_verification_schedule as schedule
    import autocode_verify as verify


def pending(proof):
    """Only never-launched sites are eligible; a partial capture is still an attempt."""
    report = proof.get("mutation_check") or {}
    if report.get("collected") is not True or (proof.get("changed_code_inputs") or {}).get("custody_gaps"):
        return 0
    return sum(
        row.get("outcome") == "not_run" and not row.get("evidence") and bool(row.get("site_id"))
        for file in report.get("files") or []
        for row in file.get("mutants") or []
    )


def allowance(state: RunState):
    limits = (state.get("settings") or {}).get("limits") or {}
    seconds = limits.get("stage_timeout_seconds") or mutation.BUDGET_SECONDS
    total = limits.get("max_seconds")
    if total:
        seconds = min(seconds, max(0, total - budget_state.active_seconds_or_zero(state)))
    return max(0, seconds)


def minimum_window(proof):
    """Reserve the observed reference duration as well as one mutant's minimum slot."""
    reference = (proof.get("mutation_check") or {}).get("reference_output")
    receipt = next((row for row in proof.get("changed_code_receipts", []) if row.get("output") == reference), {})
    duration = receipt.get("duration_seconds")
    observed = duration if type(duration) in (int, float) and math.isfinite(duration) and duration > 0 else 0
    return mutation.MUTANT_FLOOR_SECONDS + observed


def defer_review(state: RunState, previous):
    """Progressing local work gets another bounded step before a provider review."""
    current = state.get("regression_proof") or {}
    left = pending(current)
    if current.get("verdict") != verify.PASS or not left:
        return False
    identity = (current.get("execution_context") or {}).get("identity") or {}
    if not isinstance(identity, dict) or identity.get("reuse_supported") is not True:
        return False
    inputs = current.get("changed_code_inputs") or {}
    if mutation_checkpoint.FAILURE in inputs:
        failure = inputs[mutation_checkpoint.FAILURE]
        if not isinstance(failure, dict) or failure.get("kind") != "window_budget_exhausted":
            return False
    if allowance(state) < minimum_window(current):
        raise util.Paused(
            "PAUSED_TIME_LIMIT", "The saved execution allowance cannot fit the next local verification window"
        )
    previous = previous or {}
    same_context = previous.get("source_revision") == current.get("source_revision") and previous.get(
        "execution_context"
    ) == current.get("execution_context")
    if same_context:
        return left < pending(previous)
    return any(
        row.get("outcome") != "not_run" or row.get("evidence")
        for file in current["mutation_check"].get("files", [])
        for row in file.get("mutants", [])
    )


def continue_proof(state: RunState, workspace, run_dir, proof, framework, inputs, current_identity, *, fresh=False):
    """Called only after the parent proof's full reuse checks succeeded, or it just completed (``fresh``).

    Recheck its artifact/captures here before every window. The parent's exact
    source, contract, commands, runtime and dependency identity remain required.

    A proof this invocation just made continues even when its runtime identity is not reusable across
    invocations (a global interpreter, a Node or JUnit runner): nothing outside the run changed the
    runtime between its last check and the next window, and each window still compares the identity
    it was measured with. Without that, a study larger than one window could never finish on such a
    runtime, every unrun line stayed without evidence, and the run paused for a person (#3950).
    A saved proof from an earlier invocation still needs a reusable identity.
    """
    if proof.get("verdict") != verify.PASS or not pending(proof):
        return proof
    context = proof.get("execution_context") or {}
    if not fresh and not (context.get("identity") or {}).get("reuse_supported"):
        return proof
    deadline = time.monotonic() + allowance(state)
    with runner_check.track(
        state, run_dir, "regression_proof", "Continuing changed-line checks", status.persist
    ) as progress:
        # Every admitted window must reduce this finite set. No-progress and
        # attempted failures return to ordinary independent review, not a loop.
        while pending(proof) and deadline - time.monotonic() >= minimum_window(proof):
            before = pending(proof)
            original = util.read(proof["path"])
            if (
                util.file_hash(proof["path"]) != proof.get("receipt_sha256")
                or not schedule.proof_intact(original["checks"], root=Path(proof["path"]).parent)
                or not changed_code.intact(proof, original)
                or current_identity() != context["identity"]
            ):
                raise util.Paused("PAUSED_VERIFICATION_UNCERTAIN", "Retained mutation inputs or receipts changed")
            if progress:
                progress(f"Continuing changed-line checks: {before} unattempted sites remain", phase="mutation")
            proof = _window(
                state,
                workspace,
                run_dir,
                proof,
                original,
                framework,
                inputs,
                context,
                deadline,
                current_identity,
                progress=progress,
            )
            if proof.get("verdict") != verify.PASS or pending(proof) >= before:
                break
    return proof


def _window(
    state: RunState,
    workspace,
    run_dir,
    previous,
    original,
    framework,
    inputs,
    context,
    deadline,
    current_identity,
    *,
    progress=None,
):
    started, started_at = time.monotonic(), util.now()
    root = Path(previous["path"]).parent
    identifier = uuid.uuid4().hex
    out = root / f"mutation-continuation-{identifier}"
    path = root / f"verification-continuation-{identifier}.json"
    proof, result = copy.deepcopy(previous), copy.deepcopy(original)
    tree = None
    uncertain = False
    failure = None
    try:
        tree = verify.make_tree(
            workspace,
            result["base"],
            out / "scratch" / "candidate",
            workspace,
            result["changes"],
            dependencies_from=state.get("project_workspace") or workspace,
            ignored_inputs=inputs,
        )
        measured = changed_code.collect(
            proof,
            result,
            framework,
            workspace,
            root,
            tree,
            context,
            source_paths=source_scope.paths(state),
            previous=previous,
            evidence_dir=out / "mutation-check",
            deadline=deadline,
            progress=progress,
        )
        refresh_failure = None
        if problem := mutation_checkpoint.replacement_error(previous["mutation_check"], measured):
            retained, receipts, inputs, refresh_failure = mutation_checkpoint.retain(
                previous, proof, measured, problem, deadline_expired=time.monotonic() >= deadline
            )
            proof.update(mutation_check=retained, changed_code_receipts=receipts, changed_code_inputs=inputs)
            proof["changed_code_evidence"] = changed_code.reviewed(proof, [])
            if refresh_failure["kind"] != "window_budget_exhausted":
                proof["verdict"] = verify.UNVERIFIED
                proof.setdefault("unverified", []).append(problem)
        else:
            proof["mutation_check"] = measured
        if current_identity() != context["identity"]:
            proof["verdict"] = verify.UNVERIFIED
            proof.setdefault("unverified", []).append("Execution context changed during mutation continuation")
        duration = round(time.monotonic() - started, 3)
        continuation = {
            "previous_path": previous["path"],
            "previous_sha256": previous["receipt_sha256"],
            "pending_before": pending(previous),
            "pending_after": pending(proof),
            "duration_seconds": duration,
            "reason": "Continue unattempted sites; retain every completed, failed and timed-out observation",
        }
        if refresh_failure:
            continuation["failure"] = refresh_failure
        proof.update(
            path=str(path), proved_at=util.now(), duration_seconds=duration, mutation_continuation=continuation
        )
        for key in (
            "mutation_check",
            "changed_code_inputs",
            "changed_code_evidence",
            "changed_code_receipts",
            "verdict",
            "unverified",
            "mutation_continuation",
        ):
            result[key] = copy.deepcopy(proof.get(key))
        util.atomic_json(path, result)
        proof["receipt_sha256"] = util.file_hash(path)
        state["regression_proof"] = proof
        state.setdefault("regression_proofs", []).append(
            {
                "kind": "mutation_continuation",
                "verdict": proof["verdict"],
                "path": str(path),
                "source_revision": proof["source_revision"],
                "proof_context": util.digest(context),
                "proved_at": proof["proved_at"],
                **continuation,
            }
        )
        record = {
            "stage": "regression_proof",
            "role": "runner",
            "runner_owned": True,
            "engine": "runner",
            "iteration": state.get("iteration"),
            "started_at": started_at,
            "finished_at": util.now(),
            "duration_seconds": duration,
            "output": str(path),
            "metrics": {
                "provider_tokens": {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                }
            },
            "summary": f"Changed-line checks continued: {continuation['pending_before']} → {continuation['pending_after']} unattempted sites",
        }
        if refresh_failure:
            record.update(
                error_kind="MutationReferenceUnavailable",
                reason=refresh_failure["reason"],
                summary=f"Mutation refresh unavailable; {continuation['pending_after']} unattempted sites and prior observations retained",
            )
        state.setdefault("stages", []).append(record)
        state.setdefault("history", []).append(record)
        return proof
    except BaseException as error:
        uncertain = isinstance(error, verify.command_receipt.OwnershipUncertain)
        failure = type(error).__name__
        raise
    finally:
        # Extra windows are active verification work, counted even when stopped.
        # The saved cap is never raised and the next model preflight sees the use.
        elapsed = max(0, time.monotonic() - started)
        budget_state.add_active_seconds(state, elapsed, treat_falsy_as_zero=True)
        if failure is not None:
            attempt = out / "attempt.json"
            record = {
                "stage": "regression_proof",
                "role": "runner",
                "runner_owned": True,
                "engine": "runner",
                "started_at": started_at,
                "finished_at": util.now(),
                "duration_seconds": round(elapsed, 3),
                "summary": f"Changed-line continuation stopped: {failure}",
                "output": str(attempt),
                "previous_path": previous["path"],
                "previous_sha256": previous["receipt_sha256"],
                "error_kind": failure,
                "metrics": {
                    "provider_tokens": {
                        "input_tokens": 0,
                        "cached_input_tokens": 0,
                        "output_tokens": 0,
                        "reasoning_output_tokens": 0,
                    }
                },
            }
            util.atomic_json(attempt, record)
            state.setdefault("stages", []).append(record)
            state.setdefault("history", []).append(record)
        status.persist(Path(run_dir) / "state.json", state)
        if tree is not None and not uncertain:
            verify.remove_tree(workspace, tree)
