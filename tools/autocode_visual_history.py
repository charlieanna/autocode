"""Parent-owned original native review bytes; history is never CURRENT source.

This runtime module has one retained writer per owned run. Real kernel/native
namespace continuity is an explicit CODE dependency and is not implemented by
file modes, stage flags, checksums or the retained JSON. No controller import,
state-file read, process launch, provider request or executable replay occurs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contract
    from . import autocode_design_inventory as inventory
    from . import autocode_design_manifest as design
    from . import autocode_image_delivery as delivery
    from . import autocode_protected_blob_read as protected_blobs
    from . import autocode_route_fit as route_fit
    from . import autocode_source_scope as source_scope
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_util as util
    from . import autocode_visual_acceptance as acceptance
    from . import autocode_visual_checkpoint as checkpoint
    from . import autocode_visual_evidence as captures
    from . import autocode_visual_profile as visual_profile
    from . import autocode_visual_scope as scope
    from . import autocode_visual_task_criteria as task_criteria
except ImportError:
    import autocode_contract_identity as contract
    import autocode_design_inventory as inventory
    import autocode_design_manifest as design
    import autocode_image_delivery as delivery
    import autocode_protected_blob_read as protected_blobs
    import autocode_route_fit as route_fit
    import autocode_source_scope as source_scope
    import autocode_source_snapshot as source_snapshot
    import autocode_util as util
    import autocode_visual_acceptance as acceptance
    import autocode_visual_checkpoint as checkpoint
    import autocode_visual_evidence as captures
    import autocode_visual_profile as visual_profile
    import autocode_visual_scope as scope
    import autocode_visual_task_criteria as task_criteria


def _require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "Repeated retained native-history key")
            result[key] = value
        return result

    return json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda value: _require(False, "Invalid retained native-history number"),
    )


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _technical_hold(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except util.Paused:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RuntimeError) as error:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Incomplete native history: " + str(error)) from error

    return guarded


def record_identity(record):
    # Shared with visual_runtime; adding history locators cannot change the
    # original native stage identity used by acceptance receipts.
    return {
        key: deepcopy(record.get(key))
        for key in (
            "stage",
            "role",
            "route_role",
            "engine",
            "exit_code",
            "launch_route",
            "expected_session",
            "thread_id",
            "source_revision",
            "contract_revision",
            "contract_hash",
            "task_id",
            "finished_at",
            "events",
            "output",
            "reported_output",
            "before_ref",
            "after_ref",
            "rework_evidence",
            "visual_runtime",
            "report_only",
            "report_repaired",
            "repaired_by",
            "applied_original_events",
            "timed_out",
            "interrupted",
            "dry_run",
            "truncated_output",
            "rejected",
        )
    }


def runtime_files(runtime_root):
    root = Path(runtime_root).resolve()
    excluded = {"__pycache__", "node_modules"}
    if excluded.intersection(root.parts):
        return {}
    paths = []
    # Preserve the complete fresh code map, without visiting excluded subtrees.
    # Like rglob, do not descend through directory symlinks; file symlinks retain
    # their existing is_file/hash behavior. No result survives this invocation.
    for directory, directories, files in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name not in excluded]
        for name in directories + files:
            path = Path(directory) / name
            if path.suffix in (".py", ".mjs", ".cjs"):
                paths.append(path)
    return {str(path.relative_to(root)): util.file_hash(path) for path in sorted(paths) if path.is_file()}


def _snapshot(value):
    _require(
        isinstance(value, dict)
        and set(value) in ({"head", "files", "revision"}, {"head", "files", "revision", "source_paths"})
        and ("source_paths" not in value or source_snapshot.is_canonical_paths(value["source_paths"]))
        and isinstance(value["head"], str)
        and value["head"]
        and isinstance(value["files"], dict)
        and all(
            isinstance(name, str)
            and name
            and not Path(name).is_absolute()
            and ".." not in Path(name).parts
            and isinstance(identity, str)
            and identity
            for name, identity in value["files"].items()
        )
        and value["revision"] == util.digest({key: value[key] for key in ("head", "files")}),
        "Invalid full original source snapshot",
    )
    return value


def _task_binding(state: RunState, snapshot, runtime_sha256):
    _require(contract.approved(state), "Native history needs the actual approved contract")
    task = state.get("current_task")
    saved = cast(dict, state["goal_contract"])
    _require(
        isinstance(task, dict)
        and task.get("id")
        and task.get("contract_hash") == saved["hash"]
        and task.get("contract_revision") == saved["revision"],
        "Native history has a foreign assigned task",
    )
    return {
        "contract": deepcopy(saved),
        "task": deepcopy(task),
        "settings": deepcopy(state["settings"]),
        "source": deepcopy(_snapshot(snapshot)),
        "runtime_sha256": runtime_sha256,
    }


def _no_credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            _require(
                re.sub("[^a-z]", "", key.lower())
                not in {
                    "auth",
                    "authorization",
                    "apikey",
                    "token",
                    "accesstoken",
                    "refreshtoken",
                    "password",
                    "secret",
                    "credentials",
                    "credential",
                },
                "Native history refuses credential-bearing saved settings",
            )
            _no_credentials(item)
    elif isinstance(value, list):
        for item in value:
            _no_credentials(item)


@dataclass(frozen=True)
class PreparedCheckpoint:
    """Process-local exact commit intent, not committed history or eligibility."""

    owner: object
    nonce: str
    binding_json: bytes
    proposal_json: bytes
    authorization_sha256: str
    decision_sha256: str
    authorized_acceptance: str


class NativeReviewHistory:
    """One parent retained writer; all reads reauthenticate native protection.

    protection.verify_namespace(query=..., selector=...) must be concrete runner
    CODE validating actual original launch, native shell-only kernel containment
    and complete subsequent launch continuity. It must fail on an unsealed or
    uncontained writer/crash gap. This is a protection primitive, NOT a callback
    claiming an image/native verdict. All image/report/capture/delivery semantics
    are concretely replayed here. No default, registry or saved state hook exists.
    """

    @_technical_hold
    def __init__(self, workspace, run_dir, *, protection):
        self.workspace = Path(workspace).resolve()
        self.run = Path(run_dir).absolute()
        _require(
            self.run.parent == self.workspace / ".autocode" / "runs"
            and self.run.resolve() == self.run
            and self.run.is_dir(),
            "Native history needs the existing actual owned run",
        )
        self.root = self.run / "visual-native-history"
        _require(
            protection is not None and callable(getattr(protection, "verify_namespace", None)),
            "Missing actual native kernel/protected-namespace continuity verifier",
        )
        self.protection = protection
        self.runtime_root = Path(__file__).resolve().parent
        self._prepared = {}
        self._safe(self.run, exists=True)

    def _safe(self, path, *, exists=False):
        path = Path(path).absolute()
        _require(path.name != "state.json", "Native history refuses state-file evidence paths")
        _require(
            path.is_relative_to(self.workspace)
            and ".." not in path.parts
            and not any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(self.workspace)),
            "Native history refuses external/symlinked authority paths",
        )
        _require(path.resolve().is_relative_to(self.workspace), "Native history path escaped its owner")
        if exists:
            _require(path.is_file() or path.is_dir(), "Missing retained native authority")
        return path

    def _guard(self, selector, metadata, *, phase, members):
        query = {
            "kind": "native-history-namespace",
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "namespace": str(self.root),
            "phase": phase,
            "selector": deepcopy(selector),
            "original_record": record_identity(metadata["record"]),
            "binding": deepcopy(metadata["binding"]),
            "original_producer": record_identity(metadata["producer_record"])
            if metadata.get("producer_record")
            else None,
            "producer_task": deepcopy(metadata.get("producer_task")),
            "original_scope_basis": deepcopy(metadata.get("scope_basis")),
            "operator_source": deepcopy(metadata.get("context", {}).get("producer"))
            if metadata.get("context", {}).get("producer", {}).get("kind") == "operator_supplied_source"
            else None,
            "member_hashes": deepcopy(members),
        }
        proof = self.protection.verify_namespace(query=deepcopy(query), selector=deepcopy(selector))
        _require(
            isinstance(proof, dict)
            and set(proof)
            == {"kind", "query_sha256", "evidence_hashes", "protected_refs", "original_launch_id", "through_launch_id"}
            and proof["kind"] == query["kind"]
            and proof["query_sha256"] == util.digest(query)
            and _hash(proof["original_launch_id"])
            and _hash(proof["through_launch_id"]),
            "Missing exact authenticated actual native protection continuity",
        )
        pins, protected = proof["evidence_hashes"], proof["protected_refs"]
        _require(
            isinstance(pins, dict)
            and pins
            and isinstance(protected, list)
            and protected
            and len(protected) == len(set(protected))
            and set(protected) <= set(pins),
            "Native protection has no complete protected launch evidence",
        )
        for name, expected in pins.items():
            path = self._safe(name, exists=True)
            _require(
                path.is_relative_to(self.run)
                and path.is_file()
                and _hash(expected)
                and util.file_hash(path) == expected,
                "Actual native protection evidence changed",
            )
        return deepcopy(proof)

    def _live_bytes(self, name):
        raw = Path(name).absolute()
        _require(raw.name != "state.json", "Native history refuses state-file evidence paths")
        if raw.is_relative_to(self.runtime_root):
            _require(
                raw.resolve() == raw
                and raw.is_file()
                and raw.suffix in (".py", ".mjs", ".cjs")
                and not any(
                    part.is_symlink() for part in (raw, *raw.parents) if part.is_relative_to(self.runtime_root)
                ),
                "Runtime proof must be exact non-symlinked code bytes",
            )
            return raw.read_bytes()
        path = self._safe(name, exists=True)
        _require(path.is_file(), "Native proof needs a regular owned file")
        return path.read_bytes()

    def _collect(self, metadata, expected):
        members = {}
        for name, digest in expected.items():
            raw = self._live_bytes(name)
            _require(_hash(digest) and _sha(raw) == digest, "Original native proof changed before retention")
            members[str(Path(name).absolute())] = raw
        return members

    def _reader(self, metadata, members):
        def read(name):
            path = Path(name)
            path = path if path.is_absolute() else self.workspace / path
            key = str(path.absolute())
            _require(path.name != "state.json", "Native history refuses state-file evidence paths")
            _require(
                ".." not in path.parts and key in metadata["member_hashes"] and key in members,
                "Original native evidence omitted or substituted: " + key,
            )
            raw = members[key]
            _require(_sha(raw) == metadata["member_hashes"][key], "Protected original native bytes changed")
            return raw

        return read

    def _stage(self, metadata, read, *, stage):
        record, snapshot = metadata["record"], metadata["binding"]["source"]
        _require(
            record.get("stage") == record.get("role") == stage
            and record.get("route_role", stage) == stage
            and record.get("engine") == "opencode"
            and type(record.get("exit_code")) is int
            and record["exit_code"] == 0
            and not any(
                record.get(key)
                for key in (
                    "report_only",
                    "report_repaired",
                    "repaired_by",
                    "applied_original_events",
                    "timed_out",
                    "interrupted",
                    "dry_run",
                    "truncated_output",
                    "rejected",
                )
            ),
            "Historical authority requires the actual original successful native stage",
        )
        bound = metadata["binding"]
        route = bound["settings"]["roles"][stage]
        _require(
            (route.get("engine") or bound["settings"].get("engine")) == "opencode"
            and record["launch_route"].get("model") == route["model"]
            and (record["launch_route"].get("provider") or record["engine"]) == (route.get("provider") or "opencode")
            and record["launch_route"].get("reasoning_effort") == route.get("reasoning_effort"),
            "Original actual stage route differs from the exact saved route/effort",
        )
        for key, expected in {
            "task_id": bound["task"]["id"],
            "contract_hash": bound["contract"]["hash"],
            "contract_revision": bound["contract"]["revision"],
            "source_revision": snapshot["revision"],
        }.items():
            _require(record.get(key) == expected, "Original native stage identity differs: " + key)
        seal = record["rework_evidence"]
        _require(seal["version"] == 1 and Path(seal["run_dir"]) == self.run, "Missing original accepted stage seal")
        for key in ("stage", "task_id", "contract_hash", "contract_revision", "source_revision"):
            _require(seal["binding"][key] == record[key], "Original stage seal binding changed")
        for path, expected in seal["hashes"].items():
            _require(_sha(read(path)) == expected, "Original accepted stage evidence changed")
        for key in ("events", "output", "reported_output"):
            if record.get(key):
                path = str(Path(record[key]).absolute())
                _require(
                    seal["paths"].get(key) == path and path in seal["hashes"], "Original native stage artifact unsealed"
                )
        report = _json(read(record["output"]))
        _require(util.digest(report) == seal["report_digest"], "Original accepted report seal changed")
        for key in ("before_ref", "after_ref"):
            _require(_json(read(record[key])) == snapshot, "Original stage was not read-only on its own source")
        events = read(record["events"])
        _require(events.endswith(b"\n"), "Truncated original native events")
        rows = [_json(line) for line in events.splitlines()]
        sessions = {row.get("sessionID") for row in rows}
        _require(len(sessions) == 1 and all(sessions), "Missing original native session")
        session = sessions.pop()
        _require(
            session == (record.get("expected_session") or record.get("thread_id")),
            "Original native session differs from the actual launch",
        )
        raw = read(record.get("reported_output") or record["output"])
        native = acceptance._terminal(events, _json(raw), session)
        _require(_json(raw) == report, "Original native report differs from accepted report")
        return report, raw, events, native

    def _replay_profile(self, old, context, read, metadata):
        """Recheck copied original launch semantics, not a saved readiness flag.

        These are byte/shape/config guards in addition to the actual kernel launch
        continuity primitive. They never prove that a child actually launched.
        """
        directory = Path(context["plugin_path"]).parent
        profile = visual_profile._profile(old)
        if "native_isolation" in context:
            return self._replay_native_profile(old, context, read, metadata, profile)
        proof = _json(read(directory / "profile.json"))
        final_raw = read(directory / "opencode-final.json")
        final = _json(final_raw)
        _require(
            proof["version"] == 1
            and proof["profile"] == profile
            and proof["contract_hash"] == metadata["binding"]["contract"]["hash"]
            and proof["contract_revision"] == metadata["binding"]["contract"]["revision"]
            and proof["visual_acceptance"] == "NOT_VERIFIED"
            and proof["child_identity"] == context["child_identity"]
            and proof["child_identity"]["command_sha256"] == util.digest(context["command"])
            and proof["child_identity"]["environment_sha256"] == context["environment_sha256"]
            and proof["child_identity"]["executable_sha256"] == profile["executable_sha256"]
            and util.digest(final) == context["config_sha256"] == proof["configuration_sources"]["inline_sha256"],
            "Original stage-local native profile/config/child identity changed",
        )
        route = old["settings"]["roles"]["sol"]
        _require(
            route["model"] == profile["model"]
            and route["reasoning_effort"] == profile["reasoning_effort"]
            and (route.get("engine") or old["settings"].get("engine")) == "opencode"
            and route.get("provider") in (None, "opencode"),
            "Original approved visual route/effort changed",
        )
        identity = (
            old["settings"].get("transport_identities", {}).get("opencode", old["settings"].get("transport_identity"))
        )
        _require(
            proof["original_transport"] == identity and util.digest(identity) == profile["transport_identity_sha256"],
            "Original approved transport identity changed",
        )
        command = context["command"]
        first_file = command.index("--file")
        base = command[:first_file]
        _require(
            len(base) >= 2 and base[1] == "run" and len(base[2:]) % 2 == 0,
            "Original native visual command is malformed",
        )
        args = {}
        for key, value in zip(base[2::2], base[3::2], strict=False):
            _require(
                key in ("--dir", "--format", "--agent", "--model", "--title", "--variant") and key not in args,
                "Original visual command resumed or added an unsupported option",
            )
            args[key] = value
        _require(
            args.get("--model") == profile["model"]
            and args.get("--variant") == profile["reasoning_effort"]
            and args.get("--format") == "json"
            and Path(args.get("--dir", "")) == self.workspace
            and isinstance(args.get("--agent"), str)
            and args["--agent"].startswith("autocode_sol")
            and command[first_file:] == [value for row in context["images"] for value in ("--file", row["path"])],
            "Original route, effort, workspace or exact image attachments changed",
        )
        agent = args["--agent"]
        boundary = proof["containment"]
        _require(
            boundary["version"] == 1
            and (
                boundary["platform"] == "darwin"
                and boundary.get("backend") in (None, "seatbelt")
                or boundary["platform"] == "linux"
                and boundary.get("backend") == "bubblewrap"
                and isinstance(boundary.get("backend_version"), str)
                and re.fullmatch(r"bubblewrap [0-9]+(?:\.[0-9]+)+", boundary["backend_version"])
                and Path(boundary["profile"]).name == "policy.bwrap"
            )
            and boundary["native_version"] == visual_profile.VERSION
            and Path(boundary["workspace"]) == self.workspace
            and boundary["allow_write"] is False
            and boundary["tools"] == ["bash"]
            and not any(key.startswith("loopback_") for key in boundary),
            "Original profile was not supported read-only shell-only native containment",
        )
        for name in ("shell", "profile", "conformance"):
            _require(
                _sha(read(directory / ("containment-" + name))) == boundary[name + "_sha256"],
                "Original actual containment copy changed",
            )
        conformance = _json(read(directory / "containment-conformance"))
        _require(
            conformance["tool"] == "bash"
            and type(conformance["exit"]) is int
            and conformance["exit"] == 0
            and conformance["native_version"] == visual_profile.VERSION
            and conformance["profile_sha256"] == boundary["profile_sha256"]
            and conformance["shell_sha256"] == boundary["shell_sha256"]
            and (
                boundary["platform"] != "linux"
                or conformance.get("backend") == boundary["backend"]
                and conformance.get("backend_version") == boundary["backend_version"]
            )
            and re.search(r"Operation not permitted|Permission denied|Read-only file system", conformance["output"]),
            "Original actual native denied-write conformance changed",
        )
        # profile.json sorts object keys, so its permission map cannot carry
        # last-match order. opencode-final.json preserves the actual inline bytes.
        policy = final["agent"][agent]["permission"]
        _require(
            policy == proof["permission"]
            and isinstance(policy, dict)
            and set(policy) == {"*", "bash", "external_directory"}
            and policy["*"] == policy["external_directory"] == "deny"
            and isinstance(policy["bash"], dict)
            and policy["bash"]
            and all(action in ("allow", "deny") for action in policy["bash"].values())
            and [list(item) for item in policy["bash"].items()] == proof["bash_rule_order"]
            and proof["globally_wider_permissions"] is False,
            "Original effective ordered native denies changed",
        )
        agents = final["agent"]
        _require(
            set(agents) == {agent, "title", "summary", "compaction"}
            and agents[agent]["mode"] == "primary"
            and agents[agent]["model"] == profile["model"]
            and agents[agent]["permission"] == policy
            and all(agents[name] == {"disable": True} for name in ("title", "summary", "compaction"))
            and final["model"] == final["small_model"] == profile["model"]
            and final["enabled_providers"] == ["openai"]
            and final["shell"] == boundary["shell"]
            and final["share"] == "disabled"
            and final["autoupdate"] is False
            and final["snapshot"] is False
            and final.get("permission", "deny") == "deny"
            and proof["configuration_sources"]["project_config_disabled"] is True,
            "Original exact effective isolated native configuration changed",
        )
        activity = proof["activity_plugin"]
        _require(
            final["plugin"] == [Path(activity["staged"]).as_uri(), Path(context["plugin_path"]).as_uri()]
            and _sha(read(activity["staged"])) == activity["sha256"]
            and _sha(read(Path(metadata["runtime_root"]) / "providers" / "opencode_activity.mjs")) == activity["sha256"]
            and _sha(read(directory / "visual-profile.py")) == metadata["runtime_files"]["autocode_visual_profile.py"],
            "Original exact native plugin/profile code changed",
        )
        audit = _json(read(directory / "audit-options.json"))
        _require(
            audit == context["audit_options"]
            and audit["path"] == context["audit_path"]
            and audit["attempt_id"] == context["attempt_id"]
            and audit["binding_sha256"] == util.digest(metadata["receipt"]["binding"])
            and audit["max_requests"] == proof["max_requests"] == profile["max_requests"]
            and audit["reviewer"]
            == proof["reviewer"]
            == {"provider": "opencode", "model": profile["model"], "agent": agent},
            "Original image audit cap, native agent or attempt binding changed",
        )
        sdk = directory / "config" / "opencode"
        manifest = _json(read(sdk / visual_profile.MANIFEST))
        _require(
            _sha(read(sdk / visual_profile.MANIFEST)) == profile["sdk_manifest_sha256"]
            and manifest["version"] == 1
            and isinstance(manifest["files"], dict)
            and manifest["files"]
            and proof["sdk_manifest_sha256"] == profile["sdk_manifest_sha256"]
            and Path(proof["sdk_source"]) == self.workspace / profile["sdk_path"],
            "Original pinned stage-local SDK manifest changed",
        )
        for name, digest in manifest["files"].items():
            _require(
                not Path(name).is_absolute()
                and ".." not in Path(name).parts
                and _hash(digest)
                and _sha(read(sdk / name)) == digest,
                "Original stage-local SDK dependency byte changed",
            )

    def _replay_native_profile(self, old, context, read, metadata, profile):
        actual = self.protection.verify_original_image_configuration(metadata["record"], context)
        descriptor, boundary = actual["descriptor"], actual["boundary"]
        if descriptor.get("backend") == "kilocode":
            return self._replay_kilo_profile(old, context, read, metadata, profile, actual)
        manifest = _json(read(descriptor["manifest"]))
        _require(
            all(
                manifest[key] == value
                for key, value in descriptor.items()
                if key not in ("manifest", "manifest_sha256")
            )
            and _sha(read(descriptor["manifest"])) == descriptor["manifest_sha256"]
            and manifest["role"] == "sol"
            and manifest["image_acceptance"] == "NOT_VERIFIED",
            "Original same-owner native isolation changed",
        )
        for name, digest in descriptor["files"].items():
            _require(_sha(read(name)) == digest, "Original native image/SDK/config byte changed")
        identity = (
            old["settings"].get("transport_identities", {}).get("opencode", old["settings"].get("transport_identity"))
        )
        route = old["settings"]["roles"]["sol"]
        _require(
            manifest["saved_settings_sha256"] == util.digest(old["settings"])
            and manifest["saved_transport_sha256"] == util.digest(identity) == profile["transport_identity_sha256"]
            and route["model"] == manifest["model"] == profile["model"]
            and route["reasoning_effort"] == manifest["reasoning_effort"] == profile["reasoning_effort"]
            and descriptor["child_identity"]["executable_sha256"] == profile["executable_sha256"],
            "Original native profile/settings/route changed",
        )
        inline = descriptor["configuration_sources"]["inline"]
        raw = read(inline["path"])
        final = _json(raw)
        observed = _json(read(boundary["conformance"]))["launch_identity"]
        _require(
            _sha(raw) == inline["sha256"] == observed["final_inline_sha256"]
            and util.digest(final) == context["config_sha256"]
            and {key: observed[key] for key in descriptor["child_identity"]} == descriptor["child_identity"]
            and observed["command_sha256"] == util.digest(context["command"]),
            "Original final native conformance inputs changed",
        )
        agent = "autocode_sol"
        permission = visual_profile.containment.shell_permissions(observed["effective_permission"])
        _require(
            observed["effective_agent"] == agent
            and observed["effective_tools"].get("bash") is True
            and all(value is False for key, value in observed["effective_tools"].items() if key != "bash")
            and final["agent"][agent]["permission"] == permission
            and list(final["agent"][agent]["permission"]["bash"].items()) == list(permission["bash"].items())
            and permission["*"] == permission["external_directory"] == "deny"
            and final["model"] == final["small_model"] == profile["model"]
            and final["enabled_providers"] == ["openai"]
            and final["share"] == "disabled"
            and final["autoupdate"] is False
            and final["snapshot"] is False
            and final["shell"] == boundary["shell"],
            "Original effective native permissions changed",
        )
        target = Path(descriptor["manifest"]).parent
        activity = target / "activity.mjs"
        _require(
            final["plugin"] == [activity.as_uri(), Path(context["plugin_path"]).as_uri()]
            and _sha(read(activity))
            == _sha(read(Path(metadata["runtime_root"]) / "providers" / "opencode_activity.mjs"))
            and context["prompt"] == descriptor["image_review"]["prompt"]
            and context["schema"] == descriptor["image_review"]["schema"]
            and all(_sha(read(row["path"])) == row["sha256"] for row in (context["prompt"], context["schema"]))
            and context["command"][context["command"].index("--file") :]
            == [value for row in context["images"] for value in ("--file", row["path"])],
            "Original native plugins, prompt or PNG attachments changed",
        )
        audit = context["audit_options"]
        _require(
            audit["max_requests"] == profile["max_requests"]
            and audit["path"] == context["audit_path"]
            and audit["attempt_id"] == context["attempt_id"]
            and audit["binding_sha256"] == util.digest(metadata["receipt"]["binding"])
            and audit["reviewer"] == {"provider": "opencode", "model": profile["model"], "agent": agent},
            "Original native image audit changed",
        )
        sdk = Path(descriptor["configuration_sources"]["OPENCODE_CONFIG_DIR"])
        saved = _json(read(sdk / visual_profile.MANIFEST))
        _require(
            _sha(read(sdk / visual_profile.MANIFEST))
            == manifest["sdk_manifest_sha256"]
            == profile["sdk_manifest_sha256"]
            and Path(manifest["sdk_source"]) == self.workspace / profile["sdk_path"],
            "Original native SDK identity changed",
        )
        for name, digest in saved["files"].items():
            _require(
                not Path(name).is_absolute() and ".." not in Path(name).parts and _sha(read(sdk / name)) == digest,
                "Original native SDK member changed",
            )

    def _replay_kilo_profile(self, old, context, read, metadata, profile, actual):
        """Original protected launch and parent source bytes, never CURRENT."""
        try:
            from . import autocode_kilo_native as kilo
            from . import autocode_kilo_source_relay as source
        except ImportError:
            import autocode_kilo_native as kilo
            import autocode_kilo_source_relay as source
        selected = kilo.selected(profile, old["settings"], "sol")
        _require(selected is not None, "original Kilo profile/role binding changed")
        binding, identity = selected
        descriptor, boundary = actual["descriptor"], actual["boundary"]
        manifest = _json(read(descriptor["manifest"]))
        _require(
            _sha(read(descriptor["manifest"])) == descriptor["manifest_sha256"]
            and all(
                manifest[key] == value
                for key, value in descriptor.items()
                if key not in ("manifest", "manifest_sha256")
            )
            and manifest["saved_settings_sha256"] == util.digest(old["settings"])
            and manifest["saved_transport_sha256"] == util.digest(identity) == binding["transport_identity_sha256"]
            and descriptor["compiled_identity"]["sha256"] == binding["executable_sha256"]
            and manifest["sdk_manifest_sha256"] == binding["sdk_manifest_sha256"],
            "original compiled Kilo/source/profile bytes changed",
        )
        for name, sha in descriptor["files"].items():
            _require(_sha(read(name)) == sha, "original Kilo SDK/plugin/config changed")
        inline = descriptor["configuration_sources"]["inline"]
        final_raw = read(inline["path"])
        final = _json(final_raw)
        observed = _json(read(boundary["conformance"]))["launch_identity"]
        _require(
            _sha(final_raw) == inline["sha256"] == observed["final_inline_sha256"]
            and util.digest(final) == context["config_sha256"]
            and descriptor["child_identity"] == {key: observed[key] for key in descriptor["child_identity"]}
            and observed["command_sha256"] == util.digest(context["command"])
            and final["enabled_providers"] == ["anthropic"]
            and final["model"] == final["small_model"] == kilo.MODEL
            and final["provider"]["anthropic"]["options"]
            == {"apiKey": source.PUBLIC_API_KEY, "baseURL": "https://api.anthropic.com/v1"}
            and final["plugin"] == [Path(context["plugin_path"]).as_uri()],
            "original Kilo config/transport changed",
        )
        audit = context["audit_options"]
        _require(
            audit["max_requests"] == profile["max_requests"]
            and audit["plugin_sha256"] == context["plugin_sha256"]
            and audit["binding_sha256"] == util.digest(metadata["receipt"]["binding"])
            and audit["reviewer"] == {"provider": "kilocode", "model": kilo.MODEL, "agent": "autocode_sol"},
            "original Kilo audit/source binding changed",
        )
        locator = descriptor["source_relay"]
        parent_path = locator["journal_path"]
        _require(
            parent_path in metadata["receipt"]["evidence_hashes"]
            and _sha(read(parent_path)) == metadata["receipt"]["evidence_hashes"][parent_path],
            "original closed parent source journal is not in protected publication",
        )
        source.register_retained_original(
            source._REGISTRATION,
            locator=locator,
            parent_bytes=read(parent_path),
            audit_bytes=read(context["audit_path"]),
            launch_seal=actual["source_launch_seal"],
            hooks=locator["source_hooks"],
            audit_options=audit,
        )

    def _replay_scope(self, metadata, *, seen, cache):
        """Rederive original scope from authenticated original dependencies.

        The real protected launch journal must bind this basis and measured
        restored-write union to the ORIGINAL launch; these retained fields are
        not standalone authority. No saved accepted-owner list is consumed.
        """
        basis = metadata["scope_basis"]
        body = metadata["binding"]["contract"]["body"]
        manifest = metadata["manifest"]["body"]
        admission = scope.admit(body, manifest)
        task = metadata["binding"]["task"]
        owner = task["milestone_id"]
        prior = admission["milestone_ids"][: admission["milestone_ids"].index(owner)]
        _require(
            set(basis) == {"prerequisite_selectors", "input_review_selectors", "source_delta"}
            and list(basis["prerequisite_selectors"]) == prior,
            "Original scope omitted authenticated earlier owner commitments",
        )
        for mid, selector in basis["prerequisite_selectors"].items():
            original = self._verify_checkpoint(selector, seen=seen, cache=cache)
            _require(
                original["milestone_id"] == mid
                and original["metadata"]["binding"]["contract"] == metadata["binding"]["contract"],
                "Original scope borrowed foreign owner/contract authority",
            )
        inputs = {}
        selectors = basis["input_review_selectors"]
        _require(
            isinstance(selectors, list) and len({row["sha256"] for row in selectors}) == len(selectors),
            "Repeated original scope input review",
        )
        for selector in selectors:
            original = self._verify_review(selector, seen=seen, cache=cache)
            _require(
                original["metadata"]["binding"]["contract"] == metadata["binding"]["contract"]
                and admission["milestone_ids"].index(original["metadata"]["binding"]["task"]["milestone_id"])
                <= admission["milestone_ids"].index(owner),
                "Original scope borrowed future/foreign input review",
            )
            for cid, footprint in original["case_footprints"].items():
                if admission["case_owners"][cid] in prior:
                    inputs[cid] = sorted(
                        set(inputs.get(cid, []))
                        | {str(Path(path).relative_to(self.workspace)) for path in footprint["input_hashes"]}
                    )
        delta = basis["source_delta"]
        _require(
            set(delta) == {"binding_sha256", "changed_paths"}
            and delta["binding_sha256"] == util.digest(metadata["binding"]),
            "Original measured source/write lineage differs from its exact binding",
        )
        derived = scope.derive(
            body,
            manifest,
            task,
            accepted_milestones=prior,
            accepted_case_input_paths=inputs,
            changed_paths=delta["changed_paths"],
        )
        _require(
            derived == metadata["current"]["visual_scope"],
            "Original native scope differs from authenticated complete-case dependencies",
        )

    def _replay_review(self, metadata, members, *, seen=(), cache=None):
        read = self._reader(metadata, members)
        receipt, context = metadata["receipt"], metadata["context"]
        old = {
            "workspace": str(self.workspace),
            "goal_contract": metadata["binding"]["contract"],
            "settings": metadata["binding"]["settings"],
            "current_task": metadata["binding"]["task"],
            "user_events": metadata["user_events"],
        }
        _require(contract.approved(old), "Retained original contract approval cannot be authenticated")
        _snapshot(metadata["binding"]["source"])
        _require(
            metadata["current"] == context["current"]
            and context["snapshot"] == metadata["binding"]["source"]
            and receipt["binding"] == acceptance._binding_identity(metadata["current"], metadata["manifest"]),
            "Original task/source/reference/reviewer binding changed",
        )
        _require(
            receipt["version"] == 2
            and receipt["receipt_sha256"]
            == util.digest({key: value for key, value in receipt.items() if key != "receipt_sha256"}),
            "Invalid original scoped receipt",
        )
        acceptance._check_scoped_receipt(receipt)
        self._replay_scope(metadata, seen=seen, cache={} if cache is None else cache)
        _require(
            metadata["binding"]["runtime_sha256"] == util.digest(metadata["runtime_files"]),
            "Original immutable runtime proof is incomplete",
        )
        for name, digest in metadata["runtime_files"].items():
            _require(_sha(read(Path(metadata["runtime_root"]) / name)) == digest, "Original runtime code bytes changed")
        _require(
            _json(read(context["launch_manifest"]))
            == {key: value for key, value in context.items() if key not in ("launch_manifest", "launch_sha256")},
            "Original parent-pinned visual launch changed",
        )
        for path, expected in {
            **context["pins"],
            context["launch_manifest"]: context["launch_sha256"],
            **receipt["evidence_hashes"],
        }.items():
            _require(_sha(read(path)) == expected, "Original visual/reference/launch evidence changed")
        _require(
            _json(read(context["manifest_path"])) == metadata["manifest"]["body"], "Original manifest bytes changed"
        )
        for artifact in design.all_artifacts(metadata["manifest"]["body"]):
            _require(
                _sha(read(Path(metadata["manifest"]["root"]) / artifact["path"])) == artifact["sha256"],
                "Original complete reference artifact changed",
            )
        report, raw, events, native = self._stage(metadata, read, stage="sol")
        validation = metadata["functional"]
        _require(
            validation.get("verdict") == "PASS"
            and all(validation.get(key) == value for key, value in report.items())
            and validation["source_revision"] == metadata["binding"]["source"]["revision"]
            and validation["output"] == metadata["record"]["output"],
            "Missing original accepted functional validation",
        )
        for path, expected in validation["evidence_hashes"].items():
            _require(_sha(read(path)) == expected, "Original functional proof input changed")
        reviewer, producer = receipt["reviewer"], context["producer"]
        _require(
            reviewer["record_sha256"] == util.digest(record_identity(metadata["record"]))
            and reviewer["session_id"] == native["session_id"]
            and reviewer["runtime_hash"] == metadata["binding"]["runtime_sha256"]
            and reviewer["finished_at"] == metadata["record"]["finished_at"]
            and {key: reviewer[key] for key in ("provider", "model")} == metadata["current"]["reviewer"],
            "Original independent reviewer provenance changed",
        )
        route = metadata["record"]["launch_route"]
        _require(
            route["model"] == reviewer["model"]
            and (route.get("provider") or metadata["record"]["engine"]) == reviewer["provider"],
            "Original actual reviewer route differs from its saved route",
        )
        if producer["kind"] == "operator_supplied_source":
            initial, task = old["goal_contract"]["body"]["initial_task"], old["current_task"]
            all_ids = {row["id"] for row in old["goal_contract"]["body"]["acceptance_criteria"]}
            _require(
                metadata["producer_record"] is None
                and metadata["producer_task"] is None
                and initial["kind"] == task["kind"] == "validate"
                and task["milestone_id"] == initial["milestone_id"]
                and set(task_criteria.ids(task)) == set(initial["acceptance_criteria"]) == all_ids
                and producer["task_id"] == task["id"]
                and producer["model"] is None
                and producer["session_id"] is None
                and producer["contract_revision"] == old["goal_contract"]["revision"]
                and producer["contract_hash"] == old["goal_contract"]["hash"]
                and producer["source_revision"] == metadata["binding"]["source"]["revision"]
                and producer["source_files_sha256"] == util.digest(metadata["binding"]["source"]["files"])
                and producer["approval_event_sha256"] == util.digest(old["goal_contract"]["approval_event"])
                and reviewer["producer_kind"] == "operator_supplied_source"
                and reviewer["operator_source"] == producer,
                "Original operator-supplied source needs exact initial validation and actual protected approval",
            )
            # Actual no-prior-writer/orchestration absence comes from the full
            # original launch journal in _guard, never a saved "no writers" flag.
        else:
            builder = metadata["producer_record"]
            _require(
                builder.get("stage") == builder.get("role") == "terra"
                and builder.get("engine") == "opencode"
                and type(builder.get("exit_code")) is int
                and builder["exit_code"] == 0
                and builder["events"] == producer["events"]
                and builder["output"] == producer["output"]
                and builder["task_id"] == producer["producer_task_id"] == metadata["producer_task"]["id"]
                and builder["contract_hash"]
                == metadata["producer_task"]["contract_hash"]
                == metadata["binding"]["contract"]["hash"]
                and builder["contract_revision"]
                == metadata["producer_task"]["contract_revision"]
                == metadata["binding"]["contract"]["revision"]
                and builder["source_revision"] == metadata["binding"]["source"]["revision"]
                and builder["launch_route"]["model"] == producer["model"] == old["settings"]["roles"]["terra"]["model"]
                and builder["launch_route"].get("reasoning_effort")
                == old["settings"]["roles"]["terra"].get("reasoning_effort")
                and not any(
                    builder.get(key)
                    for key in (
                        "report_only",
                        "report_repaired",
                        "repaired_by",
                        "applied_original_events",
                        "timed_out",
                        "interrupted",
                        "dry_run",
                        "truncated_output",
                        "rejected",
                    )
                ),
                "Original actual Builder role/route/source/exit provenance changed",
            )
            _require(
                producer["kind"] == "native_builder"
                and producer["model"] != reviewer["model"]
                and producer["session_id"] != reviewer["session_id"]
                and _json(read(producer["after_ref"])) == metadata["binding"]["source"],
                "Original native Builder/Validator split cannot be authenticated",
            )
            builder_events = read(producer["events"])
            _require(builder_events.endswith(b"\n"), "Original Builder native events were truncated")
            builder_rows = [_json(line) for line in builder_events.splitlines()]
            builder_phases = [row for row in builder_rows if row.get("type") in ("step_start", "step_finish")]
            _require(
                {row.get("sessionID") for row in builder_rows} == {producer["session_id"]}
                and not any(row.get("type") == "error" for row in builder_rows)
                and builder_phases
                and builder_phases[-1]["type"] == "step_finish"
                and builder_phases[-1]["part"]["reason"] == "stop"
                and (builder.get("expected_session") or builder.get("thread_id")) == producer["session_id"],
                "Original successful actual native Builder terminal/session changed",
            )
            _snapshot(_json(read(builder["before_ref"])))
        selected = set(receipt["binding"]["visual_scope"]["required_case_ids"])
        images, footprints = [], {}
        case_rows = {row["id"]: row for row in receipt["cases"]}
        report_rows = {row["id"]: row for row in report["design_results"]}
        _require(
            len(report_rows) == len(report["design_results"])
            and set(report_rows) == {case["id"] for case in metadata["manifest"]["body"]["cases"]}
            and all(
                set(row) == acceptance._SCOPED_REPORT_KEYS
                and row["criterion_ids"] == receipt["binding"]["case_criteria"][cid]
                for cid, row in report_rows.items()
            ),
            "Original full report roster/mapping or fields changed",
        )
        for key in ("task_id", "contract_hash", "contract_revision"):
            _require(report[key] == receipt["binding"][key], "Original image report identity changed: " + key)
        _require(
            report["design_manifest_hash"] == receipt["binding"]["manifest_body_hash"],
            "Original report changed its full reference identity",
        )
        self._replay_profile(old, context, read, metadata)
        for case in metadata["manifest"]["body"]["cases"]:
            cid = case["id"]
            if cid not in selected:
                _require(
                    report_rows[cid]["status"] == "NOT_VERIFIED"
                    and all(
                        report_rows[cid][key] == ""
                        for key in ("capture_ref", "capture_sha256", "candidate_ref", "comparison_ref")
                    ),
                    "Historical outside case claimed image authority",
                )
                continue
            row = case_rows[cid]
            bundle, refs = captures.verify_original_bytes(
                old,
                row["capture_ref"],
                row["capture_sha256"],
                case=case,
                current=metadata["binding"]["source"],
                read_bytes=read,
                engine_hashes=metadata["capture_engine_hashes"],
            )
            _require(
                report_rows[cid]["status"] == row["verdict"]
                and report_rows[cid]["criterion_ids"] == row["criterion_ids"]
                and report_rows[cid]["capture_ref"] == row["capture_ref"]
                and report_rows[cid]["capture_sha256"] == row["capture_sha256"]
                and report_rows[cid]["candidate_ref"] == row["candidate_ref"]
                and report_rows[cid]["comparison_ref"] == row["comparison_ref"]
                and str(self.workspace / bundle["artifacts"]["candidate"]["path"]) == row["candidate_ref"],
                "Original report/capture/case outcome changed",
            )
            if row["verdict"] == "PASS":
                _require(
                    row["comparison_ref"]
                    and read(row["comparison_ref"])
                    and _sha(read(row["comparison_ref"])) == row["comparison_sha256"],
                    "Original passing comparison missing",
                )
            for kind, digest in (
                ("reference", case["artifacts"]["screenshot"]["sha256"]),
                ("candidate", bundle["artifacts"]["candidate"]["sha256"]),
            ):
                images.append({"case_id": cid, "kind": kind, "sha256": digest, "mime": "image/png"})
            original_inputs = {str(self.workspace / name): digest for name, digest in bundle["inputs"].items()}
            source_files = {
                name: identity
                for name, identity in metadata["binding"]["source"]["files"].items()
                if any(inventory.path_intersects(name, path) for path in case["implementation_paths"])
            }
            footprints[cid] = {
                "input_hashes": original_inputs,
                "implementation_paths": deepcopy(case["implementation_paths"]),
                "source_files": source_files,
                "capture_engine_hashes": deepcopy(metadata["capture_engine_hashes"]),
                "verdict": row["verdict"],
                "case_criteria": deepcopy(row["criterion_ids"]),
            }
        transport = delivery
        if context.get("native_isolation", {}).get("backend") == "kilocode":
            try:
                from . import autocode_kilo_image_delivery as transport
            except ImportError:
                import autocode_kilo_image_delivery as transport
        observed = transport.verify_retained(
            metadata["record"],
            events=events,
            report=raw,
            images=images,
            binding=receipt["binding"],
            audit_path=context["audit_path"],
            audit_bytes=read(context["audit_path"]),
            attempt_id=context["attempt_id"],
            plugin_sha256=context["plugin_sha256"],
        )
        _require(observed == receipt["delivery"], "Original exact native final image delivery changed")
        return footprints

    def _publish(self, kind, metadata, members):
        metadata = deepcopy(metadata)
        metadata["kind"] = kind
        metadata["version"] = 1
        metadata["member_hashes"] = {name: _sha(raw) for name, raw in sorted(members.items())}
        raw = _canonical(metadata)
        name = _sha(raw)
        target = self.root / kind / name
        selector = {"kind": kind, "path": str(target / "manifest.json"), "sha256": name}
        self._guard(selector, metadata, phase="before-parent-publication", members=metadata["member_hashes"])
        self._safe(target)
        _require(not target.exists(), "Native history does not overwrite an original publication")
        target.mkdir(mode=0o700, parents=True)
        for digest in sorted(set(metadata["member_hashes"].values())):
            content = next(content for content in members.values() if _sha(content) == digest)
            path = target / digest
            with path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            path.chmod(0o400)
        with (target / "manifest.json").open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        (target / "manifest.json").chmod(0o400)
        directory = os.open(target, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self._guard(selector, metadata, phase="after-parent-publication", members=metadata["member_hashes"])
        return selector

    def _load(self, selector):
        _require(
            isinstance(selector, dict)
            and set(selector) == {"kind", "path", "sha256"}
            and selector["kind"] in ("reviews", "rejections", "checkpoints")
            and _hash(selector["sha256"]),
            "Invalid native history selector",
        )
        path = self._safe(selector["path"], exists=True)
        _require(
            path == self.root / selector["kind"] / selector["sha256"] / "manifest.json",
            "Foreign native history publication",
        )
        raw = path.read_bytes()
        _require(_sha(raw) == selector["sha256"], "Retained native history manifest changed")
        metadata = _json(raw)
        _require(
            metadata["kind"] == selector["kind"] and metadata["version"] == 1 and _canonical(metadata) == raw,
            "Malformed original native history publication",
        )
        self._guard(selector, metadata, phase="before-native-replay", members=metadata["member_hashes"])
        pins = metadata["member_hashes"]
        _require(all(_hash(digest) for digest in pins.values()), "Invalid original native evidence pin")
        try:
            blobs = protected_blobs.read(path.parent, pins.values())
        except (OSError, ValueError) as error:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Protected original evidence copy changed") from error
        return metadata, {name: blobs[digest] for name, digest in pins.items()}

    @_technical_hold
    def retain_review(self, state: RunState, record, receipt, *, context, accepted_validation, snapshot):
        _require(
            scope.policy(cast(dict, state["goal_contract"])["body"]) is not None
            and snapshot == source_scope.snapshot(self.workspace, state),
            "Native retention requires the actual fresh opted-in source",
        )
        _no_credentials(state["settings"])
        metadata = {
            "binding": _task_binding(state, snapshot, context["current"]["runtime_hash"]),
            "user_events": [deepcopy(cast(dict, state["goal_contract"])["approval_event"])],
            "current": deepcopy(context["current"]),
            "record": deepcopy(record),
            "receipt": deepcopy(receipt),
            "context": deepcopy(context),
            "functional": deepcopy(accepted_validation),
            "manifest": deepcopy(state["settings"]["design_manifest"]),
        }
        builder = next(
            (
                row
                for row in reversed(state.get("stages", []))
                if row.get("stage") == "terra" and row.get("events") == context["producer"].get("events")
            ),
            None,
        )
        if context["producer"]["kind"] == "operator_supplied_source":
            _require(
                builder is None
                and not any(
                    str(row.get("stage", "")).startswith("terra") or row.get("role") in ("terra", "builder")
                    for row in state.get("stages", [])
                )
                and not any(
                    state.get(key)
                    for key in (
                        "parent_run",
                        "parent_batch",
                        "orchestration_batch",
                        "orchestration_history",
                        "worker_results",
                    )
                ),
                "Operator initial source has prior writer/orchestration provenance",
            )
            metadata["producer_record"] = metadata["producer_task"] = None
        else:
            _require(
                context["producer"]["kind"] == "native_builder" and builder is not None,
                "Missing actual original Builder record for native retention",
            )
            assert builder is not None  # _require above refused a missing Builder record
            metadata["producer_record"] = deepcopy(builder)
            tasks = [state["current_task"], *state.get("task_archive", [])]
            originals = [task for task in tasks if task.get("id") == builder["task_id"]]
            _require(len(originals) == 1, "Original actual Builder task/handoff is missing or ambiguous")
            metadata["producer_task"] = deepcopy(originals[0])
        facts = self.fresh_facts(state=state, snapshot=snapshot)
        admission = scope.admit(cast(dict, state["goal_contract"])["body"], metadata["manifest"]["body"])
        owner = cast(dict, state["current_task"])["milestone_id"]
        prior = admission["milestone_ids"][: admission["milestone_ids"].index(owner)]
        _require(
            set(prior) <= set(facts["historical_milestone_ids"]), "Original review has unauthenticated prior ownership"
        )
        measured = self.protection.measured_source_delta(state=state, snapshot=snapshot)
        _require(
            isinstance(measured, dict)
            and set(measured) == {"binding_sha256", "changed_paths"}
            and measured["binding_sha256"] == util.digest(metadata["binding"]),
            "Original review lacks exact authenticated measured source/write lineage",
        )
        metadata["scope_basis"] = {
            "prerequisite_selectors": {mid: facts["checkpoint_selectors"][mid] for mid in prior},
            "input_review_selectors": self._scope_input_review_selectors(
                metadata["binding"]["contract"], owner, admission
            ),
            "source_delta": deepcopy(measured),
        }
        metadata["runtime_root"] = str(self.runtime_root)
        metadata["runtime_files"] = runtime_files(self.runtime_root)
        _require(
            util.digest(metadata["runtime_files"]) == context["current"]["runtime_hash"],
            "Native runtime changed before original retention",
        )
        expected = {
            **receipt["evidence_hashes"],
            **context["pins"],
            context["launch_manifest"]: context["launch_sha256"],
            **record["rework_evidence"]["hashes"],
            **accepted_validation["evidence_hashes"],
        }
        expected.update({str(self.runtime_root / name): digest for name, digest in metadata["runtime_files"].items()})
        for key in ("before_ref", "after_ref"):
            expected[record[key]] = util.file_hash(self._safe(record[key], exists=True))
        if builder is not None:
            for key in ("events", "output", "reported_output", "before_ref", "after_ref"):
                if builder.get(key):
                    expected[builder[key]] = util.file_hash(self._safe(builder[key], exists=True))
        members = self._collect(metadata, expected)
        engine = {
            name: util.file_hash(Path(captures.__file__).with_name(name))
            for name in ("autocode_visual_capture.py", "autocode_visual_browser.cjs")
        }
        metadata["capture_engine_hashes"] = engine
        metadata["member_hashes"] = {name: _sha(raw) for name, raw in members.items()}
        self._replay_review(metadata, members)
        _require(
            snapshot == source_scope.snapshot(self.workspace, state), "Source changed during original native retention"
        )
        for name, digest in expected.items():
            _require(_sha(self._live_bytes(name)) == digest, "Original evidence changed during retention")
        return self._publish("reviews", metadata, members)

    def _replay_rejection(self, metadata, members):
        read = self._reader(metadata, members)
        bound, validation, context = metadata["binding"], metadata["functional"], metadata["context"]
        report, _raw, _events, _native = self._stage(metadata, read, stage="sol")
        _require(
            validation.get("verdict") in ("FAIL", "BLOCKED")
            and all(validation.get(key) == value for key, value in report.items())
            and validation["source_revision"] == bound["source"]["revision"]
            and validation["output"] == metadata["record"]["output"]
            and context["snapshot"] == bound["source"]
            and context["status"] == "READY"
            and metadata["current"] == context["current"]
            and bound["runtime_sha256"] == util.digest(metadata["runtime_files"]),
            "rejection lost its actual nonpassing functional report or original source/runtime",
        )
        for name, digest in metadata["runtime_files"].items():
            _require(_sha(read(Path(metadata["runtime_root"]) / name)) == digest, "original rejection runtime changed")
        for name, digest in {
            **validation["evidence_hashes"],
            **context["pins"],
            context["launch_manifest"]: context["launch_sha256"],
        }.items():
            _require(_sha(read(name)) == digest, "original nonpassing evidence changed")
        _require(
            _json(read(context["launch_manifest"]))
            == {key: value for key, value in context.items() if key not in ("launch_manifest", "launch_sha256")},
            "rejection original visual launch changed",
        )
        self._replay_scope(metadata, seen=(), cache={})
        # This separate original evidence can authorize a bounded correction.
        # It is never a review receipt, image PASS, checkpoint or completion.
        _require("receipt" not in metadata, "a functional rejection cannot retain passing image authority")

    @_technical_hold
    def retain_rejection(self, state: RunState, record, *, context, accepted_validation, snapshot):
        _require(
            accepted_validation.get("verdict") in ("FAIL", "BLOCKED")
            and snapshot == source_scope.snapshot(self.workspace, state),
            "rejection needs current actual failing evidence",
        )
        _no_credentials(state["settings"])
        metadata = {
            "binding": _task_binding(state, snapshot, context["current"]["runtime_hash"]),
            "user_events": [deepcopy(cast(dict, state["goal_contract"])["approval_event"])],
            "current": deepcopy(context["current"]),
            "record": deepcopy(record),
            "context": deepcopy(context),
            "functional": deepcopy(accepted_validation),
            "manifest": deepcopy(state["settings"]["design_manifest"]),
            "runtime_root": str(self.runtime_root),
            "runtime_files": runtime_files(self.runtime_root),
        }
        builder = next(
            (
                row
                for row in reversed(state.get("stages", []))
                if row.get("stage") == "terra" and row.get("events") == context["producer"].get("events")
            ),
            None,
        )
        if context["producer"]["kind"] == "native_builder":
            _require(builder is not None, "correction lost its real original Builder producer")
            assert builder is not None  # _require above refused a missing Builder record
            metadata["producer_record"] = deepcopy(builder)
            tasks = [state["current_task"], *state.get("task_archive", [])]
            originals = [task for task in tasks if task.get("id") == builder["task_id"]]
            _require(len(originals) == 1, "rejection producer task is missing or ambiguous")
            metadata["producer_task"] = deepcopy(originals[0])
        else:
            _require(
                context["producer"]["kind"] == "operator_supplied_source" and builder is None,
                "initial rejection requires original approved operator source, never fabricated Builder",
            )
            metadata["producer_record"] = metadata["producer_task"] = None
        admission = scope.admit(cast(dict, state["goal_contract"])["body"], metadata["manifest"]["body"])
        _require(
            admission["milestone_ids"] == [cast(dict, state["current_task"])["milestone_id"]],
            "only same-owner nonpassing correction is implemented",
        )
        metadata["scope_basis"] = {
            "prerequisite_selectors": {},
            "input_review_selectors": [],
            "source_delta": self.protection.measured_source_delta(state=state, snapshot=snapshot),
        }
        expected = {
            **context["pins"],
            context["launch_manifest"]: context["launch_sha256"],
            **record["rework_evidence"]["hashes"],
            **accepted_validation["evidence_hashes"],
        }
        expected.update({str(self.runtime_root / name): digest for name, digest in metadata["runtime_files"].items()})
        for value in (record, builder):
            if value is None:
                continue
            for key in ("events", "output", "reported_output", "before_ref", "after_ref"):
                if value.get(key):
                    expected[value[key]] = util.file_hash(self._safe(value[key], exists=True))
        members = self._collect(metadata, expected)
        metadata["member_hashes"] = {name: _sha(raw) for name, raw in members.items()}
        self._replay_rejection(metadata, members)
        _require(snapshot == source_scope.snapshot(self.workspace, state), "source changed during rejection retention")
        return self._publish("rejections", metadata, members)

    @_technical_hold
    def verify_rejection(self, selector):
        _require(selector["kind"] == "rejections", "passing review cannot substitute for a rejection")
        metadata, members = self._load(selector)
        self._replay_rejection(metadata, members)
        proof = self._guard(selector, metadata, phase="after-native-replay", members=metadata["member_hashes"])
        return {"selector": deepcopy(selector), "metadata": metadata, "protection_proof": proof}

    @_technical_hold
    def verify_review(self, selector):
        return self._verify_review(selector, seen=(), cache={})

    def _verify_review(self, selector, *, seen, cache):
        metadata, members = self._load(selector)
        key = ("reviews", selector["sha256"])
        _require(key not in seen and metadata["kind"] == "reviews", "Cyclic or foreign original native review")
        if key in cache:
            # Per-call memoization only. _load has rechecked every protected pin
            # and actual native continuity; no cached boolean/file authority.
            result = deepcopy(cache[key])
        else:
            footprints = self._replay_review(metadata, members, seen=(*seen, key), cache=cache)
            result = {"selector": deepcopy(selector), "metadata": deepcopy(metadata), "case_footprints": footprints}
            cache[key] = deepcopy(result)
        result["protection_proof"] = self._guard(
            selector, metadata, phase="after-native-replay", members=metadata["member_hashes"]
        )
        return result

    def _writes_since_review(self, state: RunState, snapshot, review):
        """Require the actual protected write journal, including restored writes.

        Current equal bytes alone cannot erase an authorized shared-path write.
        No implementation of this native journal is invented by this module.
        """
        query = {
            "kind": "writes-since-original-review",
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "review_selector": deepcopy(review["selector"]),
            "original_record": record_identity(review["metadata"]["record"]),
            "original_binding": deepcopy(review["metadata"]["binding"]),
            "current_binding": _task_binding(state, snapshot, util.digest(runtime_files(self.runtime_root))),
        }
        verifier = getattr(self.protection, "writes_since_review", None)
        _require(callable(verifier), "Missing actual native protected source-write continuity journal")
        assert callable(verifier)  # _require above refused anything else
        proof = verifier(query=deepcopy(query), selector=deepcopy(review["selector"]))
        _require(
            isinstance(proof, dict)
            and set(proof)
            == {
                "kind",
                "query_sha256",
                "changed_paths",
                "evidence_hashes",
                "protected_refs",
                "original_launch_id",
                "through_launch_id",
            }
            and proof["kind"] == query["kind"]
            and proof["query_sha256"] == util.digest(query)
            and _hash(proof["original_launch_id"])
            and _hash(proof["through_launch_id"]),
            "Source writes need exact actual launch/source lineage, never a saved unchanged flag",
        )
        _require(
            proof["original_launch_id"] == review["protection_proof"]["original_launch_id"]
            and proof["through_launch_id"] == review["protection_proof"]["through_launch_id"],
            "Source write journal omitted an original or later actual launch",
        )
        paths, pins, protected = proof["changed_paths"], proof["evidence_hashes"], proof["protected_refs"]
        _require(
            isinstance(paths, list)
            and all(
                isinstance(path, str) and path and not Path(path).is_absolute() and ".." not in Path(path).parts
                for path in paths
            )
            and len(paths) == len(set(paths))
            and isinstance(pins, dict)
            and pins
            and isinstance(protected, list)
            and protected
            and len(protected) == len(set(protected))
            and set(protected) <= set(pins),
            "Source write journal is incomplete or unprotected",
        )
        for name, digest in pins.items():
            path = self._safe(name, exists=True)
            _require(
                path.is_relative_to(self.run) and path.is_file() and _hash(digest) and util.file_hash(path) == digest,
                "Actual source-write journal evidence changed",
            )
        return {"changed_paths": paths, "evidence_hashes": deepcopy(pins)}

    def _retained_pins(self, selector, metadata):
        parent = Path(selector["path"]).parent
        return {
            selector["path"]: selector["sha256"],
            **{str(parent / digest): digest for digest in set(metadata["member_hashes"].values())},
        }

    def _current_cases(self, state: RunState, snapshot, review, *, evidence_pins):
        metadata = review["metadata"]
        saved = metadata["binding"]
        # Original proof stays retained and authenticated, but cannot contribute
        # CURRENT facts after a separately approved contract revision.
        if saved["contract"] != state["goal_contract"]:
            return []
        if saved["settings"] != state["settings"] or saved["runtime_sha256"] != util.digest(
            runtime_files(Path(__file__).parent)
        ):
            return []
        write_proof = self._writes_since_review(state, snapshot, review)
        evidence_pins.update(write_proof["evidence_hashes"])
        changed = write_proof["changed_paths"]
        current = []
        for cid, footprint in review["case_footprints"].items():
            protected_inputs = [str(Path(path).relative_to(self.workspace)) for path in footprint["input_hashes"]]
            if any(
                inventory.path_intersects(path, dependency)
                for path in changed
                for dependency in [*footprint["implementation_paths"], *protected_inputs]
            ):
                continue
            if footprint["verdict"] != "PASS":
                continue
            files = {
                name: value
                for name, value in snapshot["files"].items()
                if any(inventory.path_intersects(name, path) for path in footprint["implementation_paths"])
            }
            if files != footprint["source_files"]:
                continue
            try:
                if all(
                    util.file_hash(self._safe(path, exists=True)) == digest
                    for path, digest in footprint["input_hashes"].items()
                ):
                    current.append(cid)
            except (OSError, ValueError, util.Paused):
                pass
        return current

    @_technical_hold
    def prepare_checkpoint(
        self,
        state: RunState,
        *,
        review_selector,
        completion_record,
        accepted_decision,
        prerequisite_selectors,
        snapshot,
    ):
        _require(snapshot == source_scope.snapshot(self.workspace, state), "Checkpoint preparation source is not fresh")
        review = self.verify_review(review_selector)
        own = review["metadata"]
        _require(
            own["binding"]["task"] == state["current_task"]
            and own["binding"]["source"] == snapshot
            and own["binding"]["contract"] == state["goal_contract"]
            and route_fit.same(own["binding"]["settings"], state["settings"])
            and own["binding"]["runtime_sha256"] == util.digest(runtime_files(self.runtime_root))
            and own["receipt"]["scope_verdict"] == "PASS",
            "Checkpoint needs its own exact current accepted native image/functional review",
        )
        admission = scope.admit(
            cast(dict, state["goal_contract"])["body"], state["settings"]["design_manifest"]["body"]
        )
        owner = cast(dict, state["current_task"])["milestone_id"]
        prior = admission["milestone_ids"][: admission["milestone_ids"].index(owner)]
        _require(
            isinstance(prerequisite_selectors, dict) and list(prerequisite_selectors) == prior,
            "Checkpoint intent requires every earlier committed serial owner",
        )
        facts = self.fresh_facts(state=state, snapshot=snapshot)
        _require(set(prior) <= set(facts["eligible_milestone_ids"]), "Earlier committed image prerequisites are stale")
        metadata = {
            "binding": _task_binding(state, snapshot, own["binding"]["runtime_sha256"]),
            "user_events": [deepcopy(cast(dict, state["goal_contract"])["approval_event"])],
            "record": deepcopy(completion_record),
            "accepted_decision": deepcopy(accepted_decision),
            "review_selector": deepcopy(review_selector),
            "prerequisite_selectors": deepcopy(prerequisite_selectors),
        }
        expected = dict(completion_record["rework_evidence"]["hashes"])
        for key in ("events", "output", "before_ref", "after_ref"):
            expected[completion_record[key]] = util.file_hash(self._safe(completion_record[key], exists=True))
        members = self._collect(metadata, expected)
        metadata["member_hashes"] = {name: _sha(raw) for name, raw in members.items()}
        report, raw, events, native = self._stage(metadata, self._reader(metadata, members), stage="astra_review")
        _require(
            accepted_decision.get("status") in ("CONTINUE", "DONE", "COMPLETE", "TASK_COMPLETE")
            and all(accepted_decision.get(key) == value for key, value in report.items()),
            "Checkpoint commit needs the actual accepted original Completion decision",
        )
        metadata["completion_native"] = native
        # This remains a prepared intent: no publication and no historical owner.
        nonce = uuid.uuid4().hex
        authorization = util.digest(
            {
                "binding": metadata["binding"],
                "decision": accepted_decision,
                "review_selector": review_selector,
                "prerequisite_selectors": prerequisite_selectors,
                "completion_native": native,
                "stage_record_sha256": util.digest(record_identity(completion_record)),
            }
        )
        handle = PreparedCheckpoint(
            self,
            nonce,
            _canonical(metadata["binding"]),
            _canonical({"metadata": metadata, "members": {name: raw.hex() for name, raw in members.items()}}),
            authorization,
            checkpoint.assignment_identity(accepted_decision),
            checkpoint.binding(state, snapshot),
        )
        self._prepared[nonce] = handle
        return handle

    @_technical_hold
    def commit_checkpoint(self, prepared, state: RunState, *, snapshot):
        _require(
            type(prepared) is PreparedCheckpoint
            and prepared.owner is self
            and self._prepared.get(prepared.nonce) is prepared,
            "Checkpoint needs its original unreplayed process-local intent",
        )
        proposal = _json(prepared.proposal_json)
        metadata = proposal["metadata"]
        _require(
            snapshot == source_scope.snapshot(self.workspace, state)
            and metadata["binding"]["runtime_sha256"] == util.digest(runtime_files(self.runtime_root))
            and prepared.binding_json
            == _canonical(_task_binding(state, snapshot, metadata["binding"]["runtime_sha256"])),
            "Checkpoint changed before its actual guarded commit",
        )
        review = self.verify_review(metadata["review_selector"])
        _require(
            review["metadata"]["binding"]["source"] == snapshot, "Own native review changed before checkpoint commit"
        )
        for owner, selector in metadata["prerequisite_selectors"].items():
            prior = self.verify_checkpoint(selector)
            _require(prior["milestone_id"] == owner, "Checkpoint substituted an earlier owner")
        owner = cast(dict, state["current_task"])["milestone_id"]
        facts = self.fresh_facts(state=state, snapshot=snapshot)
        admission = scope.admit(
            cast(dict, state["goal_contract"])["body"], state["settings"]["design_manifest"]["body"]
        )
        _require(
            set(admission["milestone_ids"][: admission["milestone_ids"].index(owner)])
            <= set(facts["eligible_milestone_ids"]),
            "Earlier owner eligibility changed before guarded commit",
        )
        metadata["milestone_id"] = owner
        metadata["commit_authorization_sha256"] = prepared.authorization_sha256
        members = {name: bytes.fromhex(raw) for name, raw in proposal["members"].items()}
        self._stage(metadata, self._reader(metadata, members), stage="astra_review")
        selector = self._publish("checkpoints", metadata, members)
        del self._prepared[prepared.nonce]
        return selector

    @_technical_hold
    def verify_checkpoint(self, selector):
        return self._verify_checkpoint(selector, seen=(), cache={})

    def _verify_checkpoint(self, selector, *, seen, cache):
        metadata, members = self._load(selector)
        key = ("checkpoints", selector["sha256"])
        _require(key not in seen and metadata["kind"] == "checkpoints", "Cyclic or foreign committed native checkpoint")
        if key in cache:
            result = deepcopy(cache[key])
            result["protection_proof"] = self._guard(
                selector, metadata, phase="after-checkpoint-replay", members=metadata["member_hashes"]
            )
            return result
        seen = (*seen, key)
        report, raw, events, native = self._stage(metadata, self._reader(metadata, members), stage="astra_review")
        _require(
            native == metadata["completion_native"]
            and all(metadata["accepted_decision"].get(key) == value for key, value in report.items())
            and metadata["accepted_decision"]["status"] in ("CONTINUE", "DONE", "COMPLETE", "TASK_COMPLETE"),
            "Original committed Completion decision changed",
        )
        review = self._verify_review(metadata["review_selector"], seen=seen, cache=cache)
        _require(
            review["metadata"]["binding"] == metadata["binding"]
            and review["metadata"]["receipt"]["scope_verdict"] == "PASS"
            and metadata["milestone_id"] == metadata["binding"]["task"]["milestone_id"],
            "Checkpoint borrowed unrelated visual authority",
        )
        authorization = util.digest(
            {
                "binding": metadata["binding"],
                "decision": metadata["accepted_decision"],
                "review_selector": metadata["review_selector"],
                "prerequisite_selectors": metadata["prerequisite_selectors"],
                "completion_native": native,
                "stage_record_sha256": util.digest(record_identity(metadata["record"])),
            }
        )
        _require(
            metadata["commit_authorization_sha256"] == authorization, "Original exact checkpoint admission changed"
        )
        admission = scope.admit(metadata["binding"]["contract"]["body"], review["metadata"]["manifest"]["body"])
        owner = metadata["milestone_id"]
        prior = admission["milestone_ids"][: admission["milestone_ids"].index(owner)]
        _require(
            list(metadata["prerequisite_selectors"]) == prior, "Original committed checkpoint omitted prior owners"
        )
        for identity, prerequisite in metadata["prerequisite_selectors"].items():
            authenticated = self._verify_checkpoint(prerequisite, seen=seen, cache=cache)
            _require(authenticated["milestone_id"] == identity, "Original checkpoint borrowed another prerequisite")
        result = {
            "milestone_id": metadata["milestone_id"],
            "selector": deepcopy(selector),
            "metadata": deepcopy(metadata),
        }
        cache[key] = deepcopy(result)
        result["protection_proof"] = self._guard(
            selector, metadata, phase="after-checkpoint-replay", members=metadata["member_hashes"]
        )
        return result

    def _selectors(self, kind):
        # A retained physical copy is evidence, not a committed scheduling
        # decision. The original guarded parent publication owns this roster.
        _require(kind in ("reviews", "checkpoints"), "Unknown native history kind")
        self._safe(self.root)
        roster = getattr(self.protection, "committed_selectors", None)
        _require(callable(roster), "Missing guarded committed native history roster")
        selected = roster(kind)
        _require(isinstance(selected, list), "Malformed committed native history roster")
        paths = set()
        for selector in selected:
            _require(
                isinstance(selector, dict)
                and set(selector) == {"kind", "path", "sha256"}
                and selector["kind"] == kind
                and isinstance(selector["path"], str)
                and _hash(selector["sha256"]),
                "Malformed committed native history selector",
            )
            path = self._safe(selector["path"], exists=True)
            _require(
                path.is_file()
                and path.name == "manifest.json"
                and path.parent.parent == self.root / kind
                and path.parent.name == selector["sha256"]
                and str(path) not in paths
                and util.file_hash(path) == selector["sha256"],
                "Committed native original disappeared, changed or borrowed another namespace",
            )
            paths.add(str(path))
        return deepcopy(selected)

    def _scope_input_review_selectors(self, saved_contract, owner, admission):
        # Verify every retained original before partitioning; a foreign contract
        # is not CURRENT input authority and is never copied into this basis.
        # Future same-contract reviews also cannot lend earlier launch authority.
        owners = admission["milestone_ids"]
        _require(owner in owners, "Original scope needs an approved current owner")
        limit = owners.index(owner)
        selected, cache = [], {}
        for selector in self._selectors("reviews"):
            original = self._verify_review(selector, seen=(), cache=cache)
            bound = original["metadata"]["binding"]
            if bound["contract"] != saved_contract:
                continue
            _require(bound["task"]["milestone_id"] in owners, "Original input review has an unknown owner")
            if owners.index(bound["task"]["milestone_id"]) <= limit:
                selected.append(deepcopy(selector))
        return selected

    @_technical_hold
    def fresh_facts(self, *, state: RunState, snapshot):
        _require(
            snapshot == source_scope.snapshot(self.workspace, state), "Scheduling facts need the actual fresh source"
        )
        admission = scope.admit(
            cast(dict, state["goal_contract"])["body"], state["settings"]["design_manifest"]["body"]
        )
        _require(admission is not None, "Native facts require the reviewed complete-case policy")
        design.verify(state["settings"]["design_manifest"])
        cache: dict = {}
        checkpoints = [
            self._verify_checkpoint(selector, seen=(), cache=cache) for selector in self._selectors("checkpoints")
        ]
        committed_rows = [
            row for row in checkpoints if row["metadata"]["binding"]["contract"] == state["goal_contract"]
        ]
        committed = {row["milestone_id"] for row in committed_rows}
        _require(
            len(committed) == len(committed_rows),
            "Ambiguous original committed owner; parent transaction reconciliation required",
        )
        checkpoint_selectors = {row["milestone_id"]: deepcopy(row["selector"]) for row in committed_rows}
        history = [owner for owner in admission["milestone_ids"] if owner in committed]
        inputs: dict
        current, inputs, pins = set(), {}, {}
        for selector in self._selectors("reviews"):
            review = self._verify_review(selector, seen=(), cache=cache)
            pins.update(self._retained_pins(selector, review["metadata"]))
            pins.update(review["protection_proof"]["evidence_hashes"])
            # Foreign originals remain checked history. They do not participate
            # in this contract's case IDs, input footprints or eligibility.
            if review["metadata"]["binding"]["contract"] != state["goal_contract"]:
                continue
            current.update(self._current_cases(state, snapshot, review, evidence_pins=pins))
            for cid, footprint in review["case_footprints"].items():
                if admission["case_owners"][cid] in committed:
                    inputs[cid] = sorted(
                        set(inputs.get(cid, []))
                        | {str(Path(path).relative_to(self.workspace)) for path in footprint["input_hashes"]}
                    )
        for row in checkpoints:
            pins.update(self._retained_pins(row["selector"], row["metadata"]))
            pins.update(row["protection_proof"]["evidence_hashes"])
        eligible = scope.eligible_milestones(
            cast(dict, state["goal_contract"])["body"],
            state["settings"]["design_manifest"]["body"],
            authenticated_milestone_ids=history,
            current_case_ids=[cid for cid in admission["case_ids"] if cid in current],
        )
        for name, digest in pins.items():
            path = self._safe(name, exists=True)
            _require(
                path.is_relative_to(self.run) and path.is_file() and _hash(digest) and util.file_hash(path) == digest,
                "Retained native scheduling evidence changed during authentication",
            )
        _require(
            snapshot == source_scope.snapshot(self.workspace, state),
            "Source changed during native scheduling authentication",
        )
        return {
            "historical_milestone_ids": history,
            "eligible_milestone_ids": eligible,
            "current_case_ids": [cid for cid in admission["case_ids"] if cid in current],
            "case_input_paths": inputs,
            "evidence_hashes": pins,
            "protected_refs": list(pins),
            "checkpoint_selectors": checkpoint_selectors,
            "current_binding": _task_binding(state, snapshot, util.digest(runtime_files(Path(__file__).parent))),
        }

    @_technical_hold
    def scope_authority(self, *, state: RunState, snapshot):
        facts = self.fresh_facts(state=state, snapshot=snapshot)
        task = cast(dict, state["current_task"])
        admission = scope.admit(
            cast(dict, state["goal_contract"])["body"], state["settings"]["design_manifest"]["body"]
        )
        prior = admission["milestone_ids"][: admission["milestone_ids"].index(task["milestone_id"])]
        inputs = {
            cid: paths for cid, paths in facts["case_input_paths"].items() if admission["case_owners"][cid] in prior
        }
        # Full measured source lineage/observed restored-write union remains a
        # required protected-journal input. Never invent it from current hashes.
        measured = self.protection.measured_source_delta(state=state, snapshot=snapshot)
        _require(
            isinstance(measured, dict)
            and set(measured) == {"binding_sha256", "changed_paths"}
            and measured["binding_sha256"] == util.digest(facts["current_binding"]),
            "Missing actual source lineage/write accounting",
        )
        return scope.derive(
            cast(dict, state["goal_contract"])["body"],
            state["settings"]["design_manifest"]["body"],
            task,
            accepted_milestones=[owner for owner in facts["historical_milestone_ids"] if owner in prior],
            accepted_case_input_paths=inputs,
            changed_paths=measured["changed_paths"],
        )

    @_technical_hold
    def authorize_initial_assignment(self, state: RunState, decision, *, snapshot):
        _require(
            snapshot == source_scope.snapshot(self.workspace, state)
            and state.get("current_task") is None
            and contract.approved(state),
            "Initial assignment needs actual user approval and a fresh source with no assigned task",
        )
        admission = scope.admit(
            cast(dict, state["goal_contract"])["body"], state["settings"]["design_manifest"]["body"]
        )
        _require(admission is not None, "Initial visual assignment needs exact reviewed complete-case admission")
        body = cast(dict, state["goal_contract"])["body"]
        initial = body["initial_task"]
        # Match the domain's normalized actual approval decision exactly. The
        # whole initial_task includes objective/paths that live outside next_task.
        expected = {
            "status": "CONTINUE",
            "next_objective": initial["objective"],
            "affected_paths": list(body["milestones"][0]["affected_paths"]) if initial["affected_paths"] else [],
            "next_task": {key: value for key, value in initial.items() if key not in ("objective", "affected_paths")},
        }
        _require(
            initial["milestone_id"] == admission["milestone_ids"][0] and decision == expected,
            "Initial authority cannot invent or change the normalized approved first task",
        )
        design.verify(state["settings"]["design_manifest"])
        query = {
            "kind": "approved-initial-assignment",
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "contract": deepcopy(state["goal_contract"]),
            "settings": deepcopy(state["settings"]),
            "snapshot": deepcopy(snapshot),
            "decision": deepcopy(decision),
        }
        verifier = getattr(self.protection, "verify_initial_approval", None)
        _require(callable(verifier), "Missing actual protected user-approval/source/runtime verification")
        assert callable(verifier)  # _require above refused anything else
        proof = verifier(query=deepcopy(query))
        _require(
            isinstance(proof, dict)
            and set(proof) == {"kind", "query_sha256", "evidence_hashes", "protected_refs"}
            and proof["kind"] == query["kind"]
            and proof["query_sha256"] == util.digest(query)
            and isinstance(proof["evidence_hashes"], dict)
            and proof["evidence_hashes"]
            and isinstance(proof["protected_refs"], list)
            and proof["protected_refs"]
            and set(proof["protected_refs"]) <= set(proof["evidence_hashes"]),
            "Initial approval cannot come from a saved flag/token alone",
        )
        for name, digest in proof["evidence_hashes"].items():
            path = self._safe(name, exists=True)
            _require(
                path.is_relative_to(self.run) and _hash(digest) and util.file_hash(path) == digest,
                "Actual original user-approval evidence changed",
            )
        _require(
            snapshot == source_scope.snapshot(self.workspace, state),
            "Source changed during original initial approval verification",
        )
        return checkpoint.VerifiedInitialAssignment(
            checkpoint.binding(state, snapshot, initial=True),
            snapshot["revision"],
            util.digest(runtime_files(self.runtime_root)),
            checkpoint.assignment_identity(decision),
        )
