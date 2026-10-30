"""Immutable incident packets and stage-boundary novelty admission.

Only nested recovery_packet/recovery_change and diagnosis bindings on existing
requests, plans and stage records are written here. Dispatch receipts live on
stages.recovery_novelty, so restart, task reassignment and session rotation cannot create a second history.
There is no controller, process termination, model call, or acceptance shortcut.
"""

from __future__ import annotations

try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope


import base64
import contextlib
import copy
import re
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_builder_failure as builder_failure
    from . import autocode_builder_policy as builder_policy
    from . import autocode_check_refs as check_refs
    from . import autocode_cost as cost
    from . import autocode_efficiency as efficiency
    from . import autocode_failure_classification as classification
    from . import autocode_failures as failures
    from . import autocode_process as processes
    from . import autocode_recovery_inputs as inputs
    from . import autocode_recovery_limits as recovery_limits
    from . import autocode_recovery_novelty as novelty
    from . import autocode_resolver_retry_route as retry_route
    from . import autocode_retained_work as retained
    from . import autocode_rework_policy as rework
    from . import autocode_role_names as role_names
    from . import autocode_route_fit as route_fit
    from . import autocode_stop as stop
    from . import autocode_support as support
    from . import autocode_time_left as time_left
    from . import autocode_tool_containment as containment
    from . import autocode_util as util
except ImportError:
    import autocode_builder_failure as builder_failure
    import autocode_builder_policy as builder_policy
    import autocode_check_refs as check_refs
    import autocode_cost as cost
    import autocode_efficiency as efficiency
    import autocode_failure_classification as classification
    import autocode_failures as failures
    import autocode_process as processes
    import autocode_recovery_inputs as inputs
    import autocode_recovery_limits as recovery_limits
    import autocode_recovery_novelty as novelty
    import autocode_resolver_retry_route as retry_route
    import autocode_retained_work as retained
    import autocode_rework_policy as rework
    import autocode_role_names as role_names
    import autocode_route_fit as route_fit
    import autocode_stop as stop
    import autocode_support as support
    import autocode_time_left as time_left
    import autocode_tool_containment as containment
    import autocode_util as util


def _stale(reason):
    raise util.Paused("PAUSED_STALE_HANDOFF", "Recovery packet: " + reason)


def _read(path):
    try:
        value = util.read(path)
        if not isinstance(value, dict):
            _stale("original artifact is not an object")
        return value
    except (OSError, ValueError, TypeError) as error:
        _stale(f"original artifact is unavailable or malformed: {error}")


def _hash(path):
    try:
        return util.file_hash(path)
    except OSError as error:
        _stale(f"original pinned artifact is unavailable: {error}")


def _owned(path, root):
    path, root = Path(path).absolute(), Path(root).resolve()
    # Check the lexical path as well as resolution: symlinks cannot import evidence.
    if not path.resolve().is_relative_to(root):
        _stale("artifact is outside its owned run or workspace")
    for parent in (path, *path.parents):
        if parent.resolve() == root:
            break
        if parent.is_symlink():
            _stale("symlink in an evidence path")
    return path.resolve()


def _run_root(state: RunState, record):
    if state.get("run_dir"):
        return Path(state["run_dir"]).resolve()
    output = Path(record["output"]).resolve()
    for parent in output.parents:
        if parent.name == "iterations":
            return parent.parent
    # Existing API callers can have a flat, explicitly owned run directory.
    return output.parent


def _artifact_owned(path, workspace, run, state: RunState):
    path = _owned(path, workspace)
    # The rule acceptance and Completion routing apply. Pins here come from several stages, so a
    # contained stage's capture is owned through any launch record of this run (#419).
    if not rework.owned(
        path,
        workspace,
        run,
        settings=state.get("settings"),
        scratch=containment.recorded_scratch(state.get("stages", []), workspace),
    ):
        _stale("evidence belongs to another run")
    return path


def _wrappers(state: RunState, record, run):
    rows = [(str(Path(state["workspace"]).resolve()), "<workspace>"), (str(run), "<run>")]
    for key in ("output", "events", "reported_output", "started_at", "finished_at", "session_id", "expected_session"):
        if record.get(key):
            rows.append((str(record[key]), "<" + key + ">"))
    return [list(row) for row in rows]


# These ceilings decide whether work may launch, not what source/evidence it
# acts on. The controller still enforces them; changing one grants no novelty.
_EARLIER_CEILINGS = {
    "limits": ("iteration_ceiling", "max_seconds", "max_cost_usd"),
    "budget_origins": ("iteration_ceiling", "max_seconds", "max_cost_usd"),
}
# The milestone budget (--max-milestone-seconds) is one too (#611): it gates a writer launch
# (autocode_milestones.dispatch_guard, autocode_climb_cap). Its stops advise resuming with a new one,
# and AutoResolver may extend a default one; bound, either made the held repair a stale handoff.
# The other milestone checkpoint settings stay bound.
_CEILINGS = {
    **_EARLIER_CEILINGS,
    "budget_origins": (*_EARLIER_CEILINGS["budget_origins"], "milestone_max_seconds"),
    "milestone_checkpoints": ("max_seconds",),
}


def _bound_settings(state: RunState, ceilings, *, derived=False):
    # A model a person named at a quota stop (#184) is bound as the route it replaced,
    # and the Resolver's own climb (#1345) as the rung it left.
    settings = retry_route.unassigned(state, state.get("settings", {}))
    # The launch-boundary fit and its probe evidence describe how an effort was derived, not the
    # source, task or scope a held recovery acts on: another role's first launch must not stale it.
    if not derived:  # a binding saved before #2057 hashed these with the other settings
        for key in route_fit.DERIVED:
            settings.pop(key, None)
    for section, keys in ceilings.items():
        for key in keys:
            if section in settings:
                settings[section].pop(key, None)
        if not settings.get(section):
            settings.pop(section, None)
    roles = settings.get("roles") or {}
    if "astra" in roles:
        roles.setdefault("resolver", copy.deepcopy(roles["astra"]))
    # Investigator preparation may initialize this empty registry. No transport
    # identity has changed; any actual entry remains strictly bound below.
    if settings.get("transport_identities") == {}:
        settings.pop("transport_identities")
    return settings


def _binding(state: RunState, revision):
    contract = state.get("goal_contract") or {}
    return {
        "contract_hash": contract.get("hash"),
        "contract_revision": contract.get("revision"),
        "task_id": (state.get("current_task") or {}).get("id"),
        "source_revision": revision,
        "settings_hash": util.digest(_bound_settings(state, _CEILINGS)),
    }


def _holds(saved, state: RunState, revision):
    """Whether ``saved``, a binding recorded earlier, still holds for ``state`` at ``revision``.

    One saved before #611 bound the milestone budget with the other settings. It still holds while
    they are unchanged when hashed that way, so a run upgraded with a recovery packet keeps it; for
    that binding a changed milestone budget remains a changed setting. A binding recorded after the
    upgrade, such as the repair admission ``finish_resolution_packet`` writes for that packet, leaves
    the budget out. A binding saved before #2057 also hashed the fit's derived evidence (#2057).
    """
    current = _binding(state, revision)
    return saved == current or saved == {
        **current,
        "settings_hash": util.digest(_bound_settings(state, _EARLIER_CEILINGS, derived=True)),
    }


def _scope(state: RunState):
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    return {
        "task_id": contract.get("task_id"),
        "contract_hash": contract.get("hash"),
        "milestones": sorted(task.get("milestone_ids") or [task.get("milestone_id") or ""]),
        "criteria": sorted(task.get("acceptance_criteria") or []),
    }


def retain_diagnosis(state: RunState, record):
    """Bind one accepted diagnosis to the settings and source it actually used.

    This is the sole writer of resolution_request.diagnosis_binding. The
    controller's queue_resolution reads it through diagnosis_current before
    reusing a diagnosis. Existing binding normalization keeps budget ceilings,
    named quota routes, the Resolver's own climbs and automatic Resolver setup
    from buying another call.
    """
    request = cast(dict, state["resolution_request"])
    request["diagnosis_output"] = record["output"]
    request["diagnosis_binding"] = _binding(state, request["source_revision"])


def diagnosis_current(state: RunState, request, revision):
    """Reuse only this task/source/settings; unknown legacy settings remain held.

    Absent or malformed bindings cannot prove settings changed, so they never
    grant another diagnosis. No current value is written into old evidence.
    """
    if (
        request.get("task_id") != (state.get("current_task") or {}).get("id")
        or request.get("source_revision") != revision
        or not request.get("diagnosis_output")
    ):
        return False
    bound = request.get("diagnosis_binding")
    current = _binding(state, request["source_revision"])
    if (
        not isinstance(bound, dict)
        or set(bound) != set(current)
        or any(type(value) is not type(current[key]) or value in ("", None) for key, value in bound.items())
    ):
        return True
    return _holds(bound, state, request["source_revision"])


def _source_revision(state: RunState, stage, pointer, packet, workspace):
    """The source revision a dispatch is bound at: the current one, or for a Builder the packet's own
    when every file holds its bound content or what this packet's latest Builder attempt left, at an
    unmoved HEAD (retained_work.own_repair_source says exactly what it admits). Anything else binds
    the current revision, so other content, such as a person's new edit or a further change after
    the last attempt, remains a stale handoff."""
    current, bound = source_scope.snapshot(workspace, state), packet["binding"]["source_revision"]
    if (
        stage == "terra"
        and current["revision"] != bound
        and retained.own_repair_source(state.get("stages", []), pointer, bound, current)
    ):
        return bound
    return current["revision"]


