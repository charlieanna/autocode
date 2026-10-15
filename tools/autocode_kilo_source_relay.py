"""Parent-owned Unix relay for original Kilo source and Anthropic requests.

The child has no Internet or billing credential. Source observations arrive
before the final request; the parent independently derives and compares the
entire body. Only the original supervised launch can register a peer or publish
a closed delivery authority. Stored configuration cannot perform either action.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import http.client
import json
import math
import os
import secrets
import socket
import ssl
import stat
import struct
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from . import autocode_kilo_client_conformance as client_conformance
    from . import autocode_process as processes
    from . import autocode_util as util
except ImportError:
    import autocode_kilo_client_conformance as client_conformance
    import autocode_process as processes
    import autocode_util as util


ENDPOINT = "https://api.anthropic.com/v1/messages"
_REGISTRATION = object()
_PENDING = {}
_CLOSED = {}
UNDEFINED = object()
PUBLIC_API_KEY = "autocode-source-derivation-public-placeholder"
RUNTIME_USER_AGENT = "ai-sdk/provider-utils/4.0.46 runtime/bun/1.3.14"


class SourceRelayError(ValueError):
    pass


def _require(value, reason):
    if not value:
        raise SourceRelayError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "Duplicate source key")
            result[key] = value
        return result

    return json.loads(
        raw, object_pairs_hook=unique, parse_constant=lambda _: _require(False, "Non-finite source value")
    )


def _encode_json(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical(value):
    """The source hook's ordered, lossless grammar, not ordinary sorted JSON."""
    if value is UNDEFINED:
        node = ["undefined"]
    elif value is None:
        node = ["null"]
    elif isinstance(value, bool):
        node = ["boolean", value]
    elif isinstance(value, str):
        node = ["string", value]
    elif isinstance(value, bytes):
        node = ["bytes", len(value), base64.b64encode(value).decode()]
    elif type(value) in (int, float):
        _require(math.isfinite(value), "Non-finite source number")
        node = ["number", int(value) if value == int(value) else value]
    elif isinstance(value, list):
        return '["array",[' + ",".join(canonical(item) for item in value) + "]]"
    else:
        _require(isinstance(value, dict) and all(isinstance(k, str) for k in value), "Unsupported source value")
        return (
            '["object",[' + ",".join("[" + _encode_json(k) + "," + canonical(v) + "]" for k, v in value.items()) + "]]"
        )
    return _encode_json(node)


def decode_source(raw, *, allow_bytes=False):
    """Decode original tagged data with duplicate/arity/depth checks."""

    def decode(node, depth=0):
        _require(depth <= 80 and isinstance(node, list) and node, "Invalid canonical source node")
        tag = node[0]
        if tag in ("undefined", "null"):
            _require(len(node) == 1, "Invalid canonical source arity")
            return UNDEFINED if tag == "undefined" else None
        if tag == "bytes":
            _require(
                allow_bytes and len(node) == 3 and type(node[1]) is int and node[1] >= 0 and isinstance(node[2], str),
                "Unsealed source bytes",
            )
            value = base64.b64decode(node[2], validate=True)
            _require(len(value) == node[1] and base64.b64encode(value).decode() == node[2], "Invalid source bytes")
            return value
        _require(len(node) == 2, "Invalid canonical source arity")
        value = node[1]
        if tag in ("string", "boolean", "number"):
            _require(
                (tag == "string" and isinstance(value, str))
                or (tag == "boolean" and type(value) is bool)
                or (tag == "number" and type(value) in (int, float) and math.isfinite(value)),
                "Invalid canonical source primitive",
            )
            return value
        _require(isinstance(value, list), "Invalid canonical source container")
        if tag == "array":
            return [decode(item, depth + 1) for item in value]
        _require(tag == "object", "Invalid canonical source tag")
        result = {}
        for pair in value:
            _require(
                isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str) and pair[0] not in result,
                "Duplicate canonical source key",
            )
            result[pair[0]] = decode(pair[1], depth + 1)
        return result

    return decode(_json(raw))


def sealed_source(value):
    if isinstance(value, bytes):
        return {"bytes": len(value), "base64": base64.b64encode(value).decode(), "sha256": _sha(value)}
    if isinstance(value, list):
        return [sealed_source(item) for item in value]
    if isinstance(value, dict):
        return {key: sealed_source(item) for key, item in value.items()}
    return value


