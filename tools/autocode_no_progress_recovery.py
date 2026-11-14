"""Classify default Builder no-progress holds and settle repaired acceptance proofs.

When earlier interrupted or unchanged Builder attempts fill the default
no-progress limit before a newly verified acceptance proof returns control to
the build stage, AutoCode grants one bounded recovery through the existing
independently reviewed Resolver policy bound to that exact task, contract,
source and proof. When no safe automatic action remains, it records a concrete
failed outcome with retained evidence instead of asking a person about
``--no-progress-limit`` or retry machinery.

This module sits below controllers and never imports ``autocode_dispatch``,
``autocode_build_loop``, ``autocode_run_view``, or ``autocode_resolver_runtime``.
"""

from __future__ import annotations

import copy
import datetime as dt
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_acceptance_verification_retry as acceptance_retry
    from . import autocode_contract_identity as contracts
    from . import autocode_interventions as interventions
    from . import autocode_project_kinds as project_kinds
    from . import autocode_recovery_limits as recovery_limits
    from . import autocode_resolver as policy
    from . import autocode_resolver_human as resolver_human
    from . import autocode_run_status as run_status
    from . import autocode_source_scope as source_scope
    from . import autocode_stop as stop
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_tests as acceptance_tests
    import autocode_acceptance_verification_retry as acceptance_retry
    import autocode_contract_identity as contracts
    import autocode_interventions as interventions
    import autocode_project_kinds as project_kinds
    import autocode_recovery_limits as recovery_limits
    import autocode_resolver as policy
    import autocode_resolver_human as resolver_human
    import autocode_run_status as run_status
    import autocode_source_scope as source_scope
    import autocode_stop as stop
    import autocode_util as util


RECOVERY_SCOPE = "repaired_acceptance_no_progress"
EXHAUSTED_SCOPE = "no_progress_exhaustion"
NOTICE = "AutoCode will settle the retained no-progress hold against current verification evidence."
_DEFAULT_ORIGINS = frozenset({"runner_default", "resolver_delegated"})
_BUILD_STAGES = frozenset({"builder", "orchestrator"})
_DECISION_FIELDS = frozenset(field.name for field in fields(policy.Decision))
_RECEIPT_FIELDS = frozenset(field.name for field in fields(policy.Receipt)) - {"version"}


def notice(state: RunState) -> str:
    """Plain-English notice for an unacknowledged pre-fix default no-progress hold."""
    return NOTICE


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.timestamp()


def _load_ledger(saved: Mapping[str, Any]) -> policy.Ledger:
    attempts = saved.get("attempts") or {}
    outcomes = saved.get("outcomes") or {}
    raw_cache = saved.get("cache") or {}
    if not isinstance(attempts, Mapping) or not isinstance(outcomes, Mapping) or not isinstance(raw_cache, Mapping):
        raise ValueError("Malformed resolver ledger container")
    if any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in attempts.items()):
        raise ValueError("Malformed resolver ledger attempts")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in outcomes.items()):
        raise ValueError("Malformed resolver ledger outcomes")
    cache: dict[str, tuple[policy.Decision, policy.Receipt]] = {}
    for key, pair in raw_cache.items():
        if not isinstance(key, str) or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Malformed resolver cache entry")
        raw_decision, raw_receipt = pair
        if not isinstance(raw_decision, Mapping) or set(raw_decision) != _DECISION_FIELDS:
            raise ValueError("Malformed cached resolver decision")
        if not isinstance(raw_receipt, Mapping):
            raise ValueError("Malformed cached resolver receipt")
        version = raw_receipt.get("version", 1)
        if type(version) is not int or version not in policy.SUPPORTED_RECEIPT_VERSIONS:
            raise ValueError("Unsupported cached resolver receipt version")
        if set(raw_receipt) - {"version"} != _RECEIPT_FIELDS:
            raise ValueError("Malformed cached resolver receipt fields")
        cache[key] = (
            policy.Decision(**raw_decision),
            policy.Receipt(**{**raw_receipt, "version": version}),
        )
    return policy.Ledger(attempts=dict(attempts), outcomes=dict(outcomes), cache=cache)


