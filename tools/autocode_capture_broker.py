"""Exact asset delivery over one retained loopback listener capability.

The parent owns the socket and services only sealed asset identifiers. This is
acquisition transport, not task authority or image acceptance. A caller must
admit the original child, sandbox and source before invoking the broker.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import socket
import stat
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_TARGET_REQUESTS = 64
MAX_CAPTURE_REQUESTS = 128
MAX_TARGET_LENGTH = 4096


def _require(value, reason):
    if not value:
        raise ValueError("Capture broker: " + reason)


def _regular(path):
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode), "input is not an original regular file")
    return info


def _read(path, maximum, *, dir_fd=None):
    # No following a target-controlled alias, including during open.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        _require(
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum, "invalid bounded packet"
        )
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(maximum + 1)
        _require(len(data) <= maximum, "packet exceeds its bound")
        return data
    finally:
        os.close(fd)


def _object(raw):
    def pairs(rows):
        value = {}
        for key, item in rows:
            _require(key not in value, "duplicate packet key")
            value[key] = item
        return value

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _value: _require(False, "nonfinite packet"))


PREVIEW_ORIGIN = "http://127.0.0.1:3000"


def asset_targets(assets, preview_origin=None):
    """Map sealed virtual asset names onto the SAME listener's literal targets."""
    _require(preview_origin in (None, PREVIEW_ORIGIN), "preview origin must be the exact declared loopback origin")
    targets, previews = {}, 0
    for row in assets:
        _require(isinstance(row, dict) and set(row) == {"url", "path"}, "exact asset declaration required")
        url = row["url"]
        _require(isinstance(url, str), "asset identifier must be literal")
        if url.startswith("/"):
            target = url
        else:
            _require(
                preview_origin == PREVIEW_ORIGIN and url.startswith(PREVIEW_ORIGIN + "/"),
                "absolute asset is outside the declared preview origin",
            )
            target = url[len(PREVIEW_ORIGIN) :]
            previews += 1
        _require(
            target.startswith("/")
            and not target.startswith("//")
            and len(url) <= MAX_TARGET_LENGTH
            and all(33 <= ord(char) <= 126 for char in url)
            and not any(char in url for char in ("#", "\\"))
            and ".." not in unquote(target.partition("?")[0]).split("/")
            and url not in targets,
            "only unique literal origin request targets are supported",
        )
        targets[url] = target
    _require(preview_origin is None or previews > 0, "declared preview has no immutable asset")
    return targets


