"""Parent observations of the compiled Kilo client's model-free kernel probe.

The original source listener is borrowed before any native launch registers.
This protocol cannot arm a source, dispatch inference or publish image facts.
Only an actual supervised root and its captured same-client child can complete
the probe; saved receipt dictionaries grant no dispatch authority.
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_kilo_linux_peer as namespace_peer
    from . import autocode_process as processes
    from . import autocode_supervision as supervision
    from . import autocode_util as util
except ImportError:
    import autocode_kilo_linux_peer as namespace_peer
    import autocode_process as processes
    import autocode_supervision as supervision
    import autocode_util as util


KIND = "ACTUAL_COMPILED_KILO_CLIENT_UNIX_TCP_FOREIGN_PEER_CLOSED"
TCP_TARGET = {"host": "192.0.2.1", "port": 443, "timeout_ms": 2000}


def _require(value, reason):
    if not value:
        raise ValueError("Kilo client conformance: " + reason)


def validate_retained_receipt(body, *, executable_sha256, client_profile_sha256):
    """Validate protected old observations; this never creates live authority."""
    binding = body.get("bindings", {})
    actual = body.get("actual_call", {})
    supervisor = body.get("actual_supervision", {})
    _require(
        body.get("kind") == KIND
        and body.get("error") is None
        and body.get("error_detail") is None
        and body.get("matching_live") == []
        and binding.get("executable_sha256") == executable_sha256
        and binding.get("client_profile_sha256") == client_profile_sha256
        and type(actual.get("exit")) is int
        and actual["exit"] == 0
        and actual.get("expired") is False
        and isinstance(supervisor, dict)
        and supervisor.get("phase") == "stopped"
        and supervisor == actual.get("supervision_result"),
        "protected original client closure is missing",
    )
    root, foreign = body.get("original_root"), body.get("original_foreign")
    anchor = body.get("supervision", {}).get("provider")
    namespace = body.get("client_namespace")
    if namespace is not None:
        _require(binding.get("kernel_backend") == "linux-bwrap-pid1-v1", "namespace client lacks its original backend")
        namespace_peer.validate_retained(namespace, peer=root, anchor=anchor)
    _require(
        isinstance(root, dict)
        and isinstance(foreign, dict)
        and root.get("pid") != foreign.get("pid")
        and (namespace is not None or processes.matches(root, anchor)),
        "protected original client identities changed",
    )
    observations = body.get("observations")
    _require(isinstance(observations, list) and len(observations) == 3, "original probe observation coverage changed")
    hello, denied, complete = observations
    _require(
        processes.matches(root, hello.get("peer"))
        and processes.matches(foreign, denied.get("peer"))
        and processes.matches(root, complete.get("peer"))
        and hello.get("response", {}).get("kind") == "CLIENT_CONFORMANCE_PARENT_PONG"
        and hello["response"].get("ok") is True
        and denied.get("response", {}).get("kind") == "CLIENT_CONFORMANCE_FOREIGN_PEER_DENIED"
        and denied["response"].get("ok") is False
        and complete.get("response", {}).get("kind") == "CLIENT_CONFORMANCE_COMPLETE"
        and complete["response"].get("ok") is True,
        "protected Unix/foreign-parent observations changed",
    )
    frame = complete.get("frame", {})
    tcp = frame.get("tcp", {})
    _require(
        frame.get("foreign_exit") == 0
        and type(frame.get("foreign_exit")) is int
        and tcp.get("host") == TCP_TARGET["host"]
        and tcp.get("port") == TCP_TARGET["port"]
        and tcp.get("status") == "denied"
        and tcp.get("code") in ("EACCES", "EPERM"),
        "protected direct TCP permission refusal is missing",
    )
    return body


class ClientConformance:
    """One finite real client probe; no native/source/audit authority is made."""

    def __init__(
        self,
        *,
        listener,
        token,
        socket_path,
        executable,
        peer_identity,
        current_binding,
        receipt,
        agent,
        foreign_environment,
    ):
        self._listener, self._token, self._peer_identity = listener, token, peer_identity
        self._binding_reader = current_binding
        self._binding = deepcopy(current_binding())
        self._executable = Path(executable).resolve()
        self._receipt = Path(receipt)
        self._nonce = secrets.token_hex(32)
        self._tree = self._metadata = self._deadline = self._root = None
        self._hello = self._foreign = self._result = None
        self._namespace = None
        self._closed = False
        self._error = self._error_detail = None
        self._thread = None
        self._phase = "unstarted"
        self._stop = threading.Event()
        self._connections = set()
        self._rows = []
        self.options = {
            "version": 1,
            "mode": "root",
            "nonce": self._nonce,
            "socket": str(socket_path),
            "token": token,
            "executable": str(self._executable),
            "executable_sha256": self._binding["executable_sha256"],
            "plugin_sha256": self._binding["plugin_sha256"],
            "config_sha256": self._binding["config_sha256"],
            "agent": agent,
            "model": "anthropic/claude-opus-5-5",
            "tcp": dict(TCP_TARGET),
            "rpc_timeout_seconds": 10,
            "foreign_timeout_ms": 15000,
            "foreign_environment": dict(foreign_environment),
        }

    def start(self, tree, metadata, deadline):
        """Called by the original supervisor checkpoint while exec is held."""
        _require(self._tree is None and type(tree) is processes.ProcessTree, "missing original held process tree")
        root = tree.capture_root()
        _require(
            root is not None and processes.matches(root, metadata.get("provider")),
            "supervised root differs from original birth",
        )
        armed = supervision.receipt(metadata)
        _require(
            armed is not None and armed.get("phase") == "armed" and processes.live_processes([metadata["keeper"]]),
            "original keeper is not armed",
        )
        _require(deadline > time.monotonic(), "original probe deadline expired")
        self._tree, self._root, self._metadata, self._deadline = tree, dict(root), dict(metadata), deadline
        self._thread = threading.Thread(target=self._serve, name="autocode-kilo-client-conformance", daemon=False)
        self._thread.start()

    def _peer(self, connection):
        self._phase = "kernel_peer_identity"
        peer = self._peer_identity(connection)
        self._phase = "original_process_sample"
        self._tree.sample()
        self._phase = "original_peer_birth"
        saved = self._tree.known.get(peer["pid"])
        _require(
            saved is not None and processes.matches(saved, peer), "peer is not an actual original client descendant"
        )
        self._phase = "peer_executable"
        _require(
            Path(processes.psutil.Process(peer["pid"]).exe()).resolve() == self._executable
            and util.file_hash(self._executable) == self._binding["executable_sha256"],
            "peer is not the exact compiled client",
        )
        self._phase = "peer_birth_recheck"
        _require(
            processes.matches(peer, processes.process_table({peer["pid"]}).get(peer["pid"])),
            "peer birth changed during inspection",
        )
        return peer

    def _read(self, connection):
        raw = bytearray()
        while not raw.endswith(b"\n"):
            _require(len(raw) <= 8192 and time.monotonic() < self._deadline, "probe frame/deadline exceeded")
            chunk = connection.recv(min(4096, 8193 - len(raw)))
            _require(chunk, "truncated probe frame")
            raw.extend(chunk)
        _require(len(raw) <= 8192 and raw.count(b"\n") == 1, "ambiguous probe frame")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                _require(key not in result, "duplicate probe field")
                result[key] = value
            return result

        return json.loads(raw, object_pairs_hook=unique)

    def _handle(self, frame, peer):
        _require(
            isinstance(frame, dict) and frame.get("token") == self._token and frame.get("nonce") == self._nonce,
            "foreign probe capability",
        )
        _require(
            self._binding_reader() == self._binding and time.monotonic() < self._deadline,
            "original probe inputs/deadline changed",
        )
        root = processes.matches(self._hello or self._root, peer)
        if (
            not root
            and self._binding.get("kernel_backend") == "linux-bwrap-pid1-v1"
            and self._hello is None
            and frame.get("op") == "CLIENT_CONFORMANCE_HELLO"
            and frame.get("mode") == "root"
        ):
            self._namespace = namespace_peer.observe(peer, self._root, self._tree)
            root = True
        reply_pid = namespace_peer.reported_pid(peer) if self._namespace is not None else peer["pid"]
        if frame.get("op") == "CLIENT_CONFORMANCE_HELLO":
            _require(set(frame) == {"op", "token", "nonce", "mode"}, "unexpected hello fields")
            if frame["mode"] == "root":
                _require(root and self._hello is None, "positive channel needs the original root exactly once")
                self._hello = dict(peer)
                return {
                    "ok": True,
                    "kind": "CLIENT_CONFORMANCE_PARENT_PONG",
                    "nonce": self._nonce,
                    "peer_pid": reply_pid,
                    "peer_birth": peer["birth_identity"],
                }
            _require(
                frame["mode"] == "foreign" and not root and self._hello is not None and self._foreign is None,
                "missing actual foreign same-client peer",
            )
            self._foreign = dict(peer)
            return {
                "ok": False,
                "kind": "CLIENT_CONFORMANCE_FOREIGN_PEER_DENIED",
                "nonce": self._nonce,
                "peer_pid": reply_pid,
                "peer_birth": peer["birth_identity"],
            }
        _require(
            set(frame) == {"op", "token", "nonce", "tcp", "foreign_exit"}
            and frame["op"] == "CLIENT_CONFORMANCE_RESULT"
            and root
            and self._result is None,
            "result needs the original root exactly once",
        )
        _require(
            self._hello is not None
            and self._foreign is not None
            and type(frame["foreign_exit"]) is int
            and frame["foreign_exit"] == 0,
            "foreign rejection did not finish",
        )
        tcp = frame["tcp"]
        _require(
            isinstance(tcp, dict)
            and set(tcp) == {"host", "port", "status", "code"}
            and tcp["host"] == TCP_TARGET["host"]
            and tcp["port"] == TCP_TARGET["port"]
            and tcp["status"] == "denied"
            and tcp["code"] in ("EACCES", "EPERM"),
            "actual direct TCP permission refusal is missing",
        )
        _require(not processes.live_processes([self._foreign]), "foreign client is still live")
        self._result = deepcopy(frame)
        return {"ok": True, "kind": "CLIENT_CONFORMANCE_COMPLETE", "nonce": self._nonce}

    def _record_error(self, error):
        """Only controlled stages and trusted code positions, never message/locals."""
        self._error = type(error).__name__
        errno = getattr(error, "errno", None)
        trusted = {Path(__file__).resolve(), Path(__file__).with_name("autocode_kilo_source_relay.py").resolve()}
        frames, trace = [], error.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            path = Path(code.co_filename).resolve()
            if path in trusted and len(frames) < 8:
                frames.append({"module": path.name, "function": code.co_name, "line": trace.tb_lineno})
            trace = trace.tb_next
        self._error_detail = {
            "phase": self._phase,
            "type": self._error,
            "errno": errno if type(errno) is int else None,
            "code_frames": frames,
        }

    def _serve(self):
        while not self._stop.is_set():
            try:
                self._phase = "accept"
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError as error:
                self._record_error(error)
                return
            with connection:
                self._connections.add(connection)
                try:
                    self._phase = "socket_timeout"
                    connection.settimeout(min(10, max(0.001, self._deadline - time.monotonic())))
                    peer = self._peer(connection)
                    self._phase = "rpc_read"
                    frame = self._read(connection)
                    self._phase = "rpc_handle"
                    response = self._handle(frame, peer)
                    self._rows.append(
                        {
                            "peer": peer,
                            "frame": {key: value for key, value in frame.items() if key != "token"},
                            "response": response,
                        }
                    )
                    self._phase = "rpc_send"
                    connection.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode())
                except BaseException as error:
                    self._record_error(error)
                    self._stop.set()
                    try:  # noqa: SIM105 - Preserve propagation of grouped cleanup failures.
                        connection.sendall(b'{"ok":false,"error":"Compiled client probe refused"}\n')
                    except OSError:
                        pass
                finally:
                    self._connections.discard(connection)

    def finish(self, *, result):
        """Use actual original exit/keeper/tree closure, never a child verdict."""
        self._stop.set()
        for connection in tuple(self._connections):
            try:  # noqa: SIM105 - Preserve propagation of grouped cleanup failures.
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        complete = (
            self._thread is not None
            and not self._thread.is_alive()
            and self._error is None
            and self._hello is not None
            and self._foreign is not None
            and self._result is not None
        )
        originals = [] if self._tree is None else list(self._tree.known.values())
        if self._metadata is not None:
            originals.append(self._metadata["keeper"])
        live = processes.live_processes(originals)
        actual_supervision = None if self._metadata is None else supervision.receipt(self._metadata)
        body = {
            "kind": KIND,
            "nonce": self._nonce,
            "bindings": self._binding,
            "original_root": self._hello if self._namespace is not None else self._root,
            **({"client_namespace": self._namespace} if self._namespace is not None else {}),
            "original_foreign": self._foreign,
            "original_processes": originals,
            "matching_live": live,
            "supervision": self._metadata,
            "actual_call": result,
            "actual_supervision": actual_supervision,
            "observations": self._rows,
            "error": self._error,
            "error_detail": self._error_detail,
        }
        util.atomic_json(self._receipt, body)
        self._receipt.chmod(0o400)
        _require(
            complete
            and result.get("exit") == 0
            and result.get("expired") is False
            and (result.get("supervision_result") or {}).get("phase") == "stopped"
            and result.get("supervision_result") == actual_supervision
            and not live
            and self._binding_reader() == self._binding
            and time.monotonic() < self._deadline,
            "original compiled-client proof is incomplete",
        )
        validate_retained_receipt(
            body,
            executable_sha256=self._binding["executable_sha256"],
            client_profile_sha256=self._binding["client_profile_sha256"],
        )
        self._receipt_sha = util.file_hash(self._receipt)
        _require(time.monotonic() < self._deadline, "original probe expired before final receipt authentication")
        self._closed = True
        return self.locator()

    def locator(self):
        _require(self._closed, "no closed original compiled-client proof")
        return {"path": str(self._receipt), "sha256": self._receipt_sha}

    def require(self):
        _require(
            self._closed
            and self._error is None
            and self._binding_reader() == self._binding
            and util.file_hash(self._receipt) == self._receipt_sha,
            "actual original compiled-client conformance is missing or changed",
        )
        return self.locator()