def _narrows(failed, scope, decided=()):
    """A repair may keep fewer of the failed task's criteria; it never adds one or moves. A criterion
    the reviewed decision assigned (`decided`) is not an addition: an earlier repair may have narrowed
    it away, and the Completion Reviewer may name any criterion of the approved milestone again."""
    same = all(scope[key] == failed[key] for key in ("task_id", "contract_hash", "milestones"))
    allowed = set(failed["criteria"]) | set(decided)
    return scope == failed or (same and bool(scope["criteria"]) and set(scope["criteria"]) <= allowed)


def _decided(packet):
    """The criteria the packet's sealed decision assigns to its next task, within that task's approved
    milestone when it is the failed task's own. The Resolver cannot change either: both are hashed."""
    task = (packet.get("current_error") or {}).get("next_task") or {}
    milestone = next(
        (
            row
            for row in (packet.get("protected_obligations") or {}).get("milestones", [])
            if isinstance(row, dict) and row.get("id") == task.get("milestone_id")
        ),
        None,
    )
    if milestone is None or [task.get("milestone_id")] != packet["scope"]["milestones"]:
        return set()
    return set(task.get("acceptance_criteria") or []) & set(milestone.get("acceptance_criteria") or [])


def _returned_nothing(row):
    """Automatic recovery archived this stopped attempt because it ended without a completed
    turn (a timeout, capacity or startup failure, a denied path). Each of those routes checks
    the attempt's log or output for that before it accounts the attempt and archives it as
    abandoned and rejected, so it returned no report. A truncated review report came back cut
    short, so it still counts."""
    return all(row.get(key) for key in ("accounted", "automatic_recovery", "abandoned", "rejected")) and not row.get(
        "truncated_output"
    )


def receipts(state: RunState, *, returned=False):
    """Dispatch receipts. With ``returned``, only those of attempts that could have returned a
    result: relaunching one that returned nothing repeats no experiment (#422), and each of those
    recovery routes keeps its own bound. Any dispatch still spends an operator's grant
    (``_explicit_grant`` checks it against every receipt)."""
    rows = [*state.get("stages", [])]
    if state.get("active_stage"):
        rows.append(state["active_stage"])
    found = {}
    for row in rows:
        receipt = row.get("recovery_novelty")
        if (
            receipt
            and not row.get("dry_run")
            and not row.get("report_only")
            and not (returned and _returned_nothing(row))
        ):
            found[receipt["dispatch_id"]] = receipt
    return list(found.values())


def _archive(path, run, expected=None):
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        _stale(f"original evidence disappeared during capture: {error}")
    digest = sha256(raw).hexdigest()
    if expected is not None and digest != expected:
        _stale("original evidence changed before capture")
    target = _owned(run / "resolver" / "recovery" / "originals" / (digest + ".json"), run)
    blob = {"sha256": digest, "base64": base64.b64encode(raw).decode("ascii")}
    if target.exists():
        if util.read(target) != blob:
            _stale("retained original was changed; never overwrite it")
    else:
        util.atomic_json(target, blob)
    return {"original_path": str(path), "sha256": digest, "path": str(target), "archive_sha256": util.file_hash(target)}


def _failed_output(validation, check, stages):
    record = next((row for row in reversed(stages) if row.get("output") == validation.get("output")), {})
    ref = check.get("evidence_ref", "")
    if record.get("events") and ref.startswith("event:"):
        for event in support.events(record["events"]):
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("id") == ref[6:]:
                return str(item.get("aggregated_output") or item.get("output") or "")
    return str(check.get("error") or check.get("summary") or "")


def _incidents(state: RunState, decision, record, wrappers):
    validation = state.get("validation") or {}
    task = state.get("current_task") or {}
    # Historical validation is not the cause of a different current assignment.
    if validation.get("task_id") not in (None, task.get("id")):
        validation = {}
    criteria = _scope(state)["criteria"]
    invariant = util.digest(
        [
            row
            for row in cast(dict, state.get("goal_contract", {})).get("body", {}).get("acceptance_criteria", [])
            if row.get("id") in criteria
        ]
    )
    affected = util.digest(_scope(state))
    request = decision.get("user_request") or {}
    failure = (state.get("failure_history") or {}).get(record.get("failure_key"), {})
    status = (failure.get("identity") or {}).get("error_class")
    if record.get("timed_out"):
        status = "PAUSED_PROVIDER_TIMEOUT"
    elif decision.get("operational_diagnosis"):
        status = "PAUSED_INVALID_OUTPUT"
    cause = novelty.classify(user_request=request, status=status)
    result = []
    for check in validation.get("checks", []):
        if type(check.get("exit_code")) is not int or check["exit_code"] == 0:
            continue
        output = _failed_output(validation, check, state.get("stages", []))
        # Test harness timing is a proven volatile wrapper, not application timestamps.
        output = re.sub(r"(?m)^Ran (\d+) tests? in [0-9.]+s$", r"Ran \1 tests in <duration>", output)
        failure = output or f"exit_code={check['exit_code']}"
        command = check.get("command") or "validation"
        item = novelty.Incident(
            novelty.normalize(command, wrappers),
            novelty.failure_text(failure, command, wrappers),
            invariant,
            affected,
            cause,
        )
        result.append(asdict(item))
    if not result:
        findings = decision.get("findings") or validation.get("findings") or []
        for finding in findings:
            text = finding.get("finding") or finding.get("evidence")
            if text:
                result.append(
                    asdict(
                        novelty.Incident(
                            record.get("stage") or "review",
                            novelty.normalize(text, wrappers),
                            invariant,
                            affected,
                            cause,
                        )
                    )
                )
    if not result:
        result.append(
            asdict(
                novelty.Incident(
                    record.get("stage") or "review",
                    novelty.normalize(
                        decision.get("summary")
                        or decision.get("blocker")
                        or decision.get("next_objective")
                        or "Unresolved review",
                        wrappers,
                    ),
                    invariant,
                    affected,
                    cause,
                )
            )
        )
    return result


def prepare_resolution(state: RunState, decision, record):
    """Capture after the existing queue's authoritative scope/evidence checks."""
    request = cast(dict, state["resolution_request"])
    if request.get("recovery_packet"):
        load_packet(request["recovery_packet"], _run_root(state, record))
        return
    # This serial policy cannot attest an integrated worker graph. Keep the
    # already-validated Resolver handoff and its existing finite bounds, without
    # opening worker files or treating a foreign artifact as serial evidence.
    if (state.get("current_task") or {}).get("milestone_ids") or any(
        state.get(key) for key in ("parent_run", "parent_batch", "orchestration_batch", "orchestration_history")
    ):
        request["recovery_novelty_skipped"] = "parallel_or_integrated_scope_uses_existing_resolver_bounds"
        return
    run, workspace = _run_root(state, record), Path(state["workspace"]).resolve()
    current = source_scope.snapshot(workspace, state)
    if current["revision"] != request["source_revision"]:
        _stale("source changed during incident capture")
    if "recovery_packet" in record:
        prior = load_packet(record["recovery_packet"], run)
        if not _holds(prior["binding"], state, current["revision"]) or prior["current_error"] != decision:
            _stale("saved incident draft cannot be repinned to changed inputs")
        request["recovery_packet"] = copy.deepcopy(record["recovery_packet"])
        if decision.get("recovery_change"):
            request["recovery_change"] = copy.deepcopy(decision["recovery_change"])
        return
    wrappers = _wrappers(state, record, run)
    try:
        observed_inputs, input_pins = inputs.capture(state, run)
    except (ValueError, OSError, KeyError) as error:
        _stale(str(error))
    request.setdefault("evidence_hashes", {}).update(input_pins)
    paths = list((state.get("current_task") or {}).get("affected_paths") or decision.get("affected_paths") or [])
    sources, originals = {}, []
    for path, digest in request.get("evidence_hashes", {}).items():
        _artifact_owned(path, workspace, run, state)
        originals.append(_archive(path, run, digest))
    for relative in current["files"]:
        if not any(relative == path.rstrip("/") or relative.startswith(path.rstrip("/") + "/") for path in paths):
            continue
        path = _owned(workspace / relative, workspace)
        if path.is_file():
            originals.append(_archive(path, run))
            with contextlib.suppress(UnicodeError):
                sources[relative] = path.read_text()  # The byte-exact original remains available even for binary files.
    records = [
        copy.deepcopy(row)
        for row in state.get("stages", [])
        if row.get("stage") in ("terra", "astra_resolve", "astra_diagnose", "sol", "astra_review")
    ]
    # Preserve diffs and logs from all prior repair attempts before later paths can change.
    seen = {row["original_path"] for row in originals}
    for row in records:
        for key in ("diff_ref", "events", "output", "before_ref", "after_ref"):
            path = row.get(key)
            if path and path not in seen and Path(path).is_file():
                _artifact_owned(path, workspace, run, state)
                originals.append(_archive(path, run))
                seen.add(path)
    packet = {
        "version": 1,
        "run_dir": str(run),
        "binding": _binding(state, current["revision"]),
        "source_output": record["output"],
        "settings": copy.deepcopy(state.get("settings", {})),
        "scope": _scope(state),
        "incidents": _incidents(state, decision, record, wrappers),
        "originals": originals,
        "sources": sources,
        "wrappers": wrappers,
        "current_error": copy.deepcopy(decision),
        "validation": copy.deepcopy(state.get("validation")),
        "prior_attempts": records,
        "prior_receipts": receipts(state),
        "findings": copy.deepcopy(state.get("findings_ledger", [])),
        "protected_obligations": copy.deepcopy(cast(dict, state.get("goal_contract", {})).get("body", {})),
        "protected_tests": copy.deepcopy(state.get("settings", {}).get("protected_tests", {})),
        "model_pins": copy.deepcopy(state.get("settings", {}).get("roles", {})),
        "saved_limits": copy.deepcopy(state.get("settings", {}).get("limits", {})),
        "permitted_controls": list(observed_inputs),
        "inputs": observed_inputs,
        "input_pins": input_pins,
        "failure_history": copy.deepcopy(state.get("failure_history", {})),
    }
    path = _owned(run / "resolver" / "recovery" / (util.digest(packet) + ".json"), run)
    if path.exists() and util.read(path) != packet:
        _stale("incident draft changed; no repinning")
    if not path.exists():
        util.atomic_json(path, packet)
    pointer = {"path": str(path), "sha256": util.file_hash(path)}
    request["recovery_packet"] = pointer
    record["recovery_packet"] = copy.deepcopy(pointer)
    if decision.get("recovery_change"):
        request["recovery_change"] = copy.deepcopy(decision["recovery_change"])


