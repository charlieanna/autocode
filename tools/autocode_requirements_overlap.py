"""Settle a saved run's Requirements background pass, which AutoCode no longer starts (#3233).

AutoCode used to run the full Requirements pass while the person read its blocking questions,
assuming each question's offered default, and kept that report when the answers matched (#3580).
The owner removed it on 2026-11-19: AutoCode makes no assumption about an answer and waits for
the person. No new run starts such a pass.

A run saved by the terminal chat while that pass was running can still be resumed. ``recover``
settles it on the ordinary resume path: its report is never adopted, the provider it owned is
stopped only through its verified process identity (a reused PID is never signalled), and its
observed spend is accounted once as a discarded speculative attempt.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_process as processes
    from . import autocode_util as util
except ImportError:
    import autocode_process as processes
    import autocode_util as util

#: The sidecar a saved background pass checkpointed its candidate state to.
SIDECAR_NAME = "requirements-overlap-state.json"

#: Per-signal grace while stopping a verified owned provider: SIGTERM, then SIGKILL.
SIGNAL_GRACE_SECONDS = 2.0


def provider_identity(record) -> dict | None:
    """The authenticated launch identity a saved attempt recorded; a bare PID is never authority."""
    supervision = record.get("supervision") if isinstance(record, dict) else None
    provider = supervision.get("provider") if isinstance(supervision, dict) else None
    if isinstance(provider, dict) and type(provider.get("pid")) is int and provider.get("birth_identity") is not None:
        return provider
    return None


def _default_live(identities):
    return processes.live_processes(identities)


def _default_kill(pid, sig) -> bool:
    try:
        # Providers are launched with start_new_session=True, so a verified provider pid leads a
        # private process group; recheck that invariant before signalling a group, as the stage
        # supervisor does, and only ever the process the identity verified.
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _default_pause(seconds):
    time.sleep(seconds)


def _alive(identity, *, live):
    """True/False for a verified live/dead process, None when its identity cannot be established."""
    try:
        return bool(live([identity]))
    except (processes.ProcessError, OSError, ValueError, KeyError, TypeError):
        return None


def _await_exit(identity, *, live, pause, grace) -> bool:
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if _alive(identity, live=live) is False:
            return True
        pause(0.05)
    return False


def stop_verified(record, *, live=None, kill=None, pause=None) -> dict:
    """Stop the provider a saved attempt owned, only once its recorded identity still verifies.

    ``live`` is the existing process-identity boundary (``autocode_process.live_processes``): the
    saved birth identity must match the current process, so a reused PID is a different process
    and is never signalled, and a record without an authenticated identity is reported rather
    than guessed at. Returns what happened, for the run's evidence.
    """
    live = _default_live if live is None else live
    kill = _default_kill if kill is None else kill
    pause = _default_pause if pause is None else pause
    identity = provider_identity(record)
    if identity is None:
        return {
            "settled": False,
            "signalled": False,
            "reason": "the saved attempt carries no authenticated process identity",
        }
    alive = _alive(identity, live=live)
    if alive is None:
        return {
            "settled": False,
            "signalled": False,
            "reason": "the owned process's identity could not be verified",
        }
    if not alive:
        return {"settled": True, "signalled": False, "reason": "the owned process already exited"}
    signalled = False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        state_now = _alive(identity, live=live)
        if state_now is False:
            return {"settled": True, "signalled": signalled, "reason": "the owned process was stopped"}
        if state_now is None:
            return {
                "settled": False,
                "signalled": signalled,
                "reason": "the owned process's identity became unavailable while stopping it",
            }
        if kill(identity["pid"], sig):
            signalled = True
        if _await_exit(identity, live=live, pause=pause, grace=SIGNAL_GRACE_SECONDS):
            return {"settled": True, "signalled": signalled, "reason": "the owned process was stopped"}
    return {
        "settled": False,
        "signalled": signalled,
        "reason": "the owned process was signalled; its exit stayed unconfirmed",
    }


def recover(
    state: RunState,
    run_dir,
    *,
    save,
    account_stage,
    save_record,
    event_metrics,
    settle_owned=None,
    now=None,
) -> dict | None:
    """Reconcile a saved run's unfinished background pass when the run is loaded again (#3233).

    Runs in the ordinary run-loading/reconciliation path, before the early return for a missing
    main ``active_stage``, on every resume — chat or not. A persisted ``running`` overlap record
    means the last controller never settled its background call: that report is never adopted or
    replayed. The saved sidecar is authenticated against this run (its task and workspace) and
    the attempt against its recorded process identity, which must still verify before anything is
    signalled; observed spend is recovered exactly once, the discarded outcome persisted, and a
    repeated resume no-ops. Authentication or usage that cannot be established stays an explicit
    recorded uncertainty — never invented zero spend, never an adopted report.
    """
    current = state.get("requirements_overlap") or {}
    if current.get("status") != "running" and not current.get("cleanup_pending"):
        return None
    run_dir = Path(run_dir)
    reason = "the run resumed before the background pass finished; its unfinished report is never adopted"
    entry = {**current, "status": "discarded", "at": (now or util.now)(), "reason": reason}
    if current.get("cleanup_pending"):
        state.setdefault("requirements_overlap_history", []).append(dict(current))
    entry.pop("cleanup_pending", None)
    entry.pop("uncertainty", None)
    attempt = None
    sidecar = run_dir / SIDECAR_NAME
    try:
        saved = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
    except (OSError, ValueError):
        saved = {}
    if (
        isinstance(saved, dict)
        and saved.get("task_id") == state.get("task_id")
        and saved.get("workspace") == state.get("workspace")
        and saved.get("run_dir") == state.get("run_dir")
        and current.get("attempt_nonce")
        and (saved.get("requirements_overlap") or {}).get("attempt_nonce") == current["attempt_nonce"]
        and isinstance(saved.get("active_stage"), dict)
        and saved["active_stage"].get("stage") == "requirements_gather"
        and saved["active_stage"].get("requirements_overlap_nonce") == current["attempt_nonce"]
    ):
        attempt = saved["active_stage"]
    else:
        entry["uncertainty"] = "no attempt authenticated to this run was found; the pass's spend is not observed"
        entry["cleanup_pending"] = True
    if attempt is not None:
        owned = (settle_owned or stop_verified)(attempt)
        entry["process"] = owned
        if not owned.get("settled"):
            entry["uncertainty"] = owned.get("reason")
            entry["cleanup_pending"] = True
        attempt["speculative"] = True
        attempt["outcome"] = "discarded"
        attempt["reason"] = reason
        if not attempt.get("metrics"):
            if attempt.get("events") and Path(attempt["events"]).is_file():
                with contextlib.suppress(OSError, ValueError, TypeError):
                    attempt["metrics"] = event_metrics(attempt["events"])
            if not attempt.get("metrics"):
                # Neither saved metrics nor authenticated events establish the attempt's spend:
                # keep that gap explicit on the entry and the attempt — never invented zero
                # spend presented as completely accounted.
                entry.setdefault(
                    "uncertainty", "the attempt's observed usage could not be read; its spend is not observed"
                )
                attempt["uncertainty"] = entry["uncertainty"]
        previous = next(
            (
                row
                for row in state.get("stages") or []
                if row.get("requirements_overlap_nonce") == current["attempt_nonce"]
            ),
            None,
        )
        if previous is None:
            attempt.pop("accounted", None)
            account_stage(state, attempt)
            attempt["overlap_accounted"] = True
            save_record(state, attempt)
    state["requirements_overlap"] = entry
    save(run_dir / "state.json", state)
    return entry


def resume(state: RunState, run_dir, runner) -> dict | None:
    """Settle a saved unfinished pass on the ordinary resume path (``autocode.reconcile_active``)."""
    result = recover(
        state,
        run_dir,
        save=runner.write_json,
        account_stage=runner.account_stage,
        save_record=runner.save_record,
        event_metrics=runner.support.event_metrics,
    )
    if result and result.get("cleanup_pending"):
        raise runner.support.Paused("PAUSED_PROCESS_CLEANUP", result["uncertainty"])
    return result
