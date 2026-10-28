"""Independent pipe lifeline for one birth-identified provider process tree."""

from __future__ import annotations

import contextlib
import json
import math
import os
import queue
import select
import signal
import socket
import sys
import threading
import time

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_elapsed_timing as elapsed_timing
    from . import autocode_process as processes
    from . import autocode_session_boundary as boundary
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_elapsed_timing as elapsed_timing
    import autocode_process as processes
    import autocode_session_boundary as boundary
    import autocode_util as util


class KeeperTree(processes.ProcessTree):
    """A CLI keeper excludes itself and may stop its exact configured parent."""

    # Passive timing for the terminal receipt (#1143): seconds spent in whole-host session and
    # process-group scans since the keeper last reset it. It measures; it never bounds or decides.
    enumeration_seconds = 0.0

    def _timed_enumeration(self, scan, *args, **kwargs):
        started = time.monotonic()
        try:
            return scan(*args, **kwargs)
        finally:
            self.enumeration_seconds += time.monotonic() - started

    def _group_candidates(self, *args, **kwargs):
        return self._timed_enumeration(super()._group_candidates, *args, **kwargs)

    def _session_candidates(self, *args, **kwargs):
        return self._timed_enumeration(super()._session_candidates, *args, **kwargs)

    def inventory(self):
        return list(self.known.values())

    def admit(self, nested, owner):
        if (
            not isinstance(nested, dict)
            or nested.get("schema") != 1
            or not isinstance(nested.get("nonce"), str)
            or len(nested["nonce"]) != 32
            or not processes.matches(owner, nested.get("owner"))
        ):
            raise processes.ProcessError("Invalid nested supervision declaration")
        rows = [nested[key] for key in ("keeper", "provider")]
        if (
            any(
                not isinstance(row, dict)
                or type(row.get("pid")) is not int
                or row["pid"] <= 0
                or type(row.get("birth_identity")) not in (int, float)
                or not math.isfinite(row["birth_identity"])
                or row["birth_identity"] <= 0
                for row in rows
            )
            or len({row["pid"] for row in rows}) != 2
        ):
            raise processes.ProcessError("Invalid nested supervision identities")
        table = processes.process_table({row["pid"] for row in rows})
        if any(
            not processes.matches(row, table.get(row["pid"]))
            or table[row["pid"]]["parent"] != owner["pid"]
            or not self._included(row)
            for row in rows
        ):
            raise processes.ProcessError("Nested supervision is not owned by this CLI")
        # Only the sampling thread admits identities, so a stale sample cannot
        # overwrite this transfer. Native sampling still handles PID reuse.
        self.known.update({row["pid"]: dict(row) for row in rows})
        if not hasattr(self, "declarations"):
            self.declarations = {}
        self.declarations[nested["nonce"]] = dict(nested)
        for row in rows:
            if row.get("session") == row["pid"] == row.get("group"):
                self.sessions[row["pid"]] = dict(row)
                self.groups[row["pid"]] = dict(row)

    def admit_nested_session(self, record, nested, owner):
        declared = getattr(self, "declarations", {}).get(nested.get("nonce")) if isinstance(nested, dict) else None
        if declared != nested or not processes.matches(owner, nested.get("owner")):
            raise processes.ProcessError("Session transition has no admitted enclosing provider")
        creator = record.get("creator") if isinstance(record, dict) else None
        if not isinstance(creator, dict) or type(creator.get("pid")) is not int or creator["pid"] <= 0:
            raise processes.ProcessError("Invalid nested session creator")
        # A cancelled kernel request cannot change SID. Do not turn its queued
        # notice into an enclosing-guard failure after that exact birth exits.
        current = processes.process_table({creator["pid"]}).get(creator["pid"])
        if not processes.matches(creator, current) or current["state"].startswith("Z"):
            return False
        value = receipts.load(nested)
        if record not in (value or {}).get("session_admissions", []):
            raise processes.ProcessError("Nested session transition has no durable ownership receipt")
        try:
            self.admit_session(creator)
        except processes.ProcessError:
            current = processes.process_table({creator["pid"]}).get(creator["pid"])
            if not processes.matches(creator, current) or current["state"].startswith("Z"):
                return False
            raise
        return True

    def signal(self, rows, sig):
        parent = [row for row in rows if row["pid"] == os.getppid() == self.pid]
        super().signal([row for row in rows if row not in parent], sig)
        for row in parent:
            current = processes.process_table({self.pid}).get(self.pid)
            if processes.matches(row, current):
                try:
                    os.kill(self.pid, sig)
                except ProcessLookupError:
                    pass
                except PermissionError as error:
                    raise processes.ProcessError("Cannot signal supervised CLI owner") from error