def load_packet(pointer, run):
    try:
        path = _owned(pointer["path"], run)
        if util.file_hash(path) != pointer["sha256"]:
            _stale("draft hash changed")
        packet = util.read(path)
        if packet.get("version") != 1 or Path(packet["run_dir"]).resolve() != Path(run).resolve():
            _stale("draft belongs to another run or version")
        for original in packet["originals"]:
            archived = _owned(original["path"], run)
            if util.file_hash(archived) != original["archive_sha256"]:
                _stale("retained original archive changed")
            blob = util.read(archived)
            if sha256(base64.b64decode(blob["base64"], validate=True)).hexdigest() != original["sha256"]:
                _stale("retained original bytes changed")
        for value in packet["incidents"]:
            novelty.Incident(**value)
        return packet
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        _stale(f"unavailable or malformed evidence: {error}")


def _attest(packet, change):
    """``(identity, None)`` for a proposed change the packet attests, else ``(None, why)`` (#418).

    recovery_change is optional model advice. The packet attests a proposal bounded by its
    sources, or an observed input transition, with the original discriminating check, that
    cites pinned originals. Its identity is still None when it proves no structural novelty
    (an unsupported grammar or unchanged structure). Any other proposal is unproven, exactly
    like recovery_change=null: it buys no novelty and is never a stale handoff; ``why`` names
    the check it failed. Tampered retained evidence still raises in load_packet.
    """
    if not change:
        return None, None
    if not isinstance(change, dict):
        return None, "not an object"
    operations = {row["operation"] for row in packet["incidents"]}
    allowed = set(packet["sources"]) - set(packet["protected_tests"].get("files", {}))
    ident = None
    is_input = change.get("target") in packet.get("permitted_controls", [])
    if is_input:
        incident_ids = {novelty.Incident(**row).id for row in packet["incidents"]}
        for prior in reversed(packet["prior_receipts"]):
            if incident_ids.intersection(prior.get("incident_ids", [])) and prior.get("packet"):
                original = load_packet(prior["packet"], packet["run_dir"])
                if "inputs" in original:
                    ident = inputs.change_identity(
                        change, packet["inputs"], original["inputs"], operations=operations, wrappers=packet["wrappers"]
                    )
                break
    else:
        ident = novelty.change_identity(
            change, sources=packet["sources"], allowed_paths=allowed, operations=operations, wrappers=packet["wrappers"]
        )
    if not ident and not novelty.bounded_change(
        change, sources=packet["sources"], allowed_paths=allowed, operations=operations, wrappers=packet["wrappers"]
    ):
        bounds = "an attested input transition" if is_input else "a bounded attested source change"
        return None, f"not {bounds} with the original discriminating check"
    refs = change.get("evidence_refs", [])
    available = {row["original_path"] for row in packet["originals"]}
    if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
        return None, "evidence_refs do not cite pinned originals"
    validation = packet.get("validation") or {}
    event_refs = {row.get("evidence_ref") for row in validation.get("checks", [])}
    streams = {
        row["events"]
        for row in packet["prior_attempts"]
        if row.get("stage") == "sol"
        and row.get("events")
        and row.get("output") == validation.get("output")
        and row["events"] in available
    }
    resolved_refs = {
        next(iter(streams)) if ref.startswith("event:") and ref in event_refs and len(streams) == 1 else ref
        for ref in refs
    }
    if not resolved_refs <= available:
        return None, "evidence_refs do not cite pinned originals"
    if is_input and not resolved_refs.intersection(packet["input_pins"]):
        return None, "changed input does not cite its attested input evidence"
    return ident, None


def _sort_change(packet, change):
    """``(change, None)`` for a proposal the packet attests, else ``(None, unattested)``.

    ``unattested`` is ``{"change", "reason"}``: advice a repair plan passes to the Builder,
    never a ``recovery_change`` (#418, #422).
    """
    _, why = _attest(packet, change)
    if why:
        return None, {
            "change": copy.deepcopy(change),
            "reason": f"Recovery packet: proposed change is unproven ({why})",
        }
    return copy.deepcopy(change), None


def validate_decision(state: RunState, value, record):
    """Record a Resolver or Completion decision's proposed change and its identity.

    An unproven proposal is kept with recovery_change_id None, like an unsupported grammar:
    admit_dispatch then grants it no novelty, route_known_change does not route it, and
    finish_resolution_packet passes it to the Builder only as unattested_change.
    """
    request = state.get("resolution_request") or {}
    if not request.get("recovery_packet"):
        return
    packet = load_packet(request["recovery_packet"], _run_root(state, record))
    if value.get("recovery_change"):
        change = copy.deepcopy(value["recovery_change"])
        ident, _ = _attest(packet, change)
        request["recovery_change"] = change
        request["recovery_change_id"] = ident


def diagnosis_change(request, change, run_dir):
    """Sort the change a diagnosis proposed into ``(attested, unattested)`` (#422).

    Only a change the incident packet attests becomes the repair plan's ``recovery_change``,
    which admit_dispatch attests again before the Builder runs. A diagnosis's accepted retry
    needs no change, and a proposal is usually unprovable: an operational packet's incident
    names the failed stage, not a check command, and without a packet (parallel or integrated
    scope) nothing attests it. Such a proposal goes to the Builder as ``unattested_change``
    with the reason, a key admit_dispatch never reads: it is advice, like the recommendation's
    guidance, and neither voids the retry nor counts as a new experiment.

    The caller has already loaded this packet (finish_resolution_packet), so a stale packet
    raises as it did there. An unproven proposal is recorded with the check it failed (#418).
    """
    if not change:
        return None, None
    if not request.get("recovery_packet"):
        return None, {
            "change": copy.deepcopy(change),
            "reason": "No incident packet attests a proposal in parallel or integrated scope",
        }
    packet = load_packet(request["recovery_packet"], run_dir)
    try:
        return _sort_change(packet, change)
    except (util.Paused, ValueError, KeyError, TypeError, AttributeError) as error:
        # An unproven proposal no longer raises (#418). What still can is a changed earlier
        # packet that an input transition cites, or an unexpected error. finish_operational_diagnosis
        # must not raise after its evaluation, so this is recorded as advice, not a stale handoff.
        # It grants nothing: admit_dispatch never reads unattested_change.
        return None, {"change": copy.deepcopy(change), "reason": str(error)}


def prepare_diagnosis(state: RunState, request, record, run_dir):
    """Use the same exact packet for an explicitly requested operational diagnosis."""
    candidate = copy.deepcopy(state)
    candidate["run_dir"] = str(Path(run_dir).resolve())
    candidate["resolution_request"] = copy.deepcopy(request)
    decision = {
        "summary": request["description"],
        "findings": [],
        "operational_diagnosis": True,
        "user_request": {"kind": "none"},
        "affected_paths": (state.get("current_task") or {}).get("affected_paths", []),
    }
    # A Builder report rejection is not an old independent Validator's incident.
    candidate.pop("validation", None)
    prepare_resolution(candidate, decision, record)
    for key in ("recovery_packet", "recovery_novelty_skipped"):
        if key in cast(dict, candidate["resolution_request"]):
            request[key] = copy.deepcopy(cast(dict, candidate["resolution_request"])[key])


