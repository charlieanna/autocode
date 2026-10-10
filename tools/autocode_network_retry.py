"""One no-effect retry for an explicitly retryable native transport failure.

Only this module writes network_retry_binding/network_retry_chain on attempt
records. Admission reads them and retained stage history; no run-state key,
provider route, budget, permission, or session is changed. Legacy attempts
without the launch binding and ambiguous streams keep their existing hold.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
from pathlib import Path

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_interventions as interventions
    from . import autocode_jobs as jobs
    from . import autocode_process as processes
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_interventions as interventions
    import autocode_jobs as jobs
    import autocode_process as processes
    import autocode_source_scope as source_scope
    import autocode_util as util

MAX_ERROR_BYTES = 65536
NETWORK_CODES = frozenset({"ECONNRESET", "ETIMEDOUT", "EPIPE"})
HTTP_CODES = frozenset({500, 502, 503, 504})
CONFLICT = re.compile(
    r"quota|credits?|rate.?limit|429|authenticat|unauthori|content.?filter|output(?:.?token)?.?limit", re.I
)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate transport field")
        result[key] = value
    return result


def _finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite transport field")
    return result


def retryable_error(raw):
    """An error-only APIError is not a model/tool diagnostic or a partial turn."""
    if not isinstance(raw, str):
        return None
    try:
        if len(raw.encode("utf-8")) > MAX_ERROR_BYTES:
            return None
        row = json.loads(
            raw,
            object_pairs_hook=_unique,
            parse_float=_finite,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if (
            not isinstance(row, dict)
            or row.get("type") != "error"
            or set(row) - {"type", "error", "timestamp", "sessionID"}
        ):
            return None
        if "timestamp" in row and (type(row["timestamp"]) is not int or row["timestamp"] < 0):
            return None
        if "sessionID" in row and (not isinstance(row["sessionID"], str) or not row["sessionID"]):
            return None
        error = row.get("error")
        if not isinstance(error, dict) or set(error) != {"name", "data"} or error["name"] != "APIError":
            return None
        data = error["data"]
        if not isinstance(data, dict) or set(data) - {
            "message",
            "isRetryable",
            "statusCode",
            "metadata",
            "responseHeaders",
            "responseBody",
            "url",
        }:
            return None
        if (
            data.get("isRetryable") is not True
            or not isinstance(data.get("message"), str)
            or not data["message"]
            or CONFLICT.search(data["message"])
        ):
            return None
        metadata = data.get("metadata", {})
        # OpenCode 1.18.33 MessageV2.fromError/ProviderError.parseAPICallError:
        # ECONNRESET retains code/syscall/message; HTTP errors may retain url.
        if not isinstance(metadata, dict) or set(metadata) - {"code", "syscall", "message", "url"}:
            return None
        if not all(isinstance(value, str) for value in metadata.values()):
            return None
        if "url" in metadata and not metadata["url"]:
            return None
        if CONFLICT.search(metadata.get("message", "")):
            return None
        for name in ("url", "responseBody"):
            if name in data and not isinstance(data[name], str):
                return None
        if CONFLICT.search(data.get("responseBody", "")):
            return None
        if "responseHeaders" in data and (
            not isinstance(data["responseHeaders"], dict)
            or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in data["responseHeaders"].items()
            )
        ):
            return None
        code, status = metadata.get("code"), data.get("statusCode")
        if ("code" in metadata and not isinstance(code, str)) or ("statusCode" in data and type(status) is not int):
            return None
        if code is not None and code not in NETWORK_CODES:
            return None
        if status is not None and (type(status) is not int or status not in HTTP_CODES):
            return None
        return (code or str(status)) if code in NETWORK_CODES or status in HTTP_CODES else None
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return None


def _read(path, root, limit):
    path, root = Path(path), Path(root)
    if (
        not path.is_absolute()
        or path.resolve(strict=True) != path
        or not path.is_relative_to(root.resolve(strict=True))
    ):
        raise ValueError("Retry artifact is not owned")
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Retry artifact has an alias")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("Retry artifact is not a bounded plain file")
        data = os.read(fd, limit + 1)
        if (
            len(data) != info.st_size
            or len(data) > limit
            or any(
                getattr(os.fstat(fd), key) != getattr(info, key)
                for key in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            )
        ):
            raise ValueError("Retry artifact changed")
        return data
    finally:
        os.close(fd)


def _binding(state, record, before, workspace):
    return util.digest(
        {
            "workspace": str(Path(workspace).resolve(strict=True)),
            "source": before["revision"],
            "head": before["head"],
            "settings": state["settings"],
            "task": state.get("task"),
            "current_task": state.get("current_task"),
            "contract": state.get("goal_contract"),
            "stage": record["stage"],
            "role": record["role"],
            "iteration": record["iteration"],
            "route": record.get("launch_route"),
            "engine": record.get("engine"),
            "effort": record.get("reasoning_effort"),
            "session": record.get("expected_session"),
        }
    )


def _index(workspace):
    name = (
        subprocess.check_output(
            ["git", "rev-parse", "--git-path", "index"], cwd=workspace, timeout=10, stderr=subprocess.DEVNULL
        )
        .decode()
        .strip()
    )
    path = Path(name)
    if not path.is_absolute():
        path = Path(workspace) / path
    return hashlib.sha256(_read(path, path.parent, 8 * 1024 * 1024)).hexdigest()


def bind_attempt(state, record, before, workspace):
    """Capture immutable retry authority before the existing owned launch."""
    # Inherit before retry-authority checks: unsupported routes and missing
    # launch authority cannot reset a consumed failed chain. Scan the rejected
    # suffix so older saved intermediaries without a marker cannot reset it.
    for previous in reversed(state.get("stages", [])):
        if previous.get("runner_owned") or previous.get("report_only"):
            continue
        if not previous.get("rejected"):
            break  # An accepted ordinary attempt starts a new chain.
        if "network_retry_chain" in previous:
            chain = previous["network_retry_chain"]
            record["network_retry_chain"] = (
                dict(chain) if isinstance(chain, dict) else {"consumed": True, "invalid": True}
            )
            break
    if (
        record.get("engine") != "opencode"
        or record.get("report_only")
        or record.get("expected_session") is not None
        or record.get("stage") in jobs.STAGES
    ):
        return
    try:
        binding = _binding(state, record, before, workspace)
        index = _index(workspace)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return  # Missing retry authority leaves ordinary launch behavior unchanged.
    record["network_retry_binding"] = {"version": 1, "hash": binding, "index": index}


def _absent(path):
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    return False


def _output(record, root):
    path = Path(record["output"])
    if (
        not path.is_absolute()
        or path.suffix != ".json"
        or path.parent.resolve(strict=True) != path.parent
        or not path.parent.is_relative_to(root)
        or any(parent.is_symlink() for parent in path.parents)
        or record["events"] != str(path.with_suffix(".jsonl"))
        or record["before_ref"] != str(path.with_suffix(".before.json"))
    ):
        raise ValueError("Retry artifact namespace differs")
    return path


def recover(runtime, state, run_dir, workspace, error):
    """Archive once, only after exact launch/source and normal cleanup admission."""
    record = state.get("active_stage") or {}
    run_dir = Path(run_dir)
    if not isinstance(record, dict):
        return False
    activity = record.get("activity") or {}
    if not isinstance(activity, dict):
        return False
    if (
        getattr(error, "status", None) != "PAUSED_PROVIDER_UNCERTAIN"
        or state.get("status") != "RUNNING"
        or record.get("engine") != "opencode"
        or record.get("stage") in jobs.STAGES
        or not record.get("finished_at")
        or type(record.get("exit_code")) is not int
        or record["exit_code"] <= 0
        or record.get("expected_session") is not None
        or any(record.get(key) for key in ("timed_out", "interrupted", "cleanup_error", "report_only", "rejected"))
        or any(
            state.get(key)
            for key in ("pending_report_repair", "uncertain_artifacts", "pending_questions", "pause_requested")
        )
        or any(activity.get(key) for key in ("active_tool_count", "completed_tool_count"))
    ):
        return False
    agent_request = state.get("agent_request") or {}
    if not isinstance(agent_request, dict):
        return False
    request = state.get("user_request") or agent_request.get("request")
    if request and (not isinstance(request, dict) or request.get("kind") != "none"):
        return False
    try:
        if run_dir.resolve(strict=True) != run_dir or any(parent.is_symlink() for parent in run_dir.parents):
            return False
        if not all(_absent(run_dir / name) for name in ("pause-requested", "active-processes.json")):
            return False
        if interventions.pending(run_dir):
            return False
        output = _output(record, run_dir)
        if not all(
            _absent(path)
            for path in (output, output.with_suffix(".reported.json"), output.with_suffix(".response.txt"))
        ):
            return False
        raw = _read(record["events"], run_dir, MAX_ERROR_BYTES)
        code = retryable_error(raw.decode("utf-8"))
        if not code:
            return False
        before = json.loads(_read(record["before_ref"], run_dir, 8 * 1024 * 1024))
        after = source_scope.snapshot(workspace, state, base_snapshot=runtime.support.snapshot)
        binding = record.get("network_retry_binding")
        if (
            binding != {"version": 1, "hash": _binding(state, record, before, workspace), "index": _index(workspace)}
            or before != after
        ):
            return False
        metadata = record["supervision"]
        pin = hashlib.sha256(_read(metadata["receipt"], run_dir, receipts.MAX_RECEIPT_BYTES)).hexdigest()
        owned = {
            "exit_code": record["exit_code"],
            "supervision": metadata,
            "supervision_sha256": pin,
            "supervision_errors": [],
        }
        final = receipts.load(metadata, root=run_dir, pin=pin)
        if not receipts.completed(owned, root=run_dir) or not final:
            return False
        # The live controller is the owner; only its stopped owned actors must be absent.
        if processes.live_processes([metadata["provider"], metadata["keeper"], *final["processes"]]):
            return False
        runtime.assert_stage_stopped(record)
        chain = record.get("network_retry_chain")
        if chain is not None:
            return False  # A persisted child or malformed chain never gets a new allowance.
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        UnicodeError,
        subprocess.SubprocessError,
        processes.ProcessError,
        util.Paused,
        interventions.InterventionError,
    ):
        return False
    runtime.timeout_recovery_guard(state)
    runtime.account_stage(state, record)
    after_path = output.with_suffix(".after.json")
    runtime.write_json(after_path, after)
    record.update(
        after_ref=str(after_path),
        source_revision=after["revision"],
        changed_files=[],
        abandoned=True,
        automatic_recovery=True,
        network_retry_chain={
            "root_attempt": runtime.attempt_id(record),
            "binding": binding["hash"],
            "consumed": True,
            "cause": code,
        },
        supervision_sha256=pin,
        network_error_sha256=hashlib.sha256(raw).hexdigest(),
    )
    reason = (
        f"Retryable provider network failure ({code}) before any observed turn/tool effects; one fresh same-stage retry"
    )
    originals = runtime.archive_rejected_stage(state, run_dir, record, reason)
    runtime.count_automatic_recovery(state)
    state.setdefault("user_events", []).append(
        {
            "kind": "automatic_provider_network_recovery",
            "actor": "runner",
            "at": runtime.now(),
            "stage": record["stage"],
            "events": record["events"],
            "reason": reason,
        }
    )
    runtime.write_json(run_dir / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    print(f"{runtime.autocode_status.role_name(record['stage'], state)}: {reason}; failed attempt retained", flush=True)
    return True