def _builder_row_unchanged(row: Mapping[str, Any]) -> bool:
    if (
        row.get("timed_out")
        or row.get("interrupted")
        or row.get("rejected")
        or row.get("abandoned")
        or row.get("report_only")
    ):
        return True
    if type(row.get("exit_code")) is not int or row["exit_code"] != 0:
        return True
    return not bool(row.get("changed_files"))


def _contract_approved(state: RunState) -> bool:
    try:
        return bool(contracts.approved(state))
    except (KeyError, TypeError, AttributeError, ValueError):
        return False


def default_builder_hold(state: RunState) -> dict[str, Any] | None:
    """Return structured details of a runner-default Builder no-progress hold, or None."""
    stage = state.get("next_stage")
    if stage not in _BUILD_STAGES:
        return None
    settings = state.get("settings") or {}
    limits = settings.get("limits") or {}
    limit = limits.get("no_progress_batches")
    count = state.get("no_progress_batches", 0)
    if type(limit) is not int or limit < 1 or type(count) is not int or count < limit:
        return None
    origin = (settings.get("budget_origins") or {}).get("no_progress_batches")
    if origin not in _DEFAULT_ORIGINS or not _contract_approved(state):
        return None
    contract = state.get("goal_contract") or {}
    contract_hash = contract.get("hash")
    if not isinstance(contract_hash, str) or not contract_hash:
        return None
    stages = state.get("stages") or []
    trailing: list[tuple[int, dict[str, Any]]] = []
    for index in range(len(stages) - 1, -1, -1):
        row = stages[index]
        if not isinstance(row, dict) or row.get("runner_owned") or row.get("accounted") is not True:
            continue
        semantic = row.get("original_stage") or row.get("stage")
        if semantic != "builder":
            continue
        if not _builder_row_unchanged(row):
            break
        trailing.append((index, row))
    if not trailing:
        return None
    trailing.reverse()
    last_index, last_row = trailing[-1]
    return {
        "stage": stage,
        "count": count,
        "limit": limit,
        "origin": origin,
        "contract_hash": contract_hash,
        "last_builder_index": last_index,
        "last_builder_finished_at": last_row.get("finished_at"),
        "builder_attempts": [
            {
                "stage_index": index,
                "output": row.get("output"),
                "finished_at": row.get("finished_at"),
                "timed_out": bool(row.get("timed_out")),
                "interrupted": bool(row.get("interrupted")),
                "rejected": bool(row.get("rejected")),
                "changed_files": list(row.get("changed_files") or []),
            }
            for index, row in trailing
        ],
    }


def _legacy_no_progress_request(state: RunState) -> dict[str, Any] | None:
    issued = resolver_human.retained(state)
    if not issued or issued.get("scope") != "operational_exhaustion":
        return None
    escalations = (state.get("resolver") or {}).get("human_escalations") or {}
    escalation = escalations.get(issued.get("request_id")) if isinstance(escalations, Mapping) else None
    if not isinstance(escalation, Mapping):
        return None
    origin = ((escalation.get("identity") or {}).get("proposal") or {}).get("origin") or {}
    if origin.get("pause_status") != "PAUSED_NO_PROGRESS" or origin.get("stage") not in _BUILD_STAGES:
        return None
    return issued


def unsettled_reasons(
    state: RunState,
    run_dir: Path | str | None = None,
    *,
    allow_retained_no_progress: bool = False,
) -> list[str]:
    """List unsettled execution, ownership, approval, or queue conditions blocking automation."""
    retained_req = _legacy_no_progress_request(state) if allow_retained_no_progress else None
    reasons = [key for key in recovery_limits.UNSETTLED if state.get(key)]
    if state.get("pause_requested"):
        reasons.append("pause_requested")
    if state.get("pending_questions") and not (
        retained_req is not None and state.get("pending_questions") == retained_req.get("questions")
    ):
        reasons.append("pending_questions")
    if stop.applied_stop(state) is not None:
        reasons.append("applied_stop")
    user_req = state.get("user_request")
    if (
        isinstance(user_req, Mapping)
        and user_req.get("kind") not in (None, "none")
        and not (retained_req is not None and user_req == retained_req.get("request"))
    ):
        reasons.append("user_request")
    agent_req = (state.get("agent_request") or {}).get("request")
    if isinstance(agent_req, Mapping) and agent_req.get("kind") not in (None, "none") and retained_req is None:
        reasons.append("agent_request")
    if run_dir is not None:
        run_path = Path(run_dir)
        if (run_path / "pause-requested").exists():
            reasons.append("pause_requested_file")
        if stop.pending_stop(run_path) is not None:
            reasons.append("pending_stop")
        interruptions = resolver_human.pending_interruptions(run_path)
        for key in ("pending", "pause_requested", "error"):
            if interruptions.get(key):
                reasons.append(f"pending_interruption:{key}")
    return reasons