def _live_grant(state: RunState, packet, record, authorization):
    # The caller passes only this CLI invocation's successfully guarded object.
    # Its durable audit copy never carries either of these live-use markers.
    if (
        not isinstance(authorization, dict)
        or authorization.get("consumed") is not True
        or authorization.get("_novelty_consumed")
        or (authorization.get("stage") or (authorization.get("identity") or {}).get("stage")) != record["stage"]
        or state.get("next_stage")
        not in (record["stage"], "orchestrator" if record["stage"] == "terra" else record["stage"])
        or state.get("pending_report_repair")
    ):
        return None
    revision = packet["binding"]["source_revision"]
    binding = _binding(state, revision)
    if authorization.get("novelty_packet"):
        held = _request(state, record["stage"]).get("novelty_hold") or {}
        valid = (
            authorization.get("actor") == "user_cli"
            and authorization["novelty_packet"] == util.digest(packet)
            and authorization.get("binding") == packet["binding"]
            and _holds(authorization.get("dispatch_binding"), state, revision)
            and _holds(held.get("binding"), state, revision)
            and held.get("scope") == _scope(state)
        )
    else:
        previous: dict = next((row for row in reversed(state.get("stages", [])) if row.get("failure_key")), {})
        repeated = failures.repeated(state, previous) if previous else None
        valid = (
            authorization.get("consumed") is True
            and repeated is not None
            and authorization.get("failure_key") == previous.get("failure_key")
            and authorization.get("identity") == repeated["identity"]
            and type(authorization.get("count")) is int
            and authorization["count"] == repeated["count"]
            and authorization.get("source_revision")
            == previous.get("source_revision")
            == packet["binding"]["source_revision"]
            and (previous.get("original_stage") or previous.get("stage")) == record["stage"]
            and repeated["identity"].get("stage") == record["stage"]
            and previous.get("task_id", binding["task_id"]) == binding["task_id"]
        )
    return (
        util.digest(
            {key: value for key, value in authorization.items() if key not in ("consumed", "_novelty_consumed")}
        )
        if valid
        else None
    )


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (AttributeError, ValueError, TypeError):
        return None


def _builder_grant(state: RunState, packet, request: dict, record, prior):
    """Verify outstanding serial authority using its real dispatch history too."""
    stage = record["stage"]
    if stage not in ("astra_resolve", "terra") or not builder_policy.enabled(state):
        return None
    key = builder_policy.key(state)
    lane = state.get("builder_retries", {}).get(key) or {}
    grant: dict = next(
        (row for row in reversed(state.get("builder_retry_decisions", [])) if row.get("milestone_key") == key), {}
    )
    source = packet.get("source_output")
    at = _time(grant.get("at"))
    if (
        not at
        or state.get("builder_retry_key") != key
        or lane.get("action") != "retry"
        or grant.get("owner") != "user_cli"
        or grant.get("action") != "retry"
        or not source
        or grant.get("failure") != source
        or lane.get("failures", [])[-1:] != [source]
        or type(grant.get("attempt")) is not int
        or grant["attempt"] != len(lane["failures"])
        or grant.get("selected_model") != state["settings"]["roles"]["terra"].get("model")
        or grant.get("selected_effort") != state["settings"]["roles"]["terra"].get("reasoning_effort")
    ):
        return None
    event: dict = next(
        (
            row
            for row in reversed(state.get("user_events", []))
            if row.get("kind") == "builder_retry"
            and sorted(row.get("milestone_ids", [])) == packet["scope"]["milestones"]
        ),
        {},
    )
    if (
        event.get("actor") != "user_cli"
        or event.get("mode") != "serial"
        or event.get("at") != grant["at"]
        or event.get("failure") != source
    ):
        return None
    pending = state.get("resolution_request") if stage == "astra_resolve" else state.get("repair_plan")
    if request is not pending or request.get("source_output") != source or not request.get("recovery_packet"):
        return None
    if stage == "terra" and (
        not request.get("recovery_admission") or (state.get("current_task") or {}).get("kind") != "implement"
    ):
        return None
    rows = state.get("stages", [])
    origin = next((index for index, row in reversed(list(enumerate(rows))) if row.get("output") == source), None)
    if origin is None:
        return None
    finished = _time(rows[origin].get("finished_at"))
    if (
        not finished
        or finished > at
        or rows[origin].get("source_revision") != packet["binding"]["source_revision"]
        or rows[origin].get("contract_hash") != packet["binding"]["contract_hash"]
    ):
        return None
    original = next((row for row in packet["originals"] if row["original_path"] == source), None)
    if not original or _hash(_owned(source, packet["run_dir"])) != original["sha256"]:
        _stale("Builder retry source no longer matches its bound accepted failure")
    ident = util.digest(grant)
    if any(
        row.get("grant_id") == ident and (stage == "astra_resolve" or row.get("action") == "repair") for row in prior
    ):
        return None
    diagnoses = 0
    for row in rows[origin + 1 :]:
        if (
            row.get("stage") not in ("terra", "astra_resolve", "astra_diagnose")
            or row.get("report_only")
            or row.get("dry_run")
        ):
            continue
        started = _time(row.get("started_at"))
        if started is None:
            return None  # An unknown/legacy dispatch is not outstanding credit.
        if started < at:
            continue
        receipt = row.get("recovery_novelty") or {}
        if (
            stage != "terra"
            or row.get("stage") != "astra_resolve"
            or receipt.get("grant_kind") != "builder"
            or receipt.get("grant_id") != ident
            or receipt.get("packet") != request["recovery_packet"]
        ):
            return None
        diagnoses += 1
        if diagnoses > 1:
            return None
    return ident


def _diagnosis_grant(state: RunState, packet, request: dict, record):
    """The Builder retry an accepted operational diagnosis recommended (#422).

    The failure it diagnosed is a repeated rejected Builder report (a diagnosis is admitted
    only for a pending report repair), so there is usually no source change to propose: the
    diagnosis and its recommendation, which the Builder receives in its repair plan, are the
    new information.

    The grant is spent by one Builder attempt that returns a result; novelty decides that
    against returned receipts. An operator's grant (--retry-failed-stage, a Builder retry) is
    bound to one invocation or failure and is spent by the dispatch it admits, even one that
    times out. This one is bound to the accepted outcome and its packet, which a timeout does
    not change, so an attempt automatic recovery archived without a report does not spend it;
    that recovery route's own budget bounds the relaunch. A further returned attempt needs new
    evidence or an explicit retry. The same unchanged failure cannot buy a second diagnosis:
    novelty holds its incident at dispatch, before the diagnostic cap is charged (every
    --resume-paused clears the Resolver's per-failure attempts, so they are not that guard).
    """
    pointer = request.get("recovery_packet")
    recommendation = request.get("recommendation") or {}
    if (
        record["stage"] != "terra"
        or request is not state.get("repair_plan")
        or request.get("kind") != "operational-diagnosis"
        or recommendation.get("action") != "retry"
        or not packet["current_error"].get("operational_diagnosis")
    ):
        return None
    retry: dict = next(
        (
            row
            for entry in (state.get("failure_history") or {}).values()
            for row in reversed(entry.get("diagnostic_retries") or [])
            if row.get("recovery_packet") == pointer and row.get("recommendation") == recommendation
        ),
        {},
    )
    # The runner's own accepted retry outcome, not only the plan that cites it.
    accepted = retry.get("receipt") and any(
        row.get("runner_owned")
        and (row.get("decision") or {}).get("action") == "retry"
        and (row.get("receipt") or {}).get("idempotency_key") == retry["receipt"]
        for row in state.get("stages", [])
    )
    return util.digest({"diagnosis_retry": retry["receipt"], "packet": pointer}) if accepted else None


# Pauses for a stage output the runner rejected; an Investigator's retry of one runs the stage once more.
REJECTED_OUTPUT = ("PAUSED_INVALID_OUTPUT", "PAUSED_REPEATED_FAILURE")


def _investigation_grant(state: RunState, request, record):
    """The one Builder attempt an Investigator's retry of this packet's rejected Builder output promises.

    The runner rejected what a Builder this packet admitted returned (it also wrote outside its
    assignment, say) before any check of the incident, and the stuck-stage Investigator verified a
    cause and recommended a retry (autocode_stuck_job), which for these pauses runs the stage once
    more. As with an accepted operational diagnosis (#422), the verified guidance in the Builder's
    prompt is the new information. Bound to that investigation, while its guidance is in force, and to
    the packet; novelty spends it on one attempt that returns a result. An investigation of any other
    pause, such as a novelty hold (PAUSED_NO_PROGRESS), grants nothing.
    """
    pointer, current = request.get("recovery_packet"), state.get("stuck_investigation") or {}
    if (
        record["stage"] != "terra"
        or not current.get("in_force")
        or current.get("stage") != "terra"
        or current.get("status") not in REJECTED_OUTPUT
    ):
        return None
    entry: dict = next(
        (
            row
            for row in reversed(state.get("stuck_investigations") or [])
            if row.get("identity") == current.get("identity")
        ),
        {},
    )
    # The rejected attempt it followed is the latest that returned a result: a relaunch automatic
    # recovery archived without a report (a provider timeout) neither replaces it nor spends the grant.
    last: dict = next(
        (
            row
            for row in reversed(state.get("stages", []))
            if row.get("stage") == "terra"
            and not row.get("report_only")
            and not row.get("dry_run")
            and not _returned_nothing(row)
        ),
        {},
    )
    asked, rejected = _time(entry.get("requested_at")), _time(last.get("finished_at"))
    if (
        entry.get("outcome") != "retried"
        or entry.get("trigger") != "rejected_output"
        or not last.get("rejected")
        or (last.get("recovery_novelty") or {}).get("packet") != pointer
        or not asked
        or not rejected
        or asked < rejected
    ):
        return None
    return util.digest(
        {"stuck_investigation": entry["identity"], "requested_at": entry["requested_at"], "packet": pointer}
    )