def compare_derived_request(actual, expected):
    """The actual body cannot update its independently derived expectation."""
    _require(isinstance(actual, bytes) and isinstance(expected, bytes), "Request comparison needs original bytes")
    _require(actual == expected, "Final Anthropic body differs from original source derivation")
    return {"sha256": _sha(actual), "bytes": len(actual)}


def parent_anthropic_key(data_home):
    """Read only the original parent's native Anthropic API login.

    This is called inside the parent at dispatch, never copied to child inputs,
    journals, receipts or debug output. Unknown login modes are refused.
    """
    root = Path(data_home).absolute()
    _require(root == root.resolve(), "Parent auth data path is aliased")
    folder = root / "kilo"
    _require(folder == folder.resolve(), "Parent Kilo auth directory is aliased")
    fd = os.open(folder / "auth.json", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == os.getuid()
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_nlink == 1
            and 0 < before.st_size <= 65536,
            "Parent Anthropic auth identity is unsupported",
        )
        raw = os.read(fd, 65537)
        after = os.fstat(fd)
        _require(
            len(raw) == before.st_size
            and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "Parent auth changed while reading",
        )
        saved = _json(raw)
        auth = saved.get("anthropic") if isinstance(saved, dict) else None
        _require(isinstance(auth, dict) and auth.get("type") == "api", "Parent Anthropic API login is unavailable")
        key = auth.get("key")
        _require(
            isinstance(key, str) and key and len(key) <= 16384 and "\r" not in key and "\n" not in key,
            "Parent Anthropic API login is unavailable",
        )
        return key
    finally:
        os.close(fd)