class AssetBroker:
    """One real listener lease; no URL, file path or headers are request inputs."""

    def __init__(self, root, assets, requests, responses, *, deadline, preview_origin=None):
        self.root = Path(root).resolve()
        self.requests, self.responses = Path(requests), Path(responses)
        _require(type(deadline) in (int, float) and deadline > time.monotonic(), "expired acquisition")
        _require(
            self.requests.is_dir()
            and self.responses.is_dir()
            and not self.requests.is_symlink()
            and not self.responses.is_symlink(),
            "owned IPC directories required",
        )
        self.deadline = deadline
        self.nonce = uuid.uuid4().hex
        self.targets = asset_targets(assets, preview_origin)
        self.assets = {}
        for row in assets:
            _require(isinstance(row, dict) and set(row) == {"url", "path"}, "exact asset declaration required")
            url, name = row["url"], row["path"]
            relative = Path(name)
            _require(
                isinstance(name, str)
                and not relative.is_absolute()
                and ".." not in relative.parts
                and str(relative) == name,
                "asset has a nonliteral source path",
            )
            path = self.root / relative
            _require(
                not any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(self.root)),
                "asset aliases are unsupported",
            )
            info = _regular(path)
            _require(info.st_size <= MAX_ASSET_BYTES, "asset exceeds byte-preserving bound")
            raw = _read(path, MAX_ASSET_BYTES)
            self.assets[url] = {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        _require(self.assets, "empty asset inventory")
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.bind(("127.0.0.1", 0))
            self.identity = self._socket_identity()
            _require(
                preview_origin is None or self.identity["address"][1] != 3000,
                "preview origin collides with the original primary listener",
            )
        except BaseException:
            self.socket.close()
            raise
        self.directory_fds = []
        try:
            for path in (self.requests, self.responses):
                self.directory_fds.append(os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        except BaseException:
            for fd in self.directory_fds:
                os.close(fd)
            self.socket.close()
            raise
        self.next_sequence = 0
        self.counts = {asset: 0 for asset in self.assets}
        self.seen = set()
        self.receipts = []
        self.closed = False
        self.poisoned = False

    def _socket_identity(self):
        fd = self.socket.fileno()
        info = os.fstat(fd)
        return {
            "fd": fd,
            "device": info.st_dev,
            "inode": info.st_ino,
            "mode": info.st_mode,
            "family": int(self.socket.family),
            "type": int(self.socket.type),
            "address": list(self.socket.getsockname()),
            "reuse_address": self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR),
            "reuse_port": self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT),
        }

    def require_current(self):
        _require(not self.closed and time.monotonic() < self.deadline, "acquisition lease is closed or expired")
        _require(
            self._socket_identity() == self.identity
            and self.identity["address"][0] == "127.0.0.1"
            and not self.identity["reuse_address"]
            and not self.identity["reuse_port"],
            "listener capability changed",
        )
        for path, fd in zip((self.requests, self.responses), self.directory_fds, strict=False):
            actual, original = path.lstat(), os.fstat(fd)
            _require(
                stat.S_ISDIR(actual.st_mode) and (actual.st_dev, actual.st_ino) == (original.st_dev, original.st_ino),
                "IPC directory identity changed",
            )
        for value in self.assets.values():
            _require(
                hashlib.sha256(_read(value["path"], MAX_ASSET_BYTES)).hexdigest() == value["sha256"],
                "sealed asset changed during acquisition",
            )

    def request(self, packet):
        self.require_current()
        _require(not self.poisoned, "acquisition was poisoned by a consumed failure")
        _require(
            isinstance(packet, dict)
            and set(packet) == {"nonce", "sequence", "asset"}
            and packet["nonce"] == self.nonce
            and type(packet["sequence"]) is int
            and packet["sequence"] == self.next_sequence,
            "foreign, reordered or replayed request",
        )
        asset = packet["asset"]
        _require(isinstance(asset, str) and asset in self.assets, "unknown asset request")
        _require(
            self.next_sequence < MAX_CAPTURE_REQUESTS and self.counts[asset] < MAX_TARGET_REQUESTS,
            "capture or exact target request bound reached",
        )
        # Authority is consumed before I/O. A failed request is never retried.
        self.next_sequence += 1
        self.counts[asset] += 1
        self.poisoned = True
        expected = self.assets[asset]
        host, port = self.identity["address"]
        connection = None
        try:
            connection = http.client.HTTPConnection(
                host, port, timeout=min(1.0, max(0.001, self.deadline - time.monotonic()))
            )
            connection.request("GET", self.targets[asset], headers={"Host": f"{host}:{port}", "Connection": "close"})
            response = connection.getresponse()
            headers = response.getheaders()
            _require(
                response.status == 200
                and not any(
                    name.lower() in ("location", "set-cookie", "www-authenticate", "proxy-authenticate")
                    for name, _ in headers
                ),
                "redirect, authentication, cookie or non-success response",
            )
            raw = response.read(expected["bytes"] + 1)
            _require(
                len(raw) == expected["bytes"] and hashlib.sha256(raw).hexdigest() == expected["sha256"],
                "actual HTTP bytes differ from the sealed asset",
            )
            types = [value for name, value in headers if name.lower() == "content-type"]
            _require(
                len(types) == 1 and types[0] and "\r" not in types[0] and "\n" not in types[0],
                "missing or ambiguous content type",
            )
            self.require_current()
            result = {
                "nonce": self.nonce,
                "sequence": packet["sequence"],
                "asset": asset,
                "status": 200,
                "content_type": types[0],
                "sha256": expected["sha256"],
                "body": base64.b64encode(raw).decode("ascii"),
            }
        finally:
            if connection is not None:
                connection.close()
        self.poisoned = False
        self.seen.add(asset)
        self.receipts.append({key: value for key, value in result.items() if key != "body"})
        return result

    def service(self):
        """Run on the original waiter's activity callback, never a second owner."""
        self.require_current()
        _require(not self.poisoned, "acquisition was poisoned by a consumed failure")
        expected = self.requests / f"{self.next_sequence}.json"
        entries = os.listdir(self.directory_fds[0])
        names = {f"{number}.{suffix}" for number in range(MAX_CAPTURE_REQUESTS) for suffix in ("json", "tmp")}
        _require(all(name in names for name in entries), "unexpected IPC member")
        if expected.name not in entries:
            return False
        packet = _object(_read(expected.name, 2 * MAX_TARGET_LENGTH + 256, dir_fd=self.directory_fds[0]))
        result = self.request(packet)
        try:
            target = self.responses / expected.name
            raw = (json.dumps(result, sort_keys=True) + "\n").encode()
            temporary = target.with_suffix(".tmp")
            _require(not target.exists(), "repeated parent response")
            fd = os.open(
                temporary.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
                dir_fd=self.directory_fds[1],
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
            os.rename(temporary.name, target.name, src_dir_fd=self.directory_fds[1], dst_dir_fd=self.directory_fds[1])
        except BaseException:
            self.poisoned = True
            raise
        return True

    def complete(self):
        self.require_current()
        _require(not self.poisoned, "acquisition was poisoned by a consumed failure")
        _require(self.seen == set(self.assets), "not every sealed asset was fetched")
        return list(self.receipts)

    def close(self):
        if not self.closed:
            self.closed = True
            for fd in self.directory_fds:
                os.close(fd)
            self.socket.close()