# The actor of the scoped retry grant AutoCode records itself at a no-progress hold (#2485).
AUTOMATIC_ACTOR = "autocode"


def _automatic_grants(state: RunState, packet, stage):
    """AutoCode's own scoped retry grants of ``packet`` at ``stage`` (recover_no_progress), oldest first."""
    digest = util.digest(packet)
    return [
        row
        for row in state.get("failure_retry_authorizations") or []
        if isinstance(row, dict)
        and row.get("actor") == AUTOMATIC_ACTOR
        and row.get("stage") == stage
        and row.get("novelty_packet") == digest
    ]


def _automatic_grant(state: RunState, packet, request: dict, record, prior):
    """The one scoped retry AutoCode authorized itself at this packet's no-progress hold (#2485), while
    unspent and still bound to the hold's source, settings, contract and scope. Any dispatch it admits
    spends it, as a person's --retry-failed-stage grant is spent."""
    held = request.get("novelty_hold") or {}
    revision = packet["binding"]["source_revision"]
    for grant in reversed(_automatic_grants(state, packet, record["stage"])):
        ident = util.digest(grant)
        if any(row.get("grant_id") == ident for row in prior):
            continue
        if (
            grant.get("binding") == packet["binding"]
            and _holds(grant.get("dispatch_binding"), state, revision)
            and _holds(held.get("binding"), state, revision)
            and held.get("scope") == _scope(state)
        ):
            return ident
    return None


def _explicit_grant(state: RunState, packet, request, record, prior, authorization):
    ident = _live_grant(state, packet, record, authorization)
    if ident is not None and not any(row.get("grant_id") == ident for row in prior):
        return ident, "invocation"
    ident = _builder_grant(state, packet, request, record, prior)
    if ident is not None:
        return ident, "builder"
    # One use is decided by novelty against attempts that returned a result. A diagnosis grant
    # one of those already carries is spent; it must not hide an Investigator's later grant.
    ident = _diagnosis_grant(state, packet, request, record)
    if ident is not None and not any(row.get("grant_id") == ident for row in receipts(state, returned=True)):
        return ident, "diagnosis"
    ident = _investigation_grant(state, request, record)
    if ident is not None:
        return ident, "investigation"
    ident = _automatic_grant(state, packet, request, record, prior)
    return ident, "automatic" if ident is not None else None


def finish_resolution_packet(state: RunState, request, plan):
    for key in ("recovery_packet", "recovery_change", "recovery_change_id"):
        if key in request:
            plan[key] = copy.deepcopy(request[key])
    if request.get("recovery_packet"):
        run = Path(state.get("run_dir") or Path(request["recovery_packet"]["path"]).parents[2]).resolve()
        packet = load_packet(request["recovery_packet"], run)
        # A plan's recovery_change is one the packet attests. An unproven proposal reaches the
        # Builder only as advice with the check it failed, as a diagnosis's does (#418, #422);
        # Builder admission then weighs only the reviewed decision's own proposal, as when the
        # Resolver proposes none.
        _, unattested = _sort_change(packet, request.get("recovery_change"))
        if unattested:
            plan.pop("recovery_change", None)
            plan.pop("recovery_change_id", None)
            plan["unattested_change"] = unattested
        admission = {
            "packet": request["recovery_packet"],
            "binding": _binding(state, packet["binding"]["source_revision"]),
            "scope": _scope(state),
            "retry_charge": copy.deepcopy(state.get("builder_retry_decisions", [])[-1:]),
        }
        path = _owned(run / "resolver" / "recovery" / (util.digest(admission) + ".json"), run)
        if path.exists() and util.read(path) != admission:
            _stale("repair admission receipt changed")
        if not path.exists():
            util.atomic_json(path, admission)
        plan["recovery_admission"] = {"path": str(path), "sha256": util.file_hash(path)}


def _verify_reports(state: RunState, decision, record, accepted, run_dir):
    """Original seals remain authoritative; a freshly repinned packet is not."""
    rework.verify_existing(record)
    rework.verify_existing(accepted)
    if util.digest(_read(record["output"])) != util.digest(decision):
        _stale("Completion body differs from its original sealed decision")
    validation = cast(dict, state["validation"])
    report = check_refs.resolve(_read(accepted["output"]))
    if any(validation.get(key) != value for key, value in report.items()) or any(
        validation.get(key) != accepted.get(key)
        for key in ("task_id", "contract_hash", "contract_revision", "source_revision")
    ):
        _stale("accepted Validator body or provenance differs from its original seal")
    pins = validation.get("evidence_hashes") or {}
    if not pins:
        _stale("accepted failure has no original evidence pins")
    workspace, run = Path(state["workspace"]).resolve(), Path(run_dir).resolve()
    for path, digest in pins.items():
        if _hash(_artifact_owned(path, workspace, run, state)) != digest:
            _stale("original failed-check evidence changed during recovery admission")
    for check in validation.get("checks", []):
        if type(check.get("exit_code")) is not int or check["exit_code"] == 0:
            continue
        ref = check.get("evidence_ref", "")
        if ref.startswith("event:"):
            if accepted["events"] not in pins:
                _stale("failed event lacks its original event-stream pin")
        else:
            receipt = _artifact_owned(ref, workspace, run, state)
            output = _read(receipt).get("full_output")
            if not isinstance(output, str) or not output:
                _stale("original failed receipt lost its output path")
            raw = _artifact_owned(output, workspace, run, state)
            if str(receipt) not in pins or str(raw) not in pins:
                _stale("failed receipt and original log must both be pinned")


def _completed_owner(state: RunState, record):
    active = state.get("active_stage")
    if not active:
        return
    if (
        any(active.get(key) != record.get(key) for key in ("stage", "output", "events", "task_id"))
        or not record.get("finished_at")
        or not record.get("processes")
        or record.get("cleanup_error")
        or record.get("uncertain")
        or record.get("interrupted")
        or record.get("timed_out")
    ):
        raise util.Paused(
            "PAUSED_WORKSPACE_BUSY",
            "Reconcile the owned active or uncertain stage before recovery; no worker is restarted",
        )
    try:
        worker = processes.recorded_worker_state(record)
    except (ValueError, KeyError, TypeError):
        worker = {"checked": False, "alive": None}
    if worker.get("checked") is not True or worker.get("alive") is not False:
        raise util.Paused(
            "PAUSED_WORKSPACE_BUSY",
            "Reconcile the owned provider or its descendants before recovery; no worker is restarted",
        )