class Observer:
    """Observe a sibling without reaping it or manufacturing its exit code."""

    def __init__(self, identity):
        self.identity = identity
        self.pid = identity["pid"]

    def poll(self):
        # The return sentinel says only 'not running', never a provider result.
        return None if processes.live_processes([self.identity]) else object()

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout or 0)
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise processes.ProcessError("Observed provider remains alive")
            time.sleep(0.02)
        return None


def stop_timing(published, requested, loop_exit, samples):
    """The terminal receipt's bounded passive phase timing (#1143).

    Every mark is in seconds relative to ``published``, the monotonic moment the terminal
    receipt was built, so it is zero or negative; the receipt's ``observed_at``, taken as it
    is written, gives that moment's wall clock. ``requested`` is the stop trigger (the owner's
    finished message, owner loss, the deadline), None when no trigger ran. ``samples`` holds
    the sampling loop's latest KeeperTree sample (``last``: the one in flight when a request
    arrived during it) and a discharge's final sample (``final``), each a (started, finished,
    enumeration seconds) triple or None. The record only reports; nothing waits on it.
    """

    def offset(moment):
        return None if moment is None else round(moment - published, 6)

    def sample(record):
        if record is None:
            return None
        started, finished, enumeration = record
        return {"started": offset(started), "finished": offset(finished), "enumeration_seconds": round(enumeration, 6)}

    return {
        "requested": offset(requested),
        "loop_exit": offset(loop_exit),
        "last_sample": sample(samples.get("last")),
        "final_sample": sample(samples.get("final")),
    }


def _leads_session(identity):
    try:
        return identity.get("group") == identity["pid"] == os.getsid(identity["pid"])
    except OSError:
        return False


def _reply_session(listener, cookie, trigger_lock, triggered, done):
    # Native cookie validation is outside the short trigger gate. Disk writes
    # and enclosing handoffs never hold this lock; only the final decision and
    # immediate syscall reply are serialized with shutdown.
    valid = boundary.request_valid(listener, cookie)
    with trigger_lock:
        if triggered.is_set() or done.is_set():
            boundary.reject_request(listener, cookie)
            return False
        return boundary.continue_request(listener, cookie) if valid else False


