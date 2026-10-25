"""Bounded, portable recordings and source payloads for model-free replay.

This module reads only caller-supplied public run projections. It never reads
state.json, imports a controller, or executes a recorded provider/tool command.
Original evidence stays untouched; exported bytes have their own redacted pins.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    from . import autocode_replay_bindings as identity_bindings
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_replay_bindings as identity_bindings
    import autocode_source_snapshot as source_snapshot
    import autocode_test_cases as test_cases
    import autocode_util as util
    import autocode_workflows as workflows

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_RECORDING_BYTES = 64 * 1024 * 1024
MAX_FILES = 10000
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_INTERNAL = {".git", ".autocode", ".autocode-ui"}
_SECRET = re.compile(
    r"(?:password|passwd|secret|access[_-]?token|auth[_-]?token|api[_-]?key|authorization|credential|token)\Z", re.I
)
TOP_KEYS = frozenset(
    {
        "version",
        "kind",
        "origin",
        "launch",
        "blobs",
        "snapshots",
        "dispatches",
        "artifacts",
        "actions",
        "checks",
        "expected",
    }
)
DISPATCH_KEYS = frozenset(
    {
        "id",
        "ordinal",
        "stage",
        "iteration",
        "role",
        "route_role",
        "engine",
        "provider",
        "model",
        "reasoning_effort",
        "report_only",
        "original_stage",
        "turn",
        "task_id",
        "milestone_id",
        "contract_hash",
        "criteria_revision",
        "session",
        "output_mode",
        "supports_sessions",
        "before",
        "after",
        "events",
        "report",
        "response",
        "exit_code",
        "stopped",
        "bindings",
    }
)
SYMBOLS = frozenset(
    {
        "workspace",
        "run_dir",
        "output",
        "events",
        "session",
        "source_revision",
        "task_id",
        "contract_hash",
        "capture_nonce",
    }
)


class RecordingError(ValueError):
    """A recording is incomplete, corrupt, unsafe, or outside v1 support."""


def relative_path(name, *, source=False):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise RecordingError("Recording path is not a contained literal path")
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or any(part in (".", "..") for part in path.parts):
        raise RecordingError("Recording path is not a contained literal path")
    if source and any(part.casefold() in _INTERNAL for part in path.parts):
        raise RecordingError("Recording source crosses runner or Git storage")
    return path


def _root(path):
    path = Path(path).absolute()
    if path.resolve() != path or any(parent.is_symlink() for parent in (path, *path.parents)):
        raise RecordingError("Recording root is aliased")
    return path


def _read(root, name, *, limit=MAX_ARTIFACT_BYTES):
    """Read through held no-follow directories; special/swapped files refuse."""
    root = _root(root)
    relative = relative_path(name)
    flags = os.O_RDONLY | os.O_NOFOLLOW
    directory = os.open(root, flags | os.O_DIRECTORY)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(relative.name, flags | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise RecordingError("Recording leaf is not a bounded regular file")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                data = stream.read(limit + 1)
            after = os.fstat(descriptor)
            identity = lambda x: (x.st_dev, x.st_ino, x.st_mode, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
            named = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
            if len(data) > limit or identity(before) != identity(after) or identity(after) != identity(named):
                raise RecordingError("Recording leaf changed during inspection")
            return data, stat.S_IMODE(after.st_mode)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise RecordingError("Recording object has missing or unknown fields")


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise RecordingError("Recording digest is invalid")


def file_identity(data, mode=0o644):
    return ("executable:" if mode & 0o111 else "") + hashlib.sha256(data).hexdigest()


def _identity(value):
    if value == "deleted":
        return
    if not isinstance(value, str):
        raise RecordingError("Recording source identity is invalid")
    if value.startswith("symlink:"):
        target = value.removeprefix("symlink:")
        # v1 carries links without following them; external/parent links refuse.
        relative_path(target, source=True)
    else:
        _hash(value.removeprefix("executable:"))


def _workflow_pin(value):
    if not isinstance(value, str) or value not in workflows.WORKFLOWS:
        raise RecordingError("Recording initial workflow pin is invalid")
    return value


def _validate(document):
    _object(document, TOP_KEYS)
    if type(document["version"]) is not int or document["version"] != 1 or document["kind"] != "autocode-replay":
        raise RecordingError("Unsupported recording version")
    _object(document["origin"], {"run_name", "runner_revision", "provenance", "verdict"})
    _object(document["launch"], {"task", "settings", "base"}, {"git_pack", "workflow_pin"})
    if "workflow_pin" in document["launch"]:
        _workflow_pin(document["launch"]["workflow_pin"])
    if not isinstance(document["launch"]["task"], str) or not document["launch"]["task"].strip():
        raise RecordingError("Recording brief is missing")
    settings = document["launch"]["settings"]
    if not isinstance(settings, dict) or settings.get("engine") not in ("codex", "opencode"):
        raise RecordingError("Recording engine is unsupported")
    if any(
        key in settings
        for key in (
            "replay_recording",
            "transport_identity",
            "transport_identities",
            "provider_environment",
            "provider_config",
        )
    ):
        raise RecordingError("Recording contains external transport authority")
    if set(settings) - SETTINGS_KEYS:
        raise RecordingError("Recording contains unsupported launch settings")
    roles = settings.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise RecordingError("Recording model routes are missing")
    for value in roles.values():
        _object(value, {"model"}, {"engine", "provider", "reasoning_effort", "model_pinned", "light_effort"})
        if not isinstance(value["model"], str) or not value["model"].strip():
            raise RecordingError("Recording model route is invalid")
        if "light_effort" in value and not isinstance(value["light_effort"], str):
            raise RecordingError("Recording model light effort is invalid")
    if "effort_origins" in settings:
        origins = settings["effort_origins"]
        if not isinstance(origins, dict) or any(
            role not in roles
            or origin
            not in ("user_explicit", "single_model", "runner_default", "catalogue_replacement", "auto_selected")
            for role, origin in origins.items()
        ):
            raise RecordingError("Recording effort origin is invalid")
    for name in ("blobs", "snapshots", "artifacts"):
        if not isinstance(document[name], dict) or len(document[name]) > MAX_FILES:
            raise RecordingError("Recording inventory is invalid or too large")
    for digest, blob in document["blobs"].items():
        _hash(digest)
        _object(blob, {"path", "bytes", "mode"})
        if (
            blob["path"] != "blobs/" + digest
            or type(blob["bytes"]) is not int
            or not 0 <= blob["bytes"] <= MAX_ARTIFACT_BYTES
        ):
            raise RecordingError("Recording blob declaration is invalid")
        if blob["mode"] not in (0o644, 0o755):
            raise RecordingError("Recording blob mode is unsupported")
    for snapshot in document["snapshots"].values():
        _object(snapshot, {"head", "files", "source_paths"})
        if not isinstance(snapshot["head"], str) or not _COMMIT.fullmatch(snapshot["head"]):
            raise RecordingError("Recording source HEAD is invalid")
        if not isinstance(snapshot["files"], dict) or len(snapshot["files"]) > MAX_FILES:
            raise RecordingError("Recording source inventory is invalid")
        for name, identity in snapshot["files"].items():
            relative_path(name, source=True)
            _identity(identity)
            if (
                identity != "deleted"
                and not identity.startswith("symlink:")
                and identity.removeprefix("executable:") not in document["blobs"]
            ):
                raise RecordingError("Recording source payload is missing")
        if not isinstance(snapshot["source_paths"], list):
            raise RecordingError("Recording source selection is invalid")
        for name in snapshot["source_paths"]:
            relative_path(name, source=True)
    if document["launch"]["base"] not in document["snapshots"]:
        raise RecordingError("Recording initial source is missing")
    for artifact in document["artifacts"].values():
        _object(artifact, {"path", "sha256", "bytes", "format", "redacted"}, {"original_sha256"})
        relative_path(artifact["path"])
        if not artifact["path"].startswith("artifacts/") or artifact["format"] not in (
            "jsonl",
            "json",
            "text",
            "receipt",
            "gitpack",
        ):
            raise RecordingError("Recording artifact declaration is invalid")
        _hash(artifact["sha256"])
        if (
            type(artifact["bytes"]) is not int
            or not 0 <= artifact["bytes"] <= MAX_ARTIFACT_BYTES
            or type(artifact["redacted"]) is not bool
        ):
            raise RecordingError("Recording artifact declaration is invalid")
    if not isinstance(document["dispatches"], list) or len(document["dispatches"]) > MAX_FILES:
        raise RecordingError("Recording dispatch inventory is invalid")
    if len({row["path"] for row in document["artifacts"].values()}) != len(document["artifacts"]):
        raise RecordingError("Recording artifact paths are duplicated")
    if document["launch"].get("git_pack") is not None and document["launch"]["git_pack"] not in document["artifacts"]:
        raise RecordingError("Recording Git source is missing")
    if "workflow_pin" in document["launch"] and any(
        row.get("stage") == workflows.STAGE for row in document["dispatches"] if isinstance(row, dict)
    ):
        raise RecordingError("Recording initial workflow pin conflicts with a recognizer dispatch")
    ids = set()
    for ordinal, row in enumerate(document["dispatches"]):
        _object(row, DISPATCH_KEYS, {"prompts_hash"})
        if row.get("prompts_hash") is not None:
            _hash(row["prompts_hash"])
        if (
            not isinstance(row["id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", row["id"])
            or row["id"] == "end"
            or row["id"] in ids
            or type(row["ordinal"]) is not int
            or row["ordinal"] != ordinal
        ):
            raise RecordingError("Recording dispatch order is invalid")
        ids.add(row["id"])
        if (
            type(row["iteration"]) is not int
            or row["iteration"] < 1
            or type(row["report_only"]) is not bool
            or type(row["supports_sessions"]) is not bool
        ):
            raise RecordingError("Recording dispatch metadata is invalid")
        if row["engine"] not in ("codex", "opencode") or row["output_mode"] not in (
            "codex_events",
            "opencode_events",
            "report_file",
        ):
            raise RecordingError("Recording event transport is unsupported")
        if row["exit_code"] is not None and type(row["exit_code"]) is not int:
            raise RecordingError("Recording exit is not a collected integer")
        if row["stopped"] not in ("normal", "timed_out", "interrupted", "uncertain"):
            raise RecordingError("Recording stop is invalid")
        if (
            any(row[key] not in document["snapshots"] for key in ("before", "after"))
            or row["events"] not in document["artifacts"]
        ):
            raise RecordingError("Recording dispatch evidence is missing")
        if any(row[key] is not None and row[key] not in document["artifacts"] for key in ("report", "response")):
            raise RecordingError("Recording dispatch report is missing")
        validate_bindings(row["bindings"])
    if not isinstance(document["actions"], list) or not isinstance(document["checks"], list):
        raise RecordingError("Recording action/check inventory is invalid")
    for action in document["actions"]:
        _object(action, {"before_dispatch", "kind", "match", "payload"})
        if action["before_dispatch"] not in ids | {"end"} or action["kind"] not in (
            "approve_plan",
            "answer",
            "approve_review",
            "feedback",
            "follow_up",
            "resume",
        ):
            raise RecordingError("Recording operator action is unsupported")
        if not isinstance(action["match"], dict) or not isinstance(action["payload"], dict):
            raise RecordingError("Recording operator action is invalid")
    check_ids = set()
    for check in document["checks"]:
        if not isinstance(check.get("id"), str) or not check["id"] or check["id"] in check_ids:
            raise RecordingError("Recording check identities are duplicated")
        check_ids.add(check["id"])
        _object(
            check,
            {
                "id",
                "dispatch",
                "purpose",
                "command",
                "repetition",
                "snapshot",
                "receipt",
                "output",
                "authentication",
                "bindings",
            },
            {"context", "supervision", "outputs"},
        )
        if (
            check["dispatch"] not in ids
            or check["snapshot"] not in document["snapshots"]
            or check["receipt"] not in document["artifacts"]
            or check["output"] not in document["artifacts"]
        ):
            raise RecordingError("Recording check evidence is missing")
        if (
            type(check["repetition"]) is not int
            or check["repetition"] < 1
            or not isinstance(check["command"], str)
            or not check["command"]
        ):
            raise RecordingError("Recording check obligation is invalid")
        if check["authentication"] not in ("intact", "invalid", "missing"):
            raise RecordingError("Recording check authentication is invalid")
        for mapping in ("supervision", "outputs"):
            if mapping in check and (
                not isinstance(check[mapping], dict)
                or any(
                    not isinstance(path, str) or not Path(path).is_absolute() or identity not in document["artifacts"]
                    for path, identity in check[mapping].items()
                )
            ):
                raise RecordingError("Recording archival command payload inventory is invalid")
        validate_bindings(check["bindings"])
    _object(document["expected"], {"verdict", "stage_sequence", "checks_mode"})
    if document["expected"]["stage_sequence"] != [row["stage"] for row in document["dispatches"]] or document[
        "expected"
    ]["checks_mode"] not in ("fresh", "recorded"):
        raise RecordingError("Recording expected sequence is invalid")


def validate_bindings(bindings):
    if not isinstance(bindings, list):
        raise RecordingError("Recording bindings must be explicit")
    seen = set()
    for binding in bindings:
        _object(binding, {"pointer", "symbol", "original_valid"}, {"suffix"})
        pointer = binding["pointer"]
        if (
            not isinstance(pointer, str)
            or not pointer.startswith("/")
            or pointer in seen
            or binding["symbol"] not in SYMBOLS
            or type(binding["original_valid"]) is not bool
        ):
            raise RecordingError("Recording binding is invalid")
        seen.add(pointer)
        if binding.get("suffix"):
            relative_path(binding["suffix"])


def rebind(value, bindings, symbols):
    """Change declared valid identity fields; invalid/foreign evidence stays invalid."""
    validate_bindings(bindings)
    result = copy.deepcopy(value)
    for binding in bindings:
        if not binding["original_valid"]:
            continue
        if binding["symbol"] not in symbols:
            raise RecordingError("Recording symbol is unavailable")
        raw_parts = binding["pointer"][1:].split("/")
        if any(re.search(r"~(?![01])", part) for part in raw_parts):
            raise RecordingError("Recording JSON pointer escape is invalid")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in raw_parts]

        def index(part):
            if not re.fullmatch(r"0|[1-9][0-9]*", part):
                raise RecordingError("Recording array pointer is not canonical")
            return int(part)

        owner = result
        try:
            for part in parts[:-1]:
                owner = owner[index(part)] if isinstance(owner, list) else owner[part]
            key = index(parts[-1]) if isinstance(owner, list) else parts[-1]
            _ = owner[key]
            replacement = symbols[binding["symbol"]]
            if binding.get("suffix"):
                replacement = str(Path(replacement) / binding["suffix"])
            owner[key] = replacement
        except (KeyError, IndexError, TypeError, ValueError):
            raise RecordingError("Recording binding does not name an existing field") from None
    return result


@dataclass(frozen=True)
class Recording:
    root: Path
    document: dict
    sha256: str
    _manifest_name: str = "recording.json"

    @property
    def manifest_path(self) -> Path:
        return self.root / self._manifest_name

    def verified(self) -> Recording:
        """Re-admit the pinned bundle; external mutable projections are not authority."""
        original = load(self.manifest_path, self.sha256)
        if original.document != self.document:
            raise RecordingError("Recording document changed after admission")
        return original  # Private decoded copy, with every bounded payload pin checked.

    def artifact(self, identity):
        entry = self.document["artifacts"][identity]
        data, _ = _read(self.root, entry["path"])
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise RecordingError("Recording artifact changed")
        return data

    def blob(self, identity):
        digest = identity.removeprefix("executable:")
        entry = self.document["blobs"][digest]
        data, _ = _read(self.root, entry["path"])
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != digest:
            raise RecordingError("Recording blob changed")
        return data


def load(path, expected_hash=None):
    path = Path(path).absolute()
    data, _ = _read(path.parent, path.name, limit=MAX_RECORDING_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    if expected_hash is not None and expected_hash != digest:
        raise RecordingError("Recording manifest changed")
    try:
        document = _json(data)
    except (ValueError, RecursionError):
        raise RecordingError("Recording manifest is not bounded JSON") from None
    _validate(document)
    if (
        len(data)
        + sum(row["bytes"] for row in document["artifacts"].values())
        + sum(row["bytes"] for row in document["blobs"].values())
        > MAX_RECORDING_BYTES
    ):
        raise RecordingError("Recording payload exceeds the total bound")
    recording = Recording(path.parent, document, digest, path.name)
    total = len(data)
    for artifact in document["artifacts"]:
        total += len(recording.artifact(artifact))
    for blob in document["blobs"]:
        total += len(recording.blob(blob))
    if total > MAX_RECORDING_BYTES:
        raise RecordingError("Recording payload exceeds the total bound")
    return recording


def recover_leaf(workspace, relative, identity, *, revisions=(), captures=()):
    """Recover exact old bytes; never substitute a later differing leaf."""
    root = _root(workspace)
    name = str(relative_path(relative, source=True))
    _identity(identity)
    if identity == "deleted":
        raise RecordingError("Deleted source has no payload")
    if identity.startswith("symlink:"):
        return identity.removeprefix("symlink:").encode()
    for capture in captures:
        if capture.get("sha256") != identity.removeprefix("executable:"):
            continue
        path = Path(capture["path"]).absolute()
        try:
            data, _ = _read(path.parent, path.name)
        except (OSError, RecordingError):
            continue
        if file_identity(data, capture.get("mode", 0o644)) == identity:
            return data
    try:
        data, mode = _read(root, name)
        if file_identity(data, mode) == identity:
            return data
    except (OSError, RecordingError):
        pass
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    for revision in revisions:
        if not isinstance(revision, str) or not _COMMIT.fullmatch(revision):
            raise RecordingError("Recording recovery revision is invalid")
        command = ["/usr/bin/git", "--literal-pathspecs", "-C", str(root)]
        result = subprocess.run(
            [*command, "ls-tree", "-z", revision, "--", name], capture_output=True, env=environment, timeout=10
        )
        entries = [row for row in result.stdout.split(b"\x00") if row]
        if result.returncode or len(entries) != 1:
            continue
        meta, returned = entries[0].split(b"\t", 1)
        mode, kind, oid = meta.split()
        if returned.decode() != name or kind != b"blob" or mode not in (b"100644", b"100755"):
            continue
        size = subprocess.run(
            [*command, "cat-file", "-s", oid.decode()], capture_output=True, env=environment, timeout=10
        )
        if size.returncode or not size.stdout.strip().isdigit() or int(size.stdout) > MAX_ARTIFACT_BYTES:
            continue
        blob = subprocess.run(
            [*command, "cat-file", "blob", oid.decode()], capture_output=True, env=environment, timeout=10
        )
        if not blob.returncode and file_identity(blob.stdout, 0o755 if mode == b"100755" else 0o644) == identity:
            return blob.stdout
    raise RecordingError("Recording cannot recover an exact intermediate source payload")


def sanitize(value, secret_values=()):
    """Structured credential masking plus the shared redactor; no opaque config export."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            safe_key = util.redact(str(key), secret_values)
            if safe_key in result:
                raise RecordingError("Redaction collides with a recording field")
            result[safe_key] = "[REDACTED]" if _SECRET.search(str(key)) else sanitize(child, secret_values)
        return result
    if isinstance(value, list):
        return [sanitize(child, secret_values) for child in value]
    return util.redact(value, secret_values) if isinstance(value, str) else value