def route_known_change(runtime, state: RunState, decision, record, *, run_dir, retry_policy):
    """Use ordinary task assignment for an attested repair, not another diagnosis."""
    change = decision.get("recovery_change")
    request = state.get("resolution_request") or {}
    if (
        (decision.get("next_task") or {}).get("kind") != "implement"
        or not isinstance(change, dict)
        or not change
        or change.get("question", "").strip()
        or not request.get("recovery_packet")
        or any(
            state.get(key)
            for key in (
                "parent_run",
                "active_runner_check",
                "uncertain_artifacts",
                "orchestration_batch",
                "pending_report_repair",
                "user_request",
                "pending_questions",
            )
        )
        or state.get("settings", {}).get("workflow")
        or not retry_policy.enabled(state)
    ):
        return False
    _completed_owner(state, record)
    validation, task = state.get("validation") or {}, state.get("current_task") or {}
    packet = load_packet(request["recovery_packet"], run_dir)
    if (
        validation.get("verdict") != "FAIL"
        or decision.get("status") != "REWORK"
        or (decision.get("user_request") or {}).get("kind") != "none"
        or not record.get("rework_evidence")
        or record.get("report_repaired")
        or packet["scope"] != _scope(state)
        or change.get("target") in packet["protected_tests"].get("files", {})
    ):
        return False
    if any(
        validation.get(key) != expected
        for key, expected in (
            ("task_id", task.get("id")),
            ("source_revision", record.get("source_revision")),
            ("contract_hash", cast(dict, state["goal_contract"])["hash"]),
            ("reviewer_role", "sol"),
        )
    ):
        return False
    accepted = next(
        (
            row
            for row in state.get("stages", [])
            if row.get("output") == validation.get("output") and row.get("stage") == "sol" and not row.get("rejected")
        ),
        None,
    )
    if (
        not accepted
        or not accepted.get("rework_evidence")
        or accepted.get("report_only")
        or accepted.get("changed_files")
    ):
        return False
    builder: dict = next(
        (
            row
            for row in reversed(state.get("stages", []))
            if row.get("stage") == "terra" and row.get("task_id") == task.get("id") and not row.get("rejected")
        ),
        {},
    )
    roles = state.get("settings", {}).get("roles", {})
    builder_model = (builder.get("launch_route") or roles.get("terra") or {}).get("model", "")
    validator_model = (accepted.get("launch_route") or roles.get("sol") or {}).get("model", "")
    if (
        not builder
        or accepted.get("role") != "sol"
        or type(accepted.get("exit_code")) is not int
        or accepted["exit_code"] != 0
        or not builder_model
        or not validator_model
        or builder_model.rsplit("/", 1)[-1] == validator_model.rsplit("/", 1)[-1]
        or (builder.get("thread_id") and builder.get("thread_id") == accepted.get("thread_id"))
    ):
        return False
    _verify_reports(state, decision, record, accepted, run_dir)
    validate_decision(state, decision, record)
    ident = request["recovery_change_id"]
    if ident is None:
        # Unknown grammar, cosmetic or unproven bounds are not autonomous progress (#418): the
        # queued Resolver route stays, and its admission decides without novelty.
        return False
    prior = [row for row in receipts(state, returned=True) if row.get("action") == "repair"]
    if any(
        novelty.decide(
            novelty.Incident(**incident),
            prior,
            action="repair",
            change_id=ident,
            expected_check=change["expected_check"],
        ).action
        != "repair"
        for incident in packet["incidents"]
    ):
        return False
    current = source_scope.snapshot(state["workspace"], state, base_snapshot=runtime.support.snapshot)
    if not _holds(packet["binding"], state, current["revision"]):
        _stale("known correction changed scope or source before assignment")
    failed = [
        check for check in validation.get("checks", []) if type(check.get("exit_code")) is int and check["exit_code"]
    ]
    if not failed:
        return False
    try:
        verified_checks = copy.deepcopy(validation["checks"])
        runtime.support.verify_checks(
            verified_checks, state["workspace"], accepted["events"], **runtime.check_evidence_options(accepted)
        )
    except ValueError as error:
        _stale("known correction lacks an executed independent failure: " + str(error))
    facts = builder_failure.check_facts(verified_checks, accepted, state["workspace"], read_events=support.events)
    if classification.classify({"record": accepted, "checks": facts, "checks_verified": True}) != "execution":
        return False  # Retain the existing queued diagnosis; do not charge an operational check failure.
    _verify_reports(state, decision, record, accepted, run_dir)
    if not _holds(
        packet["binding"],
        state,
        source_scope.snapshot(state["workspace"], state, base_snapshot=runtime.support.snapshot)["revision"],
    ):
        _stale("source or binding changed while checking the failed evidence")
    candidate = copy.deepcopy(state)
    candidate["iteration"] += 1
    try:
        runtime.lifecycle.assign_task(copy.deepcopy(candidate), decision, current)
    except ValueError:
        return False
    except util.Paused as error:
        if not error.status.startswith("PAUSED_MILESTONE_"):
            raise
    _verify_reports(state, decision, record, accepted, run_dir)
    if not _holds(
        packet["binding"],
        state,
        source_scope.snapshot(state["workspace"], state, base_snapshot=runtime.support.snapshot)["revision"],
    ):
        _stale("source or binding changed while checking the proposed correction")
    action = retry_policy.failure(
        candidate, record["output"], "Attested bounded correction: " + change["hypothesis"], classification="execution"
    )
    if action not in ("retry", "escalate", "pause", "defer"):
        raise util.Paused("PAUSED_BUILDER_CLASSIFICATION", "Verified correction returned a non-execution action")
    if action in ("pause", "defer"):
        candidate["next_stage"] = "terra"
        runtime.goals.record_decision(candidate, decision)
        cast(dict, state).clear()
        state.update(candidate)
        return True  # Existing exhaustion must not purchase a fallback diagnosis.
    try:
        runtime.lifecycle.assign_task(candidate, decision, current)
    except util.Paused as error:
        if not error.status.startswith("PAUSED_MILESTONE_"):
            raise
        runtime.milestones.handle_gate(
            candidate,
            error,
            current,
            origin={"stage": "astra_review", "output": record["output"]},
            ask_user=runtime.lifecycle.wait_for_user,
        )
        runtime.goals.record_decision(candidate, decision)
        cast(dict, state).clear()
        state.update(candidate)
        return True
    saved = cast(dict, candidate.pop("resolution_request"))
    plan = {
        "kind": "known-correction",
        "version": 1,
        "source_output": record["output"],
        "source_revision": current["revision"],
        "contract_hash": cast(dict, state["goal_contract"])["hash"],
        "tasks": [copy.deepcopy(candidate["current_task"])],
        "evidence_hashes": saved["evidence_hashes"],
        "retry_charge": {"action": action, "evidence": record["output"]},
    }
    finish_resolution_packet(candidate, saved, plan)
    candidate["repair_plan"] = plan
    candidate.setdefault("resolution_history", []).append(copy.deepcopy(plan))
    runtime.goals.record_decision(candidate, decision)
    candidate.update(
        {
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_action": decision["next_objective"],
            "next_stage": runtime.dispatch.build_stage(candidate),
        }
    )
    _verify_reports(state, decision, record, accepted, run_dir)
    if not _holds(
        packet["binding"],
        state,
        source_scope.snapshot(state["workspace"], state, base_snapshot=runtime.support.snapshot)["revision"],
    ):
        _stale("source or binding changed before committing the proposed correction")
    cast(dict, state).clear()
    state.update(candidate)
    return True


def authorize_retry(runner, state: RunState, run_dir, workspace, *, previous_settings=None, preview=False):
    """Existing explicit CLI control, bound to the actual suppressed frontier."""
    request = _request(state, state.get("next_stage"))
    held = request.get("novelty_hold")
    if not held:
        return None
    if any(state.get(key) for key in recovery_limits.UNSETTLED):
        raise ValueError(recovery_limits.UNSETTLED_REFUSAL)
    packet = load_packet(request["recovery_packet"], run_dir)
    revision = _source_revision(state, held.get("stage"), request["recovery_packet"], packet, workspace)
    route_change = retry_route.change(state, previous_settings)
    checked = state
    if route_change:
        # Prospective provenance is checked before it is saved. Every other
        # setting, source, contract and scope must still match the old hold.
        checked = {
            **state,
            "failure_retry_authorizations": [
                *state.get("failure_retry_authorizations", []),
                {
                    "actor": "user_cli",
                    "stage": "astra_resolve",
                    "novelty_packet": util.digest(packet),
                    "resolver_route_change": route_change,
                },
            ],
        }
    if not _holds(held.get("binding"), checked, revision) or held.get("scope") != _scope(state):
        raise ValueError("Scoped recovery retry is stale; source, settings or approved scope changed")
    # The saved-state admission rule is shared with the status view, which names this retry only
    # where it holds (#1770). A route change is checked against the request under the old settings.
    public = runner.resolver_human.current({**state, "settings": previous_settings} if route_change else state)
    refusal = recovery_limits.scoped_retry_refusal(state, public)
    if refusal:
        raise ValueError(refusal)
    pending = cast(dict, state).get(runner.resolver_human.PRIVATE)
    grant = {
        "actor": "user_cli",
        "at": util.now(),
        "novelty_packet": util.digest(packet),
        "binding": packet["binding"],
        "dispatch_binding": copy.deepcopy(held["binding"]),
        "stage": held.get("stage")
        or ("terra" if state.get("next_stage") == "orchestrator" else state.get("next_stage")),
        "ordinal": len(state.get("failure_retry_authorizations", [])),
    }
    if route_change:
        grant["resolver_route_change"] = route_change
    if preview:
        return grant  # Validate before run setup writes settings; this is not launch credit.
    if pending:
        grant["superseded_proposal"] = copy.deepcopy(pending)
        cast(dict, state).pop(runner.resolver_human.PRIVATE)
    state.setdefault("failure_retry_authorizations", []).append(copy.deepcopy(grant))
    state.setdefault("user_events", []).append({"kind": "failure_retry_authorized", **copy.deepcopy(grant)})
    runner.resolver_human.supersede_operational(state, "Operator explicitly authorized one scoped incident retry")
    runner.write_json(Path(run_dir) / "state.json", state)
    # As with authorize_failure_retry + its resume guard, this returned object
    # is a current-invocation exception. Its saved audit is not outstanding credit.
    return {**grant, "consumed": True}


_request = recovery_limits.held_request


def _validation_only(request, packet):
    """Classify the already-bound accepted request, never a mutable task label."""
    decision = packet["current_error"]
    if (decision.get("next_task") or {}).get("kind") != "validate":
        return False
    if request.get("source_report_not_accepted"):
        return False
    source = request.get("source_output")
    original = next((row for row in packet["originals"] if row["original_path"] == source), None)
    if (
        not source
        or source != packet["source_output"]
        or not original
        or util.digest(request.get("review")) != util.digest(decision)
    ):
        _stale("validation-only request differs from its bound accepted decision")
    path = _owned(source, packet["run_dir"])
    if _hash(path) != original["sha256"] or util.digest(_read(path)) != util.digest(decision):
        _stale("validation-only source report changed after acceptance")
    criteria = decision["next_task"].get("acceptance_criteria")
    plan = decision["next_task"].get("validation_plan")
    milestone_id = decision["next_task"].get("milestone_id")
    approved = packet["protected_obligations"]
    approved_ids = {row["id"] for row in approved.get("acceptance_criteria", [])}
    milestone = next((row for row in approved.get("milestones", []) if row["id"] == milestone_id), None)
    scoped_ids = set(milestone["acceptance_criteria"] if milestone else packet["scope"]["criteria"])
    if (
        decision.get("status") != "REWORK"
        or (decision.get("user_request") or {}).get("kind") != "none"
        or any(decision.get(key) != packet["binding"][key] for key in ("task_id", "contract_hash", "contract_revision"))
        or not isinstance(criteria, list)
        or not criteria
        or not all(isinstance(item, str) for item in criteria)
        or milestone_id not in packet["scope"]["milestones"]
        or not set(criteria) <= (approved_ids & scoped_ids)
        or not isinstance(plan, list)
        or not plan
        or not all(isinstance(check, str) and check.strip() for check in plan)
    ):
        _stale("validation-only correction lacks its approved scope or bounded checks")
    return True