def forward_anthropic(body, public_headers, *, data_home, deadline, response_limit, own_connection):
    """One fixed verified-TLS request with no proxy or redirect fallback."""
    remaining = deadline - time.monotonic()
    _require(remaining > 0, "Original source relay deadline expired")
    _require(
        isinstance(public_headers, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in public_headers.items())
        and not any(
            k.lower() in {"authorization", "x-api-key", "proxy-authorization", "cookie", "host"}
            or "\r" in k + v
            or "\n" in k + v
            for k, v in public_headers.items()
        ),
        "Unqualified request headers",
    )
    headers = {**public_headers, "x-api-key": parent_anthropic_key(data_home)}
    # HTTPSConnection does not consume proxy environment. The fixed host/path
    # and default verified TLS are the only route; no redirect is followed.
    connection = http.client.HTTPSConnection(
        "api.anthropic.com", timeout=remaining, context=ssl.create_default_context()
    )
    own_connection(connection)
    try:
        connection.request("POST", "/v1/messages", body=body, headers=headers)
        response = connection.getresponse()
        _require(not 300 <= response.status < 400, "Anthropic redirects are not supported")
        chunks, size = [], 0
        while True:
            _require(time.monotonic() < deadline, "Original source relay deadline expired")
            if connection.sock is not None:
                connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
            chunk = response.read(min(65536, response_limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            _require(size <= response_limit, "Anthropic response exceeds original bound")
        raw = b"".join(chunks)
        public = {
            k: v
            for k, v in response.headers.items()
            if k.lower() in {"content-type", "request-id", "anthropic-request-id", "retry-after"}
            or k.lower().startswith("anthropic-ratelimit-")
        }
        return int(response.status), public, raw
    finally:
        connection.close()
        own_connection(None)


def _peer_identity(connection):
    if os.uname().sysname == "Darwin":
        # Darwin sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=2. getpeereid supplies UID.
        pid = struct.unpack("i", connection.getsockopt(0, 2, struct.calcsize("i")))[0]
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        _require(
            ctypes.CDLL(None).getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) == 0,
            "Cannot authenticate Unix peer UID",
        )
        peer_uid = uid.value
    else:
        pid, peer_uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    _require(peer_uid == os.getuid() and pid > 0, "Foreign Unix source peer")
    table = processes.process_table({pid})
    _require(pid in table, "Source peer has no original birth identity")
    return processes.identity(table[pid])


@dataclass(frozen=True)
class DerivedRequest:
    body: bytes
    headers: dict
    source_digest: str
    owner_digest: str
    source_index: int
    request_id: str


class KiloSourceRelay:
    """One original launch's listener, source journal and bounded dispatch."""

    def __init__(
        self,
        directory,
        *,
        protected_inputs,
        source_hooks,
        derive,
        validate_arm,
        data_home,
        max_requests,
        frame_limit,
        response_limit,
        current_source,
        audit_options,
        parse_request,
        parse_response,
        executable,
        rpc_timeout_seconds=600,
    ):
        _require(
            callable(derive) and callable(validate_arm) and callable(current_source),
            "Missing CODE-owned source derivation or arming",
        )
        _require(callable(parse_request) and callable(parse_response), "Missing original byte decoders")
        _require(type(max_requests) is int and 0 < max_requests <= 8, "Invalid original request bound")
        _require(
            type(frame_limit) is int and frame_limit > 0 and type(response_limit) is int and response_limit > 0,
            "Missing finite original byte bounds",
        )
        self.directory = Path(directory).absolute()
        _require(
            self.directory == self.directory.resolve() and self.directory.is_dir(), "Foreign source relay namespace"
        )
        folder = self.directory.stat()
        _require(
            folder.st_uid == os.getuid() and stat.S_IMODE(folder.st_mode) == 0o700,
            "Source relay directory is not private and owned",
        )
        self._inputs, self._hooks = protected_inputs, source_hooks
        self._derive, self._validate_arm, self._current_source = derive, validate_arm, current_source
        self._data_home, self._max = data_home, max_requests
        self._frame_limit, self._response_limit = frame_limit, response_limit
        _require(
            type(rpc_timeout_seconds) in (int, float) and 0 < rpc_timeout_seconds <= 600,
            "Source RPC timeout exceeds original bound",
        )
        self._rpc_timeout = rpc_timeout_seconds
        self._audit_options = dict(audit_options)
        self._audit_path = Path(audit_options["path"])
        _require(
            self._audit_path == self._audit_path.resolve()
            and self._audit_path.is_relative_to(self.directory)
            and not self._audit_path.exists(),
            "Foreign or already used image audit",
        )
        self._parse_request, self._parse_response = parse_request, parse_response
        self._executable = Path(executable).resolve()
        self._executable_sha = util.file_hash(self._executable)
        self.id, self._token = secrets.token_hex(32), secrets.token_hex(32)
        # sockaddr_un is only 104 bytes on macOS. The listener has its own
        # short, parent-owned namespace; evidence remains in the original run.
        self._socket_directory = Path(tempfile.mkdtemp(prefix="ac-kilo-", dir="/private/tmp"))
        self.socket_path = self._socket_directory / "source.sock"
        self.journal_path = self.directory / "source.jsonl"
        self._fd = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(self._fd, 0o400)
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            _require(len(os.fsencode(self.socket_path)) < 104, "Unix source socket path exceeds native bound")
            self._listener.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            self._listener.listen(4)
            self._listener.settimeout(0.1)
        except BaseException:
            self._listener.close()
            os.close(self._fd)
            if self.socket_path.exists():
                self.socket_path.unlink()
            self._socket_directory.rmdir()
            raise
        self._stop = threading.Event()
        self._thread = None
        self._tree = self._prepared = self._deadline = self._peer = self._seal = None
        self._arms = {}
        self._sources = {}
        self._used_requests = set()
        self._projections = {}
        self._contexts = {}
        self._admission = 0
        self._sequence = 0
        self._audit_sequence = 0
        self._audit_fd = None
        self._failure = None
        self._closed = False
        self._connections = set()
        self._client_conformance = None
        self._http = None
        self._connection_lock = threading.Lock()
        _PENDING[self.id] = self

    def public_locator(self):
        return {"id": self.id, "journal_path": str(self.journal_path), "source_hooks": self._hooks}

    def child_channel(self):
        return {
            "socket": str(self.socket_path),
            "token": self._token,
            "rpc_timeout_seconds": self._rpc_timeout,
            "max_requests": self._max,
            **{key: self._audit_options[key] for key in ("reviewer", "binding_sha256", "attempt_id", "plugin_sha256")},
            "source_hooks": self._hooks,
        }

    def prepare_client_conformance(self, key, *, current_binding, receipt, agent, foreign_environment):
        _require(
            key is _REGISTRATION and self._tree is None and not self._closed and self._client_conformance is None,
            "foreign or repeated compiled-client conformance",
        )
        self._reset_unregistered_listener()
        control = client_conformance.ClientConformance(
            listener=self._listener,
            token=self._token,
            socket_path=self.socket_path,
            executable=self._executable,
            peer_identity=_peer_identity,
            current_binding=current_binding,
            receipt=receipt,
            agent=agent,
            foreign_environment=foreign_environment,
        )
        self._client_conformance = control
        return control

    def _reset_unregistered_listener(self):
        """Earlier debug events cannot enter the original client's handshake."""
        _require(
            self._tree is None
            and self._thread is None
            and self._prepared is None
            and self._peer is None
            and self._seal is None
            and self._deadline is None
            and self._audit_fd is None
            and self._client_conformance is None
            and not self._connections
            and self._admission == 0
            and not self._closed,
            "source listener is already active",
        )
        directory, path = self._socket_directory, self.socket_path
        folder, node = directory.lstat(), path.lstat()
        _require(
            directory.resolve() == directory
            and not directory.is_symlink()
            and stat.S_ISDIR(folder.st_mode)
            and folder.st_uid == os.getuid()
            and stat.S_IMODE(folder.st_mode) == 0o700
            and path == directory / "source.sock"
            and not path.is_symlink()
            and stat.S_ISSOCK(node.st_mode)
            and node.st_uid == os.getuid()
            and stat.S_IMODE(node.st_mode) == 0o600
            and self._listener.getsockname() == str(path),
            "source listener ownership changed",
        )
        self._listener.close()
        path.unlink()
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._listener.bind(str(path))
            os.chmod(path, 0o600)
            self._listener.listen(4)
            self._listener.settimeout(0.1)
        except BaseException:
            self._listener.close()
            raise

    def require_client_conformance(self):
        _require(
            type(self._client_conformance) is client_conformance.ClientConformance,
            "no actual original compiled-client conformance",
        )
        return self._client_conformance.require()

    def _append(self, kind, **value):
        row = {"sequence": self._sequence, "type": kind, **value}
        self._write_row(self._fd, row)
        self._sequence += 1

    def _write_row(self, fd, row):
        raw = (_encode_json(row) + "\n").encode()
        offset = 0
        while offset < len(raw):
            offset += os.write(fd, raw[offset:])
        os.fsync(fd)

    def _audit(self, kind, **value):
        _require(self._audit_fd is not None, "Image audit has no original registered owner")
        self._write_row(
            self._audit_fd,
            {"sequence": self._audit_sequence, "at_ms": time.time_ns() // 1_000_000, "type": kind, **value},
        )
        self._audit_sequence += 1
        _require(time.monotonic() <= self._deadline, "Original launch deadline expired during audit publication")

    def _register_original(self, key, *, prepared, tree, deadline, launch_seal):
        _require(
            key is _REGISTRATION and self._tree is None and not self._closed, "Foreign original source registration"
        )
        self.require_client_conformance()
        _require(
            type(tree) is processes.ProcessTree and tree.capture_root() is not None,
            "Missing original unreaped supervised root",
        )
        _require(type(deadline) in (int, float) and deadline > time.monotonic(), "Original launch deadline expired")
        self._prepared, self._tree, self._deadline, self._seal = prepared, tree, deadline, launch_seal
        self._audit_fd = os.open(self._audit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(self._audit_fd, 0o400)
        self._audit(
            "audit_start",
            version=3,
            max_requests=self._max,
            source_hooks=self._hooks,
            **{key: self._audit_options[key] for key in ("reviewer", "binding_sha256", "attempt_id", "plugin_sha256")},
        )
        self._thread = threading.Thread(target=self._serve, name="autocode-kilo-source", daemon=False)
        self._thread.start()

    def _authenticate(self, connection):
        _require(self._tree is not None and not self._closed, "Original source owner is not registered")
        peer = _peer_identity(connection)
        self._tree.sample()
        _require(
            peer["pid"] == self._tree.pid and processes.matches(self._tree.known.get(peer["pid"]), peer),
            "Source peer is not the original supervised Kilo root",
        )
        _require(self._peer is None or processes.matches(self._peer, peer), "Original source peer changed")
        # A shell or an arbitrary descendant must not arm the source hooks.
        # Inspect only this already authenticated original descendant.
        process = processes.psutil.Process(peer["pid"])
        _require(
            Path(process.exe()).resolve() == self._executable
            and util.file_hash(self._executable) == self._executable_sha,
            "Source peer is not the pinned compiled Kilo client",
        )
        current = processes.process_table({peer["pid"]})
        _require(processes.matches(peer, current.get(peer["pid"])), "Source peer birth changed during inspection")
        self._peer = peer
        self._current_source()
        _require(time.monotonic() <= self._deadline, "Original source deadline expired")

    def _read(self, connection):
        data = bytearray()
        while not data.endswith(b"\n"):
            _require(len(data) <= self._frame_limit, "Source frame exceeds original bound")
            chunk = connection.recv(min(65536, self._frame_limit + 1 - len(data)))
            _require(chunk, "Truncated source frame")
            data.extend(chunk)
        _require(len(data) <= self._frame_limit and data.count(b"\n") == 1, "Ambiguous source frame")
        return _json(bytes(data))

    def _handle(self, frame):
        _require(isinstance(frame, dict) and frame.get("token") == self._token, "Foreign source capability")
        op = frame.get("op")
        if op == "ARM":
            projection = decode_source(frame["canonical_projection"], allow_bytes=True)
            request_id = projection["prepared"]["headers"].get("x-autocode-image-request")
            _require(
                isinstance(request_id, str)
                and request_id
                and request_id not in self._arms
                and len(self._arms) < self._max,
                "Repeated or excessive original source arm",
            )
            original = self._validate_arm(projection, self._inputs)
            _require(isinstance(original, dict), "Arming did not return original protected inputs")
            context = self._contexts.get(request_id)
            _require(
                context is not None
                and projection.get("sessionID") == context.get("session_id")
                and projection.get("userMessageID") == context.get("user_message_id")
                and projection.get("agent") == context.get("agent"),
                "ARM differs from original review context",
            )
            raw = canonical(original)
            self._arms[request_id] = original
            self._projections[request_id] = projection
            self._append(
                "arm",
                request_id=request_id,
                original_inputs_sha256=_sha(raw.encode()),
                canonical_original_input=raw,
                peer=self._peer,
                launch_seal=self._seal,
            )
            return {"kind": "ARM", "canonical_original_input": raw}
        if op == "SOURCE_COMMIT":
            raw = frame["canonical_record"].encode()
            _require(_sha(raw) == frame["source_digest"], "Original source digest mismatch")
            source = decode_source(raw)
            request_id = source["prepared"]["headers"].get("x-autocode-image-request")
            arm = self._arms.get(request_id)
            _require(
                arm is not None
                and request_id not in self._sources
                and source.get("index") == 0
                and source.get("owner") == arm["owner"]
                and source.get("ownerDigest") == _sha(canonical(arm["owner"]).encode()),
                "Source commit has no unique original arm",
            )
            projection = self._projections[request_id]
            bind = {
                "prepared": projection["prepared"],
                "model": projection["model"],
                "capInputs": projection["capInputs"],
                "toolChoice": projection["toolChoice"],
                "retries": projection["retries"],
                "parentSessionID": projection["parentSessionID"],
            }
            _require(
                source.get("prepared") == sealed_source(projection["prepared"])
                and source.get("historyDigest") == _sha(canonical(arm["messages"]).encode())
                and source.get("bindDigest") == _sha(canonical(bind).encode()),
                "Original prepared projection, history or bind digest changed",
            )
            derived = self._derive(
                {
                    "canonical_record": frame["canonical_record"],
                    "source_digest": frame["source_digest"],
                    "owner_digest": source["ownerDigest"],
                    "source_index": source["index"],
                    "model_id": "claude-opus-5-5",
                }
            )
            _require(
                type(derived) is DerivedRequest and derived.source_digest == frame["source_digest"],
                "Missing complete independent source derivation",
            )
            _require(
                derived.source_index == 0 and derived.request_id == request_id,
                "Source derivation changed nonce or original index",
            )
            self._sources[request_id] = derived
            self._append(
                "source_commit",
                source_digest=derived.source_digest,
                owner_digest=derived.owner_digest,
                request_id=request_id,
                source_index=derived.source_index,
                canonical_record=frame["canonical_record"],
                expected_body_sha256=_sha(derived.body),
                expected_body_bytes=len(derived.body),
                expected_headers_sha256=util.digest(derived.headers),
            )
            return {
                "kind": "SOURCE_COMMIT",
                "digest": derived.source_digest,
                "ownerDigest": derived.owner_digest,
                "index": derived.source_index,
                "request_id": request_id,
            }
        if op == "NATIVE_EVENT":
            row = frame.get("row")
            _require(
                isinstance(row, dict)
                and row.get("type") in {"review_context", "native_message", "native_start", "native_finish"}
                and not any(key in row for key in ("sequence", "at_ms")),
                "Child cannot publish wire or authority facts",
            )
            if row["type"] == "review_context":
                fields = {
                    "type",
                    "request_id",
                    "session_id",
                    "user_message_id",
                    "provider",
                    "model",
                    "agent",
                    "image_capable",
                    "wire_model",
                }
                _require(
                    set(row) == fields
                    and row["provider"] == "kilocode"
                    and row["model"] == "anthropic/claude-opus-5-5"
                    and row["wire_model"] == "claude-opus-5-5"
                    and row["agent"] == self._audit_options["reviewer"]["agent"]
                    and row["image_capable"] is True
                    and isinstance(row["request_id"], str)
                    and row["request_id"] not in self._contexts,
                    "Foreign native review context",
                )
                self._contexts[row["request_id"]] = dict(row)
            self._audit(row["type"], **{key: value for key, value in row.items() if key != "type"})
            return {"kind": "NATIVE_EVENT"}
        request_id = frame.get("request_id")
        self.require_client_conformance()
        source = self._sources.get(request_id)
        _require(
            op == "REQUEST" and source is not None and request_id not in self._used_requests,
            "Request has no unused original source commit",
        )
        _require(frame.get("path") == "/v1/messages", "Unqualified inference endpoint")
        _require(
            self._admission < self._max and frame.get("admission_index") == self._admission + 1,
            "Original request bound or order changed",
        )
        for key in ("source_digest", "owner_digest", "source_index"):
            _require(frame.get(key) == getattr(source, key), "Request is not linked to original source")
        body = base64.b64decode(frame["body_base64"], validate=True)
        _require(len(body) == frame["body_bytes"] and _sha(body) == frame["body_sha256"], "Request byte fact changed")
        compare_derived_request(body, source.body)
        actual_headers = frame.get("headers")
        _require(
            isinstance(actual_headers, dict) and actual_headers.get("x-api-key") == PUBLIC_API_KEY,
            "Child request lacks its exact public placeholder",
        )
        _require(
            {key: value for key, value in actual_headers.items() if key != "x-api-key"} == source.headers,
            "Final request headers differ from source derivation",
        )
        self._current_source()
        self._admission += 1
        self._used_requests.add(request_id)
        joins = {
            "request_id": request_id,
            "admission_index": self._admission,
            "source_digest": source.source_digest,
            "owner_digest": source.owner_digest,
            "source_index": source.source_index,
        }
        self._audit(
            "source_commit",
            **joins,
            source_record_sha256=source.source_digest,
            peer_pid=self._peer["pid"],
            peer_birth=self._peer["birth_identity"],
        )
        facts = self._parse_request(body)
        _require(
            isinstance(facts, dict)
            and facts.get("body_sha256") == _sha(body)
            and facts.get("payload_complete") is True,
            "Original request decoder refused final bytes",
        )
        context = self._contexts[request_id]
        self._audit(
            "request",
            **joins,
            **{key: value for key, value in context.items() if key not in ("type", "request_id")},
            **facts,
        )
        status, headers, response = forward_anthropic(
            body,
            source.headers,
            data_home=self._data_home,
            deadline=min(self._deadline, time.monotonic() + self._rpc_timeout),
            response_limit=self._response_limit,
            own_connection=self._own_http,
        )
        self._audit(
            "response",
            request_id=request_id,
            admission_index=self._admission,
            status=status,
            body_present=bool(response),
            cloneable=True,
        )
        completion = self._parse_response(response, headers)
        _require(
            isinstance(completion, dict) and completion.get("response_body_sha256") == _sha(response),
            "Original response decoder refused complete bytes",
        )
        self._audit("request_complete", request_id=request_id, admission_index=self._admission, **completion)
        return {
            "kind": "RESPONSE",
            "status": status,
            "headers": headers,
            "body_base64": base64.b64encode(response).decode(),
            "body_sha256": _sha(response),
            "body_bytes": len(response),
        }

    def _serve(self):
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with connection:
                self._connections.add(connection)
                try:
                    connection.settimeout(max(0.001, self._deadline - time.monotonic()))
                    self._authenticate(connection)
                    response = self._handle(self._read(connection))
                    connection.sendall((_encode_json({"ok": True, **response}) + "\n").encode())
                except BaseException as error:
                    self._failure = type(error).__name__
                    self._stop.set()
                    try:  # noqa: SIM105 - Preserve propagation of grouped cleanup failures.
                        connection.sendall(b'{"ok":false,"error":"Original source relay refused"}\n')
                    except OSError:
                        pass
                finally:
                    self._connections.discard(connection)

    def _own_http(self, connection):
        with self._connection_lock:
            self._http = connection

    def close(self, *, original_exit, launch_seal):
        self._stop.set()
        self._listener.close()
        for connection in tuple(self._connections):
            try:  # noqa: SIM105 - Preserve propagation of grouped cleanup failures.
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        with self._connection_lock:
            if self._http is not None and self._http.sock is not None:
                try:  # noqa: SIM105 - Preserve propagation of grouped cleanup failures.
                    self._http.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self._http.close()
        if self._thread is not None:
            self._thread.join(timeout=20)
        _require(self._thread is None or not self._thread.is_alive(), "Original source relay cleanup is incomplete")
        self._append(
            "closed",
            original_exit=original_exit,
            failure=self._failure,
            launch_seal=launch_seal,
            audit_sha256=None if self._audit_fd is None else util.file_hash(self._audit_path),
        )
        os.close(self._fd)
        if self._audit_fd is not None:
            os.close(self._audit_fd)
        self._closed = True
        _PENDING.pop(self.id, None)
        self.socket_path.unlink(missing_ok=True)
        self._socket_directory.rmdir()
        if original_exit == 0 and self._failure is None and self._admission > 0:
            _require(launch_seal == self._seal and self._peer is not None, "Closed source launch seal changed")
            _require(time.monotonic() <= self._deadline, "Original launch expired before source closure")
            current = processes.process_table(set(self._tree.known))
            _require(
                not any(processes.matches(saved, current.get(pid)) for pid, saved in self._tree.known.items()),
                "Original supervised source children remain live",
            )
            self._journal_sha = util.file_hash(self._audit_path)
            self._parent_journal_sha = util.file_hash(self.journal_path)
            _CLOSED[self.id] = self


def require_delivery_authority(record, *, audit_bytes, binding, attempt_id, plugin_sha256):
    """An arbitrary caller dictionary cannot create an original source authority."""
    isolation = record.get("native_role_isolation", {})
    locator = isolation.get("source_relay", {})
    owner = _CLOSED.get(locator.get("id"))
    _require(
        type(owner) in (KiloSourceRelay, _RetainedSource) and owner._closed and owner._failure is None,
        "No closed original supervised source relay",
    )
    _require(
        locator == owner.public_locator()
        and (
            owner._audit_bytes == audit_bytes
            if type(owner) is _RetainedSource
            else util.file_hash(owner._audit_path) == owner._journal_sha
        ),
        "Original source journal changed",
    )
    _require(
        isinstance(audit_bytes, bytes) and binding.get("reviewer", {}).get("provider") == "kilocode",
        "Foreign native image delivery binding",
    )
    _require(
        _sha(audit_bytes) == owner._journal_sha
        and attempt_id == owner._audit_options["attempt_id"]
        and plugin_sha256 == owner._audit_options["plugin_sha256"]
        and util.digest(binding) == owner._audit_options["binding_sha256"],
        "Original audit, attempt, plugin or source binding changed",
    )
    return {
        "journal_sha256": owner._journal_sha,
        "journal_path": str(owner._audit_path),
        "source_hooks": owner._hooks,
        "peer_pid": owner._peer["pid"],
        "peer_birth": owner._peer["birth_identity"],
        "launch_seal": owner._seal,
    }


class _RetainedSource:
    """Original bytes authenticated by the existing native-history primitive."""

    def public_locator(self):
        return self._locator


def register_retained_original(key, *, locator, parent_bytes, audit_bytes, launch_seal, hooks, audit_options):
    """Only NativeReviewHistory calls this after genuine original launch replay.

    Its protected reader has already authenticated the actual held Popen seal,
    finished exit, kernel/profile/code bytes and copied namespace. This API
    checks the original source-to-wire joins; no public dictionary can invoke
    it through the delivery verifier or grant new launch authority.
    """
    _require(
        key is _REGISTRATION and isinstance(parent_bytes, bytes) and isinstance(audit_bytes, bytes),
        "Missing original native-history capability",
    )
    rows = [_json(line) for line in parent_bytes.splitlines()]
    audit = [_json(line) for line in audit_bytes.splitlines()]
    _require(
        parent_bytes.endswith(b"\n") and rows and [row.get("sequence") for row in rows] == list(range(len(rows))),
        "Original parent source journal is incomplete",
    )
    closed = rows[-1]
    _require(
        closed.get("type") == "closed"
        and closed.get("original_exit") == 0
        and closed.get("failure") is None
        and closed.get("launch_seal") == launch_seal
        and closed.get("audit_sha256") == _sha(audit_bytes),
        "Original source did not close against its actual native launch",
    )
    _require(
        audit
        and audit[0].get("source_hooks") == hooks
        and all(audit[0].get(k) == audit_options[k] for k in ("attempt_id", "plugin_sha256", "binding_sha256")),
        "Original source/audit binding changed",
    )
    arms = {row["request_id"]: row for row in rows if row.get("type") == "arm"}
    commits = [row for row in rows if row.get("type") == "source_commit"]
    requests = [row for row in audit if row.get("type") == "request"]
    _require(
        len(commits) == len(arms) == len(requests) and 0 < len(requests) <= audit[0]["max_requests"],
        "Original source arm/commit/request coverage changed",
    )
    peer = launch_seal["root"]
    for commit, request in zip(commits, requests, strict=True):
        nonce = request.get("request_id")
        arm = arms.get(nonce, {})
        original = decode_source(arm.get("canonical_original_input", ""), allow_bytes=True)
        source = decode_source(commit["canonical_record"])
        _require(
            arm.get("launch_seal") == launch_seal
            and processes.matches(peer, arm.get("peer"))
            and source.get("owner") == original["owner"]
            and source["owner"]["peerPID"] == peer["pid"]
            and source["owner"]["peerBirth"] == peer["birth_identity"],
            "Original source peer changed",
        )
        _require(
            commit.get("request_id") == nonce
            and _sha(commit["canonical_record"].encode()) == commit["source_digest"] == request.get("source_digest")
            and source.get("ownerDigest") == commit["owner_digest"] == request.get("owner_digest")
            and _sha(canonical(source["owner"]).encode()) == commit["owner_digest"]
            and source.get("index") == commit["source_index"] == request.get("source_index") == 0
            and source["prepared"]["headers"]["x-autocode-image-request"] == nonce
            and commit["expected_body_sha256"] == request.get("body_sha256"),
            "Original source acknowledgement or independently derived whole body changed",
        )
    existing = _CLOSED.get(locator["id"])
    if type(existing) is KiloSourceRelay:
        _require(
            existing.public_locator() == locator and existing._parent_journal_sha == _sha(parent_bytes),
            "Original live-process source evidence differs from retained bytes",
        )
        return
    owner = _RetainedSource()
    owner._closed, owner._failure, owner._locator = True, None, dict(locator)
    owner._audit_bytes, owner._journal_sha = audit_bytes, _sha(audit_bytes)
    owner._audit_path = Path(audit_options["path"])
    owner._hooks, owner._peer, owner._seal = hooks, peer, launch_seal
    owner._audit_options = dict(audit_options)
    _CLOSED[locator["id"]] = owner


def closed_source_evidence(record):
    """Additional original parent bytes, before protected history publication."""
    locator = record.get("native_role_isolation", {}).get("source_relay", {})
    owner = _CLOSED.get(locator.get("id"))
    _require(
        type(owner) is KiloSourceRelay
        and owner._closed
        and owner._failure is None
        and locator == owner.public_locator(),
        "Missing actual closed original parent source",
    )
    _require(util.file_hash(owner.journal_path) == owner._parent_journal_sha, "Closed original parent source changed")
    return {str(owner.journal_path): owner._parent_journal_sha}


def pending_original(locator):
    """Called only after NativeRunContext checks its exact original preparation."""
    _require(isinstance(locator, dict), "Missing original source locator")
    owner = _PENDING.get(locator.get("id"))
    _require(
        type(owner) is KiloSourceRelay
        and locator == owner.public_locator()
        and not owner._closed
        and owner._tree is None,
        "Foreign or reused original source relay",
    )
    return owner