# Policy fields only. Provider configuration, environment, tool/plugin startup and
# authenticated transport identities are never portable launch authority.
SETTINGS_KEYS = frozenset(
    {
        "engine",
        "provider",
        "roles",
        "single_model_mode",
        "budget_origins",
        "effort_origins",
        "builder_retry",
        "route_ladders",
        "orchestration",
        "report_repair",
        "evidence_provenance",
        "milestone_checkpoints",
        "headroom",
        "regression",
        "context_soft_tokens",
        "rotation_after_input_tokens",
        "limits",
        "workflow",
        "joint_planning",
        "planning_flow",
        "adaptive_planning",
        "spec_first",
        "reviewer_routing",
        "conversation_handoff",
        "output_transport",
        "stuck_investigation",
        "protected_tests",
    }
)


def output_pins(value):
    """Keep each archival output path paired with its exact original raw pin."""
    pairs = []
    for field in ("output", "full_output"):
        pin_field = field + "_sha256"
        if field not in value and pin_field not in value:
            continue  # Explicit legacy command without raw-output metadata.
        path, pin = value.get(field), value.get(pin_field)
        if not isinstance(path, str) or not path or not isinstance(pin, str) or not _HASH.fullmatch(pin):
            raise RecordingError("Recording archived command output metadata is partial or invalid")
        pairs.append((path, pin))
    return pairs