def main(control, acknowledgement, external=None, checkout_lease=None):
    message = bytearray()
    while len(message) <= 65536:
        if not select.select([control], [], [], 15)[0]:
            return 2
        chunk = os.read(control, 1)
        if not chunk:
            return 0  # owner died before admission; bootstrap also sees EOF
        if chunk == b"\n":
            break
        message.extend(chunk)
    else:
        return 2
    request = json.loads(message)
    metadata, deadline = request["metadata"], request["deadline"]
    declared_timing = request.get("timing")
    timing_admission = (
        elapsed_timing.admission(
            declared_timing.get("wall_origin_seconds"),
            declared_timing.get("monotonic_origin_seconds"),
            declared_timing.get("admitted_stage_limit_seconds"),
        )
        if isinstance(declared_timing, dict)
        else None
    )
    if timing_admission is None or timing_admission != declared_timing:
        return 2
    admitted_limit = timing_admission["admitted_stage_limit_seconds"]
    if deadline is None:
        if admitted_limit is not None:
            return 2
    elif (
        type(deadline) not in (int, float)
        or not math.isfinite(deadline)
        or admitted_limit is None
        or not math.isclose(
            deadline,
            timing_admission["monotonic_origin_seconds"] + admitted_limit,
            rel_tol=0,
            abs_tol=1e-6,
        )
    ):
        return 2
    provider = metadata["provider"]
    policy = request.get("cleanup_policy", "freeze_tree")
    if policy not in ("freeze_tree", "interrupt_root"):
        return 2
    # The window between this keeper's SIGTERM (or freeze) and its SIGKILL. The owner declares it; a
    # Builder worker's covers the stage teardown its own interrupt handler runs (#454).
    kill_after = request.get("kill_after_seconds", 2)
    if isinstance(kill_after, bool) or not isinstance(kill_after, (int, float)) or not 0 < kill_after < math.inf:
        return 2
    if not processes.matches(metadata["keeper"], processes.process_table({os.getpid()}).get(os.getpid())):
        return 2
    # Both providers and guarded CLIs lead dedicated sessions before exec.
    # Retain that group after its leader exits and is reaped: a child started
    # since the last sample is reparented but can still keep the owned group.
    # The admitted SID covers process-group changes. Kernel-mediated setsid
    # requests retain prospective new SIDs before they can leave that scope.
    leader = (provider,) if _leads_session(provider) else ()
    tree = KeeperTree(
        provider["pid"], lambda rows: None, excluded=(metadata["keeper"],), groups=leader, sessions=leader
    )
    tree.known = {provider["pid"]: provider}
    listener = None
    callback = None
    if "session_socket" in request:
        boundary.guardian_harden()
        channel = socket.socket(fileno=request["session_socket"])
        try:
            if not select.select([channel], [], [], 15)[0]:
                raise boundary.BoundaryError("Provider did not install its session boundary")
            listener = boundary.receive_listener(channel, metadata["nonce"])
            if not select.select([listener], [], [], 15)[0]:
                raise boundary.BoundaryError("Provider did not reach the session boundary probe")
            probe = boundary.receive_request(listener)
            if probe is None:
                raise boundary.BoundaryError("Session boundary probe disappeared")
            caller = boundary.caller_identity(probe)
            if not processes.matches(provider, caller) or caller["group"] != provider["pid"]:
                raise boundary.BoundaryError("Session boundary probe belongs to another process")
            if not boundary.continue_request(listener, probe["id"]):
                raise boundary.BoundaryError("Session boundary probe expired")
            if (
                not select.select([channel], [], [], 15)[0]
                or channel.recv(256) != ("verified:" + metadata["nonce"]).encode()
            ):
                raise boundary.BoundaryError("Provider did not verify the kernel session boundary")
        finally:
            channel.close()
        callback = socket.socket(fileno=request["session_callback"])
        callback.setblocking(False)
    # The liveness refusal comes after the session handshake on purpose: a
    # bootstrap that died before delivering its listener (a refused nested
    # listener dies exactly there) is named by the failed transfer in either
    # completion order, so the launcher's refusal keeps its cause. This gate
    # then covers a death after the handshake, and command launches with no
    # handshake at all; it never exits silently (#815: the launcher attaches
    # this stderr cause when the keeper dies before acknowledging).
    if not processes.live_processes([provider]):
        print("Provider exited before keeper admission", file=sys.stderr, flush=True)
        return 2
    receipt_write_lock = threading.Lock()
    reason_lock = threading.Lock()
    value = {
        **metadata,
        "phase": "armed",
        "processes": list(tree.known.values()),
        "cause": None,
        "cleanup_error": None,
        "observed_at": util.now(),
    }
    if listener is not None:
        value["session_boundary"] = "linux-setsid-notify-v1"

    def save():
        # Order snapshot construction and replacement as one publication. A
        # blocked older sampling write cannot replace a later stopping receipt.
        # The state lock is released before I/O: trigger signalling/escalation
        # never waits for this disk lock or an in-progress checkpoint.
        with receipt_write_lock:
            with reason_lock:
                timing = elapsed_timing.observation(
                    timing_admission, wall_now=time.time(), monotonic_now=time.monotonic()
                )
                if timing is None:
                    raise OSError("supervision timing observation is invalid")
                snapshot = {
                    **value,
                    "processes": [dict(row) for row in tree.inventory()],
                    "observed_at": util.now(),
                    "timing": timing,
                }
                if listener is not None or tree.admitted_sessions():
                    snapshot["session_admissions"] = tree.admitted_sessions()
            # Never replace a readable receipt with bytes this process's reader rejects.
            if not receipts.within_reader_limit(snapshot):
                raise OSError("supervision receipt exceeds the reader limit")
            util.atomic_json(metadata["receipt"], snapshot)

    try:
        save()  # durable ownership is required before the provider can exec
    except OSError:
        return 3
    os.write(acknowledgement, (json.dumps({"armed": metadata["nonce"]}) + "\n").encode())
    if external is None:
        os.close(acknowledgement)
    triggered = threading.Event()
    done = threading.Event()
    monitor_error = []
    admissions = queue.SimpleQueue()
    reason = [None]
    cleanup_started = [None]
    escalation = [None]
    # Held for the whole of a trigger, gate check through its receipt write, and taken by the
    # publication below once ``done`` is set: no trigger is then in flight and none can start,
    # so the terminal receipt depends on the cleanup, never on whether the watcher thread was
    # scheduled in time to be joined.
    trigger_lock = threading.Lock()
    wake_r, wake_w = os.pipe()  # written at publication so the watcher's select returns at once

    def trigger(cause):
        with trigger_lock:
            with reason_lock:
                if triggered.is_set() or done.is_set():
                    return
                reason[0] = cause
                cleanup_started[0] = time.monotonic()
                value.update(phase="stopping", cause=cause)
                triggered.set()
            if cause == "controller_finished" and external is not None:
                return  # A normally exiting CLI discharges without signalling itself.

            # Owner EOF and the hard deadline must not wait behind sampling or disk
            # writes. Recheck identities before signalling every recorded process.
            def signal_known(sig):
                try:
                    tree.signal(tree.inventory(), sig)
                except BaseException as error:
                    monitor_error.append(type(error).__name__)

            if policy == "interrupt_root":
                try:
                    tree.signal([provider], signal.SIGTERM)
                except BaseException as error:
                    monitor_error.append(type(error).__name__)
            else:
                signal_known(signal.SIGSTOP)
            escalation[0] = threading.Timer(kill_after, signal_known, args=(signal.SIGKILL,))
            escalation[0].daemon = True
            escalation[0].start()
            try:
                save()
            except OSError:
                monitor_error.append("ReceiptWriteError")

    def monitor_owner():
        pending = bytearray()
        try:
            while not done.is_set() and not triggered.is_set():
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    trigger("stage_deadline")
                    return
                readable = select.select(
                    [control, wake_r, *([external] if external is not None else [])],
                    [],
                    [],
                    min(0.05, remaining) if remaining is not None else 0.05,
                )[0]
                if wake_r in readable:
                    return
                if not readable:
                    continue
                if external is not None and external in readable:
                    chunk = os.read(external, 1)
                    trigger("owner_lost" if not chunk else "invalid_owner_message")
                    return
                chunk = os.read(control, 4096)
                if not chunk:
                    trigger("owner_lost")
                    return
                pending.extend(chunk)
                if len(pending) > 65536:
                    trigger("invalid_owner_message")
                    return
                while b"\n" in pending:
                    line, _, remainder = pending.partition(b"\n")
                    pending = bytearray(remainder)
                    notice = json.loads(line)
                    if (
                        external is not None
                        and isinstance(notice, dict)
                        and set(notice) == {"admit", "guard"}
                        and notice["guard"] == metadata["nonce"]
                    ):
                        admissions.put(notice["admit"])
                    elif (
                        external is not None
                        and isinstance(notice, dict)
                        and set(notice) == {"admit_session", "nested", "guard"}
                        and notice["guard"] == metadata["nonce"]
                    ):
                        admissions.put(notice)
                    else:
                        trigger(
                            "controller_finished"
                            if notice == {"finished": metadata["nonce"]}
                            else "invalid_owner_message"
                        )
                        return
        except BaseException as error:
            monitor_error.append(type(error).__name__)
            trigger("lifeline_failure")
        finally:
            os.close(wake_r)  # the only reader: even a watcher scheduled after publication ends cleanly

    watcher = threading.Thread(target=monitor_owner, daemon=True)
    watcher.start()
    result = 0
    terminal_phase = "uncertain"
    terminal_error = None
    previous_inventory = tree.inventory()
    pending_sessions = {}
    samples = {"last": None, "final": None}
    loop_exit = None

    def timed_sample(slot):
        # Passive marks for the terminal receipt (#1143); the sample itself is unchanged.
        tree.enumeration_seconds = 0.0
        started = time.monotonic()
        try:
            return processes.settled_sample(tree, notify=False)
        finally:
            samples[slot] = (started, time.monotonic(), tree.enumeration_seconds)

    def service_sessions():
        if listener is None:
            return
        for _ in range(32):
            if boundary.listener_extinct(listener) or not select.select([listener], [], [], 0)[0]:
                break
            notice = boundary.receive_request(listener)
            if notice is None:
                continue
            if triggered.is_set() or done.is_set():
                boundary.reject_request(listener, notice["id"])
                continue
            try:
                caller = boundary.caller_identity(notice)
                tree.admit_session(caller)
            except (boundary.BoundaryError, processes.ProcessError):
                if not boundary.request_valid(listener, notice["id"]):
                    continue
                raise
            save()  # Both keeper and enclosing guard retain ownership before CONTINUE.
            if not boundary.request_valid(listener, notice["id"]):
                continue
            record = next(row for row in tree.admitted_sessions() if processes.matches(row["creator"], caller))
            callback.send(
                json.dumps({"session_admission": record, "request": notice["id"], "guard": metadata["nonce"]}).encode()
            )
            pending_sessions[notice["id"]] = time.monotonic() + 15
        while callback is not None and select.select([callback], [], [], 0)[0]:
            raw = callback.recv(65537)
            if not raw:
                break  # Owner EOF is handled by the independent lifeline watcher.
            answer = json.loads(raw)
            cancelled = "session_cancelled" in answer
            field = "session_cancelled" if cancelled else "session_admitted"
            if (
                len(raw) > 65536
                or set(answer) != {field, "guard"}
                or answer["guard"] != metadata["nonce"]
                or answer[field] not in pending_sessions
            ):
                raise boundary.BoundaryError("Invalid enclosing session admission acknowledgement")
            cookie = answer[field]
            pending_sessions.pop(cookie)
            if cancelled:
                if boundary.request_valid(listener, cookie):
                    raise boundary.BoundaryError("Enclosing guard cancelled a live session request")
            else:
                _reply_session(listener, cookie, trigger_lock, triggered, done)
        if any(time.monotonic() >= limit for limit in pending_sessions.values()):
            raise boundary.BoundaryError("Enclosing session admission exceeded its bound")

    try:
        while not triggered.is_set():
            # Admission disk I/O belongs to the sampling thread. The lifeline
            # watcher must remain free to enforce EOF and the hard deadline.
            while not admissions.empty() and not triggered.is_set():
                nested = admissions.get_nowait()
                admitted = True
                if "admit_session" in nested:
                    admitted = tree.admit_nested_session(nested["admit_session"], nested["nested"], provider)
                else:
                    tree.admit(nested, provider)
                save()
                if not triggered.is_set():
                    answer = (
                        {
                            ("session_admitted" if admitted else "session_cancelled"): nested["nested"]["nonce"],
                            "creator": nested["admit_session"]["requested_session"],
                        }
                        if "admit_session" in nested
                        else {"admitted": nested["nonce"]}
                    )
                    os.write(acknowledgement, (json.dumps(answer) + "\n").encode())
            service_sessions()
            rows = timed_sample("last")
            inventory = tree.inventory()
            if previous_inventory != inventory:
                save()
                previous_inventory = inventory
            if not rows or not processes.live_processes([provider]):
                # Kernel parent-death cleanup can stop the provider before the
                # lifeline watcher is scheduled. Inspect the original owner's
                # birth here so its loss cannot look like an ordinary exit.
                if not processes.live_processes([metadata["owner"]]):
                    trigger("owner_lost")
                with reason_lock:
                    if not triggered.is_set():
                        reason[0] = "provider_stopped"
                break
            # This wait is interruptible by owner loss/deadline, not a test sleep.
            triggered.wait(0.05)
        loop_exit = time.monotonic()
        discharged = external is not None and reason[0] == "controller_finished"
        if discharged:
            remaining = [row for row in timed_sample("final") if row["pid"] != provider["pid"]]
            if remaining:
                raise processes.ProcessError("CLI discharged with live child processes")
        if not discharged and policy == "interrupt_root":
            # A dead CLI's workers still have independent lifeline keepers. EOF
            # lets them retain their pauses and settle even after the CLI itself
            # was SIGKILLed. Root exit alone must not force-kill those keepers.
            # A trigger's escalation clock is already running; do not restart it.
            started = cleanup_started[0]
            until = (time.monotonic() if started is None else started) + kill_after
            while time.monotonic() < until and processes.settled_sample(tree, notify=False):
                done.wait(0.02)
        if not discharged:
            tree.stop(Observer(provider))
        if listener is not None:
            until = time.monotonic() + 1
            while not boundary.listener_extinct(listener) and time.monotonic() < until:
                done.wait(0.01)
            if not boundary.listener_extinct(listener):
                raise processes.ProcessError("Kernel session boundary still owns live or unreaped tasks")
        terminal_phase = "discharged" if discharged else "stopped"
    except BaseException as error:
        terminal_error = f"{type(error).__name__}: {str(error)[:300]}"
        result = 4
        # Do not discard already recorded ownership when discovery fails.
        with contextlib.suppress(BaseException):
            tree.signal(tree.inventory(), signal.SIGKILL)
    finally:
        done.set()
        with contextlib.suppress(OSError):
            os.write(wake_w, b"x")
        watcher.join(timeout=1)
        # Publish the terminal phase once no trigger can run: one in flight finishes its receipt
        # write first (bounded well under the owner's 8 s wait), and none starts after ``done``.
        # A watcher thread merely not yet scheduled cannot change the receipt; only a trigger that
        # did not finish leaves the cleanup unverified.
        trigger_finished = trigger_lock.acquire(timeout=3)
        if not trigger_finished:
            terminal_phase = "uncertain"
            terminal_error = "Lifeline trigger did not finish before publication"
            result = 4
        elif monitor_error:
            # Read only now: the trigger this waited for may have recorded its failures meanwhile.
            terminal_error = "; ".join(filter(None, [terminal_error, *monitor_error]))
            result = 4
        # A late normal discharge cannot overwrite stopped with stopping.
        value.update(
            phase=terminal_phase,
            cause=reason[0] or "keeper_failure",
            cleanup_error=terminal_error,
            stop_timing=stop_timing(time.monotonic(), cleanup_started[0], loop_exit, samples),
        )
        if escalation[0]:
            escalation[0].cancel()
        try:
            save()
        except OSError:
            result = 3
        if trigger_finished:
            trigger_lock.release()  # a watcher that reaches its trigger now finds ``done`` and returns
        os.close(wake_w)
        os.close(control)
        if callback is not None:
            callback.close()
        if listener is not None:
            os.close(listener)
        if external is not None:
            os.close(acknowledgement)
            os.close(external)
    if checkout_lease is not None:
        # Verified cleanup was published before releasing the kernel lease. An
        # uncertain receipt remains in the checkout holder's retained worker
        # list, so future AutoCode admission checks its birth identities rather
        # than treating keeper exit as proof that the checkout is free.
        os.close(checkout_lease)
    return result


if __name__ == "__main__":
    raise SystemExit(main(*(int(value) for value in sys.argv[1:])))