def _safe_workspace_target(root: Path, rel_path: str) -> Path | None:
    if not isinstance(rel_path, str) or not rel_path or rel_path.startswith("/"):
        return None
    rel = Path(rel_path)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    candidate = root / rel
    try:
        for ancestor in (candidate, *candidate.parents):
            if ancestor == root:
                break
            if ancestor.is_symlink():
                return None
        resolved_parent = candidate.parent.resolve()
        if resolved_parent != root and root not in resolved_parent.parents:
            return None
    except OSError:
        return None
    return candidate


def _matching_writer_record(
    state: RunState,
    output: Any,
    *,
    contract_hash: str,
    allowed_task_ids: set[str],
    after_index: int,
) -> tuple[int, dict[str, Any], Path, str] | None:
    if not isinstance(output, str) or not output:
        return None
    matches: list[tuple[int, dict[str, Any]]] = []
    for index, row in enumerate(state.get("stages") or []):
        if not isinstance(row, dict) or row.get("runner_owned"):
            continue
        if (row.get("original_stage") or row.get("stage")) != acceptance_tests.STAGE:
            continue
        if row.get("output") == output:
            matches.append((index, row))
    if len(matches) != 1:
        return None
    writer_index, writer_record = matches[0]
    if writer_index <= after_index:
        return None
    if (
        writer_record.get("accounted") is not True
        or type(writer_record.get("exit_code")) is not int
        or writer_record["exit_code"] != 0
        or any(writer_record.get(flag) for flag in ("timed_out", "interrupted", "rejected", "abandoned", "report_only"))
        or writer_record.get("contract_hash") != contract_hash
        or writer_record.get("task_id") not in allowed_task_ids
    ):
        return None
    writer_finished = _timestamp(writer_record.get("finished_at"))
    for batch in state.get("orchestration_history") or []:
        if not isinstance(batch, Mapping):
            continue
        batch_finished = _timestamp(batch.get("finished_at"))
        if writer_finished is not None and batch_finished is not None and batch_finished >= writer_finished:
            return None
    output_path = Path(output)
    if not output_path.is_file():
        return None
    try:
        output_sha = util.file_hash(output_path)
    except OSError:
        return None
    return writer_index, writer_record, output_path, output_sha


def _verification_retry_intact(
    state: RunState,
    writer_record: Mapping[str, Any],
    output_path: Path,
    contract_hash: str,
) -> int | None:
    saved = state.get(acceptance_tests.KEY) or {}
    retries = saved.get("verification_retries") or []
    matching = [
        row
        for row in retries
        if isinstance(row, Mapping) and (row.get("record") or {}).get("output") == writer_record.get("output")
    ]
    if not matching:
        return 0
    latest = matching[-1]
    attempts = latest.get("attempts")
    if type(attempts) is not int or attempts < 1:
        return None
    pinned_record = latest.get("record")
    if not isinstance(pinned_record, Mapping) or pinned_record.get("contract_hash") != contract_hash:
        return None
    pins = latest.get("pins")
    expected_paths = {writer_record.get(key) for key in acceptance_retry.ARTIFACTS}
    if not isinstance(pins, Mapping) or set(pins) != expected_paths or any(not isinstance(p, str) for p in pins):
        return None
    try:
        for pin_path, pin_sha in pins.items():
            target = Path(pin_path)
            if not target.is_file() or util.file_hash(target) != pin_sha:
                return None
        if util.read(output_path) != latest.get("value"):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return attempts