def launch_settings(settings):
    """Whitelist policy, never transport configuration or ambient startup authority."""
    selected = {key: copy.deepcopy(value) for key, value in settings.items() if key in SETTINGS_KEYS}
    if settings.get("figma_file") or settings.get("design_manifest") or settings.get("visual_profile"):
        raise RecordingError("Recording visual/external-tool startup is unsupported")
    protected = selected.pop("protected_tests", None)
    if protected and (protected.get("files") or protected.get("command")):
        raise RecordingError("Recording retained protected-test payloads are unsupported in v1")
    orchestration = selected.get("orchestration") or {}
    if orchestration.get("enabled") and orchestration.get("max_parallel", 1) > 1:
        raise RecordingError("Recording concurrent Builder dispatch is unsupported in v1")
    selected["evidence_provenance"] = {
        "kind": "fake",
        "basis": "recorded_observations",
        "declaration": "Saved-run replay; no live inference",
    }
    return selected


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RecordingError("Recording JSON contains duplicate fields")
            result[key] = value
        return result

    try:

        def finite_float(value):
            number = float(value)
            if not math.isfinite(number):
                raise RecordingError("Recording JSON has a nonfinite number")
            return number

        return json.loads(
            data,
            object_pairs_hook=pairs,
            parse_float=finite_float,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                RecordingError("Recording JSON has a nonfinite number")
            ),
        )
    except (ValueError, RecursionError, UnicodeError):
        raise RecordingError("Recording payload is not bounded JSON") from None


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _git(workspace, arguments, *, data=None):
    # No inherited Git hooks, environment redirects, pager, global/system config.
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": os.devnull,
    }
    result = subprocess.run(
        ["/usr/bin/git", "--literal-pathspecs", "-C", str(workspace), *arguments],
        input=data,
        capture_output=True,
        timeout=10,
        env=environment,
    )
    if result.returncode:
        raise RecordingError("Recording Git source is unavailable")
    return result.stdout


