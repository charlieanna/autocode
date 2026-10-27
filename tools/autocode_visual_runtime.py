"""Bridge the existing Sol boundary to capture, transport and acceptance proof.

The controller retains prepare's context on record.visual_runtime and verifies it
immediately before Popen. Only accept_review writes acceptance (through accept);
projection and completion_check never mutate state. No capture commands, provider
calls, package installation, credentials or global configuration are managed here.
"""

from __future__ import annotations

try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope


import contextlib
import hashlib
import json
import shutil
import uuid
from copy import deepcopy
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contract
    from . import autocode_design_manifest as design
    from . import autocode_image_delivery as delivery
    from . import autocode_util as util
    from . import autocode_visual_acceptance as acceptance
    from . import autocode_visual_evidence as evidence
    from . import autocode_visual_history as history_proof
    from . import autocode_visual_scope as scope_policy
    from . import autocode_visual_task_criteria as task_criteria
except ImportError:
    import autocode_contract_identity as contract
    import autocode_design_manifest as design
    import autocode_image_delivery as delivery
    import autocode_util as util
    import autocode_visual_acceptance as acceptance
    import autocode_visual_evidence as evidence
    import autocode_visual_history as history_proof
    import autocode_visual_scope as scope_policy
    import autocode_visual_task_criteria as task_criteria


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _delivery_backend(state: RunState):
    if state.get("settings", {}).get("roles", {}).get("sol", {}).get("provider") != "kilocode":
        return delivery, "OPENCODE_CONFIG_CONTENT"
    try:
        from . import autocode_kilo_image_delivery as kilo_delivery
        from . import autocode_visual_profile as launch_profile
    except ImportError:
        import autocode_kilo_image_delivery as kilo_delivery
        import autocode_visual_profile as launch_profile
    if launch_profile._profile(state).get("mode") == launch_profile.kilo.MODE:
        return kilo_delivery, "KILO_CONFIG_CONTENT"
    return delivery, "OPENCODE_CONFIG_CONTENT"


def _unknown(reason):
    return {
        "status": "NOT_READY",
        "reason": str(reason),
        "cases": [],
        "current_all_accepted": False,
        "accepted_cases": None,
        "accepted_frames": None,
        "coverage_complete": False,
    }


def _authority_unknown(reason):
    """Scoped acceptance without runner CODE: unknown, not a failed readiness check.

    Public ``--status`` stays lock-free and must not claim NOT_READY when the only
    missing input is the exclusive-lock scope authority the runner holds.
    """
    return {
        "status": "UNKNOWN",
        "reason": str(reason),
        "cases": [],
        "current_all_accepted": None,
        "accepted_cases": None,
        "accepted_frames": None,
        "coverage_complete": False,
    }


SCOPE_AUTHORITY_UNAVAILABLE = (
    "Scoped visual acceptance needs runner CODE scope authority; public status stays lock-free"
)


def requested(state: RunState):
    constraints = cast(dict, state.get("goal_contract", {})).get("body", {}).get("constraints") or []
    return any(
        isinstance(row, str)
        and row.startswith(("VISUAL_CASE_CRITERIA=", "VISUAL_REVIEW_PROFILE=", "VISUAL_SLICE_GATES="))
        for row in constraints
    )


def _has_scope_marker(state: RunState):
    return any(
        isinstance(row, str) and row.startswith(scope_policy.MARKER)
        for row in cast(dict, state.get("goal_contract", {})).get("body", {}).get("constraints", [])
    )


def needs_scope_authority(state: RunState):
    """Whether authenticated scoped visual acceptance requires runner CODE authority."""
    return _has_scope_marker(state)


def _required_cases(state: RunState, current):
    cases = state["settings"]["design_manifest"]["body"]["cases"]
    if "visual_scope" not in current:
        return cases
    required = set(current["visual_scope"]["required_case_ids"])
    return [case for case in cases if case["id"] in required]


def _selected_captures(state: RunState, snapshot, current):
    if "visual_scope" not in current:
        selected = evidence.context(state, snapshot)
        _require(
            selected and selected["current"] and not selected["unavailable_cases"],
            "Missing CURRENT captures for declared cases; run declared validate preflight capture commands",
        )
        return selected
    required = current["visual_scope"]["required_case_ids"]
    selected = evidence.context(state, snapshot, required_case_ids=required)
    ids = [row["case"]["id"] for row in selected["current"]] if selected else []
    _require(
        selected and len(ids) == len(set(ids)) and set(ids) == set(required),
        "Missing CURRENT captures for required visual cases; outside cases remain NOT_VERIFIED",
    )
    return selected


def _seal_all_references(state: RunState, pins):
    # Every reference remains protected, including cases outside this checkpoint.
    manifest = state["settings"]["design_manifest"]
    for artifact in design.all_artifacts(manifest["body"]):
        path = _owned(state["workspace"], Path(manifest["root"]) / artifact["path"])
        _require(util.file_hash(path) == artifact["sha256"], "Sealed reference artifact changed")
        pins[str(path)] = artifact["sha256"]
    saved = manifest.get("scope_admission")
    if saved is not None:
        path = _owned(state["workspace"], Path(manifest["root"]) / saved["path"])
        _require(util.file_hash(path) == saved["sha256"], "Sealed scope reference changed")
        pins[str(path)] = saved["sha256"]
    if manifest.get("manifest_path"):
        path = _owned(state["workspace"], manifest["manifest_path"])
        _require(util.read_object(path) == manifest["body"], "Retained inventory index changed")
        pins[str(path)] = util.file_hash(path)