def _workspace_proof_matches(
    state: RunState,
    receipt: Mapping[str, Any],
    writer_record: Mapping[str, Any],
    root: Path,
    live_snapshot: Mapping[str, Any],
) -> bool:
    if receipt.get("source_revision") != writer_record.get("source_revision"):
        return False
    files = receipt.get("files")
    if not isinstance(files, Mapping) or not files:
        return False
    if not acceptance_tests._intact(dict(receipt)):
        return False
    blind = acceptance_tests.is_blind(state)
    for rel_path, meta in files.items():
        target = _safe_workspace_target(root, rel_path)
        if target is None or not isinstance(meta, Mapping) or not isinstance(meta.get("sha256"), str):
            return False
        is_prereq = Path(rel_path).name in project_kinds.ALL_TEST_PREREQUISITES
        if blind and not is_prereq:
            if target.exists() or target.is_symlink():
                return False
        else:
            if not target.is_file() or target.is_symlink():
                return False
            try:
                if util.file_hash(target) != meta["sha256"]:
                    return False
            except OSError:
                return False
    if not blind:
        return live_snapshot.get("revision") == receipt.get("source_revision")
    before_ref = writer_record.get("before_ref")
    if not isinstance(before_ref, str) or not before_ref:
        return False
    before_path = Path(before_ref)
    if not before_path.is_file():
        return False
    try:
        before_snap = util.read(before_path)
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(before_snap, Mapping):
        return False
    expected_files = dict(before_snap.get("files") or {})
    for rel_path, meta in files.items():
        if Path(rel_path).name in project_kinds.ALL_TEST_PREREQUISITES:
            expected_files[rel_path] = meta["sha256"]
    return live_snapshot.get("head") == before_snap.get("head") and live_snapshot.get("files") == expected_files


def authenticated_intervening_proof(
    state: RunState,
    workspace: Path | str,
    *,
    wave: Any = None,
) -> dict[str, Any] | None:
    """Authenticate an intervening verified acceptance proof strictly newer than the Builder hold."""
    hold = default_builder_hold(state)
    if hold is None or not acceptance_tests.enabled(state):
        return None
    saved = state.get(acceptance_tests.KEY) or {}
    if saved.get(acceptance_retry.KEY) is not None:
        return None
    try:
        if acceptance_tests.due(state, wave):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    current_task = state.get("current_task") or {}
    task_id = current_task.get("id")
    if not isinstance(task_id, str) or not task_id:
        return None
    allowed_task_ids = {task_id}
    root = Path(workspace).resolve()
    try:
        live_snapshot = source_scope.snapshot(root, state)
    except (OSError, ValueError, TypeError):
        return None
    contract = cast(dict, state["goal_contract"])
    best: tuple[int, dict[str, Any]] | None = None
    for bkey, receipt in acceptance_tests.receipts(state).items():
        if not isinstance(receipt, Mapping) or receipt.get("contract_hash") != hold["contract_hash"]:
            continue
        case_ids = receipt.get("case_ids")
        if not isinstance(case_ids, list) or not case_ids or any(not isinstance(cid, str) for cid in case_ids):
            continue
        matched = _matching_writer_record(
            state,
            receipt.get("output"),
            contract_hash=hold["contract_hash"],
            allowed_task_ids=allowed_task_ids,
            after_index=hold["last_builder_index"],
        )
        if matched is None:
            continue
        writer_index, writer_record, output_path, output_sha = matched
        retry_attempts = _verification_retry_intact(state, writer_record, output_path, hold["contract_hash"])
        if retry_attempts is None:
            continue
        if not _workspace_proof_matches(state, receipt, writer_record, root, live_snapshot):
            continue
        file_hashes = {path: str(meta["sha256"]) for path, meta in sorted((receipt.get("files") or {}).items())}
        binding_payload = {
            "contract_hash": hold["contract_hash"],
            "contract_revision": contract.get("revision"),
            "task_id": task_id,
            "task_digest": util.digest(current_task),
            "stage": hold["stage"],
            "batch_key": bkey,
            "writer_output": writer_record["output"],
            "writer_output_sha256": output_sha,
            "receipt_source_revision": receipt.get("source_revision"),
            "workspace_revision": live_snapshot.get("revision"),
            "case_ids": list(case_ids),
            "files": file_hashes,
            "verification_retry_attempts": retry_attempts,
        }
        proof = {**binding_payload, "writer_index": writer_index, "proof_digest": util.digest(binding_payload)}
        if best is None or writer_index > best[0]:
            best = (writer_index, proof)
    return best[1] if best is not None else None