def _pack(workspace, heads, secret_values):
    """Export needed Git history, refusing secret-bearing object bytes rather than repinning."""
    heads = list(dict.fromkeys(heads))
    for head in heads:
        if not _COMMIT.fullmatch(head):
            raise RecordingError("Recording Git source is invalid")
    names = _git(workspace, ["rev-list", "--objects", *heads]).splitlines()
    if len(names) > MAX_FILES:
        raise RecordingError("Recording Git history exceeds the bound")
    size = 0
    for row in names:
        oid = row.split(b" ", 1)[0].decode("ascii")
        kind = _git(workspace, ["cat-file", "-t", oid]).strip().decode("ascii")
        if kind not in ("blob", "tree", "commit"):
            raise RecordingError("Recording Git object type is unsupported")
        count = _git(workspace, ["cat-file", "-s", oid]).strip()
        if not count.isdigit() or int(count) > MAX_ARTIFACT_BYTES:
            raise RecordingError("Recording Git object exceeds the bound")
        size += int(count)
        if size > MAX_RECORDING_BYTES:
            raise RecordingError("Recording Git history exceeds the bound")
        payload = _git(workspace, ["cat-file", kind, oid])
        text = payload.decode("utf-8", "surrogateescape")
        if util.redact(text, secret_values) != text:
            raise RecordingError("Recording essential Git source needs redaction")
    payload = _git(workspace, ["pack-objects", "--stdout", "--revs"], data=("\n".join(heads) + "\n").encode())
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise RecordingError("Recording Git pack exceeds the bound")
    return payload