def _exhausted_builder(state: RunState, request, packet):
    """A known exhausted policy never needs a paid diagnosis to discover it."""
    if _validation_only(request, packet) or not builder_policy.enabled(state) or not request.get("source_output"):
        return
    # This locked preview must never turn an ambiguous source proposal into an
    # execution failure or launch an Investigator. Only sealed independent FAIL
    # evidence can prove an already exhausted execution lane here.
    validation = packet.get("validation") or {}
    binding = packet.get("binding") or {}
    try:
        from . import autocode_failure_classification as classification
    except ImportError:
        import autocode_failure_classification as classification
    if (
        request.get("provenance") == "source_report_not_accepted_review"
        or validation.get("verdict") != "FAIL"
        or any(validation.get(key) != binding.get(key) for key in ("task_id", "source_revision", "contract_hash"))
        or not any(
            row.get("stage") == "sol" and not row.get("rejected") and row.get("output") == validation.get("output")
            for row in packet.get("prior_attempts", [])
        )
        or not any(
            type(check.get("exit_code")) is int and check["exit_code"] > 0 for check in validation.get("checks", [])
        )
    ):
        return
    row: dict = next(
        (
            row
            for row in state.get("milestone_progress", {}).values()
            if row.get("contract_hash") == binding.get("contract_hash")
            and (state.get("current_task") or {}).get("milestone_id") in row.get("milestone_ids", [row.get("id")])
        ),
        {},
    )
    if row.get("builder_reassessment"):
        return  # The bounded reassessment assignment gate, not execution policy, owns this request.
    accepted = next(
        row
        for row in packet["prior_attempts"]
        if row.get("stage") == "sol" and not row.get("rejected") and row.get("output") == validation.get("output")
    )
    verified_checks = copy.deepcopy(validation.get("checks", []))
    paths = [
        accepted.get("events"),
        *[check["evidence_ref"] for check in verified_checks if not check["evidence_ref"].startswith("event:")],
    ]
    originals = {str(Path(item["original_path"]).resolve()): item["sha256"] for item in packet["originals"]}
    for path in paths:
        if not path:
            continue
        current_path = Path(path) if Path(path).is_absolute() else Path(state["workspace"]) / path
        if (
            str(current_path.resolve()) not in originals
            or not current_path.is_file()
            or util.file_hash(current_path) != originals[str(current_path.resolve())]
        ):
            _stale("verified failure evidence changed before exhaustion preview")
    try:
        support.verify_checks(
            verified_checks,
            state["workspace"],
            accepted.get("events"),
            receipt_only=accepted.get("output_mode") == "report_file",
            capture_context=accepted.get("capture_context"),
        )
    except (ValueError, OSError) as error:
        _stale("exhaustion preview lacks a verified current failure: " + str(error))
    facts = builder_failure.check_facts(verified_checks, accepted, state["workspace"], read_events=support.events)
    classification_value = classification.classify(
        {
            "record": next(
                (row for row in packet.get("prior_attempts", []) if row.get("output") == request["source_output"]), {}
            ),
            # A stalled-approach checkpoint cannot renew spent execution authority.
            "checks": facts,
            "checks_verified": True,
        }
    )
    if classification_value != "execution":
        return  # Non-execution decisions belong to the controller, never locked preview.
    candidate = copy.deepcopy(state)
    action = builder_policy.failure(
        candidate,
        request["source_output"],
        "Independent failure reached the saved Builder retry/escalation limit",
        classification="execution",
    )
    if action not in ("pause", "defer"):
        return  # A preview must not charge, grant a retry, or switch any route.
    candidate["next_stage"] = "terra"
    cast(dict, state).clear()
    state.update(candidate)
    raise util.Paused(state["status"], state["stop_reason"])


def known_builder_pause(state: RunState):
    """Keep the existing raw pause when the same saved Builder policy owns it."""
    source = (state.get("resolution_request") or {}).get("source_output")
    key = builder_policy.key(state)
    lane = state.get("builder_retries", {}).get(key) or {}
    decision = (state.get("builder_retry_decisions") or [{}])[-1]
    return bool(
        source
        and state.get("status") == "PAUSED_BUILDER_RETRY_LIMIT"
        and state.get("next_stage") == "terra"
        and lane.get("action") == "pause"
        and lane.get("failures", [])[-1:] == [source]
        and decision.get("owner") == "autoresolver"
        and decision.get("action") == "pause"
        and decision.get("failure") == source
        and decision.get("milestone_key") == key
    )


def held_reason(state: RunState, text):
    """The reason of the novelty hold on the next stage's request when ``text`` starts with it, as that
    hold's stop text (hold_stop) and a stop reason composed from it do; otherwise None."""
    reason = (_request(state, state.get("next_stage")).get("novelty_hold") or {}).get("reason")
    return reason if isinstance(reason, str) and reason and str(text).startswith(reason) else None


def hold_stop(state: RunState, reason):
    """A novelty hold's stop text: ``reason``, then what is left of each budget the one scoped retry runs
    under, so a person deciding on it sees what remains (#611): the run's spend against its cost cap as
    `--status` prints it, then its active time against the time limit and the current milestone's time
    against its budget (autocode_time_left). A budget the run does not have adds nothing, as the cost
    stops do without a cap. The figures are read from ``state`` each time the stop is composed and are
    never saved with the reason: the hold's record and AutoResolver's request keep the bare reason. A
    request asked again composes its stop anew (autocode_resolver_runtime.record_operational_exhaustion),
    and restate_hold updates a saved stop when the run's settings change."""
    view = cost.view(state)
    stated = ([cost.line(view)] if view["cap_usd"] is not None else []) + time_left.lines(state)
    return reason + (f". {' '.join(stated)}" if stated else "")


# The budgets hold_stop states after a hold's reason (#611): with a cap, `cost.line` ends "; cap $N." and,
# when a stage ran more than once, a "Stage costs: ..." sentence whose only inner dots are in amounts
# (#1728), then autocode_time_left's active-time and milestone sentences.
_STATED_BUDGETS = re.compile(
    r"\.(?: (?:Cost: .*?; cap (?:under )?\$[\d,]+\.\d+\."
    r"|Stage costs: (?:[^.\n]|\.\d)+\."
    r"|Active time: [\d,.]+ s of the [\d,.]+ s limit\."
    r"|Milestones? [^\n]*?: [\d,.]+ s of (?:its|their) [\d,.]+ s budget\.))+"
)


def restate_hold(state: RunState):
    """Restate the budgets in a novelty hold's saved stop reason, as hold_stop composes them now, keeping
    the advice after them. autocode_run_setup calls it when it saves changed settings at a stop, so a cap,
    time limit or milestone budget changed at the hold is the one the stop states, whether or not the
    request is asked again."""
    text = state.get("stop_reason")
    reason = held_reason(state, text) if isinstance(text, str) else None
    if not reason:
        return
    rest = cast(str, text)[len(reason) :]
    stated = _STATED_BUDGETS.match(rest)
    rest = rest[stated.end() :] if stated else rest.removeprefix(".")
    head = hold_stop(state, reason)
    state["stop_reason"] = head + ("" if not rest or head.endswith(".") else ".") + rest


def _no_progress_outcome(incident, action, pointer, stage):
    """The outcome that ends a run whose incident repeated with nothing new after its scoped retry.

    No automatic step is left, and asking a person to retry again would hand them AutoCode's
    recovery machinery, so the run records this finished, failed outcome with its reason and
    evidence (autocode_stop.end) and launches no further stages."""
    failure = str(incident.failure).rstrip(".")
    try:
        step = role_names.role_name(incident.operation)  # the role a person knows the stage by
    except (KeyError, ValueError, AttributeError, TypeError):
        step = str(incident.operation)
    return {
        "reason": (
            f"the {step} step kept failing for the same reason after AutoCode's own "
            f"{'repair' if action == 'repair' else 'diagnosis'} retry, with nothing new to try: {failure}."
        ),
        "evidence": {
            "incident_id": incident.id,
            "operation": incident.operation,
            "invariant": incident.invariant,
            "failure": incident.failure,
            "action": action,
            "stage": stage,
            "packet": copy.deepcopy(pointer),
        },
    }