def proof_already_spent(state: RunState, proof: Mapping[str, Any]) -> bool:
    """True when this intervening acceptance proof already authorized a no-progress recovery."""
    receipts = (state.get("resolver") or {}).get("operational_receipts") or {}
    if not isinstance(receipts, Mapping):
        return False
    digest = proof.get("proof_digest")
    writer_output = proof.get("writer_output")
    for row in receipts.values():
        if not isinstance(row, Mapping):
            continue
        inner = row.get("receipt") or {}
        if inner.get("scope") != RECOVERY_SCOPE:
            continue
        evidence = inner.get("evidence") or {}
        if (digest and evidence.get("proof_digest") == digest) or (
            writer_output and evidence.get("writer_output") == writer_output
        ):
            return True
    return False


def _prior_recovery_ids(state: RunState) -> list[str]:
    receipts = (state.get("resolver") or {}).get("operational_receipts") or {}
    if not isinstance(receipts, Mapping):
        return []
    return [
        str(identity)
        for identity, row in receipts.items()
        if isinstance(row, Mapping) and (row.get("receipt") or {}).get("scope") == RECOVERY_SCOPE
    ]


def _append_operational_record(
    state: RunState,
    run_dir: Path | str,
    *,
    scope: str,
    prefix: str,
    action: str,
    summary: str,
    evidence: dict[str, Any],
    at: str,
) -> str:
    resolver = state.setdefault("resolver", {})
    identities = resolver.setdefault("operational_diagnostics", [])
    receipts = resolver.setdefault("operational_receipts", {})
    identity = f"{prefix}-{len(identities) + 1}"
    path = Path(run_dir) / "resolver" / f"{identity}.json"
    record = {
        "stage": "resolver",
        "role": "resolver",
        "engine": "runner",
        "runner_owned": True,
        "iteration": state.get("iteration"),
        "finished_at": at,
        "exit_code": 0,
        "runner_calls": 0,
        "output": str(path),
        "summary": summary,
        "metrics": {"provider_tokens": {"input_tokens": 0, "output_tokens": 0}},
        "decision": {"action": action, "rationale": summary},
        "receipt": {"id": identity, "callbacks_used": [], "scope": scope, "evidence": evidence},
    }
    util.atomic_json(path, record)
    receipts[identity] = copy.deepcopy(record)
    identities.append(identity)
    state.setdefault("stages", []).append(copy.deepcopy(record))
    state.setdefault("history", []).append(copy.deepcopy(record))
    return identity


