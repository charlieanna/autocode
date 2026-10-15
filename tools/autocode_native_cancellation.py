"""Authenticate terminal cancellation of one original held readonly planning root.

No controller, state reader, process launch or runtime migration. The original
native owner supplies its canonical rows, current binding and safe file readers.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import math
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_process as processes
    from . import autocode_supervision as supervision
    from . import autocode_util as util
    from . import autocode_visual_history as history
except ImportError:
    import autocode_process as processes
    import autocode_supervision as supervision
    import autocode_util as util
    import autocode_visual_history as history

HELD = "planning-held"
CANCELLED = "planning-cancelled"


def require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native held cancellation: " + reason)


def readonly(preparation):
    require(
        not preparation["grants"]
        and preparation["boundary"]["allow_write"] is False
        and preparation["binding"]["task"] is None,
        "not original readonly preapproval planning",
    )


def held_body(prepared_id, preparation, metadata):
    readonly(preparation)
    require(
        type(metadata.get("schema")) is int
        and metadata["schema"] == 1
        and isinstance(metadata.get("nonce"), str)
        and len(metadata["nonce"]) == 32
        and all(c in "0123456789abcdef" for c in metadata["nonce"]),
        "missing original supervisor identity",
    )
    for key in ("owner", "keeper", "provider"):
        identity = metadata[key]
        birth = identity.get("birth_identity")
        require(
            type(identity.get("pid")) is int
            and identity["pid"] > 0
            and type(birth) in (int, float)
            and math.isfinite(birth)
            and birth > 0,
            "invalid original birth identity",
        )
    require(
        metadata["provider"].get("group") == metadata["provider"]["pid"]
        and metadata["receipt"] == str(Path(preparation["record_identity"]["output"]).with_suffix(".supervision.json")),
        "foreign original root session/receipt",
    )
    return {
        "prepared_id": prepared_id,
        "supervision": deepcopy(metadata),
        "root": deepcopy(metadata["provider"]),
        "command_sha256": preparation["command_sha256"],
        "environment_sha256": preparation["environment_sha256"],
    }


def held_row(rows, ident, preparation):
    matches = [row for row in rows if row["kind"] == HELD and row["body"]["prepared_id"] == ident]
    require(len(matches) == 1, "missing/ambiguous original held-supervisor admission")
    row = matches[0]
    require(
        row["body"] == held_body(ident, preparation, row["body"]["supervision"]),
        "changed original held-supervisor admission",
    )
    return row


def stop_facts(record):
    require(
        record.get("accounted") is True
        and type(record.get("exit_code")) is int
        and record["exit_code"] != 0
        and record.get("timed_out") is True
        and record.get("timeout_kind") in ("idle", "tool", "stage")
        and isinstance(record.get("timeout_reason"), str)
        and bool(record["timeout_reason"])
        and isinstance(record.get("finished_at"), str)
        and bool(record["finished_at"])
        and not any(
            record.get(k)
            for k in (
                "cleanup_error",
                "supervision_errors",
                "interrupted",
                "report_only",
                "dry_run",
                "rejected",
                "abandoned",
            )
        ),
        "not a collected original deadline-cancelled planning stop",
    )
    return {
        key: deepcopy(record[key])
        for key in ("accounted", "exit_code", "timed_out", "timeout_kind", "timeout_reason", "finished_at")
    }


def stopped(metadata, record):
    require(
        record.get("supervision") == metadata
        and record.get("pid") == metadata["provider"]["pid"]
        and isinstance(record.get("processes"), list),
        "record differs from original held supervisor",
    )
    receipt = supervision.receipt(metadata)
    require(
        isinstance(receipt, dict)
        and receipt.get("phase") == "stopped"
        and (
            receipt.get("cause") == "provider_stopped"
            or receipt.get("cause") == "stage_deadline"
            and record.get("timeout_kind") == "stage"
        )
        and not receipt.get("cleanup_error")
        and isinstance(receipt.get("processes"), list)
        and bool(receipt["processes"])
        and any(processes.matches(metadata["provider"], item) for item in receipt["processes"]),
        "original supervisor did not retain clean stopped-root proof",
    )
    originals = [
        *record["processes"],
        *receipt["processes"],
        metadata["provider"],
        metadata["keeper"],
        metadata["owner"],
    ]
    require(not processes.live_processes(originals), "original owner/keeper/root/descendants remain live or uncertain")
    return receipt


def authenticate(
    *,
    rows,
    prepared,
    sealed,
    released,
    finished,
    state: "RunState",
    record,
    snapshot,
    binding,
    fresh_snapshot,
    safe,
    read_json,
    check_record,
):
    require(state.get("status") == "PAUSED_PROCESS_CLEANUP", "not the original cleanup checkpoint")
    stop = stop_facts(record)
    pending = set(prepared) - set(finished)
    require(
        len(pending) == 1 and not any(row["kind"].startswith("approval-") for row in rows),
        "missing sole unapproved held planning tail",
    )
    ident = next(iter(pending))
    original = prepared[ident]["body"]
    readonly(original)
    require(ident not in released, "released launch is outside held cancellation")
    held = held_row(rows, ident, original)
    metadata = held["body"]["supervision"]
    if ident in sealed:
        require(
            sealed[ident]["body"]["root"] == metadata["provider"]
            and sealed[ident]["body"]["barrier"]["supervision_envelope"]["supervision"] == metadata,
            "seal differs from original held admission",
        )
    require(
        original["binding"] == binding and snapshot == rows[0]["body"]["source"] == fresh_snapshot,
        "source/settings/runtime changed after original held admission",
    )
    retained = deepcopy(record)
    retained.update(source_revision=snapshot["revision"], after_ref=original["record_identity"]["after_ref"])
    check_record(original["record_identity"], history.record_identity(retained))
    safe(metadata["receipt"], file=True)
    stopped(metadata, record)
    require(read_json(safe(record["before_ref"], file=True).read_bytes()) == snapshot, "original before-image changed")
    after = safe(retained["after_ref"])
    if after.exists():
        require(read_json(safe(str(after), file=True).read_bytes()) == snapshot, "retained after-image changed")
    pins = {
        name: util.file_hash(safe(name, file=True))
        for name in (record["events"], record["before_ref"], metadata["receipt"])
    }
    for key in ("output", "reported_output"):
        name = record.get(key)
        if name and safe(name).exists():
            pins[name] = util.file_hash(safe(name, file=True))
    retained.update(rejected=True, abandoned=True)
    terminal = {
        "prepared_id": ident,
        "held_id": held["id"],
        "record": history.record_identity(retained),
        "after": deepcopy(snapshot),
        "possible_writes": [],
        "pins": pins,
        "stop": stop,
        "supervision": deepcopy(metadata),
        "processes": deepcopy(record["processes"]),
    }
    return retained, terminal


def check_terminal(terminal, preparation, held, *, released, check_record):
    readonly(preparation)
    require(
        not released
        and terminal["held_id"] == held["id"]
        and terminal["supervision"] == held["body"]["supervision"]
        and terminal["after"] == preparation["binding"]["source"]
        and terminal["possible_writes"] == []
        and terminal["record"]["rejected"] is True,
        "terminal cancellation escaped its original readonly held admission",
    )
    check_record(preparation["record_identity"], terminal["record"])
    stop = terminal["stop"]
    require(
        set(stop) == {"accounted", "exit_code", "timed_out", "timeout_kind", "timeout_reason", "finished_at"}
        and all(
            type(terminal["record"].get(key)) is type(stop[key]) and terminal["record"][key] == stop[key]
            for key in ("exit_code", "timed_out", "finished_at")
        ),
        "terminal stop contradicts original collected record",
    )
    retained = {
        **terminal["record"],
        **stop,
        "pid": terminal["supervision"]["provider"]["pid"],
        "supervision": terminal["supervision"],
        "processes": terminal["processes"],
    }
    retained.pop("rejected", None)
    stop_facts(retained)
    stopped(terminal["supervision"], retained)