def recover_no_progress(runner, state: RunState, run_dir, workspace, error):
    """RECOVERY_CHAIN row (autocode_recovery_dispatch): a no-progress hold decides itself (#2485).

    An incident that repeats with nothing new holds its stage (novelty.NO_PROGRESS). A person used
    to authorize the one scoped retry with --resume-paused --retry-failed-stage; now AutoCode records
    that grant itself and the loop dispatches the stage again, where admission spends it
    (_automatic_grant) under the existing limits. When the same hold comes back after that retry,
    nothing automatic is left, so the run ends with a finished, failed outcome recorded as
    AutoCode's own stop (autocode_stop.end): never a request for a flag, token or packet
    (AGENTS.md "What a person is asked"). A hold for another reason, one whose binding no longer
    holds, or one with unsettled work pauses as before. Returns True when it recorded the grant.
    """
    if getattr(error, "status", None) != "PAUSED_NO_PROGRESS":
        return False
    request = _request(state, state.get("next_stage"))
    held = request.get("novelty_hold") or {}
    pointer = request.get("recovery_packet")
    stage = held.get("stage")
    # Holds saved before the bare cause was recorded carry it inside their stop text.
    cause = held.get("cause") or (novelty.NO_PROGRESS if novelty.NO_PROGRESS in str(held.get("reason") or "") else None)
    if (
        not held
        or not pointer
        or cause != novelty.NO_PROGRESS
        or state.get("next_stage") not in (stage, "orchestrator" if stage == "terra" else stage)
    ):
        return False
    if any(state.get(key) for key in recovery_limits.UNSETTLED):
        return False
    packet = load_packet(pointer, run_dir)
    revision = _source_revision(state, stage, pointer, packet, workspace)
    if not _holds(held.get("binding"), state, revision) or held.get("scope") != _scope(state):
        return False
    taken = _automatic_grants(state, packet, stage)
    if taken:
        spent = {row.get("grant_id") for row in receipts(state)}
        if not all(util.digest(grant) in spent for grant in taken):
            return False  # The grant stands unspent: another guard holds the stage, and its pause says which.
        action = "repair" if stage == "terra" else "diagnosis"
        incidents = [novelty.Incident(**row) for row in packet["incidents"]]
        incident = next((row for row in incidents if row.id == held.get("incident_id")), incidents[0])
        ended = _no_progress_outcome(incident, action, pointer, stage)
        stop.end(state, ended["reason"], ended["evidence"], at=util.now())
        runner.write_json(Path(run_dir) / "state.json", state)
        raise util.Paused(stop.STOP_STATUS, stop.stop_reason(state))
    grant = {
        "actor": AUTOMATIC_ACTOR,
        "at": util.now(),
        "novelty_packet": util.digest(packet),
        "binding": copy.deepcopy(packet["binding"]),
        "dispatch_binding": copy.deepcopy(held["binding"]),
        "stage": stage,
        "incident_id": held.get("incident_id"),
        "ordinal": len(state.get("failure_retry_authorizations", [])),
    }
    state.setdefault("failure_retry_authorizations", []).append(grant)
    state.pop("stop_reason", None)
    runner.write_json(Path(run_dir) / "state.json", state)
    print(f"{stage}: the same incident repeated with nothing new; AutoCode takes its one scoped retry", flush=True)
    return True


def admit_dispatch(state: RunState, record, workspace, run_dir, *, retry_authorization=None):
    """Run inside the caller's final locked admission, before any allowance charge."""
    stage = record.get("stage")
    if stage not in ("terra", "astra_resolve", "astra_diagnose") or record.get("report_only") or record.get("dry_run"):
        return
    request = _request(state, stage)
    pointer = request.get("recovery_packet")
    if not pointer:
        return  # Legacy requests retain their existing conservative limits.
    packet = load_packet(pointer, run_dir)
    if stage == "astra_resolve" and (
        request.get("source_output") != packet.get("source_output")
        or ("review" in request and util.digest(request["review"]) != util.digest(packet["current_error"]))
    ):
        _stale("pending resolution differs from its original bound source or decision")
    if packet.get("inputs"):
        try:
            current_inputs, current_pins = inputs.capture(state, run_dir)
        except (ValueError, OSError, KeyError) as error:
            _stale(str(error))
        if current_inputs != packet["inputs"] or current_pins != packet["input_pins"]:
            _stale("external input changed after incident capture")
    revision = _source_revision(state, stage, pointer, packet, workspace)
    bound = _binding(state, revision)
    # A repair has a newly assigned task, but the approved stable scope must match;
    # a pinned repair task may only narrow the failed task's criteria (#423).
    expected = {**packet["binding"], "task_id": bound["task_id"]} if stage == "terra" else packet["binding"]
    scope = _scope(state)
    within = packet["scope"] == scope
    if stage == "terra" and request.get("recovery_admission"):
        pin = request["recovery_admission"]
        path = _owned(pin["path"], run_dir)
        if not path.is_file() or util.file_hash(path) != pin["sha256"]:
            _stale("repair admission receipt is missing or changed")
        admission = util.read(path)
        if admission.get("packet") != pointer or admission.get("scope") != scope:
            _stale("repair admission changed packet or scope")
        expected = admission["binding"]
        within = _narrows(packet["scope"], scope, _decided(packet))
    if not _holds(expected, state, revision) or not within:
        _stale("current source, task, settings, contract or scope changed before admission")
    active = state.get("active_stage")
    if active and active is not record and active.get("output") != record.get("output"):
        raise util.Paused(
            "PAUSED_NO_PROGRESS", "Reconcile the owned active recovery attempt; no duplicate worker will launch"
        )
    if any(state.get(key) for key in ("uncertain_artifacts", "active_runner_check", "orchestration_batch")):
        raise util.Paused(
            "PAUSED_NO_PROGRESS", "Reconcile owned active or uncertain workers before recovery; do not restart them"
        )
    if stage == "astra_resolve":
        _exhausted_builder(state, request, packet)
    prior = receipts(state)
    dispatch_id = util.digest(
        {"output": record.get("output"), "started_at": record.get("started_at"), "packet": pointer, "stage": stage}
    )
    if record.get("recovery_novelty"):
        if record["recovery_novelty"].get("dispatch_id") != dispatch_id:
            _stale("saved dispatch receipt changed")
        return
    proposal = request.get("recovery_change") or packet["current_error"].get("recovery_change")
    # An unproven proposal is treated like none: it buys no novelty, and is never a stale
    # handoff (#418). It is still named in a hold, with the check it failed.
    change_id, unproven = _attest(packet, proposal)
    change = proposal if isinstance(proposal, dict) else {}
    action = "repair" if stage == "terra" else "diagnosis"
    considered = [row for row in receipts(state, returned=True) if action != "repair" or row.get("action") == "repair"]
    if (
        action == "repair"
        and packet["current_error"].get("operational_diagnosis")
        and any(entry.get("count", 0) for entry in packet["failure_history"].values())
    ):
        considered.append(
            {
                "incident_ids": [novelty.Incident(**row).id for row in packet["incidents"]],
                "action": "repair",
                "change_id": None,
            }
        )
    grant, grant_kind = _explicit_grant(state, packet, request, record, prior, retry_authorization)
    decisions = []
    for raw in packet["incidents"]:
        incident = novelty.Incident(**raw)
        same = [row for row in considered if incident.id in row.get("incident_ids", [])]
        is_input = change.get("target") in packet.get("permitted_controls", [])
        decision = novelty.decide(
            incident,
            considered,
            action=action,
            change_id=None if is_input else change_id,
            changed_input=change_id if is_input else None,
            explicit_grant=grant,
            expected_check=change.get("expected_check"),
            unresolved_question=change.get("question")
            or (
                f"Why does {incident.operation} violate {incident.invariant}: {incident.failure}?" if not same else None
            ),
            workers="uncertain"
            if any(state.get(key) for key in ("uncertain_artifacts", "active_runner_check", "orchestration_batch"))
            else "stopped",
        )
        if decision.action in ("hold", "request"):
            reason = (
                f"Incident {incident.id[:12]} ({incident.operation}): {decision.reason}. Exact packet: {pointer['path']}. "
                "After inspection, --resume-paused --retry-failed-stage authorizes one attempt under existing limits"
            )
            if proposal and change_id is None:
                reason += (
                    f". Proposed change is unproven ({unproven or 'unsupported grammar or unchanged structure'}), "
                    "not accepted as a new experiment"
                )
            request["novelty_hold"] = {
                "binding": bound,
                "scope": _scope(state),
                "stage": stage,
                "incident_id": incident.id,
                "reason": reason,
                "cause": decision.reason,  # the bare policy reason (recover_no_progress reads it, #2485)
            }
            efficiency.record_observation(
                state,
                event_id="recovery-hold:" + util.digest({"packet": pointer, "incident": incident.id}),
                kind="suppressed",
                category="diagnosis" if action == "diagnosis" else "build",
                reason="duplicate_no_new_information",
                provenance={"incident_id": incident.id, "packet": pointer, "reason": reason},
            )
            stop = hold_stop(state, reason)
            state.update({"stop_reason": stop})
            raise util.Paused("PAUSED_NO_PROGRESS", stop)
        decisions.append(decision)
    record["recovery_novelty"] = {
        "version": 1,
        "dispatch_id": dispatch_id,
        "incident_ids": [novelty.Incident(**row).id for row in packet["incidents"]],
        "action": action,
        "reason": decisions[0].reason,
        "change_id": change_id,
        "packet": copy.deepcopy(pointer),
        "grant_id": grant,
        "grant_kind": grant_kind,
        "expected_checks": [row["operation"] for row in packet["incidents"]],
    }
    if grant_kind == "invocation":
        retry_authorization["_novelty_consumed"] = True
