"""Immutable progressive evidence, not authority or a mutable state store.

The state owner calls prepare(), persist(), then records the returned identity.
verify() reads only that exact state-provided identity; it never scans for latest.
Contract, predecessor, candidate/plan and source identities are caller-supplied
bindings, not authenticated here. Activation and source freshness belong to the
caller. Initial proposals may have no predecessor; all other kinds require one.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

VERSION = 1
KINDS = frozenset({"proposal", "revision", "review", "checkpoint"})
_FIELDS = {"version", "kind", "contract_token", "predecessor_identity",
           "candidate_identity", "plan_identity", "source_snapshot_identity", "report"}
_PATH = re.compile(r"progressive/(proposal|revision|review|checkpoint)-([0-9a-f]{64})\.json\Z")


def _text(value):
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise ValueError("artifact binding must be a nonempty string without NUL")


def _json_value(value):
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            _json_value(child)
    elif type(value) is list:
        for child in value:
            _json_value(child)
    elif type(value) not in (str, int, float, bool, type(None)):
        raise ValueError("artifact must contain only JSON values")


def _bytes(envelope):
    if type(envelope) is not dict or set(envelope) != _FIELDS:
        raise ValueError("invalid progressive artifact envelope fields")
    if type(envelope["version"]) is not int or envelope["version"] != VERSION:
        raise ValueError("unsupported progressive artifact version")
    if type(envelope["kind"]) is not str or envelope["kind"] not in KINDS:
        raise ValueError("unsupported progressive artifact kind")
    for key in ("contract_token", "candidate_identity", "plan_identity", "source_snapshot_identity"):
        _text(envelope[key])
    if envelope["predecessor_identity"] is not None or envelope["kind"] != "proposal":
        _text(envelope["predecessor_identity"])
    if type(envelope["report"]) is not dict:
        raise ValueError("artifact report must be a JSON object")
    _json_value(envelope)
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _identity(envelope):
    # util.digest uses precisely the canonical serialization checked by _bytes.
    digest = util.digest(envelope)
    return {"version": VERSION, "path": f"progressive/{envelope['kind']}-{digest}.json",
            "sha256": digest}


def prepare(kind, *, contract_token, predecessor_identity, candidate_identity,
            plan_identity, source_snapshot_identity, report):
    """Return a detached JSON envelope and its versioned content identity.

    Candidate/plan identities are computed before this envelope to avoid circular
    approval hashes. Source identity may describe assignment or validated source;
    the caller must select the appropriate snapshot, never substitute old proof.
    """
    envelope = {"version": VERSION, "kind": kind, "contract_token": contract_token,
                "predecessor_identity": predecessor_identity, "candidate_identity": candidate_identity,
                "plan_identity": plan_identity, "source_snapshot_identity": source_snapshot_identity,
                "report": report}
    envelope = json.loads(_bytes(envelope))
    return envelope, _identity(envelope)


def _name(identity):
    if type(identity) is not dict or set(identity) != {"version", "path", "sha256"}:
        raise ValueError("invalid state-provided artifact identity")
    if type(identity["version"]) is not int or identity["version"] != VERSION:
        raise ValueError("unsupported artifact identity version")
    path = identity["path"]
    match = _PATH.fullmatch(path) if type(path) is str else None
    if not match or match[2] != identity["sha256"]:
        raise ValueError("artifact identity must name its hash inside progressive/")
    return path.split("/")[1]


@contextlib.contextmanager
def _directory(run_dir, *, create=False):
    if run_dir is None or not os.fspath(run_dir):
        raise ValueError("an existing run directory is required")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    # Directory descriptors keep accesses contained even if names are replaced.
    root = os.open(Path(run_dir), flags)
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("progressive", dir_fd=root)
            os.fsync(root)
        directory = os.open("progressive", flags, dir_fd=root)
        try:
            yield directory
        finally:
            os.close(directory)
    finally:
        os.close(root)


def _read(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("artifact must be a regular file")
        return stream.read()


def persist(run_dir, envelope):
    """Durably publish without replacement; return identity before pointer update.

    Identical existing bytes are recovery-safe. Any differing bytes at the same
    name (including a hash collision) fail closed. OSError propagates for missing
    directories, symlinks and filesystem failures. Requires local hard-link and
    directory-fsync support; never falls back to an overwriting rename.
    """
    data = _bytes(envelope)
    identity = _identity(json.loads(data))
    name = _name(identity)
    with _directory(run_dir, create=True) as directory:
        temporary = ".artifact-" + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                        follow_symlinks=False)
            except FileExistsError:
                if _read(directory, name) != data:
                    raise ValueError("refusing to overwrite different artifact bytes or hash collision") from None
                # Also complete durability after a crash between link and fsync.
                existing = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    os.fsync(existing)
                finally:
                    os.close(existing)
            os.fsync(directory)
        finally:
            os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
    return identity


def verify(run_dir, identity):
    """Read exact state identity, verifying file bytes, format and content address.

    This proves integrity relative to the supplied identity, not reviewer
    authenticity, authority, current source freshness or activation eligibility.
    """
    name = _name(identity)
    with _directory(run_dir) as directory:
        data = _read(directory, name)
    if hashlib.sha256(data).hexdigest() != identity["sha256"]:
        raise ValueError("progressive artifact file hash mismatch")
    envelope = json.loads(data)
    if _bytes(envelope) != data or _identity(envelope) != identity:
        raise ValueError("progressive artifact envelope identity mismatch")
    return envelope