def recover(
    runtime: Any,
    state: RunState,
    run_dir: Path | str,
    workspace: Path | str,
    *,
    wave: Any = None,
) -> bool:
    """Grant one bounded recovery for a repaired acceptance proof through independent review."""
    run_path = Path(run_dir)
    with interventions.serialized(run_path):
        hold = default_builder_hold(state)
        if hold is None or unsettled_reasons(state, run_path):
            return False
        proof = authenticated_intervening_proof(state, workspace, wave=wave)
        if proof is None or proof_already_spent(state, proof):
            return False
        contract = cast(dict, state["goal_contract"])
        saved_resolver = state.setdefault("resolver", {})
        try:
            ledger = _load_ledger(saved_resolver)
            snapshot = policy.ContractSnapshot(
                **{
                    key: contract[key]
                    for key in ("task_id", "revision", "hash", "body", "approval_status", "approval_event")
                },
                origin=contract.get("origin") or "",
            )
        except (KeyError, TypeError, ValueError):
            return False
        new_limit = hold["count"] + 1
        blocker = policy.Blocker(
            id=f"repaired-acceptance-{str(proof['proof_digest'])[:16]}",
            kind="implementation",
            description="Default Builder no-progress limit reached before newly verified acceptance tests",
            evidence_refs=(str(proof["writer_output"]), str(proof["batch_key"]), *sorted(proof["files"])),
            risk_flags=(),
        )
        context = {
            "kind": "no_progress_batches",
            "from": hold["limit"],
            "to": new_limit,
            "used": hold["count"],
            "stage": hold["stage"],
            "origin": hold["origin"],
            "contract_hash": contract["hash"],
            "contract_revision": contract["revision"],
            "task_id": proof["task_id"],
            "task_digest": proof["task_digest"],
            "source_revision": proof["workspace_revision"],
        }
        boundaries = policy.Boundaries(allowed_actions=frozenset({"continue"}), allowed_fields=frozenset())

        def propose(request: policy.ResolverRequest) -> policy.Proposal:
            return policy.Proposal(
                action="continue",
                payload={
                    "reason": (
                        f"Extend default no_progress_batches from {hold['limit']} to {new_limit} while preserving "
                        f"used count {hold['count']} after verified acceptance proof {proof['batch_key']}"
                    ),
                    "guidance": (
                        "Proceed to the build stage with the newly verified acceptance tests while retaining all "
                        "failed Builder attempts, selected models and spending caps."
                    ),
                    "evidence_refs": [str(proof["writer_output"]), str(proof["batch_key"])],
                },
                rationale="Verified acceptance tests landed after the prior Builder no-progress streak",
            )

        def review(request: policy.ResolverRequest, proposal: policy.Proposal) -> policy.Review:
            fresh_hold = default_builder_hold(state)
            fresh_unsettled = unsettled_reasons(state, run_path)
            fresh_proof = authenticated_intervening_proof(state, workspace, wave=wave)
            if (
                proposal.action != "continue"
                or fresh_hold is None
                or fresh_unsettled
                or fresh_proof is None
                or fresh_proof["proof_digest"] != proof["proof_digest"]
                or proof_already_spent(state, fresh_proof)
                or request.context.get("to") != fresh_hold["count"] + 1
                or request.contract.hash != contract.get("hash")
            ):
                return policy.Review(
                    approved=False, rationale="Independently verified proof or boundary no longer matches"
                )
            return policy.Review(
                approved=True,
                rationale="Independently confirmed bound recovery against current contract, source, and acceptance proof",
            )

        decision, receipt = policy.resolve(
            policy.ResolverRequest(
                blocker=blocker,
                contract=snapshot,
                context=context,
                evidence=proof,
                boundaries=boundaries,
                ledger=ledger,
                propose=propose,
                review=review,
                clock=runtime.now,
            )
        )
        saved_resolver.update(
            attempts=ledger.attempts,
            outcomes=ledger.outcomes,
            cache={key: _plain(pair) for key, pair in ledger.cache.items()},
        )
        if (
            receipt.in_scope_reason != "proposal within boundaries"
            or receipt.review_verdict != "approved"
            or decision.action != "continue"
        ):
            runtime.write_json(run_path / "state.json", state)
            return False

        settings = state["settings"]
        settings["limits"]["no_progress_batches"] = new_limit
        settings.setdefault("budget_origins", {})["no_progress_batches"] = "resolver_delegated"
        at = runtime.now()
        summary = (
            f"AutoCode granted one bounded Builder attempt ({hold['limit']} -> {new_limit}, preserving "
            f"used count {hold['count']}) after verifying intervening acceptance proof {proof['batch_key']}."
        )
        _append_operational_record(
            state,
            run_path,
            scope=RECOVERY_SCOPE,
            prefix="no-progress-proof-recovery",
            action="continue",
            summary=summary,
            evidence={
                "grant_kind": "extend_default_no_progress_limit",
                "from": hold["limit"],
                "to": new_limit,
                "used": hold["count"],
                "hold": hold,
                **proof,
                "resolver_idempotency_key": receipt.idempotency_key,
            },
            at=at,
        )
        runtime.write_json(run_path / "state.json", state)
        return True


