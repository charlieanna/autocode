"""Authenticate Kilo's actual PID-1 child of the original bubblewrap owner.

The original supervisor owns bubblewrap. With --as-pid-1 its single direct
child is the namespace's initial process: if it exits, that namespace dies.
This is not authority for arbitrary descendants or a replacement process.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from . import autocode_native_linux_boundary as kernel
    from . import autocode_process as processes
except ImportError:
    import autocode_native_linux_boundary as kernel
    import autocode_process as processes


def _require(value, reason):
    if not value:
        raise ValueError("Kilo namespace peer: " + reason)


def _namespace(path):
    value = os.readlink(path)
    _require(
        value.startswith("pid:[") and value.endswith("]") and value[5:-1].isdigit(),
        "invalid kernel PID namespace identity",
    )
    return value


def observe(peer, anchor, tree):
    _require(
        type(tree) is processes.ProcessTree and os.uname().sysname == "Linux", "original Linux process owner required"
    )
    tree.sample()
    _require(
        processes.matches(tree.capture_root(), anchor)
        and anchor["pid"] == tree.pid
        and processes.matches(tree.known.get(peer["pid"]), peer)
        and peer["pid"] != anchor["pid"],
        "peer is not in the original live owner",
    )
    process = processes.psutil.Process(peer["pid"])
    _require(
        process.ppid() == anchor["pid"]
        and Path(processes.psutil.Process(anchor["pid"]).exe()).resolve() == kernel.executable(),
        "peer is not the original bubblewrap direct child",
    )
    status = Path(f"/proc/{peer['pid']}/status").read_text()
    rows = [line.split()[1:] for line in status.splitlines() if line.startswith("NSpid:")]
    _require(rows == [[str(peer["pid"]), "1"]], "peer is not the initial process of one child PID namespace")
    owner_namespace = _namespace(f"/proc/{anchor['pid']}/ns/pid")
    child_namespace = _namespace(f"/proc/{peer['pid']}/ns/pid")
    _require(
        owner_namespace == _namespace("/proc/self/ns/pid") and owner_namespace != child_namespace,
        "peer PID namespace is not a distinct child of this original owner",
    )
    current = processes.process_table({peer["pid"], anchor["pid"]})
    _require(
        processes.matches(current.get(peer["pid"]), peer) and processes.matches(current.get(anchor["pid"]), anchor),
        "birth changed while reading kernel namespace",
    )
    return {
        "kind": "original-bwrap-pid1-v1",
        "anchor": dict(anchor),
        "client": dict(peer),
        "parent_pid": anchor["pid"],
        "namespace_pid": 1,
        "owner_namespace": owner_namespace,
        "client_namespace": child_namespace,
    }


def validate_retained(value, *, peer, anchor):
    _require(
        isinstance(value, dict)
        and isinstance(peer, dict)
        and isinstance(anchor, dict)
        and set(value)
        == {"kind", "anchor", "client", "parent_pid", "namespace_pid", "owner_namespace", "client_namespace"}
        and value["kind"] == "original-bwrap-pid1-v1"
        and processes.matches(value["anchor"], anchor)
        and processes.matches(value["client"], peer)
        and type(value["parent_pid"]) is int
        and value["parent_pid"] == anchor["pid"]
        and type(value["namespace_pid"]) is int
        and value["namespace_pid"] == 1
        and isinstance(value["owner_namespace"], str)
        and isinstance(value["client_namespace"], str)
        and value["owner_namespace"] != value["client_namespace"],
        "original namespace peer receipt changed",
    )
    return value


def reported_pid(peer):
    """Map the observed peer into its own namespace without replacing its birth."""
    if os.uname().sysname != "Linux":
        return peer["pid"]
    status = Path(f"/proc/{peer['pid']}/status").read_text()
    rows = [line.split()[1:] for line in status.splitlines() if line.startswith("NSpid:")]
    _require(
        len(rows) == 1
        and rows[0]
        and rows[0][0] == str(peer["pid"])
        and all(value.isdigit() and int(value) > 0 for value in rows[0]),
        "peer namespace mapping changed",
    )
    current = processes.process_table({peer["pid"]})
    _require(processes.matches(current.get(peer["pid"]), peer), "peer birth changed during namespace mapping")
    return int(rows[0][-1])
