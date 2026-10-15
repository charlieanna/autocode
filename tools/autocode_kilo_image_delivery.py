"""Verify Anthropic image wire receipts from the original Kilo parent relay.

The parent authenticates its supervised child, source-hook pins, sealed V3
record and SDK-derived whole request bytes. A supplied dictionary or a plugin
configuration grants no authority. Native/report checks retain the existing
delivery contract; parsing alone is never image-review acceptance.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

try:
    from . import autocode_image_delivery as delivery
    from . import autocode_util as util
except ImportError:
    import autocode_image_delivery as delivery
    import autocode_util as util


PROVIDER = "kilocode"
MODEL = "anthropic/claude-opus-5-5"
SESSION_MAX_REQUESTS = delivery.SESSION_MAX_REQUESTS
_HEX = re.compile(r"[a-f0-9]{64}")


def _require(condition, message):
    if not condition:
        raise ValueError("Kilo image delivery: " + message)


def _hash(value):
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def verify(record, *, events, report, images, binding, audit_path, attempt_id, plugin_sha256):
    """The concrete native owner authenticates protection and parent closure."""
    path = Path(audit_path)
    _require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), "missing or symlinked audit")
    return verify_retained(
        record,
        events=events,
        report=report,
        images=images,
        binding=binding,
        audit_path=audit_path,
        audit_bytes=path.read_bytes(),
        attempt_id=attempt_id,
        plugin_sha256=plugin_sha256,
    )


def verify_retained(record, *, events, report, images, binding, audit_path, audit_bytes, attempt_id, plugin_sha256):
    """Use genuine protected original bytes, never predicted or current aliases."""
    reviewer = binding.get("reviewer", {})
    _require(
        reviewer.get("provider") == PROVIDER and reviewer.get("model") == MODEL, "wrong independent reviewer route"
    )
    _require(
        isinstance(audit_bytes, bytes) and 0 < len(audit_bytes) <= 16 * 1024 * 1024 and audit_bytes.endswith(b"\n"),
        "missing or truncated original audit",
    )
    rows = [json.loads(line) for line in audit_bytes.splitlines()]
    _require(
        rows
        and all(isinstance(row, dict) for row in rows)
        and [row.get("sequence") for row in rows] == list(range(len(rows))),
        "incomplete audit sequence",
    )
    _require(all(type(row.get("at_ms")) is int and row["at_ms"] > 0 for row in rows), "missing transport times")
    first = rows[0]
    _require(
        first.get("type") == "audit_start"
        and first.get("version") == 3
        and first.get("attempt_id") == attempt_id
        and first.get("binding_sha256") == util.digest(binding)
        and first.get("plugin_sha256") == plugin_sha256,
        "foreign audit provenance",
    )
    _require(sum(row.get("type") == "audit_start" for row in rows) == 1, "mixed audit attempts")
    _require(
        type(first.get("max_requests")) is int and 1 <= first["max_requests"] <= SESSION_MAX_REQUESTS,
        "session request bound exceeded",
    )
    expected = first.get("reviewer", {})
    _require(
        all(expected.get(key) == reviewer[key] for key in ("provider", "model"))
        and isinstance(expected.get("agent"), str)
        and expected["agent"],
        "unbound reviewer admission",
    )

    # This owner-only API verifies its actual retained launch/child barrier and
    # whole-body SDK derivation. No caller-supplied 'armed' or 'verified' flag is
    # interpreted here, and no import of controller or private run state occurs.
    try:
        if __package__:
            from .autocode_kilo_source_relay import require_delivery_authority
        else:
            from autocode_kilo_source_relay import require_delivery_authority
    except ImportError as error:
        raise ValueError("Kilo image delivery: original parent source relay is unavailable") from error
    authority = require_delivery_authority(
        record, audit_bytes=audit_bytes, binding=binding, attempt_id=attempt_id, plugin_sha256=plugin_sha256
    )
    _require(
        isinstance(authority, dict) and authority.get("journal_sha256") == hashlib.sha256(audit_bytes).hexdigest(),
        "foreign original parent journal",
    )
    hooks = authority.get("source_hooks", {})
    _require(
        isinstance(hooks, dict)
        and isinstance(hooks.get("path"), str)
        and hooks["path"]
        and _hash(hooks.get("sha256"))
        and first.get("source_hooks") == hooks,
        "source hook pins changed",
    )
    peer = authority.get("peer_pid"), authority.get("peer_birth")
    _require(
        type(peer[0]) is int
        and peer[0] > 0
        and type(peer[1]) in (int, float)
        and math.isfinite(peer[1])
        and peer[1] > 0,
        "missing authenticated native peer",
    )
    requests = [row for row in rows if row.get("type") == "request"]
    commits = [row for row in rows if row.get("type") == "source_commit"]
    _require(
        1 <= len(requests) <= first["max_requests"] and len(requests) == len(commits), "unattributed source commit"
    )
    _require(
        [row.get("admission_index") for row in requests] == list(range(1, len(requests) + 1)),
        "request admission count mismatch",
    )
    joined = {}
    for commit in commits:
        key = commit.get("request_id"), commit.get("admission_index")
        _require(
            isinstance(key[0], str) and key[0] and type(key[1]) is int and key not in joined,
            "duplicate or missing source commit identity",
        )
        _require(
            all(_hash(commit.get(field)) for field in ("source_digest", "owner_digest", "source_record_sha256"))
            and commit["source_record_sha256"] == commit["source_digest"]
            and type(commit.get("source_index")) is int
            and commit["source_index"] >= 0
            and type(commit.get("peer_pid")) is int
            and type(commit.get("peer_birth")) in (int, float)
            and (commit.get("peer_pid"), commit.get("peer_birth")) == peer,
            "foreign source commit",
        )
        joined[key] = commit
    _require(
        isinstance(images, list)
        and images
        and all(
            isinstance(image, dict)
            and image.get("mime") == "image/png"
            and type(image.get("bytes")) is int
            and image["bytes"] > 0
            and _hash(image.get("sha256"))
            for image in images
        ),
        "unsupported bound image payloads",
    )
    expected_images = Counter((image["sha256"], image["mime"], image["bytes"]) for image in images)
    for request in requests:
        key = request.get("request_id"), request.get("admission_index")
        commit = joined.get(key, {})
        _require(
            commit
            and commit["sequence"] < request["sequence"]
            and all(request.get(field) == commit[field] for field in ("source_digest", "owner_digest", "source_index")),
            "source acknowledgement does not precede its request",
        )
        _require(
            request.get("serialized_model") == "claude-opus-5-5"
            and request.get("reasoning_effort") == "high"
            and request.get("request_wire_format") == "anthropic_messages"
            and request.get("payload_complete") is True
            and _hash(request.get("body_sha256")),
            "foreign final Anthropic wire request",
        )
        actual = request.get("images")
        _require(
            isinstance(actual, list)
            and all(
                isinstance(image, dict)
                and type(image.get("bytes")) is int
                and image["bytes"] > 0
                and image.get("mime") == "image/png"
                and _hash(image.get("sha256"))
                for image in actual
            ),
            "partial final PNG payload",
        )
        _require(
            Counter((image["sha256"], image["mime"], image["bytes"]) for image in actual) == expected_images,
            "exact PNG payloads did not reach the reviewer",
        )
    _require(
        set(joined) == {(request["request_id"], request["admission_index"]) for request in requests},
        "unused source commit",
    )
    _require(
        all(row.get("response_model") == "claude-opus-5-5" for row in rows if row.get("type") == "request_complete"),
        "foreign final response model",
    )
    native = [json.loads(line) for line in events.splitlines()]
    finishes = [row["part"] for row in native if row.get("type") == "step_finish"]
    _require(finishes and finishes[-1].get("reason") == "stop", "no successful native finish")
    finish = finishes[-1]
    phases = [row for row in rows if row.get("type") == "native_finish" and row.get("part_id") == finish["id"]]
    _require(
        len(phases) == 1
        and phases[0].get("session_id") == finish["sessionID"]
        and phases[0].get("message_id") == finish["messageID"]
        and phases[0].get("reason") == "stop",
        "finish is not correlated to native events",
    )
    # Retain the original sequence numbers and ORIGINAL evidence bytes. Only the
    # separately authenticated parent source rows are excluded from the generic
    # v3 native/tool/response/report correlation, whose rules remain unchanged.
    return delivery._verify_session(
        [row for row in rows if row.get("type") != "source_commit"],
        native,
        finish,
        phases[0],
        images,
        binding,
        report,
        events,
        Path(audit_path),
        audit_bytes,
        _require,
    )
