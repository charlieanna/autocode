"""Pure progressive allowance policy; no persistence or runtime authority checks.

Callers MUST authenticate approved-work allocations, lineage, limit-change
authority/provenance, recovery grants and attempt/outcome/time receipts. Work
IDs denote runner-owned allocation units, not criterion IDs or model NEW labels.
The runner must persist returned ledgers atomically through its single writer,
retain admission/account_stage fences, and enforce checks at stage boundaries.
These records do not replace authoritative whole-run/milestone accounting.
"""

import math
from copy import deepcopy

DEFAULT_REVIEWS = 2
DEFAULT_LOCAL_SECONDS = 5400
DEFAULT_RUN_SECONDS = 43200


class BudgetExhausted(ValueError):
    """A stage-boundary allowance has been exhausted."""


def _id(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a nonempty runner-owned identity")
    return value


def _number(value, *, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (integer and type(value) is not int):
        raise ValueError("Expected a finite nonnegative budget number")
    return value


def new_ledger(
    *,
    review_limit=DEFAULT_REVIEWS,
    local_seconds_limit=DEFAULT_LOCAL_SECONDS,
    run_seconds_limit=DEFAULT_RUN_SECONDS,
    run_seconds_used=0,
    limit_provenance="default",
):
    """Create policy records; zero limits explicitly mean unlimited.

    Supply existing aggregate usage, including initial planning. Local seed time
    is attributed later, without adding that time to the aggregate again.
    Configured nondefault limits require their actual provenance, not 'default'.
    """
    limits = {
        "reviews": _number(review_limit, integer=True),
        "local_seconds": _number(local_seconds_limit),
        "run_seconds": _number(run_seconds_limit),
    }
    _id(limit_provenance)
    if limit_provenance == "default" and limits != {
        "reviews": DEFAULT_REVIEWS,
        "local_seconds": DEFAULT_LOCAL_SECONDS,
        "run_seconds": DEFAULT_RUN_SECONDS,
    }:
        raise ValueError("Configured limits need explicit provenance")
    return {
        "version": 1,
        "defaults": limits,
        "defaults_provenance": limit_provenance,
        "seed_seconds_available": run_seconds_used,
        "pools": {},
        "work_pools": {},
        "allocations": {},
        "attempts": {},
        "time_receipts": {},
        "run_seconds": _number(run_seconds_used),
        "run_limit": limits["run_seconds"],
        "run_limit_provenance": limit_provenance,
        "limit_changes": {},
    }


def allocate(
    ledger,
    allocation_id,
    pool_id,
    *,
    approved_work=(),
    inherit_work=(),
    seed_reviews=0,
    seed_seconds=0,
    review_limit=None,
    seconds_limit=None,
    provenance="default",
    recovery_grants=(),
):
    """Reserve before planning, or bind retry/replacement/split to an old pool.

    Fresh pools require explicitly supplied previously unallocated approved work.
    Inheritance must resolve to one live pool; ambiguous/missing lineage fails
    closed. Seeds and existing finite extensions apply only on pool creation.
    Recovery grants are authenticated records {id, provenance, used_by}; used_by
    is optional. Seeds are reconciled historical usage: the caller retains its
    initial attempt/accounting fences, resolves any historical refunds first,
    and never replays seeded time as a new receipt here. Seed reviews must already
    include consumed recovery calls.
    """
    _id(allocation_id)
    _id(pool_id)
    work = sorted({_id(item) for item in approved_work})
    inherited = sorted({_id(item) for item in inherit_work})
    _number(seed_reviews, integer=True)
    _number(seed_seconds)
    _id(provenance)
    grants = {}
    for grant in recovery_grants:
        key = _id(grant["id"])
        _id(grant["provenance"])
        if key in grants:
            raise ValueError("Duplicate recovery grant")
        if grant.get("used_by") is not None:
            _id(grant["used_by"])
        grants[key] = deepcopy(grant)
    request = {
        "pool": pool_id,
        "approved_work": work,
        "inherit_work": inherited,
        "seed_reviews": seed_reviews,
        "seed_seconds": seed_seconds,
        "review_limit": review_limit,
        "seconds_limit": seconds_limit,
        "provenance": provenance,
        "recovery_grants": grants,
    }
    if allocation_id in ledger["allocations"]:
        if ledger["allocations"][allocation_id] != request:
            raise ValueError("Allocation identity reused with different inputs")
        return deepcopy(ledger)
    result = deepcopy(ledger)
    if inherited:
        pools = {result["work_pools"].get(item) for item in inherited}
        if pools != {pool_id} or pool_id not in result["pools"]:
            raise ValueError("Missing or ambiguous allowance lineage")
        if (
            work
            or seed_reviews
            or seed_seconds
            or review_limit is not None
            or seconds_limit is not None
            or grants
            or provenance != "default"
        ):
            raise ValueError("Inherited work cannot replenish its pool")
    else:
        if not work or pool_id in result["pools"]:
            raise ValueError("Fresh pool requires new approved work")
        if any(item in result["work_pools"] for item in work):
            raise ValueError("Approved work already has an allowance")
        reviews = result["defaults"]["reviews"] if review_limit is None else _number(review_limit, integer=True)
        seconds = result["defaults"]["local_seconds"] if seconds_limit is None else _number(seconds_limit)
        if (review_limit is not None or seconds_limit is not None) and provenance == "default":
            raise ValueError("Existing extensions require recorded provenance")
        if seed_seconds > result["seed_seconds_available"]:
            raise ValueError("Seed time must already exist in aggregate usage")
        if sum(bool(g.get("used_by")) for g in grants.values()) > seed_reviews:
            raise ValueError("Seed reviews must include used recovery grants")
        result["pools"][pool_id] = {
            "review_limit": reviews,
            "seconds_limit": seconds,
            "reviews_used": seed_reviews,
            "seconds_used": seed_seconds,
            "provenance": (result["defaults_provenance"] if provenance == "default" else provenance),
            "recovery_grants": grants,
        }
        result["seed_seconds_available"] -= seed_seconds
        for item in work:
            result["work_pools"][item] = pool_id
    result["allocations"][allocation_id] = request
    return result


def check_budget(ledger, pool_id, *, review=False):
    """Return a boundary status record; do not impose a wall-clock watchdog."""
    pool = ledger["pools"][pool_id]
    exhausted = []
    if ledger["run_limit"] and ledger["run_seconds"] >= ledger["run_limit"]:
        exhausted.append("run_seconds")
    if pool["seconds_limit"] and pool["seconds_used"] >= pool["seconds_limit"]:
        exhausted.append("local_seconds")
    if review and pool["review_limit"] and pool["reviews_used"] >= pool["review_limit"]:
        exhausted.append("reviews")
    return {"allowed": not exhausted, "exhausted": exhausted, "pool": pool_id}


def admit(ledger, attempt_id, pool_id, *, review=False, recovery_grant=None):
    """Bind every provider attempt at launch; report-only repair uses review=False.

    One-use recovery admissions count as reviews, but are never refundable.
    Replaying an admission never charges again, even after usage exhausts limits.
    """
    _id(attempt_id)
    _id(pool_id)
    if type(review) is not bool or (recovery_grant is not None and not review):
        raise ValueError("Invalid review admission")
    binding = {"pool": pool_id, "review": review, "recovery_grant": recovery_grant}
    previous = ledger["attempts"].get(attempt_id)
    if previous is not None:
        if any(previous[key] != value for key, value in binding.items()):
            raise ValueError("Attempt identity cannot be rebound")
        return deepcopy(ledger)
    status = check_budget(ledger, pool_id, review=review)
    pool = ledger["pools"][pool_id]
    if recovery_grant is not None:
        grant = pool["recovery_grants"].get(_id(recovery_grant))
        if not grant or grant.get("used_by"):
            raise ValueError("Unknown or consumed recovery grant")
        status["exhausted"] = [item for item in status["exhausted"] if item != "reviews"]
    if status["exhausted"]:
        raise BudgetExhausted(", ".join(status["exhausted"]))
    result = deepcopy(ledger)
    result["attempts"][attempt_id] = {**binding, "refunded": False, "outcome": None}
    if review:
        result["pools"][pool_id]["reviews_used"] += 1
    if recovery_grant is not None:
        result["pools"][pool_id]["recovery_grants"][recovery_grant]["used_by"] = attempt_id
    return result


def refund_review(ledger, attempt_id, *, exit_code, timed_out=False):
    """Apply one authenticated terminal outcome; exit-zero rejection still counts."""
    if type(exit_code) is not int or type(timed_out) is not bool:
        raise ValueError("Expected a terminal process outcome")
    outcome = {"exit_code": exit_code, "timed_out": timed_out}
    attempt = ledger["attempts"][attempt_id]
    if attempt["outcome"] is not None and attempt["outcome"] != outcome:
        raise ValueError("Conflicting terminal outcome")
    result = deepcopy(ledger)
    row = result["attempts"][attempt_id]
    row["outcome"] = outcome
    if row["review"] and row["recovery_grant"] is None and not row["refunded"] and (timed_out or exit_code != 0):
        result["pools"][row["pool"]]["reviews_used"] -= 1
        row["refunded"] = True
    return result


def record_time(ledger, receipt_id, attempt_id, seconds):
    """Account one terminal duration per attempt, against its launch pool.

    Includes failed/rejected attempts and repairs. The receipt is incremental
    usage not already included in new_ledger's aggregate or a pool's seed.
    """
    _id(receipt_id)
    _number(seconds)
    pool_id = ledger["attempts"][attempt_id]["pool"]
    receipt = {"attempt": attempt_id, "pool": pool_id, "seconds": seconds}
    previous = ledger["time_receipts"].get(receipt_id)
    if previous is not None:
        if previous != receipt:
            raise ValueError("Conflicting time receipt")
        return deepcopy(ledger)
    if any(row["attempt"] == attempt_id for row in ledger["time_receipts"].values()):
        raise ValueError("Attempt time already accounted under another receipt")
    result = deepcopy(ledger)
    result["time_receipts"][receipt_id] = receipt
    result["pools"][pool_id]["seconds_used"] += seconds
    result["run_seconds"] += seconds
    return result


def change_limit(ledger, change_id, kind, limit, *, provenance, explicit, pool_id=None):
    """Apply an authenticated explicit ceiling change without erasing history.

    No automatic default-limit recovery exists here. Provenance is supplied by
    the existing auditable caller; explicit=True is not itself authorization.
    """
    _id(change_id)
    _id(provenance)
    if explicit is not True or kind not in ("reviews", "local_seconds", "run_seconds"):
        raise ValueError("Budget changes require explicit authority and a known limit")
    _number(limit, integer=kind == "reviews")
    if (kind == "run_seconds") != (pool_id is None):
        raise ValueError("Local changes require a pool; aggregate changes do not")
    change = {"kind": kind, "limit": limit, "pool": pool_id, "provenance": provenance}
    if change_id in ledger["limit_changes"]:
        if ledger["limit_changes"][change_id] != change:
            raise ValueError("Conflicting limit-change identity")
        return deepcopy(ledger)
    result = deepcopy(ledger)
    if kind == "run_seconds":
        result["run_limit"] = limit
        result["run_limit_provenance"] = provenance
    else:
        key = "review_limit" if kind == "reviews" else "seconds_limit"
        result["pools"][pool_id][key] = limit
        result["pools"][pool_id]["provenance"] = provenance
    result["limit_changes"][change_id] = change
    return result