def _source_snapshot(root, reference):
    path = Path(reference)
    if not path.is_absolute():
        path = root / path
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise RecordingError("Recording source evidence is outside its owned run") from None
    value = _json(_read(root, relative)[0])
    if not isinstance(value, dict) or not isinstance(value.get("files"), dict):
        raise RecordingError("Recording source snapshot is missing")
    actual = util.digest({"head": value.get("head"), "files": value["files"]})
    if value.get("revision") != actual:
        raise RecordingError("Recording source snapshot identity is damaged")
    return {key: copy.deepcopy(value.get(key, [])) for key in ("head", "files", "source_paths")}


def _legacy_summary(run_root, stem, pin):
    """Resolve old UUID summaries only by their exact owned stem and public pin.

    This compatibility inspection belongs to export, never the pure status view.
    No scratch-output parent, old absolute path or arbitrary ancestor is searched.
    """
    if not isinstance(stem, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", stem):
        raise RecordingError("Recording archived summary selector is invalid")
    _hash(pin)
    directory = _root(run_root) / "check-replay"
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    pattern = re.compile(re.escape(stem) + r"-[0-9a-f]{32}")
    matches, candidates, total = [], 0, 0
    try:
        anchored = os.fstat(descriptor)
        with os.scandir(descriptor) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_FILES:
                    raise RecordingError("Recording archived summary inventory exceeds its bound")
                if not pattern.fullmatch(entry.name):
                    continue
                candidates += 1
                if candidates > 128:
                    raise RecordingError("Recording archived summary candidates exceed their bound")
                named = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISDIR(named.st_mode):
                    raise RecordingError("Recording archived summary directory is not plain")
                relative = "check-replay/" + entry.name + "/replay.json"
                data, _mode = _read(run_root, relative, limit=min(4 * 1024 * 1024, 16 * 1024 * 1024 - total))
                total += len(data)
                if total > 16 * 1024 * 1024:
                    raise RecordingError("Recording archived summaries exceed their byte bound")
                value = _json(data)
                if not isinstance(value, dict):
                    raise RecordingError("Recording archived summary is malformed")
                if util.digest(value) == pin:
                    matches.append(str(Path(run_root) / relative))
        named = directory.lstat()
        identity = lambda info: (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_gid,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        if identity(anchored) != identity(named):
            raise RecordingError("Recording archived summary directory changed during inspection")
    finally:
        os.close(descriptor)
    if len(matches) != 1:
        raise RecordingError("Recording has no unique retained original check summary")
    return matches[0]


def export(projection, destination, secret_values=()):
    """Export a public replay_source projection, recovering every intermediate leaf.

    Input fields: version, workspace, run_dir, origin, task, settings, records,
    revisions, captures, actions and checks, plus optional workflow_pin.
    ``records`` are whitelisted attempt
    projections; no state or ledger file is discovered by this module.
    """
    _object(
        projection,
        {
            "version",
            "workspace",
            "run_dir",
            "origin",
            "task",
            "settings",
            "records",
            "revisions",
            "captures",
            "actions",
            "checks",
        },
        {"workflow_pin"},
    )
    if projection["version"] != 1 or not projection["records"]:
        raise RecordingError("Recording has no replayable attempts")
    workspace, run_root = _root(projection["workspace"]), _root(projection["run_dir"])
    destination = _root(destination)
    if destination.exists():
        raise RecordingError("Recording destination must be new")
    document = {
        "version": 1,
        "kind": "autocode-replay",
        "origin": sanitize(projection["origin"], secret_values),
        "launch": {
            "task": sanitize(projection["task"], secret_values),
            "settings": sanitize(launch_settings(projection["settings"]), secret_values),
            "base": "source-0",
            **({"workflow_pin": _workflow_pin(projection["workflow_pin"])} if "workflow_pin" in projection else {}),
        },
        "blobs": {},
        "snapshots": {},
        "dispatches": [],
        "artifacts": {},
        "actions": sanitize(projection["actions"], secret_values),
        "checks": [],
        "expected": {"verdict": projection["origin"]["verdict"], "stage_sequence": [], "checks_mode": "fresh"},
    }
    payloads = {}

    def artifact(identity, path, format):
        path = Path(path)
        try:
            name = path.relative_to(run_root).as_posix()
        except ValueError:
            raise RecordingError("Recording artifact is outside its owned run") from None
        original = _read(run_root, name)[0]
        if format in ("json", "receipt"):
            parsed = _json(original)
            masked = sanitize(parsed, secret_values)
            redacted = masked != parsed
            safe = original if format == "receipt" and not redacted else _bytes(masked)
        elif format == "jsonl":
            redacted = any(
                sanitize(_json(line), secret_values) != _json(line) for line in original.splitlines() if line.strip()
            )
            safe = b"".join(
                _bytes(sanitize(_json(line), secret_values)).replace(b"\n", b"") + b"\n"
                for line in original.splitlines()
                if line.strip()
            )
        else:
            safe = util.redact(original.decode("utf-8"), secret_values).encode()
            redacted = safe != original
        target = "artifacts/" + identity
        document["artifacts"][identity] = {
            "path": target,
            "sha256": hashlib.sha256(safe).hexdigest(),
            "original_sha256": hashlib.sha256(original).hexdigest(),
            "bytes": len(safe),
            "format": format,
            "redacted": redacted,
        }
        payloads[target] = safe
        return identity

    snapshots, contexts = [], {}
    for ordinal, record in enumerate(projection["records"]):
        if not record.get("before_ref") or not record.get("after_ref"):
            raise RecordingError("Recording attempt lacks immutable source snapshots")
        before, after = (_source_snapshot(run_root, record[key]) for key in ("before_ref", "after_ref"))
        before_id, after_id = "source-" + str(ordinal * 2), "source-" + str(ordinal * 2 + 1)
        document["snapshots"].update({before_id: before, after_id: after})
        snapshots.extend((before, after))
        row = {key: copy.deepcopy(record.get(key)) for key in DISPATCH_KEYS}
        if "prompts_hash" in record:
            identity = record["prompts_hash"]
            if identity is not None:
                _hash(identity)
                if util.redact(identity, secret_values) != identity:
                    raise RecordingError("Recording cannot redact an essential archived prompt identity")
            row["prompts_hash"] = copy.deepcopy(identity)
        row.update(
            id="dispatch-" + str(ordinal),
            ordinal=ordinal,
            before=before_id,
            after=after_id,
            events=artifact("events-" + str(ordinal), record["events"], "jsonl"),
            report=None,
            response=None,
            bindings=record.get("bindings", []),
        )
        row["report_only"] = bool(record.get("report_only"))
        row["original_stage"] = record.get("original_stage") if row["report_only"] else None
        packet = {}
        if record.get("prompt"):
            prompt_name = Path(record["prompt"]).relative_to(run_root).as_posix()
            prompt = _read(run_root, prompt_name)[0].decode("utf-8")
            marker = "\nCURRENT HANDOFF DATA\n"
            if marker in prompt:
                packet = _json(prompt.rpartition(marker)[2])
                task = packet.get("current_task") or {}
                row["milestone_id"] = task.get("milestone_id")
                row["task_id"] = task.get("id") or row.get("task_id")
                contract = packet.get("goal_contract") or packet.get("contract") or {}
                row["contract_hash"] = record.get("contract_hash") or contract.get("hash")
        task = identity_bindings.semantic_task(packet.get("current_task"))
        contract = packet.get("goal_contract") or packet.get("contract") or {}
        names = (packet.get("regression_proof") or {}).get("case_scope")
        contexts[row["id"]] = {
            "contract_body": copy.deepcopy(contract.get("body")),
            "task": task,
            "case_ids": sorted(
                names
                if isinstance(names, list)
                else [case["id"] for case in test_cases.plan_cases(contract.get("body"))]
            ),
        }
        row["supports_sessions"] = bool(record.get("supports_sessions", True))
        row["stopped"] = (
            "timed_out"
            if record.get("timed_out")
            else "interrupted"
            if record.get("interrupted")
            else "normal"
            if type(record.get("exit_code")) is int
            else "uncertain"
        )
        row["session"] = copy.deepcopy(
            record.get("session")
            or {
                "kind": "resume" if record.get("expected_session") is not None else "fresh",
                "id": record.get("thread_id") or record.get("expected_session"),
            }
        )
        row["route_role"] = record.get("route_role") or record["role"]
        row["model"] = record.get("model") or (record.get("launch_route") or {}).get("model")
        row["turn"] = record.get("turn", 1)
        row["provider"] = (record.get("launch_route") or {}).get("provider", record.get("provider"))
        row["output_mode"] = record.get("output_mode") or (
            "codex_events" if row["engine"] == "codex" else "opencode_events"
        )
        if record.get("output") and Path(record["output"]).is_file():
            row["report"] = artifact("report-" + str(ordinal), record["output"], "json")
        if record.get("response"):
            row["response"] = artifact("response-" + str(ordinal), record["response"], "json")
        if not row["bindings"]:
            payload = {
                "events": [
                    _json(line)
                    for line in payloads[document["artifacts"][row["events"]]["path"]].splitlines()
                    if line.strip()
                ],
                "report": _json(payloads[document["artifacts"][row["report"]]["path"]]) if row["report"] else None,
                "response": _json(payloads[document["artifacts"][row["response"]]["path"]])
                if row["response"]
                else None,
            }
            source_contexts = [
                {"revision": util.digest({"head": snap["head"], "files": snap["files"]})} for snap in (before, after)
            ]
            row["bindings"] = identity_bindings.bindings(payload, record, row, workspace, run_root, source_contexts)
        document["dispatches"].append(row)
    revisions = list(dict.fromkeys([*projection["revisions"], *[row["head"] for row in snapshots]]))
    for snapshot in snapshots:
        for name, identity in snapshot["files"].items():
            relative_path(name, source=True)
            if util.redact(name, secret_values) != name:
                raise RecordingError("Recording essential source filename needs redaction")
            _identity(identity)
            if identity == "deleted" or identity.startswith("symlink:"):
                continue
            digest = identity.removeprefix("executable:")
            if digest in document["blobs"]:
                continue
            data = recover_leaf(workspace, name, identity, revisions=revisions, captures=projection["captures"])
            text = data.decode("utf-8", "surrogateescape")
            if util.redact(text, secret_values) != text:
                raise RecordingError("Recording essential source needs redaction")
            document["blobs"][digest] = {
                "path": "blobs/" + digest,
                "bytes": len(data),
                "mode": 0o755 if identity.startswith("executable:") else 0o644,
            }
            payloads["blobs/" + digest] = data
    gitpack = _pack(workspace, revisions, secret_values)
    payloads["artifacts/git-source"] = gitpack
    document["artifacts"]["git-source"] = {
        "path": "artifacts/git-source",
        "sha256": hashlib.sha256(gitpack).hexdigest(),
        "original_sha256": hashlib.sha256(gitpack).hexdigest(),
        "bytes": len(gitpack),
        "format": "gitpack",
        "redacted": False,
    }
    document["launch"]["git_pack"] = "git-source"
    for ordinal, check in enumerate(projection["checks"]):
        row = copy.deepcopy(check)
        row["id"] = row.get("id") or "check-" + str(ordinal)
        stem = row.pop("summary_stem", None)
        receipt_path = check.get("receipt") or _legacy_summary(run_root, stem, row.get("summary_sha256"))
        receipt_name = Path(receipt_path).relative_to(run_root).as_posix()
        raw = _read(run_root, receipt_name)[0]
        value = _json(raw)
        if not isinstance(value, dict):
            raise RecordingError("Recording archived check is malformed")
        summary_pin, receipt_pin = row.pop("summary_sha256", None), row.pop("receipt_sha256", None)
        if (summary_pin and util.digest(value) != summary_pin) or (
            receipt_pin and hashlib.sha256(raw).hexdigest() != receipt_pin
        ):
            raise RecordingError("Recording archived check no longer matches its public pin")
        nested = value.get("checks") or []
        nested = list(nested.values()) if isinstance(nested, dict) else nested if isinstance(nested, list) else []
        commands = [*nested, *([value["receipt"]] if isinstance(value.get("receipt"), dict) else []), value]
        outputs = {}
        ownership = {}
        for command in commands:
            if not isinstance(command, dict):
                raise RecordingError("Recording archived command is malformed")
            metadata = command.get("supervision") or {}
            if metadata.get("receipt"):
                ownership[metadata["receipt"]] = command.get("supervision_sha256")
            for output, pin in output_pins(command):
                if output in outputs and outputs[output] != pin:
                    raise RecordingError("Recording archived command output pins conflict")
                outputs[output] = pin
        output = check.get("output") or next(iter(outputs), None)
        if not output:
            raise RecordingError("Recording archived check has no retained output")
        row["receipt"] = artifact("check-" + str(ordinal), receipt_path, "receipt")
        row["output"] = artifact("check-log-" + str(ordinal), output, "text")
        row["supervision"] = {}
        for index, (path, pin) in enumerate({**check.get("supervision", {}), **ownership}.items()):
            identity = artifact("ownership-" + str(ordinal) + "-" + str(index), path, "receipt")
            if pin and document["artifacts"][identity]["original_sha256"] != pin:
                raise RecordingError("Recording archived ownership pin is damaged")
            row["supervision"][str(path)] = identity
        row["outputs"] = {}
        for index, (path, pin) in enumerate(outputs.items()):
            identity = artifact("output-" + str(ordinal) + "-" + str(index), path, "text")
            if document["artifacts"][identity]["original_sha256"] != pin:
                raise RecordingError("Recording archived command output pin is damaged")
            row["outputs"][str(path)] = identity
        row.setdefault("context", contexts.get(row["dispatch"]))
        document["checks"].append(row)
    document["expected"]["stage_sequence"] = [row["stage"] for row in document["dispatches"]]
    _validate(document)  # Complete recoverability/refusal before writing the first leaf.
    if len(_bytes(document)) + sum(map(len, payloads.values())) > MAX_RECORDING_BYTES:
        raise RecordingError("Recording payload exceeds the total bound")
    destination.mkdir(mode=0o700, parents=True)
    for name, data in payloads.items():
        path = destination / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(0o644)
    manifest = destination / "recording.json"
    manifest.write_bytes(_bytes(document))
    load(manifest)
    return manifest


def _destination(workspace, name):
    root = _root(workspace)
    relative = relative_path(name, source=True)
    path = root / relative
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise RecordingError("Recording destination has an aliased parent")
    return path


def verify_source(recording, snapshot_id, workspace):
    recording = recording.verified()
    snapshot = recording.document["snapshots"][snapshot_id]
    actual = source_snapshot.snapshot(_root(workspace), paths=snapshot["source_paths"])
    if actual["head"] != snapshot["head"] or actual["files"] != snapshot["files"]:
        raise RecordingError("Replay diverged: source bytes, modes or Git HEAD differ")
    return util.digest({"head": actual["head"], "files": actual["files"]})


def materialize(recording, snapshot_id, workspace):
    """Apply only declared source effects; never Git/runner paths or fixture commands."""
    recording = recording.verified()
    snapshot = recording.document["snapshots"][snapshot_id]
    root = _root(workspace)
    names = (
        set(source_snapshot.snapshot(root, paths=snapshot["source_paths"])["files"])
        if (root / ".git").exists()
        else set()
    )
    names.update(snapshot["files"])
    for name in sorted(names):
        path = _destination(root, name)
        identity = snapshot["files"].get(name, "deleted")
        if path.is_symlink() and identity == "symlink:" + os.readlink(path):
            continue
        if (
            path.is_file()
            and not path.is_symlink()
            and identity not in ("deleted",)
            and not identity.startswith("symlink:")
        ):
            data, mode = _read(root, name)
            if file_identity(data, mode) == identity:
                continue
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            raise RecordingError("Replay source destination is not a leaf")
        if identity == "deleted":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if identity.startswith("symlink:"):
            path.symlink_to(identity.removeprefix("symlink:"))
        else:
            with path.open("xb") as stream:
                stream.write(recording.blob(identity))
            path.chmod(0o755 if identity.startswith("executable:") else 0o644)
    if (root / ".git").exists():
        current = _git(root, ["rev-parse", "HEAD"]).decode().strip()
        if current != snapshot["head"]:
            _git(root, ["update-ref", "HEAD", snapshot["head"]])
            _git(root, ["read-tree", snapshot["head"]])
    return snapshot_id


def prepare_workspace(recording, workspace):
    recording = recording.verified()
    root = _root(workspace)
    if root.exists() and any(root.iterdir()):
        raise RecordingError("Replay workspace must be empty")
    pack = recording.document["launch"].get("git_pack")
    if not pack:
        raise RecordingError("Recording lacks the original Git source objects")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    snapshot = recording.document["snapshots"][recording.document["launch"]["base"]]
    arguments = ["init", "--template=", "-q"]
    if len(snapshot["head"]) == 64:
        arguments += ["--object-format=sha256"]
    _git(root, arguments)
    _git(root, ["index-pack", "--stdin"], data=recording.artifact(pack))
    _git(root, ["update-ref", "HEAD", snapshot["head"]])
    _git(root, ["read-tree", snapshot["head"]])
    materialize(recording, recording.document["launch"]["base"], root)
    verify_source(recording, recording.document["launch"]["base"], root)
    return root


def copy_into(recording, destination):
    recording = recording.verified()
    destination = _root(destination)
    if destination.exists():
        raise RecordingError("Recording copy destination must be new")
    original = recording
    destination.mkdir(mode=0o700, parents=True)
    for name, data in [
        ("recording.json", _bytes(original.document)),
        *[(row["path"], original.artifact(identity)) for identity, row in original.document["artifacts"].items()],
        *[(row["path"], original.blob(identity)) for identity, row in original.document["blobs"].items()],
    ]:
        path = destination / relative_path(name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644)
    # Preserve exact manifest spelling, not a reserialization with a different pin.
    (destination / "recording.json").write_bytes(
        _read(original.root, original.manifest_path.name, limit=MAX_RECORDING_BYTES)[0]
    )
    return load(destination / "recording.json", original.sha256)


def match_action(recording, action, view):
    """Fresh public tokens remain with TaskRun; compare only semantic request fields."""
    del recording
    needs = view.get("needs") or {}
    selected = {key: needs.get(key) for key in ("kind", "stage", "questions", "scope", "criteria") if key in needs}
    if action["kind"] == "approve_plan":
        plan = view.get("displayed_plan") or {}
        selected["plan"] = {
            key: plan.get(key) for key in ("acceptance_criteria", "constraints", "permission_boundaries")
        }
    if selected != action["match"]:
        raise RecordingError("Replay diverged: current operator request differs from the recording")
    return copy.deepcopy(action["payload"])


def verify_finished(recording, view, *, consumed_actions=None):
    recording = recording.verified()
    if consumed_actions is None and recording.document["actions"]:
        raise RecordingError("Replay diverged: operator action consumption is unverified")
    if consumed_actions is not None and (
        type(consumed_actions) is not int or consumed_actions != len(recording.document["actions"])
    ):
        raise RecordingError("Replay diverged: recording has unused operator actions")
    progress = view.get("replay_progress") or {}
    if progress.get("manifest_sha256") != recording.sha256 or progress.get("consumed") != len(
        recording.document["dispatches"]
    ):
        raise RecordingError("Replay diverged: recording has unused or extra dispatches")
    expected = recording.document["expected"]["verdict"]
    result = view.get("replay_result") or {}
    if progress.get("checks") == "recorded" or result.get("simulated"):
        used = result.get("consumed_checks")
        wanted = [row["id"] for row in recording.document["checks"]]
        if result.get("manifest_sha256") != recording.sha256 or not isinstance(used, list) or used != wanted:
            raise RecordingError("Replay diverged: archival obligation ledger is missing, foreign or incomplete")
    if result.get("pending") and expected in ("TASK_COMPLETE", "COMPLETE"):
        raise RecordingError("Replay diverged: archival completion is still pending")
    actual = (
        result.get("recorded_verdict") if result.get("simulated") and not result.get("pending") else view.get("status")
    )
    if actual != expected:
        raise RecordingError("Replay diverged: public terminal verdict differs from the recording")
    return {
        "verdict": actual,
        "simulated": bool(result.get("simulated")),
        "completion_current": False if result.get("simulated") else view.get("completion_current"),
    }