def _owned(root, path):
    _require(".." not in Path(path).parts, "Visual authority must not traverse parents")
    return evidence.local_file(root, path)


def _pins(root, pins):
    _require(isinstance(pins, dict) and pins, "Missing sealed evidence hashes")
    for path, expected in pins.items():
        _require(util.file_hash(_owned(root, path)) == expected, "Sealed visual evidence changed: " + path)


def verify_reference(state: RunState):
    """Keep retained inputs intact even when capture/delivery is NOT_READY.

    This launch gate checks only the saved reference's structure, identity,
    owned files and original bytes. Mapping, future captures and visual review
    readiness remain separate; this check cannot grant visual acceptance.
    """
    try:
        record = state.get("settings", {}).get("design_manifest")
        _require(record, "Approved visual contract requires its retained design manifest")
        _require(isinstance(record, dict), "Invalid retained design manifest record")
        assert isinstance(record, dict)  # _require above refused anything else
        root = Path(state["workspace"]).resolve()
        _require(
            Path(record["root"]).resolve().is_relative_to(root),
            "Retained design references must be owned by the workspace",
        )
        design.verify(record)
        for artifact in design.all_artifacts(record["body"]):
            _owned(root, Path(record["root"]) / artifact["path"])
        if record.get("manifest_path"):
            _owned(root, record["manifest_path"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", str(error)) from error


def _runtime_hash():
    root = Path(__file__).resolve().parent
    # Include controller/provider bytes without importing those higher layers.
    return util.digest(
        {
            str(path.relative_to(root)): util.file_hash(path)
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and path.suffix in (".py", ".mjs", ".cjs")
            and "__pycache__" not in path.parts
            and "node_modules" not in path.parts
        }
    )


def _route(state: RunState):
    route = state["settings"]["roles"]["sol"]
    engine = route.get("engine") or state["settings"].get("engine")
    _require(engine == "opencode" and route.get("model"), "Visual review needs a known native Sol route")
    return {"provider": route.get("provider") or engine, "model": route["model"]}


def _current(state: RunState, snapshot, manifest_sha, *, scope_authority=None):
    _require(contract.approved(state), "Visual acceptance requires an authenticated approved contract")
    saved = cast(dict, state["goal_contract"])
    criteria = deepcopy(saved["body"]["acceptance_criteria"])
    definitions = [
        {key: value for key, value in row.items() if key not in ("status", "evidence")}
        for row in state["acceptance_criteria"]
    ]
    reason = "Current criterion definitions differ from the approved contract"
    _require(len(definitions) == len(criteria), reason)
    for definition, approved in zip(definitions, criteria, strict=False):
        # install_draft projects runtime rows to id/criterion/status/evidence.
        # Only its omitted metadata may be absent; any present definition must
        # still equal the approved row. Bind the full approved criteria below.
        expected = dict(approved)
        for field in ("verification_method", "human_review", "sources", "current_behavior"):
            if field not in definition:
                expected.pop(field, None)
        _require(
            definition == expected and all(type(value) is type(expected[key]) for key, value in definition.items()),
            reason,
        )
    manifest = state["settings"]["design_manifest"]
    root = Path(state["workspace"]).resolve()
    _require(
        Path(manifest["root"]).resolve().is_relative_to(root),
        "External exports require workspace-owned copies retained by design_manifest.retain",
    )
    for case in manifest["body"]["cases"]:
        for artifact in case["artifacts"].values():
            _owned(root, Path(manifest["root"]) / artifact["path"])
    design.verify(manifest)
    constraints = saved["body"].get("constraints", [])
    _require(
        isinstance(constraints, list) and all(isinstance(row, str) for row in constraints),
        "Invalid approved contract constraints",
    )
    marker = "VISUAL_CASE_CRITERIA="
    mappings = [row[len(marker) :] for row in constraints if row.startswith(marker)]
    _require(len(mappings) == 1, "Exactly one approved VISUAL_CASE_CRITERIA constraint is required")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "Duplicate JSON key in VISUAL_CASE_CRITERIA: " + key)
            result[key] = value
        return result

    cases = json.loads(
        mappings[0],
        object_pairs_hook=unique_object,
        parse_constant=lambda value: _require(False, "Invalid JSON constant in VISUAL_CASE_CRITERIA: " + value),
    )
    _require(
        isinstance(cases, dict) and set(cases) == {case["id"] for case in manifest["body"]["cases"]},
        "VISUAL_CASE_CRITERIA must map exactly the declared case IDs",
    )
    ids = {row["id"] for row in criteria}
    _require(
        all(
            isinstance(mapped, list)
            and mapped
            and all(isinstance(cid, str) for cid in mapped)
            and len(mapped) == len(set(mapped))
            and set(mapped) <= ids
            for mapped in cases.values()
        ),
        "VISUAL_CASE_CRITERIA needs nonempty unique approved CID lists",
    )
    _require(
        snapshot["revision"] == util.digest({key: snapshot[key] for key in ("head", "files")}),
        "Invalid full source snapshot",
    )
    current = {
        "task_id": cast(dict, state["current_task"])["id"],
        "contract_revision": saved["revision"],
        "contract_hash": saved["hash"],
        "source_revision": snapshot["revision"],
        "runtime_hash": _runtime_hash(),
        "criteria": criteria,
        "case_criteria": cases,
        "reviewer": _route(state),
        "manifest_body_hash": manifest["manifest_hash"],
        "manifest_file_sha256": manifest_sha,
    }
    if scope_policy.policy(saved["body"]) is not None:
        _require(callable(scope_authority), "Missing runner CODE scope authority for visual slice gates")
        _require(snapshot == source_scope.snapshot(root, state), "Caller source snapshot is not the fresh owned source")
        before = util.digest({"contract": saved, "task": state["current_task"], "settings": state["settings"]})
        # The callback must authenticate native historical receipts, input
        # footprints and measured source deltas, then independently derive scope.
        # It is injected CODE, never a saved/model callback or cached PASS flag.
        derived = scope_authority(state=state, snapshot=deepcopy(snapshot))
        _require(
            before
            == util.digest(
                {"contract": state["goal_contract"], "task": state["current_task"], "settings": state["settings"]}
            )
            and snapshot == source_scope.snapshot(root, state),
            "Scope authority changed its task/configuration or source inputs",
        )
        scope_policy.check_bound_scope(
            derived,
            case_ids=[case["id"] for case in manifest["body"]["cases"]],
            case_criteria=cases,
            criterion_ids=[row["id"] for row in criteria],
        )
        admission = scope_policy.admit(saved["body"], manifest["body"])
        _require(
            admission is not None and derived["case_owners"] == admission["case_owners"],
            "Derived scope changed the approved full case owner roster",
        )
        task = cast(dict, state["current_task"])
        _require(
            derived["milestone_ids"] == [task.get("milestone_id")]
            and derived["task_criterion_ids"] == task.get("acceptance_criteria")
            and derived["affected_paths"] == task.get("affected_paths")
            and task.get("kind") in ("implement", "validate")
            and (derived["mode"] != "integration" or task["kind"] == "validate"),
            "Derived scope differs from the actual current task",
        )
        current["visual_scope"] = deepcopy(derived)
    acceptance.binding(current, manifest)
    return current


def _session(record, run):
    raw = _owned(run, record["events"]).read_bytes()
    _require(raw.endswith(b"\n"), "Truncated stage events")
    rows = [json.loads(line) for line in raw.splitlines()]
    sessions = {row.get("sessionID") for row in rows}
    _require(len(sessions) == 1 and all(sessions), "Missing or ambiguous native session")
    session = sessions.pop()
    expected = record.get("expected_session") or record.get("thread_id")
    _require(expected == session and record.get("thread_id", session) == session, "Unexpected native session")
    return session


def _producer(state: RunState, run, snapshot):
    record = next((row for row in reversed(state.get("stages", [])) if row.get("stage") == "terra"), None)
    if record is None:
        goal, task = cast(dict, state.get("goal_contract", {})), state.get("current_task") or {}
        initial = goal.get("body", {}).get("initial_task") or {}
        writers = any(
            str(row.get("stage", "")).startswith("terra") or row.get("role") in ("terra", "builder")
            for row in state.get("stages", [])
        )
        ids = {row["id"] for row in goal.get("body", {}).get("acceptance_criteria", [])}
        _require(
            contract.approved(state)
            and initial.get("kind") == task.get("kind") == "validate"
            and not writers
            and not any(
                state.get(key)
                for key in (
                    "parent_run",
                    "parent_batch",
                    "orchestration_batch",
                    "orchestration_history",
                    "worker_results",
                )
            )
            and snapshot.get("files")
            and ids
            # Canonical acceptance_criteria, with a strict read-only legacy alias (#560).
            and set(task_criteria.ids(task)) == ids
            and set(initial.get("acceptance_criteria", [])) == ids
            and task.get("milestone_id") == initial.get("milestone_id"),
            "Missing current independent Builder provenance or approved operator-supplied validation source",
        )
        return {
            "kind": "operator_supplied_source",
            "model": None,
            "session_id": None,
            "task_id": task["id"],
            "contract_revision": goal["revision"],
            "contract_hash": goal["hash"],
            "source_revision": snapshot["revision"],
            "source_files_sha256": util.digest(snapshot["files"]),
            "approval_event_sha256": util.digest(goal["approval_event"]),
        }
    _require(
        record
        and record.get("role") == "terra"
        and record.get("engine") == "opencode"
        and type(record.get("exit_code")) is int
        and record["exit_code"] == 0
        and record.get("source_revision") == snapshot["revision"]
        and not any(record.get(key) for key in ("rejected", "report_only", "timed_out", "interrupted")),
        "Missing current independent Builder provenance (Builder PASS is not visual authority)",
    )
    _require(
        record.get("task_id") == cast(dict, state["current_task"])["id"]
        or cast(dict, state["current_task"]).get("kind") == "validate",
        "A different implementation task needs its own accepted producer",
    )
    after = _owned(run, record["after_ref"])
    _require(util.read_object(after) == snapshot, "Builder post-write source does not match the validated source")
    model = (record.get("launch_route") or {}).get("model")
    _require(model, "Unknown Builder model route")
    return {
        "kind": "native_builder",
        "model": model,
        "session_id": _session(record, run),
        "producer_task_id": record["task_id"],
        "source_revision": snapshot["revision"],
        "after_ref": str(after),
        "after_sha256": util.file_hash(after),
        "events": str(_owned(run, record["events"])),
        "output": str(_owned(run, record["output"])),
    }


def _write(path, data):
    with path.open("xb") as stream:
        stream.write(data)
    path.chmod(0o400)
    return str(path), util.file_hash(path)


def prepare(
    state: RunState,
    stage,
    workspace,
    run_dir,
    base,
    command,
    env,
    prompt,
    *,
    launch_authority=None,
    current_snapshot=None,
    scope_authority=None,
):
    return _prepare(
        state,
        stage,
        workspace,
        run_dir,
        base,
        command,
        env,
        prompt,
        launch_authority=launch_authority,
        current_snapshot=current_snapshot,
        scope_authority=scope_authority,
    )


def _prepare(
    state: RunState,
    stage,
    workspace,
    run_dir,
    base,
    command,
    env,
    prompt,
    *,
    launch_authority=None,
    current_snapshot=None,
    scope_authority=None,
    staging_directory=None,
    capture_authority=None,
):
    """Return (context, argv, env, prompt); NOT_READY never changes launch inputs.

    scope_authority(state=..., snapshot=...) is separately injected runner CODE.
    For an opted-in contract it must freshly authenticate historical native proof,
    exact prior capture inputs and source deltas, and return the full derived
    scope. No default exists. Stored context or configuration cannot supply it.

    launch_authority is injected runner CODE, not saved/model configuration. It
    receives final command/environment, plugin_path, directory, images, reviewer;
    it must recheck an already-qualified stage configuration (including bootstrap,
    effective global denies, protected tools and the exact provider route), without
    installs or global writes. Return owned evidence_hashes and image_limits
    {width,height,pixels,bytes} for byte-preserving PNG delivery. No default exists.
    A config-less OpenCode plugin can bootstrap its SDK globally, so it is unsafe.
    """
    if stage != "sol":
        return None, command, env, prompt
    if not state.get("settings", {}).get("design_manifest"):
        return (
            (_unknown("Approved visual contract requires its retained design manifest") if requested(state) else None),
            command,
            env,
            prompt,
        )
    constraints = cast(dict, state.get("goal_contract", {})).get("body", {}).get("constraints", [])
    if not any(isinstance(row, str) and row.startswith("VISUAL_CASE_CRITERIA=") for row in constraints):
        return _unknown("Exactly one approved VISUAL_CASE_CRITERIA mapping is required"), command, env, prompt
    try:
        root, run = Path(workspace).resolve(), Path(run_dir).resolve()
        _require(
            root == Path(state["workspace"]).resolve() and run.is_relative_to(root / ".autocode" / "runs"),
            "Visual launch requires the actual owned run",
        )
        snapshot = current_snapshot or source_scope.snapshot(root, state)
        raw = (json.dumps(state["settings"]["design_manifest"]["body"], sort_keys=True, indent=2) + "\n").encode()
        if _has_scope_marker(state):
            current = _current(state, snapshot, hashlib.sha256(raw).hexdigest(), scope_authority=scope_authority)
            selected = _selected_captures(state, snapshot, current)
        else:
            # Preserve the legacy all-case selection and failure behavior.
            selected = evidence.context(state, snapshot)
            _require(
                selected and selected["current"] and not selected["unavailable_cases"],
                "Missing CURRENT captures for declared cases; run declared validate preflight capture commands",
            )
            current = _current(state, snapshot, hashlib.sha256(raw).hexdigest())
        capture_pins = {}
        if staging_directory is not None:
            _require(callable(capture_authority), "Missing original native capture authority")
            captures = {
                row["case"]["id"]: {"capture_ref": row["capture_ref"], "capture_sha256": row["capture_sha256"]}
                for row in selected["current"]
            }
            capture_pins = capture_authority(state=state, snapshot=deepcopy(snapshot), captures=captures)
            _pins(run, capture_pins)
        _require(
            callable(launch_authority), "No prequalified stage-config/bootstrap authority; preparation hook pending"
        )
        _require(
            "--model" in command
            and command[command.index("--model") + 1] == current["reviewer"]["model"]
            and "--session" not in command
            and "--file" not in command
            and "-f" not in command,
            "Visual review requires the saved Sol route and fresh parent CLI file attachments",
        )
        producer = _producer(state, run, snapshot)
        _require(producer["model"] != current["reviewer"]["model"], "Builder and Validator models are not independent")
        base = Path(base)
        _require(
            base.is_absolute()
            and base.parent.resolve().is_relative_to(run)
            and not any(p.is_symlink() for p in (base, *base.parents) if p.is_relative_to(root)),
            "Visual stage base must be owned and nonsymlinked",
        )
        directory = (
            base.parent.resolve() / (base.name + ".visual-" + uuid.uuid4().hex)
            if staging_directory is None
            else Path(staging_directory)
        )
        _require(
            directory.is_absolute()
            and directory.is_relative_to(run)
            and ".." not in directory.parts
            and directory.parent.is_dir()
            and not any(p.is_symlink() for p in (directory, *directory.parents) if p.is_relative_to(root)),
            "Visual staging directory must be fresh, owned and nonsymlinked",
        )
        directory.mkdir(mode=0o700)
        pins = dict([_write(directory / "manifest.json", raw)])
        pins.update(capture_pins)
        transport, config_key = _delivery_backend(state)
        plugin = directory / "image-delivery.mjs"
        pins.update([_write(plugin, Path(transport.__file__).with_suffix(".mjs").read_bytes())])
        images, captures = [], {row["case"]["id"]: row for row in selected["current"]}
        if "visual_scope" in current:
            _seal_all_references(state, pins)
        for index, case in enumerate(_required_cases(state, current)):
            item = captures[case["id"]]
            capture, refs = evidence.verify(
                state, item["capture_ref"], item["capture_sha256"], case=case, current=snapshot
            )
            paths = [
                (
                    "reference",
                    Path(state["settings"]["design_manifest"]["root"]) / case["artifacts"]["screenshot"]["path"],
                ),
                ("candidate", root / capture["artifacts"]["candidate"]["path"]),
            ]
            for kind, source in paths:
                source = _owned(root, source)
                target = directory / f"{index}-{kind}.png"
                pins.update([_write(target, source.read_bytes())])
                images.append(
                    {
                        "case_id": case["id"],
                        "kind": kind,
                        "path": str(target),
                        "sha256": pins[str(target)],
                        "mime": "image/png",
                        "width": design.png_dimensions(target)[0],
                        "height": design.png_dimensions(target)[1],
                        "bytes": target.stat().st_size,
                    }
                )
            refs += [str(path) for _, path in paths]
            refs += [
                str(Path(state["settings"]["design_manifest"]["root"]) / a["path"]) for a in case["artifacts"].values()
            ]
            pins.update({str(_owned(root, ref)): util.file_hash(_owned(root, ref)) for ref in refs})
        if producer["kind"] == "native_builder":
            pins.update({producer[key]: util.file_hash(producer[key]) for key in ("events", "output", "after_ref")})
        bound = acceptance.binding(current, state["settings"]["design_manifest"])
        attempt = uuid.uuid4().hex
        audit = directory / "delivery.jsonl"
        child = dict(env)
        config = json.loads(child.get(config_key, "{}"))
        if config_key == "KILO_CONFIG_CONTENT":
            _require(staging_directory is not None, "Kilo source audit needs its original native owner")
            config["plugin"] = [plugin.as_uri()]
        else:
            config.setdefault("plugin", []).append(plugin.as_uri())
        child[config_key] = json.dumps(config)
        _require("--agent" in command, "Visual delivery requires the actual named reviewer agent")
        child["AUTOCODE_IMAGE_AUDIT"] = json.dumps(
            {
                "path": str(audit),
                "attempt_id": attempt,
                "binding_sha256": util.digest(bound),
                "reviewer": {**current["reviewer"], "agent": command[command.index("--agent") + 1]},
                **({"plugin_sha256": pins[str(plugin)]} if config_key == "KILO_CONFIG_CONTENT" else {}),
            }
        )
        argv = [*command, *(value for row in images for value in ("--file", row["path"]))]
        qualified = launch_authority(
            command=deepcopy(argv),
            env=dict(child),
            plugin_path=plugin,
            directory=directory,
            images=deepcopy(images),
            reviewer=deepcopy(current["reviewer"]),
        )
        _require(isinstance(qualified, dict), "No qualified stage-local image transport")
        _require(qualified.get("command", argv) == argv, "Visual authority may not reroute the model or image inputs")
        child = qualified.get("environment", child)
        _require(
            isinstance(child, dict)
            and all(isinstance(key, str) and isinstance(value, str) for key, value in child.items()),
            "Invalid visual child environment",
        )
        config = json.loads(child[config_key])
        audit_options = json.loads(child["AUTOCODE_IMAGE_AUDIT"])
        _require(
            type(audit_options.get("max_requests")) is int
            and 0 < audit_options["max_requests"] <= delivery.SESSION_MAX_REQUESTS,
            "Visual authority must supply an approved bounded session request cap",
        )
        _pins(run, qualified["evidence_hashes"])
        limits = qualified["image_limits"]
        _require(
            all(type(limits[key]) is int and limits[key] > 0 for key in ("width", "height", "pixels", "bytes")),
            "No qualified byte-preserving image bounds",
        )
        _require(
            all(
                row["width"] <= limits["width"]
                and row["height"] <= limits["height"]
                and row["width"] * row["height"] <= limits["pixels"]
                and row["bytes"] <= limits["bytes"]
                for row in images
            ),
            "Image exceeds qualified no-transform bounds; resized originals are NOT_VERIFIED",
        )
        pins.update(qualified["evidence_hashes"])
        context = {
            "status": "READY" if staging_directory is None else "STAGED",
            "current": current,
            "snapshot": snapshot,
            "producer": producer,
            "captures": captures,
            "images": images,
            "manifest_path": str(directory / "manifest.json"),
            "plugin_path": str(plugin),
            "plugin_sha256": pins[str(plugin)],
            "audit_path": str(audit),
            "attempt_id": attempt,
            "pins": pins,
            "command": argv,
            "config_sha256": util.digest(config),
            "environment_sha256": util.digest(child),
            "audit_options": audit_options,
            "child_identity": qualified.get("child_identity"),
        }
        path, sha = _write(directory / "launch.json", (json.dumps(context, sort_keys=True) + "\n").encode())
        context.update(launch_manifest=path, launch_sha256=sha)
        packet = {"images": images, "captures": captures, "case_criteria": current["case_criteria"]}
        if "visual_scope" in current:
            packet["visual_scope"] = deepcopy(current["visual_scope"])
        note = (
            "\nVISUAL REVIEW: parent CLI attachments are ordered reference then candidate for each case. "
            "Inspect BOTH images independently. Hashes/capture success are provenance, never a visual PASS. "
            "Report every declared case and exact criterion_ids; unavailable inspection is NOT_VERIFIED.\n"
            + (
                "Outside required_case_ids report NOT_VERIFIED with empty capture_ref, capture_sha256, "
                "candidate_ref and comparison_ref; do not claim inspection.\n"
                if "visual_scope" in current
                else ""
            )
            + json.dumps(packet)
        )
        marker = "\nCURRENT HANDOFF DATA\n"
        before, separator, after = prompt.rpartition(marker)
        if separator:
            handoff = json.loads(after)
            handoff["visual_review"] = packet
            prompt = before + note + marker + json.dumps(handoff, indent=2)
        else:
            prompt += note
        return context, argv, child, prompt
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
        return _unknown(error), command, env, prompt


def _prepared(context, state: RunState, run, snapshot, *, scope_authority=None):
    _require(
        context and context.get("status") == "READY", "Visual stage was not prepared with qualified image delivery"
    )
    _require(run.is_relative_to(Path(state["workspace"]).resolve() / ".autocode" / "runs"), "Foreign visual run")
    path = _owned(run, context["launch_manifest"])
    _require(util.file_hash(path) == context["launch_sha256"], "Visual prelaunch manifest changed")
    saved = util.read_object(path)
    _require(
        saved == {key: value for key, value in context.items() if key not in ("launch_manifest", "launch_sha256")},
        "Parent-pinned visual launch fields changed",
    )
    _pins(state["workspace"], context["pins"])
    _require(
        _current(state, snapshot, context["current"]["manifest_file_sha256"], scope_authority=scope_authority)
        == context["current"],
        "Visual source, contract, task, route, criteria or runtime changed",
    )
    _require(_producer(state, run, snapshot) == context["producer"], "Builder provenance changed")
    required = _required_cases(state, context["current"])
    if "visual_scope" in context["current"]:
        _require(
            set(context["captures"]) == {case["id"] for case in required},
            "Prepared captures differ from the freshly authenticated scope",
        )
    for case in required:
        row = context["captures"][case["id"]]
        evidence.verify(state, row["capture_ref"], row["capture_sha256"], case=case, current=snapshot)
    return saved


def verify_prelaunch(context, state: RunState, *, run_dir, current_snapshot, command, env, scope_authority=None):
    """Controller calls under its admission lock immediately before Popen."""
    if context is None and not _has_scope_marker(state):
        return
    try:
        _prepared(context, state, Path(run_dir).resolve(), current_snapshot, scope_authority=scope_authority)
        _require(
            command == context["command"]
            and util.digest(env) == context["environment_sha256"]
            and util.digest(json.loads(env[_delivery_backend(state)[1]])) == context["config_sha256"]
            and json.loads(env["AUTOCODE_IMAGE_AUDIT"]) == context["audit_options"],
            "Visual launch configuration changed",
        )
        identity = context.get("child_identity")
        if identity is not None:
            executable = shutil.which(command[0], path=env.get("PATH", ""))
            _require(
                executable
                and str(Path(executable).resolve()) == identity["executable"]
                and util.file_hash(executable) == identity["executable_sha256"]
                and util.digest(command) == identity["command_sha256"]
                and util.digest(env) == identity["environment_sha256"],
                "Qualified visual executable changed",
            )
        _require(not Path(context["audit_path"]).exists(), "Visual audit path was already used")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", str(error)) from error


def _record_identity(record):
    return history_proof.record_identity(record)


def _stage(record, current, *, state: RunState, run, snapshot, accepted_validation, context):
    _require(
        record.get("stage") == record.get("role") == "sol"
        and record.get("route_role", "sol") == "sol"
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
        "Only a successful original actual Sol Validator can accept visual evidence",
    )
    route = record["launch_route"]
    _require(
        route["model"] == current["reviewer"]["model"]
        and (route.get("provider") or record["engine"]) == current["reviewer"]["provider"],
        "Actual reviewer route differs from the saved route",
    )
    seal = record["rework_evidence"]
    _require(seal["version"] == 1 and Path(seal["run_dir"]).resolve() == run, "Missing existing accepted report seal")
    for key in ("stage", "task_id", "contract_hash", "contract_revision", "source_revision"):
        _require(record.get(key) == seal["binding"][key], "Sealed stage provenance changed: " + key)
    _pins(run, seal["hashes"])
    for key in ("events", "output", "reported_output"):
        if record.get(key):
            path = str(_owned(run, record[key]))
            _require(seal["paths"].get(key) == path and path in seal["hashes"], "Unsealed stage artifact: " + key)
    report = util.read_object(_owned(run, record["output"]))
    _require(
        util.digest(report) == seal["report_digest"]
        and isinstance(accepted_validation, dict)
        and all(accepted_validation.get(key) == value for key, value in report.items()),
        "Missing caller-accepted functional validation or report changed after acceptance",
    )
    _require(
        accepted_validation.get("source_revision") == snapshot["revision"]
        and accepted_validation.get("output") == record["output"],
        "Functional validation is not current",
    )
    _pins(state["workspace"], accepted_validation["evidence_hashes"])
    for key in ("before_ref", "after_ref"):
        observed = util.read_object(_owned(run, record[key]))
        _require(observed == snapshot == context["snapshot"], "Full read-only source gate failed")
    session = _session(record, run)
    producer = context["producer"]
    _require(session != producer["session_id"] and route["model"] != producer["model"], "Builder cannot review itself")
    return {
        "stage": "sol",
        "accepted": True,
        "read_only": True,
        "independent": True,
        "source_full_gate": True,
        **current["reviewer"],
        "session_id": session,
        "producer_session_id": producer["session_id"],
        "producer_model": producer["model"],
        "runtime_hash": current["runtime_hash"],
        "finished_at": record["finished_at"],
        "producer_kind": producer["kind"],
        "operator_source": producer if producer["kind"] == "operator_supplied_source" else None,
        "record_sha256": util.digest(_record_identity(record)),
    }


def accept_review(
    runtime,
    state: RunState,
    record,
    *,
    run_dir,
    current_snapshot,
    accepted_validation=None,
    scope_authority=None,
    native_history=None,
):
    """Call inside apply_review_result's transaction AFTER all functional gates.

    accepted_validation is its freshly checked validation object, never a model
    report or a persisted state hint. runtime is reserved for the controller's
    calling convention; no private controller calls or supplied delivery override.
    NOT_READY leaves the functional report intact without minting acceptance.
    Functional FAIL/BLOCKED remains available to the ordinary rework path; it
    cannot acquire visual authority. Attempted READY acceptance still pauses on
    any failed visual gate, rather than entering report-format repair.
    """
    if record.get("stage") != "sol":
        return None
    context = record.get("visual_runtime")
    scoped = _has_scope_marker(state)
    if scoped:
        try:
            _require(
                state.get("settings", {}).get("design_manifest"), "Missing retained manifest for visual slice gates"
            )
            raw = (json.dumps(state["settings"]["design_manifest"]["body"], sort_keys=True, indent=2) + "\n").encode()
            _current(state, current_snapshot, hashlib.sha256(raw).hexdigest(), scope_authority=scope_authority)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", str(error)) from error
    if isinstance(context, dict) and context.get("status") == "NOT_READY":
        if scoped and not (
            isinstance(accepted_validation, dict) and accepted_validation.get("verdict") in ("FAIL", "BLOCKED")
        ):
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", context.get("reason", "Missing current scoped visual proof"))
        return None
    if isinstance(accepted_validation, dict) and accepted_validation.get("verdict") in ("FAIL", "BLOCKED"):
        if scoped:
            _require(
                type(native_history) is history_proof.NativeReviewHistory,
                "nonpassing native evidence requires its concrete original history",
            )
            native_history.retain_rejection(
                state, record, context=context, accepted_validation=accepted_validation, snapshot=current_snapshot
            )
        return None
    if not state.get("settings", {}).get("design_manifest"):
        if requested(state):
            raise util.Paused(
                "PAUSED_VISUAL_EVIDENCE", "Approved visual contract requires its retained design manifest"
            )
        return None
    try:
        run = Path(run_dir).resolve()
        _prepared(context, state, run, current_snapshot, scope_authority=scope_authority)
        current = context["current"]
        auxiliary = {
            **context["pins"],
            context["launch_manifest"]: context["launch_sha256"],
            **record["rework_evidence"]["hashes"],
            **accepted_validation["evidence_hashes"],
        }
        if context.get("native_isolation", {}).get("backend") == "kilocode":
            try:
                from . import autocode_kilo_source_relay as original_source
            except ImportError:
                import autocode_kilo_source_relay as original_source
            auxiliary.update(original_source.closed_source_evidence(record))
        auxiliary.update(
            {
                str(_owned(run, record[key])): util.file_hash(_owned(run, record[key]))
                for key in ("before_ref", "after_ref")
            }
        )

        def capture(ref, sha, *, case, current):
            selected = context["captures"][case["id"]]
            _require(
                ref == selected["capture_ref"] and sha == selected["capture_sha256"],
                "Report changed selected current capture",
            )
            body, refs = evidence.verify(state, ref, sha, case=case, current=current_snapshot)
            _pins(state["workspace"], auxiliary)
            return body, list(dict.fromkeys([*refs, *auxiliary]))

        stage = partial(
            _stage,
            state=state,
            run=run,
            snapshot=current_snapshot,
            accepted_validation=accepted_validation,
            context=context,
        )
        # Receipt metadata is parent-pinned, never taken from report fields.
        transport = partial(
            _delivery_backend(state)[0].verify,
            audit_path=context["audit_path"],
            attempt_id=context["attempt_id"],
            plugin_sha256=context["plugin_sha256"],
        )
        candidate = deepcopy(state)
        receipt = acceptance.accept(
            candidate,
            record,
            run_dir=run,
            current=current,
            manifest_path=context["manifest_path"],
            verify_stage=stage,
            verify_capture=capture,
            verify_delivery=transport,
        )
        _require(receipt is not None, "No authenticated image delivery; visual acceptance remains NOT_VERIFIED")
        _pins(state["workspace"], auxiliary)
        if scoped:
            _require(
                type(native_history) is history_proof.NativeReviewHistory,
                "Missing concrete original native-history retention/protection owner",
            )
            # Still inside the original accepted-review transaction. The single
            # acceptance ledger is published only after protected retention and
            # concrete native/capture/delivery replay succeed.
            native_history.retain_review(
                state,
                record,
                receipt,
                context=context,
                accepted_validation=accepted_validation,
                snapshot=current_snapshot,
            )
            _pins(state["workspace"], auxiliary)
        state["visual_acceptance_receipts"] = candidate["visual_acceptance_receipts"]
        return receipt
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", str(error)) from error


def projection(state: RunState, *, current_snapshot, scope_authority=None):
    """Recheck current authority, without trusting model/stored summary fields."""
    if not state.get("settings", {}).get("design_manifest"):
        return _unknown("Approved visual contract requires its retained design manifest") if requested(state) else None
    # Lock-free callers (public --status) have no runner CODE callback. Report
    # unknown instead of failing closed as NOT_READY; prepare/accept still require it.
    if _has_scope_marker(state) and not callable(scope_authority):
        return _authority_unknown(SCOPE_AUTHORITY_UNAVAILABLE)
    try:
        manifest = state["settings"]["design_manifest"]
        raw = (json.dumps(manifest["body"], sort_keys=True, indent=2) + "\n").encode()
        current = _current(state, current_snapshot, hashlib.sha256(raw).hexdigest(), scope_authority=scope_authority)
        if "visual_scope" in current:
            selected = _selected_captures(state, current_snapshot, current)
        else:
            selected = evidence.context(state, current_snapshot)
            _require(
                selected and selected["current"] and not selected["unavailable_cases"],
                "Missing CURRENT implementation captures",
            )
        receipts: list = state.get("visual_acceptance_receipts", [])
        hashes, eligible = {}, []
        for receipt in receipts:
            matching = [
                record
                for record in state.get("stages", [])
                if util.digest(_record_identity(record)) == receipt.get("reviewer", {}).get("record_sha256")
            ]
            if len(matching) != 1:
                continue
            record = matching[0]
            try:
                run = Path(record["rework_evidence"]["run_dir"]).resolve()
                _prepared(record.get("visual_runtime"), state, run, current_snapshot, scope_authority=scope_authority)
                _require(_session(record, run) == receipt["reviewer"]["session_id"], "Reviewer session changed")
            except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
                continue
            eligible.append(receipt)
            for path in receipt.get("evidence_hashes", {}):
                with contextlib.suppress(ValueError, OSError):
                    hashes[path] = util.file_hash(_owned(state["workspace"], path))
        result = acceptance.summary(
            eligible,
            current=current,
            manifest=manifest,
            evidence_hashes=hashes,
            verified_capture_hashes=[row["capture_sha256"] for row in selected["current"]],
        )
        history = acceptance.summary(receipts, current=current, manifest=manifest)
        for row, old in zip(result["cases"], history["cases"], strict=False):
            row["historical_accepted_at"] = old["historical_accepted_at"]
        if "visual_scope" in current:
            return {
                **result,
                "status": (
                    "VERIFIED"
                    if result["current_all_accepted"]
                    else "SCOPED_VERIFIED"
                    if result["scope_all_accepted"]
                    else "NOT_VERIFIED"
                ),
            }
        return {**result, "status": "VERIFIED" if result["coverage_complete"] else "NOT_VERIFIED"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
        return _unknown(error)


def completion_check(state: RunState, *, current_snapshot, scope_authority=None):
    """An explicit strict-design gate; never changes TaskRun.done by itself.

    ``passed`` is True/False when authenticated, or None when scoped acceptance
    cannot be judged without runner CODE (lock-free public status).
    """
    result = projection(state, current_snapshot=current_snapshot, scope_authority=scope_authority)
    if result is not None and result.get("status") == "UNKNOWN" and result.get("current_all_accepted") is None:
        return {
            "required": True,
            "passed": None,
            "reason": result.get("reason", SCOPE_AUTHORITY_UNAVAILABLE),
        }
    return {
        "required": result is not None,
        "passed": result is None or result["current_all_accepted"] is True,
        "reason": None
        if result is None or result["current_all_accepted"]
        else result.get("reason", "Current independent visual acceptance is incomplete"),
    }


def completion_allowed(state: RunState, *, current_snapshot, scope_authority=None):
    """Legacy functional completion is not proof of an opt-in visual contract."""
    if not requested(state):
        return True
    # Unknown (no scope authority) is not permission to complete; only True is.
    return completion_check(state, current_snapshot=current_snapshot, scope_authority=scope_authority)["passed"] is True


def require_completion(state: RunState, *, current_snapshot, scope_authority=None):
    if not completion_allowed(state, current_snapshot=current_snapshot, scope_authority=scope_authority):
        raise util.Paused(
            "PAUSED_VISUAL_EVIDENCE", "Current independent image-delivery and visual-acceptance evidence is required"
        )
