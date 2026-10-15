"""Exact Kilo native inputs, separate from the built-in OpenCode contract.

This module does not select roles, launch a controller, install an SDK or grant
image acceptance. An approved profile binds both clients; a Kilo native launch
still needs the original owner, source acknowledgement and actual containment.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


MODE = "isolated-mixed-kilo-claude"
RELEASE_VERSION = "7.8.3"
VERSION = "7.8.3-issue1063-source"
MODEL = "anthropic/claude-opus-5-5"
ROLES = frozenset({"sol", "completion"})
CONFIG_ENV = "KILO_CONFIG_CONTENT"


def _require(condition, reason):
    if not condition:
        raise ValueError("Kilo native: " + reason)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _owned_name(value):
    return (
        isinstance(value, str)
        and value.startswith(".autocode/")
        and not Path(value).is_absolute()
        and ".." not in Path(value).parts
        and str(Path(value)) == value
    )


def profile_value(value, *, builtin_version, max_requests, image_bounds):
    """Validate the explicit mixed profile; metadata is never native readiness."""
    keys = {
        "version", "mode", "opencode_version", "model", "reasoning_effort",
        "sdk_path", "sdk_manifest_sha256", "transport_identity_sha256",
        "executable_sha256", "max_requests", "image_limits", "kilo",
    }
    _require(isinstance(value, dict) and set(value) in (keys, keys | {"capture_runtime"}), "unsupported profile fields")
    _require(
        type(value["version"]) is int
        and value["version"] == 1
        and value["mode"] == MODE
        and value["opencode_version"] == builtin_version
        and value["model"] == MODEL
        and value["reasoning_effort"] == "high",
        "mixed mode requires original OpenCode and exact Claude High",
    )
    _require(_owned_name(value["sdk_path"]), "original OpenCode SDK must remain workspace-owned")
    _require(
        all(_sha(value[key]) for key in ("sdk_manifest_sha256", "transport_identity_sha256", "executable_sha256")),
        "missing original OpenCode client pins",
    )
    binding = value["kilo"]
    _require(
        isinstance(binding, dict)
        and set(binding) == {
            "version", "release_version", "sdk_path", "sdk_manifest_sha256", "transport_identity_sha256",
            "executable_sha256", "source_hooks", "build_acceptance",
        }
        and binding["version"] == VERSION
        and binding["release_version"] == RELEASE_VERSION
        and _owned_name(binding["sdk_path"])
        and binding["sdk_path"] != value["sdk_path"]
        and all(_sha(binding[key]) for key in ("sdk_manifest_sha256", "transport_identity_sha256", "executable_sha256")),
        "missing distinct exact Kilo client binding",
    )
    hooks = binding["source_hooks"]
    _require(
        isinstance(hooks, dict) and set(hooks) == {"path", "sha256"}
        and _owned_name(hooks["path"]) and _sha(hooks["sha256"])
        and Path(hooks["path"]).is_relative_to(binding["sdk_path"]),
        "source hooks need their exact owned SDK manifest",
    )
    build = binding["build_acceptance"]
    _require(isinstance(build, dict) and set(build) == {"result", "root", "peer"}, "closed build needs Root and peer bindings")
    for ref in build.values():
        _require(
            isinstance(ref, dict) and set(ref) == {"path", "sha256"}
            and _owned_name(ref["path"]) and _sha(ref["sha256"])
            and Path(ref["path"]).is_relative_to(binding["sdk_path"]),
            "closed build evidence must be pinned within its owned Kilo SDK",
        )
    _require(
        type(value["max_requests"]) is int and 0 < value["max_requests"] <= max_requests,
        "request cap exceeds the approved native bound",
    )
    limits = value["image_limits"]
    _require(
        isinstance(limits, dict) and set(limits) == set(image_bounds)
        and all(type(limits[key]) is int and 0 < limits[key] <= bound for key, bound in image_bounds.items()),
        "image limits exceed original byte-preserving bounds",
    )
    if "capture_runtime" in value:
        capture = value["capture_runtime"]
        _require(
            isinstance(capture, dict) and set(capture) == {"path", "sha256"}
            and _owned_name(capture["path"]) and _sha(capture["sha256"]),
            "capture runtime needs its exact owned manifest",
        )
    return deepcopy(value)


def selected(profile, settings, role):
    """Return only the exact explicitly saved Kilo reviewer binding."""
    if profile.get("mode") != MODE:
        return None
    route = settings.get("roles", {}).get(role, {})
    if route.get("provider") != "kilocode":
        _require(role not in ROLES, "mixed reviewer lacks its explicit saved Kilo route")
        _require(route.get("provider") in (None, "opencode"), "non-reviewer transport changed")
        return None
    _require(
        role in ROLES and route.get("engine", settings.get("engine")) == "opencode"
        and route.get("model") == MODEL and route.get("reasoning_effort") == "high",
        "only the former OpenAI reviewer roles may select Claude/Kilo High",
    )
    record = settings.get("transport_identities", {}).get("role:" + role)
    _require(
        isinstance(record, dict) and set(record) == {"provider", "identity"}
        and record["provider"] == "kilocode" and isinstance(record["identity"], dict)
        and set(record["identity"]) == {"engine", "identity_version", "executable", "version", "config_path", "config_sha256"}
        and record["identity"].get("engine") == "kilocode"
        and type(record["identity"].get("identity_version")) is int
        and record["identity"].get("identity_version") == 1
        and record["identity"].get("version") == VERSION
        and _sha(record["identity"].get("config_sha256"))
        and util.digest(record["identity"]) == profile["kilo"]["transport_identity_sha256"],
        "missing immutable role-specific Kilo transport identity",
    )
    return deepcopy(profile["kilo"]), deepcopy(record["identity"])