def conclude_exhausted(
    runtime: Any,
    state: RunState,
    run_dir: Path | str,
    workspace: Path | str,
    error: Exception | None = None,
    *,
    wave: Any = None,
    superseded_request_id: str | None = None,
) -> bool:
    """Record a concrete failed outcome when a default Builder hold has no safe recovery left."""
    if error is not None:
        if getattr(error, "status", None) != "PAUSED_NO_PROGRESS":
            return False
        if not recovery_limits.no_progress_bound_holds(state, str(error)):
            return False
    hold = default_builder_hold(state)
    if hold is None:
        return False
    run_path = Path(run_dir)
    if unsettled_reasons(state, run_path):
        return False
    proof = authenticated_intervening_proof(state, workspace, wave=wave)
    if proof is not None and not proof_already_spent(state, proof):
        return False
    at = runtime.now()
    prior_recoveries = _prior_recovery_ids(state)
    if prior_recoveries:
        reason = (
            f"Builder did not produce accepted source progress after {hold['count']} attempts "
            f"(limit {hold['limit']}, including one proof-authorized attempt), and no further safe automatic recovery remains."
        )
    else:
        reason = (
            f"Builder did not produce accepted source progress across {hold['count']} attempts "
            f"(default limit {hold['limit']}), and no intervening verified acceptance correction justifies another attempt."
        )
    evidence: dict[str, Any] = {
        "pause_status": "PAUSED_NO_PROGRESS",
        "hold": hold,
        "prior_proof_recoveries": prior_recoveries,
        "spent_proof_digest": proof.get("proof_digest") if proof else None,
        "stuck_investigation": copy.deepcopy(state.get("stuck_investigation")),
    }
    if superseded_request_id:
        evidence["superseded_request_id"] = superseded_request_id
    _append_operational_record(
        state,
        run_path,
        scope=EXHAUSTED_SCOPE,
        prefix="no-progress-exhausted",
        action="could_not_finish",
        summary=reason,
        evidence=evidence,
        at=at,
    )
    stop.end(state, reason, evidence, at=at)
    runtime.write_json(run_path / "state.json", state)
    return True


def pending_legacy(state: RunState) -> dict[str, Any] | None:
    """Recognize an unanswered pre-fix default Builder no-progress hold without changing status."""
    status = state.get("status")
    if status not in ("WAITING_FOR_USER", "PAUSED_NO_PROGRESS"):
        return None
    if default_builder_hold(state) is None:
        return None
    if not recovery_limits.no_progress_bound_holds(state):
        return None
    if status == "WAITING_FOR_USER":
        issued = _legacy_no_progress_request(state)
        if issued is None:
            return None
        if unsettled_reasons(state, None, allow_retained_no_progress=True):
            return None
        return issued
    if unsettled_reasons(state, None, allow_retained_no_progress=False):
        return None
    return {"scope": "no_progress_default", "request_id": None}


def reconcile_legacy(
    runtime: Any,
    state: RunState,
    run_dir: Path | str,
    workspace: Path | str,
    *,
    wave: Any = None,
) -> Literal["continue", "ended"] | None:
    """Settle a pre-fix default Builder no-progress checkpoint at explicit resume."""
    pending = pending_legacy(state)
    if pending is None:
        return None
    run_path = Path(run_dir)
    allow_req = state.get("status") == "WAITING_FOR_USER"
    if unsettled_reasons(state, run_path, allow_retained_no_progress=allow_req):
        return None
    superseded_id = pending.get("request_id")
    if superseded_id is not None:
        reason = "AutoCode settled the retained default no-progress hold against current verification evidence"
        if not resolver_human.supersede_operational(state, reason):
            return None
    proof = authenticated_intervening_proof(state, workspace, wave=wave)
    if proof is not None and not proof_already_spent(state, proof):
        run_status.running(state, "EXECUTING", next_stage=cast(str, state.get("next_stage")))
        state.pop("stop_reason", None)
        runtime.write_json(run_path / "state.json", state)
        return "continue"
    if conclude_exhausted(
        runtime,
        state,
        run_dir,
        workspace,
        wave=wave,
        superseded_request_id=superseded_id,
    ):
        return "ended"
    return None
