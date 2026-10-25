"""CODE-owned native launch continuity; no controller or private state reads.

UNAPPLIED source proposal. Kernel grants are conservative POSSIBLE writes,
not a claim that snapshots observe edited-and-restored writes. This module
refuses missing isolation/conformance/parent-publication integration. It does
not infer native image acceptance; NativeReviewHistory replays those semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import sys
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_command_receipt as command_receipts
    from . import autocode_contract_identity as contracts
    from . import autocode_design_manifest as design_manifest
    from . import autocode_native_assignment as assignments
    from . import autocode_native_cancellation as cancellation
    from . import autocode_native_capture_policy as capture_policy
    from . import autocode_native_check_runtime as check_runtime
    from . import autocode_native_linux_check as linux_check
    from . import autocode_native_locators as native_locators
    from . import autocode_native_stage_recovery as stage_recovery
    from . import autocode_output_cap as output_cap
    from . import autocode_process as processes
    from . import autocode_roles as roles
    from . import autocode_route_fit as route_fit
    from . import autocode_source_scope as source_scope
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_supervision as supervision
    from . import autocode_tool_containment as containment
    from . import autocode_util as util
    from . import autocode_verification_copy as copies
    from . import autocode_visual_acceptance as acceptance
    from . import autocode_visual_checkpoint as checkpoint
    from . import autocode_visual_history as history
    from . import autocode_visual_scope as scope
    from . import autocode_visual_source_transition as source_transition
    from . import autocode_writer_barrier as barriers
    from .providers import opencode_events
except ImportError:
    import autocode_command_receipt as command_receipts
    import autocode_contract_identity as contracts
    import autocode_design_manifest as design_manifest
    import autocode_native_assignment as assignments
    import autocode_native_cancellation as cancellation
    import autocode_native_capture_policy as capture_policy
    import autocode_native_check_runtime as check_runtime
    import autocode_native_linux_check as linux_check
    import autocode_native_locators as native_locators
    import autocode_native_stage_recovery as stage_recovery
    import autocode_output_cap as output_cap
    import autocode_process as processes
    import autocode_roles as roles
    import autocode_route_fit as route_fit
    import autocode_source_scope as source_scope
    import autocode_source_snapshot as source_snapshot
    import autocode_supervision as supervision
    import autocode_tool_containment as containment
    import autocode_util as util
    import autocode_verification_copy as copies
    import autocode_visual_acceptance as acceptance
    import autocode_visual_checkpoint as checkpoint
    import autocode_visual_history as history
    import autocode_visual_scope as scope
    import autocode_visual_source_transition as source_transition
    import autocode_writer_barrier as barriers
    from providers import opencode_events


POLICY = "kernel-grants-upper-bound-v1"
_ROW = re.compile(r"([0-9]{8})-([0-9a-f]{64})\.json")
_KINDS = frozenset(
    (
        "origin",
        "prepared",
        cancellation.HELD,
        cancellation.CANCELLED,
        "sealed",
        "released",
        "finished",
        "publication-prepared",
        "publication-committed",
        "approval-prepared",
        "approval-committed",
        "check-prepared",
        "check-sealed",
        "check-released",
        "check-finished",
        "capture-published",
        "assignment-prepared",
        "assignment-committed",
        "parent-commit-prepared",
        "parent-commit-acknowledged",
        "parent-commit-invalidated",
        "planning-committed",
    )
)


def _require(value, message):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native protection: " + message)


def _sha(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def _technical_hold(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except util.Paused:
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, ImportError) as error:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Malformed/incomplete native protection input") from error

    return wrapped


def _contract_approved(state: RunState):
    """Original predicate with absent/None preapproval compatibility only.

    A detached empty contract grants no approval. Malformed present contracts
    are refused rather than normalized, and the caller's state is unchanged.
    """
    _require(isinstance(state, dict), "missing original contract state")
    goal = state.get("goal_contract")
    _require(goal is None or isinstance(goal, dict), "malformed original contract")
    checked = {**state, "goal_contract": {}} if goal is None else state
    return contracts.approved(checked)


def record_route(record):
    """Saved route key from the original role, independent of stage name.

    run_role omits route_role when the route is record.role. An explicit route
    must remain a nonempty key; it never falls back to a stage or another model.
    This pure lookup grants no native/config/assignment authority.
    """
    _require(
        isinstance(record, dict) and isinstance(record.get("role"), str) and bool(record["role"].strip()),
        "missing original route role",
    )
    route = record.get("route_role")
    if route is None:
        return record["role"]
    _require(isinstance(route, str) and bool(route.strip()), "malformed explicit original route")
    return route


def _bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _json(raw):
    @_technical_hold
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "repeated protected JSON field")
            result[key] = value
        return result

    return json.loads(
        raw, object_pairs_hook=pairs, parse_constant=lambda _value: _require(False, "nonfinite protected JSON value")
    )


def possible_write_paths(grants, measured):
    """Pure union only. Neither arguments nor its result authenticate a launch.

    A directory entry represents its ENTIRE subtree, including future files.
    Keeping that entry avoids the unsound present-files-only expansion.
    """
    _require(isinstance(grants, list) and isinstance(measured, list), "missing write bound")
    result = []
    _require(
        all(isinstance(name, str) and not any(char in name for char in "*?[]") for name in grants),
        "source grants must be literal paths",
    )
    for name in [*grants, *measured]:
        _require(
            isinstance(name, str)
            and name
            and not Path(name).is_absolute()
            and ".." not in Path(name).parts
            and "\\" not in name
            and "\x00" not in name
            and name != "."
            and str(Path(name)) == name.rstrip("/")
            and not set(Path(name).parts) & {".autocode", ".autocode-ui", ".git", ".opencode"},
            "invalid source grant/change path",
        )
        name = name.rstrip("/")
        if name not in result:
            result.append(name)
    return result


def _within(name, grants, kinds):
    return any(name == grant or kinds[grant] == "directory" and name.startswith(grant + "/") for grant in grants)


_STABLE_RECORD_FIELDS = (
    "stage",
    "role",
    "route_role",
    "engine",
    "launch_route",
    "source_revision",
    "contract_revision",
    "contract_hash",
    "task_id",
    "events",
    "output",
    "reported_output",
    "before_ref",
    "after_ref",
    "expected_session",
)


def check_finished_record(original, final, *, source_after=None):
    """Bind stable original launch identity without freezing final-only facts.

    This pure comparison grants no process/native authority. The original
    protected held-root and actual supervisor checks remain separate.
    """
    _require(isinstance(original, dict) and isinstance(final, dict), "missing original/final record identity")
    for name in _STABLE_RECORD_FIELDS:
        expected = original.get(name)
        if name == "source_revision" and source_after is not None:
            _require(
                original["stage"] == original["role"] == "terra" and final.get(name) == source_after["revision"],
                "writer final source differs from the actual stopped snapshot",
            )
            continue
        if expected is not None:
            _require(final.get(name) == expected, "final record differs from original launch: " + name)
    _require(
        final.get("stage") == original.get("stage")
        and final.get("role") == original.get("role")
        and record_route(final) == record_route(original),
        "final stage/role/route differs from original launch",
    )
    return None


def check_parent_sink_identity(value, *, expected_path, byte_count):
    """Pure sink-shape guard, not an observer of a durable parent commit.

    The actual atomic JSON sink establishes regular-file/no-follow/single-link
    identity. Its mode field contains permission bits ONLY, not S_IFREG.
    """
    _require(
        isinstance(value, dict)
        and set(value) == {"path", "device", "inode", "bytes", "mode"}
        and value["path"] == str(expected_path)
        and all(type(value[key]) is int and value[key] >= 0 for key in ("device", "inode", "bytes", "mode"))
        and value["inode"] > 0
        and value["bytes"] == byte_count
        and value["mode"] <= 0o7777,
        "missing/malformed actual original parent sink identity",
    )
    return None


@_technical_hold
def check_initial_task_declaration(goal, decision, task, snapshot, findings_ledger):
    """Exact original lifecycle declaration shape, not approval/launch authority.

    Generated ID/time and automatic findings are normalized by the original
    lifecycle. The later committed full task identity remains exact, including
    these values; matching declaration alone cannot approve another assignment.
    """
    _require(isinstance(task, dict) and isinstance(findings_ledger, list), "missing normalized first task")
    spec = deepcopy(decision["next_task"])
    requested = spec.get("findings")
    open_ids = {row["id"] for row in findings_ledger if row.get("status") == "open"}
    selected = [] if spec["kind"] == "validate" else list(dict.fromkeys(requested)) if requested else sorted(open_ids)
    _require(set(selected) <= open_ids, "initial findings differ from actual open ledger")
    expected = {
        **spec,
        "objective": decision["next_objective"],
        "affected_paths": deepcopy(decision["affected_paths"]),
        "findings": selected,
        "contract_revision": goal["revision"],
        "contract_hash": goal["hash"],
        "source_revision": snapshot["revision"],
        "decision": "CONTINUE",
    }
    _require(
        set(task) == set(expected) | {"id", "assigned_at"}
        and all(task[key] == value for key, value in expected.items())
        and isinstance(task["id"], str)
        and re.fullmatch("task-[0-9a-f]{12}", task["id"]) is not None
        and isinstance(task["assigned_at"], str)
        and bool(task["assigned_at"]),
        "normalized first task differs from the original approved initial decision",
    )
    return None


@dataclass(frozen=True)
class PreparedLaunch:
    owner: object
    nonce: str
    row_id: str
    body: bytes


@dataclass(frozen=True)
class PreparedCheck:
    owner: object
    nonce: str
    row_id: str
    body: bytes


@dataclass(frozen=True)
class PreparedParentPublication:
    owner: object
    nonce: str
    row_id: str
    body: bytes


@dataclass(frozen=True)
class PreparedApproval:
    owner: object
    nonce: str
    row_id: str
    body: bytes


@dataclass(frozen=True)
class PreparedParentCommit:
    owner: object
    nonce: str
    row_id: str
    body: bytes


@dataclass(frozen=True)
class ParentCommitReceipt:
    owner: object
    nonce: str
    row_id: str
    body: bytes


class _WriterChainSnapshot:
    """Complete cold native rows for one concrete original writer operation.

    No check-boundary result is reused: its ambient copy/Git/capture inputs
    retain the original cold traversal. This process-local capability never
    authenticates a query, assignment or next-operation dispatch by itself.
    """

    def __init__(self, owner, execution, operation):
        self.owner, self.execution, self.operation = owner, execution, operation
        self.guard = None
        self.live = True
        self._identity()
        self._chain_values = deepcopy(owner._chain_cold())
        self.payload = self._payload(self._chain_values)
        self.ending = False

    @staticmethod
    def _payload(value):
        _bytes(value)  # Preserve strict original JSON validation, not key order loss.
        return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()

    @staticmethod
    def _eligible(rows):
        return not any(row["kind"] == "check-prepared" for row in rows)

    def _identity(self):
        value = self.execution
        authority, native = getattr(value, "authority", None), getattr(value, "native", None)
        nonce = getattr(native, "nonce", None)
        _require(
            self.live
            and type(self.owner) is NativeProtection
            and getattr(value, "owner", None) is self.owner
            and getattr(authority, "_owner", None) is self.owner
            and nonce is not None
            and self.owner._writer_executions.get(nonce) is value
            and self.owner._pending.get(nonce) is native,
            "foreign or expired original writer chain operation",
        )
        _require(authority._operation() is value, "foreign original writer execution identity")
        authority.admissions._context(self.operation)
        self.owner._ownership()

    def initial(self):
        self._identity()
        _require(
            self.owner._writer_chain_snapshot is self and self.guard is None,
            "writer chain initial custody is no longer available",
        )
        _require(
            type(self.payload) is bytes and self._payload(self._chain_values) == self.payload,
            "original cold writer chain content changed",
        )
        return deepcopy(self._chain_values)

    def activate(self, guard):
        self._identity()
        _require(
            self.owner._writer_chain_snapshot is self
            and self.guard is None
            and self.execution.authority._history_batch is guard
            and guard.owner is self.owner
            and guard.execution is self.execution
            and guard.operation is self.operation,
            "writer chain snapshot requires its exact original custody guard",
        )
        guard.check()
        _require(
            self.payload == guard.chain_payload and self._payload(self._chain_values) == guard.chain_payload,
            "writer chain differs from independent original cold custody",
        )
        self.guard = guard

    def read(self):
        self._identity()
        _require(
            self.owner._writer_chain_snapshot is self
            and self.guard is not None
            and self.execution.authority._history_batch is self.guard,
            "writer chain snapshot escaped its exact original custody scope",
        )
        self.guard.check()
        _require(
            type(self.payload) is bytes
            and self.payload == self.guard.chain_payload
            and self._payload(self._chain_values) == self.guard.chain_payload
            and self._payload(self.guard.chain) == self.guard.chain_payload,
            "complete original cold chain content or order changed",
        )
        # Eligibility comes from that exact authenticated cold content, never
        # a mutable reuse flag. Query-specific checks still run at each caller.
        value = (
            self._chain_values
            if not self.ending and self._eligible(self._chain_values[0])
            else self.owner._chain_cold()
        )
        self.guard.check()
        _require(
            self.payload == self.guard.chain_payload and self._payload(self._chain_values) == self.guard.chain_payload,
            "original cold chain changed while returning replay facts",
        )
        return deepcopy(value)

    def deactivate(self):
        self._identity()
        _require(
            self.owner._writer_chain_snapshot is self
            and self.guard is not None
            and self.execution.authority._history_batch is self.guard,
            "writer chain end lost its original custody scope",
        )
        self.guard.check()
        _require(
            type(self.payload) is bytes
            and self.payload == self.guard.chain_payload
            and self._payload(self._chain_values) == self.guard.chain_payload
            and self._payload(self.guard.chain) == self.guard.chain_payload,
            "original cold chain changed before its cold operation exit",
        )
        self.ending = True  # Final traversal cannot reuse any cached result.

    def close(self):
        self.live = False
        self.guard = None
        self._chain_values = None
        self.payload = None


class NativeProtection:
    """One parent journal writer with actual existing kernel lock handles.

    The caller supplies the file objects yielded by the EXISTING run/checkout
    ownership contexts, not paths, bools, metadata or a model callback. The
    exact reviewed sink v2 exposes those existing handles. Original guarded
    caller ordering and protected parent commits remain required integration.
    """

    @_technical_hold
    def __init__(
        self,
        workspace,
        run_dir,
        *,
        run_handle,
        checkout_handle,
        start_state,
        snapshot,
        activation_intent=None,
        planning_abandonment_intent=None,
    ):
        start_approved = _contract_approved(start_state)
        self.workspace = Path(workspace).absolute()
        self.run = Path(run_dir).absolute()
        self.root = self.run / "visual-native-protection"
        self.runtime = Path(__file__).resolve().parent
        self.pid = os.getpid()
        self.handles = (
            (run_handle, self.run / "writer.lock"),
            (checkout_handle, self.workspace / ".autocode" / "writer.lock"),
        )
        self._pending = {}
        self._active = {}
        self._pending_checks = {}
        self._active_checks = {}
        self._capture_boundaries = {}
        self._writer_executions = {}
        self._preparing_writer = None
        self._historical_replay = None
        self._writer_chain_snapshot = None
        self._writer_delta_validator = None
        self._assignment_validator = None
        self._assignments = {}
        self._publications = {}
        self._approvals = {}
        self._commits = {}
        self._commit_receipts = {}
        self._last_parent_ack = None
        self._ownership()
        if not self.root.exists():
            self.root.mkdir(mode=0o700)
        self._root_identity = self._inode(self.root)
        rows = self._rows()
        if not rows:
            # Attach before the FIRST native/check/planning route, never by
            # retrospectively blessing an already running or accepted pilot.
            _require(
                snapshot == util.snapshot(self.workspace)
                and not start_state.get("stages")
                and not start_state.get("current_task")
                and not any(
                    start_state.get(key)
                    for key in (
                        "active_stage",
                        "task_archive",
                        "uncertain_artifacts",
                        "pending_report_repair",
                        "parent_run",
                        "orchestration_history",
                    )
                )
                and not start_approved
                and not (self.run / "visual-native-history").exists(),
                "new native protection must precede every source-capable route",
            )
            self._append(
                "origin",
                {
                    "policy": POLICY,
                    "root_identity": self._root_identity,
                    "workspace": str(self.workspace),
                    "run_dir": str(self.run),
                    "source": deepcopy(snapshot),
                    "runtime_sha256": util.digest(history.runtime_files(self.runtime)),
                    **({"activation_intent": deepcopy(activation_intent)} if activation_intent is not None else {}),
                },
            )
        else:
            body = rows[0]["body"]
            _require(
                rows[0]["kind"] == "origin"
                and body["policy"] == POLICY
                and body["root_identity"] == self._root_identity
                and body["workspace"] == str(self.workspace)
                and body["run_dir"] == str(self.run)
                and body["runtime_sha256"] == util.digest(history.runtime_files(self.runtime)),
                "original namespace changed; never adopt/repin it",
            )
            _require(
                body.get("activation_intent") == activation_intent,
                "initial native activation inputs changed or are absent; no legacy adoption",
            )
        self._chain()
        if planning_abandonment_intent is None:
            self._source_continuity(snapshot)
        else:
            record = stage_recovery.constructor_record(planning_abandonment_intent, start_state)
            self.planning_abandonment(start_state, record, snapshot=snapshot, commit=False)

    @_technical_hold
    def require_planning_origin(self, state: RunState, *, snapshot):
        _require(
            not _contract_approved(state) and state.get("current_task") is None,
            "Scope 3 supports only original preapproval planning",
        )
        self._ownership()
        self._source_continuity(snapshot)
        self._launch_binding(state, snapshot)

    @_technical_hold
    def planning_abandonment(self, state: RunState, record, *, snapshot, commit=False):
        """Explicitly close one released readonly planning stop; never adopt its report.

        A restart cannot invent an original same-process finish. This narrow
        reconciliation instead requires the original sealed root's stopped
        supervisor and marks the retained response rejected, under both locks.
        """
        goal = state.get("goal_contract") or {}
        _require(
            not _contract_approved(state)
            and state.get("current_task") is None
            and goal.get("approval_status") != "approved"
            and not goal.get("approval_event"),
            "planning abandonment is preapproval only",
        )
        self._ownership()
        _require(
            not self._active and not self._pending and not self._active_checks and not self._pending_checks,
            "planning abandonment cannot borrow a current native handle",
        )
        rows, prepared, sealed, released, finished = self._chain()
        if state.get("status") == "PAUSED_PROCESS_CLEANUP":
            retained, terminal = cancellation.authenticate(
                rows=rows,
                prepared=prepared,
                sealed=sealed,
                released=released,
                finished=finished,
                state=state,
                record=record,
                snapshot=snapshot,
                binding=self._launch_binding(state, snapshot),
                fresh_snapshot=self._fresh_snapshot(rows),
                safe=self._safe,
                read_json=_json,
                check_record=check_finished_record,
            )
            if commit:
                after = self._safe(retained["after_ref"])
                if not after.exists():
                    util.atomic_json(after, snapshot)
                terminal["pins"][str(after)] = util.file_hash(after)
                self._ownership()
                self._pins(terminal["pins"])
                _require(snapshot == self._fresh_snapshot(rows), "source changed while cancelling held launch")
                self._append(cancellation.CANCELLED, terminal)
            return retained
        pending = set(prepared) - set(finished)
        _require(
            len(pending) == 1 and not any(row["kind"].startswith("approval-") for row in rows),
            "missing sole unapproved unfinished native stage",
        )
        ident = next(iter(pending))
        body = prepared[ident]["body"]
        _require(
            ident in sealed
            and ident in released
            and not body["grants"]
            and body["boundary"]["allow_write"] is False
            and body["binding"]["task"] is None,
            "timeout lacks its original released readonly planning root",
        )
        current = self._launch_binding(state, snapshot)
        _require(
            body["binding"] == current
            and snapshot == rows[0]["body"]["source"]
            and snapshot == self._fresh_snapshot(rows),
            "planning abandonment changed source, draft, settings or runtime",
        )
        retained = deepcopy(record)
        after_path = Path(body["record_identity"]["after_ref"])
        retained.update(after_ref=str(after_path), source_revision=snapshot["revision"], changed_files=[])
        check_finished_record(body["record_identity"], history.record_identity(retained))
        metadata = record.get("supervision") or {}
        original_supervision = sealed[ident]["body"]["barrier"]["supervision_envelope"]["supervision"]
        root = sealed[ident]["body"]["root"]
        _require(
            metadata == original_supervision
            # Admission retains SID; the sealed ProcessTree root carries only birth fields.
            # Match the root as the original live envelope did, retaining the entire admission above.
            and processes.matches(original_supervision["provider"], root)
            and original_supervision["provider"].get("group") == root.get("group"),
            "timeout supervisor differs from the original sealed owner/keeper/nonce",
        )
        self._safe(metadata["receipt"], file=True)
        receipt = supervision.receipt(metadata)
        _require(
            isinstance(receipt, dict)
            and receipt.get("phase") == "stopped"
            and receipt.get("cause") in ("provider_stopped", "controller_finished", "stage_deadline")
            and not receipt.get("cleanup_error")
            and isinstance(receipt.get("processes"), list)
            and bool(receipt["processes"])
            and processes.matches(root, receipt.get("provider"))
            and receipt["provider"].get("group") == root.get("group"),
            "missing exact original stopped timeout supervisor",
        )
        _require(
            record.get("pid") == root["pid"] and isinstance(record.get("processes") or [], list),
            "saved timeout root/inventory differs from the original seal",
        )
        originals = [
            *(record.get("processes") or []),
            *receipt["processes"],
            receipt["provider"],
            receipt["keeper"],
            original_supervision["owner"],
        ]
        _require(
            any(processes.matches(root, item) for item in receipt["processes"]),
            "stopped supervisor lost its original root inventory",
        )
        _require(not processes.live_processes(originals), "original native root/keeper/descendants remain live")
        length_hash = None
        authentication = state.get("status") == "PAUSED_PROVIDER_UNCERTAIN"
        unparsed = state.get("status") == "PAUSED_INVALID_OUTPUT"
        absent_auth_reports = []
        if authentication:
            _require(
                receipt.get("cause") in ("provider_stopped", "controller_finished")
                and record.get("engine") == "opencode"
                and record.get("timed_out") is False
                and type(record.get("exit_code")) is int
                and record["exit_code"] > 0
                and not record.get("supervision_errors"),
                "authentication rejection contradicts its original collected supervisor",
            )
            raw_auth = self._safe(record["events"], file=True).read_bytes()
            length_hash = hashlib.sha256(raw_auth).hexdigest()
            _require(
                stage_recovery.authentication_rejection(raw_auth, expected_session=record.get("expected_session")),
                "authentication rejection has unknown, mixed or non-authentication transport",
            )
            output = self._safe(record["output"])
            absent_auth_reports = [output, output.with_suffix(".reported.json"), output.with_suffix(".response.txt")]
            if record.get("reported_output"):
                absent_auth_reports.append(self._safe(record["reported_output"]))
            _require(
                not any(path.exists() or path.is_symlink() for path in absent_auth_reports),
                "authentication rejection has a response or report; explicit uncertain reconciliation is still required",
            )
        if unparsed:
            _require(
                receipt.get("cause") in ("provider_stopped", "controller_finished")
                and record.get("engine") == "opencode"
                and record.get("timed_out") is False
                and not record.get("report_only")
                and not self._safe(record["output"]).exists()
                and not record.get("supervision_errors"),
                "unparsed launch contradicts its original collected supervisor",
            )
            output = self._safe(record["output"])
            absent_auth_reports = [output, output.with_suffix(".reported.json"), output.with_suffix(".response.txt")]
            if record.get("reported_output"):
                absent_auth_reports.append(self._safe(record["reported_output"]))
            _require(
                not any(path.exists() or path.is_symlink() for path in absent_auth_reports),
                "unparsed launch has a response or report; explicit uncertain reconciliation is still required",
            )
        if state.get("status") == output_cap.STATUS:
            _require(
                receipt.get("cause") in ("provider_stopped", "controller_finished"),
                "output limit contradicts its original collected supervisor cause",
            )
            _require(
                record.get("engine") == "opencode"
                and record.get("accounted") is True
                and record.get("timed_out") is False
                and record.get("exit_code") == 0
                # A report-only launch qualifies only as the counted launch of the pending repair.
                and (not record.get("report_only") or stage_recovery.repair_launch(state, record))
                and not any(
                    record.get(key) for key in ("dry_run", "rejected", "abandoned", "interrupted", "supervision_errors")
                ),
                "output limit is not an original collected planning stop",
            )
            raw_length = self._safe(record["events"], file=True).read_bytes()
            length_hash = hashlib.sha256(raw_length).hexdigest()
            text = raw_length.decode("utf-8")
            _require(output_cap.terminal_transport(text), "output limit has unknown, mixed or incomplete transport")
            rows_length = [json.loads(line) for line in text.splitlines() if line.strip()]
            _require(
                output_cap.authenticated(record, opencode_events.normalized_events(rows_length)),
                "output limit lacks its original session and structured terminal length",
            )
        before = _json(self._safe(record["before_ref"], file=True).read_bytes())
        _require(before == snapshot, "timeout before-image differs from unchanged source")
        after_path = self._safe(str(after_path))
        if after_path.exists():
            _require(
                _json(self._safe(str(after_path), file=True).read_bytes()) == snapshot,
                "retained timeout after-image is stale",
            )
        pins = {
            name: util.file_hash(self._safe(name, file=True))
            for name in (record["events"], record["before_ref"], metadata["receipt"])
        }
        if length_hash is not None:
            pins[record["events"]] = length_hash
        for key in ("output", "reported_output"):
            name = record.get(key)
            if name and self._safe(name).exists():
                pins[name] = util.file_hash(self._safe(name, file=True))
        if length_hash is not None:
            self._pins(pins)
        if not commit:
            return retained
        if not after_path.exists():
            util.atomic_json(after_path, snapshot)
        pins[str(after_path)] = util.file_hash(after_path)
        self._ownership()
        self._pins(pins)
        _require(
            not any(path.exists() or path.is_symlink() for path in absent_auth_reports),
            "report appeared while reconciling the unparsed launch"
            if unparsed
            else "authentication response appeared while reconciling the error-only stop",
        )
        _require(snapshot == self._fresh_snapshot(rows), "source changed while reconciling the timeout")
        retained.update(rejected=True, abandoned=True)
        self._append(
            "finished",
            {
                "prepared_id": ident,
                "record": history.record_identity(retained),
                "after": deepcopy(snapshot),
                "possible_writes": [],
                "pins": pins,
                "explicit_planning_abandonment": True,
            },
        )
        return retained

    @_technical_hold
    def observe_held_supervisor(self, handle, barrier, child):
        """Original supervisor-issued child, still blocked before native inspection."""
        _require(
            type(handle) is PreparedLaunch and handle.owner is self and self._pending.get(handle.nonce) is handle,
            "missing live original held preparation",
        )
        self._ownership()
        _require(
            type(barrier) is barriers.WriterBarrier
            and barrier._child is child
            and barrier._spawned
            and not barrier._released,
            "held admission needs the original blocked barrier/Popen",
        )
        envelope = supervision.launch_envelope(child)
        _require(barrier._envelope is envelope, "held admission lost its original supervisor capability")
        facts = envelope.authenticate(
            child,
            command=barrier._bootstrap,
            environment_sha256=barrier.environment_sha256,
            options_identity=barrier._launch_identity,
        )
        body = _json(handle.body)
        _require(
            body["command_sha256"] == barrier.command_sha256
            and body["environment_sha256"] == barrier.environment_sha256,
            "held admission target differs from original preparation",
        )
        metadata = facts["supervision"]
        observed = cancellation.held_body(handle.row_id, body, metadata)
        _require(metadata["owner"]["pid"] == self.pid, "held supervisor belongs to a foreign controller")
        receipt = supervision.receipt(metadata)
        _require(
            receipt is not None and receipt.get("phase") == "armed" and not receipt.get("cleanup_error"),
            "original held supervisor did not arm",
        )
        rows = self._rows()
        _require(
            not any(
                row["kind"] in (cancellation.HELD, "sealed", "released", "finished", cancellation.CANCELLED)
                and row["body"].get("prepared_id") == handle.row_id
                for row in rows
            ),
            "held supervisor was already observed or released",
        )
        return self._append(cancellation.HELD, observed)["id"]

    @_technical_hold
    def require_prepared_source(self, handle):
        """Freshness of this sole original pending launch, before target release.

        Generic idle/source-continuity remains strict. No reconstructed handle,
        historical crash gap, sealed/released preparation or second launch is
        admitted by this same-process envelope guard.
        """
        self._ownership()
        _require(
            type(handle) is PreparedLaunch
            and handle.owner is self
            and self._pending.get(handle.nonce) is handle
            and not self._active,
            "missing original same-process pending source handle",
        )
        rows, prepared, sealed, released, finished = self._chain()
        _require(
            set(prepared) - set(finished) == {handle.row_id}
            and handle.row_id not in sealed
            and handle.row_id not in released,
            "pending source handle cannot adopt unfinished/released history",
        )
        original = prepared[handle.row_id]
        body = _json(handle.body)
        _require(
            original["body"] == body and _bytes(original["body"]) == handle.body,
            "original pending source preparation changed",
        )
        source, _goal = source_transition.cursor(rows)
        runtime = util.digest(history.runtime_files(self.runtime))
        _require(
            body["binding"]["source"] == source == self._fresh_snapshot(rows)
            and body["binding"]["runtime_sha256"] == rows[0]["body"]["runtime_sha256"] == runtime,
            "pending original source/runtime changed before release",
        )
        self._pins(body["pins"])
        self._recheck_planning_inputs(body)
        self._isolation_pins(body["native_isolation"])

    @staticmethod
    def _inode(path):
        value = path.stat(follow_symlinks=False)
        return {"device": value.st_dev, "inode": value.st_ino}

    def _safe(self, value, *, file=False):
        path = Path(value).absolute()
        _require(
            path.name != "state.json"
            and ".." not in path.parts
            and path.is_relative_to(self.workspace)
            and path.resolve() == path
            and not any(
                parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(self.workspace)
            ),
            "unsafe private/source authority path",
        )
        if file:
            _require(
                path.is_file() and stat.S_ISREG(path.stat(follow_symlinks=False).st_mode),
                "missing regular authority file",
            )
        return path

    def _ownership(self):
        import fcntl

        _require(
            os.getpid() == self.pid
            and self.workspace.resolve() == self.workspace
            and self.run.resolve() == self.run
            and self.run.parent == self.workspace / ".autocode" / "runs"
            and self.run.is_dir(),
            "foreign actual owned run",
        )
        for handle, path in self.handles:
            _require(hasattr(handle, "fileno") and not handle.closed, "missing original kernel lock handle")
            saved = os.fstat(handle.fileno())
            actual = self._safe(path, file=True).stat(follow_symlinks=False)
            _require(
                stat.S_ISREG(saved.st_mode) and (saved.st_dev, saved.st_ino) == (actual.st_dev, actual.st_ino),
                "lock handle does not own the original lock inode",
            )
            # Reassert ownership on the same open-file description. No new
            # lock file, unlock, controller or independent acquisition exists.
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Original ownership unavailable") from error
        if hasattr(self, "_root_identity"):
            _require(self._inode(self._safe(self.root)) == self._root_identity, "protected namespace replaced")

    def _pins(self, pins):
        _require(isinstance(pins, dict) and pins, "missing original evidence pins")
        for name, expected in pins.items():
            _require(
                _sha(expected) and util.file_hash(self._safe(name, file=True)) == expected,
                "original retained evidence changed",
            )

    def _rows(self):
        self._ownership()
        result, previous = [], None
        for path in sorted(self.root.iterdir()):
            match = _ROW.fullmatch(path.name)
            _require(match is not None and path.is_file() and not path.is_symlink(), "unexpected journal member")
            raw = self._safe(path, file=True).read_bytes()
            row = _json(raw)
            _require(
                set(row) == {"version", "sequence", "previous", "kind", "body", "id"}
                and row["version"] == 1
                and row["sequence"] == len(result)
                and int(match[1]) == row["sequence"]
                and row["previous"] == previous
                and row["kind"] in _KINDS
                and row["id"] == match[2]
                and row["id"] == util.digest({key: value for key, value in row.items() if key != "id"})
                and _bytes(row) == raw,
                "journal gap/replacement/noncanonical row",
            )
            result.append(row)
            previous = row["id"]
        return result

    def _append(self, kind, body):
        self._ownership()
        _require(
            getattr(self, "_writer_chain_snapshot", None) is None,
            "native append is forbidden inside an original writer recheck",
        )
        _require(kind in _KINDS, "unknown journal operation")
        rows = self._rows()
        row = {
            "version": 1,
            "sequence": len(rows),
            "previous": rows[-1]["id"] if rows else None,
            "kind": kind,
            "body": deepcopy(body),
        }
        row["id"] = util.digest(row)
        path = self.root / (f"{row['sequence']:08d}-" + row["id"] + ".json")
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), "wb") as sink:
            sink.write(_bytes(row))
            sink.flush()
            os.fsync(sink.fileno())
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return row

    @contextmanager
    def _writer_chain_scope(self, execution, operation):
        _require(getattr(self, "_writer_chain_snapshot", None) is None, "overlapping original writer chain operation")
        snapshot = _WriterChainSnapshot(self, execution, operation)
        self._writer_chain_snapshot = snapshot
        try:
            yield snapshot
        finally:
            self._writer_chain_snapshot = None
            snapshot.close()

    def _chain(self):
        snapshot = getattr(self, "_writer_chain_snapshot", None)
        if snapshot is None:
            return self._chain_cold()
        _require(
            type(snapshot) is _WriterChainSnapshot and snapshot.owner is self,
            "foreign native chain snapshot capability",
        )
        return snapshot.read()

    def _chain_cold(self):
        rows = self._rows()
        source_transition.cursor(rows)
        prepared, sealed, released, finished = {}, {}, {}, {}
        previous_source = rows[0]["body"]["source"]
        checks, check_sealed, check_released, check_finished = {}, set(), set(), set()
        for row in rows[1:]:
            body, kind = row["body"], row["kind"]
            if kind == "approval-committed":
                _require(
                    set(prepared) == set(finished) and set(checks) == check_finished,
                    "approval overlaps an unfinished native operation",
                )
                previous_source = body["snapshot"]
            elif kind == "prepared":
                _require(
                    not (set(prepared) - set(finished)) and set(checks) == check_finished,
                    "overlapping/unfinished native operation",
                )
                _require(
                    body["binding"]["source"] == previous_source
                    and body["binding"]["runtime_sha256"] == rows[0]["body"]["runtime_sha256"],
                    "source/runtime escaped the actual original launch continuity",
                )
                self._pins(body["pins"])
                self._recheck_planning_inputs(body)
                self._isolation_pins(body["native_isolation"])
                prepared[row["id"]] = row
            elif kind == cancellation.HELD:
                ident = body["prepared_id"]
                _require(
                    ident in prepared and ident not in sealed and ident not in finished,
                    "held supervision without its untouched original preparation",
                )
                cancellation.held_row(rows[: row["sequence"] + 1], ident, prepared[ident]["body"])
            elif kind == cancellation.CANCELLED:
                ident = body["prepared_id"]
                _require(ident in prepared and ident not in finished, "foreign/repeated held cancellation")
                held = cancellation.held_row(rows, ident, prepared[ident]["body"])
                _require(held["sequence"] < row["sequence"], "cancellation precedes original held admission")
                self._pins(body["pins"])
                cancellation.check_terminal(
                    body, prepared[ident]["body"], held, released=ident in released, check_record=check_finished_record
                )
                _require(
                    _json(self._safe(body["record"]["before_ref"], file=True).read_bytes()) == body["after"]
                    and _json(self._safe(body["record"]["after_ref"], file=True).read_bytes()) == body["after"],
                    "cancelled launch lost its unchanged original source",
                )
                finished[ident] = row  # Closed for continuity, never successful finished evidence.
            elif kind in ("sealed", "released", "finished"):
                ident = body["prepared_id"]
                _require(ident in prepared, "launch phase without original preparation")
                if kind == "sealed":
                    _require(ident not in sealed and ident not in finished, "replayed root seal")
                    sealed[ident] = row
                elif kind == "released":
                    _require(
                        ident in sealed and ident not in released and ident not in finished,
                        "release without exact original seal",
                    )
                    released[ident] = row
                else:
                    _require(ident in released and ident not in finished, "finish without release or repeated finish")
                    self._pins(body["pins"])
                    before, after = prepared[ident]["body"]["binding"]["source"], body["after"]
                    original = prepared[ident]["body"]["record_identity"]
                    check_finished_record(
                        original,
                        body["record"],
                        **({"source_after": after} if prepared[ident]["body"]["boundary"]["allow_write"] else {}),
                    )
                    _require(
                        body["record"]["source_revision"] == after["revision"]
                        and _json(self._safe(body["record"]["before_ref"], file=True).read_bytes()) == before
                        and _json(self._safe(body["record"]["after_ref"], file=True).read_bytes()) == after
                        and (
                            body["record"]["reported_output"] is None
                            or body["record"]["reported_output"]
                            == str(Path(original["output"]).with_suffix(".reported.json"))
                        ),
                        "retained stage finish lost original source/snapshot identity",
                    )
                    changed = util.changed_paths(before, after)
                    grants, kinds = prepared[ident]["body"]["grants"], prepared[ident]["body"]["grant_kinds"]
                    _require(
                        all(_within(name, grants, kinds) for name in changed), "net change outside enforced grant bound"
                    )
                    _require(
                        body["possible_writes"] == possible_write_paths(grants, changed), "changed write-bound policy"
                    )
                    finished[ident] = row
                    previous_source = after
            elif kind == "check-prepared":
                _require(
                    set(prepared) == set(finished) and set(checks) == check_finished,
                    "check overlaps unfinished native operation",
                )
                _require(
                    body["binding"]["source"] == previous_source
                    and body["binding"]["runtime_sha256"] == rows[0]["body"]["runtime_sha256"],
                    "check source/runtime escaped original continuity",
                )
                self._pins(body["pins"])
                self._check_boundary(body)
                checks[row["id"]] = row
            elif kind in ("check-sealed", "check-released", "check-finished"):
                ident = body["prepared_id"]
                _require(ident in checks and ident not in check_finished, "foreign/replayed check phase")
                if kind == "check-sealed":
                    _require(ident not in check_sealed, "repeated check seal")
                    check_sealed.add(ident)
                elif kind == "check-released":
                    _require(ident in check_sealed and ident not in check_released, "check release before seal")
                    check_released.add(ident)
                else:
                    _require(
                        ident in check_released and body["after"] == checks[ident]["body"]["binding"]["source"],
                        "check finish without original release or readonly source",
                    )
                    self._pins(body["pins"])
                    _require(
                        command_receipts.completed(body["result"])
                        and all(key in body["result"] for key in command_receipts.OWNERSHIP_FIELDS),
                        "check finish lacks authenticated command ownership",
                    )
                    check_finished.add(ident)
        return rows, prepared, sealed, released, finished

    def _idle(self):
        rows, prepared, _sealed, _released, finished = self._chain()
        _require(
            set(prepared) == set(finished)
            and sum(row["kind"] == "check-prepared" for row in rows)
            == sum(row["kind"] == "check-finished" for row in rows),
            "actual launch/check crash gap remains; no automatic backfill",
        )
        return rows, prepared, finished

    def _binding(self, state: RunState, snapshot):
        _require(snapshot == source_scope.snapshot(self.workspace, state), "source changed at the CODE boundary")
        return history._task_binding(state, snapshot, util.digest(history.runtime_files(self.runtime)))

    def _launch_binding(self, state: RunState, snapshot):
        history._no_credentials(state["settings"])
        if _contract_approved(state):
            return self._binding(state, snapshot)
        _require(
            state.get("current_task") is None and snapshot == util.snapshot(self.workspace),
            "preapproval launch cannot have an assigned task or changed source",
        )
        # Draft native planning is retained only as protected source continuity;
        # it grants no image, approved owner or current task authority.
        return {
            "contract": deepcopy(state.get("goal_contract")),
            "task": None,
            "settings": deepcopy(state["settings"]),
            "source": deepcopy(snapshot),
            "runtime_sha256": util.digest(history.runtime_files(self.runtime)),
        }

    def _source_continuity(self, snapshot):
        rows, _prepared, finished = self._idle()
        last, _goal = source_transition.cursor(rows)
        _require(
            snapshot == last == self._fresh_snapshot(rows),
            "source changed outside the original protected native launch chain",
        )

    def _fresh_snapshot(self, rows=None):
        rows = self._chain()[0] if rows is None else rows
        _last, goal = source_transition.cursor(rows)
        return (
            source_snapshot.snapshot(self.workspace, paths=source_scope.declared_paths(goal["body"]))
            if goal is not None
            else util.snapshot(self.workspace)
        )

    def _commit_source(self, snapshot, candidate, approval=None):
        if approval is None:
            self._source_continuity(snapshot)
        else:
            body = self._approved_candidate(approval, candidate, snapshot)
            self._source_continuity(body["snapshot"])

    def _isolation_pins(self, descriptor):
        _require(descriptor["version"] == 1, "missing original concrete native isolation descriptor")
        self._pins({**descriptor["files"], descriptor["manifest"]: descriptor["manifest_sha256"]})
        native = descriptor["child_identity"]
        binary = Path(native["executable"])
        _require(
            binary.is_absolute()
            and binary.resolve() == binary
            and binary.is_file()
            and not binary.is_symlink()
            and stat.S_ISREG(binary.stat().st_mode)
            and _sha(native["executable_sha256"])
            and util.file_hash(binary) == native["executable_sha256"],
            "original native executable changed",
        )

    def _planning_input_pins(self, binding, record):
        # A report-only repair binds the same planning inputs as the stage it repairs.
        stage = str(record.get("stage") or "").removesuffix("_report_repair")
        if binding["task"] is not None or stage not in roles.ALL_PLANNING_STAGES:
            return {}
        pins = design_manifest.planning_input_pins(binding["settings"], role=record_route(record))
        for name in pins:
            path = self._safe(name, file=True)
            _require(
                path.parent.parent.parent == self.workspace / ".autocode",
                "planning inputs are outside the original workspace input namespace",
            )
        return pins

    def _recheck_planning_inputs(self, body):
        pins = self._planning_input_pins(body["binding"], body["record_identity"])
        _require(
            all(body["pins"].get(path) == digest for path, digest in pins.items()),
            "prepared planning inputs differ from their original pins",
        )

    def _native_config(
        self, boundary, command, environment, state: RunState, record, native_role_isolation, *, writer_execution=None
    ):
        """Verify concrete original bytes; no callback or expected-shape proof.

        The narrow upstream retention/isolation correction is still required:
        current configure receipts omit these effective fields. Refusal is
        intentional; missing fields cannot be invented from final config.
        """
        try:
            from . import autocode_native_conformance as native_conformance
            from . import autocode_native_role_isolation as isolation
        except ImportError:
            import autocode_native_conformance as native_conformance
            import autocode_native_role_isolation as isolation
        if type(native_role_isolation) is isolation.KiloRoleIsolation:
            descriptor = native_role_isolation.verify_launch(
                state=state,
                command=command,
                environment=environment,
                boundary=boundary,
                writer_execution=writer_execution,
            )
            _require(
                boundary["allow_write"] is False and descriptor.get("backend") == "kilocode",
                "Kilo reviewer cannot acquire writer or borrowed backend authority",
            )
            self._isolation_pins(descriptor)
            pins = {
                name: util.file_hash(self._safe(name, file=True))
                for name in (
                    boundary["profile"],
                    boundary["shell"],
                    boundary["conformance"],
                    boundary["client_profile"],
                )
            }
            pins.update(descriptor["files"])
            pins.update(native_role_isolation.image_pins(record.get("visual_runtime")))
            pins[descriptor["manifest"]] = descriptor["manifest_sha256"]
            return pins, descriptor
        containment.verify(boundary)
        _require(
            type(native_role_isolation) is isolation.NativeRoleIsolation,
            "missing concrete CODE-owned native role isolation authority",
        )
        if boundary["allow_write"]:
            _require(
                writer_execution is not None
                and writer_execution.owner is self
                and (
                    self._preparing_writer is writer_execution
                    or writer_execution.native is not None
                    and self._writer_executions.get(writer_execution.native.nonce) is writer_execution
                ),
                "writable isolation lacks original concrete writer preparation",
            )
        descriptor = native_role_isolation.verify_launch(
            state=state,
            command=command,
            environment=environment,
            boundary=boundary,
            **({"writer_execution": writer_execution} if boundary["allow_write"] else {}),
        )
        self._isolation_pins(descriptor)
        _require(
            isinstance(command, list)
            and command
            and Path(command[0]).is_absolute()
            and Path(command[0]).resolve() == Path(command[0]),
            "target executable must be resolved/pinned",
        )
        _require(
            record.get("engine") == "opencode"
            and boundary["workspace"] == str(self.workspace)
            and boundary["tools"] == ["bash"]
            and native_locators.boundary_matches(environment.get("AUTOCODE_TOOL_CONTAINMENT", "null"), boundary)
            and environment.get("SHELL") == boundary["shell"],
            "target lost actual kernel shell boundary",
        )
        config = _json(environment.get("OPENCODE_CONFIG_CONTENT", "null"))
        _require(
            isinstance(config, dict) and environment.get("OPENCODE_DISABLE_PROJECT_CONFIG") == "1",
            "unisolated project/native config cannot establish continuity",
        )
        proof = _json(self._safe(boundary["conformance"], file=True).read_bytes())
        actual = native_conformance.verify(proof["launch_identity"], command, environment)
        _require(
            actual["version"] == 1
            and actual["effective_tools"].get("bash") is True
            and all(value is False for key, value in actual["effective_tools"].items() if key != "bash")
            and actual["final_inline_sha256"]
            == hashlib.sha256(environment["OPENCODE_CONFIG_CONTENT"].encode()).hexdigest()
            and actual["executable"] == command[0] == descriptor["child_identity"]["executable"]
            and actual["executable_sha256"] == descriptor["child_identity"]["executable_sha256"]
            and actual["command_sha256"] == util.digest(command)
            and descriptor["child_identity"]["command_sha256"] == util.digest(command)
            and descriptor["child_identity"]["environment_sha256"] == actual["environment_sha256"],
            "actual native tool/config/binary/final target differs",
        )
        _require(
            config["autoupdate"] is False
            and descriptor["conformance"]
            == {"path": boundary["conformance"], "sha256": boundary["conformance_sha256"]},
            "final concrete isolation/conformance binding differs",
        )
        agent = command[command.index("--agent") + 1]
        definition = config["agent"][agent]
        permission = definition["permission"]
        rules = actual["effective_permission"]
        # Retain native permission order; an unordered JSON map cannot prove
        # the last-match bash policy. Unknown categories or malformed native
        # rows must not disappear during filtering.
        _require(
            isinstance(rules, list)
            and all(
                isinstance(row, dict)
                and set(row) == {"permission", "pattern", "action"}
                and all(isinstance(value, str) for value in row.values())
                for row in rules
            ),
            "malformed actual ordered native permissions",
        )
        _require(
            actual["effective_agent"] == agent
            and permission["*"] == permission["external_directory"] == "deny"
            and isinstance(permission["bash"], dict)
            and permission["bash"]
            and all(action in ("allow", "deny") for action in permission["bash"].values())
            and containment.shell_permissions(rules) == permission
            and list(containment.shell_permissions(rules)["bash"].items()) == list(permission["bash"].items())
            and native_conformance.non_shell_denied(rules)
            and config["shell"] == boundary["shell"],
            "exact effective shell-only policy changed",
        )
        route = state["settings"]["roles"][record_route(record)]
        _require(
            command[command.index("--model") + 1] == route["model"]
            and command[command.index("--variant") + 1] == route["reasoning_effort"],
            "saved route/reasoning changed",
        )
        pins = {
            name: util.file_hash(self._safe(name, file=True))
            for name in (boundary["profile"], boundary["shell"], boundary["conformance"])
        }
        startup = proof.get("readonly_startup_boundary")
        if boundary["allow_write"]:
            _require(
                isinstance(startup, dict)
                and startup["allow_write"] is False
                and startup["workspace"] == str(self.workspace)
                and startup["write_paths"] == [],
                "writer lacks its original readonly native setup boundary",
            )
            containment.verify(startup)
            for key in ("profile", "shell", "conformance"):
                path = self._safe(startup[key], file=True)
                _require(path.is_relative_to(self.run), "readonly startup control is outside its original run")
                pins[str(path)] = util.file_hash(path)
        else:
            _require(startup is None, "readonly final launch has an unrelated startup boundary")
        pins.update(descriptor["files"])
        pins.update(native_role_isolation.image_pins(record.get("visual_runtime")))
        pins[descriptor["manifest"]] = descriptor["manifest_sha256"]
        return pins, descriptor

    def _launch_record_identity(self, record, binding, command):
        original = history.record_identity(record)
        _require(
            record.get("command") == command
            and original["engine"] == "opencode"
            and isinstance(original["stage"], str)
            and original["stage"]
            and isinstance(original["role"], str)
            and original["role"]
            and isinstance(original["launch_route"], dict),
            "record is not the actual prepared native target",
        )
        route = binding["settings"]["roles"][record_route(original)]
        _require(
            original["launch_route"]
            == {key: route.get(key) for key in ("engine", "provider", "model", "reasoning_effort")},
            "prepared record changed saved route",
        )
        if binding["task"] is not None:
            _require(
                original["task_id"] == binding["task"]["id"]
                and original["contract_hash"] == binding["contract"]["hash"]
                and original["contract_revision"] == binding["contract"]["revision"],
                "prepared record changed actual approved task/contract",
            )
        for name in ("events", "output", "before_ref"):
            path = self._safe(original[name], file=name == "before_ref")
            _require(path.is_relative_to(self.run), "original launch artifact slot is outside its run")
        before = self._safe(original["before_ref"], file=True)
        _require(
            before.name.endswith(".before.json") and _json(before.read_bytes()) == binding["source"],
            "original before snapshot/slot differs from actual launch source",
        )
        expected_after = str(before.with_name(before.name[: -len(".before.json")] + ".after.json"))
        _require(original["after_ref"] in (None, expected_after), "original after snapshot slot differs")
        original["after_ref"] = expected_after
        if original["reported_output"] is not None:
            _require(
                original["reported_output"] == str(Path(original["output"]).with_suffix(".reported.json")),
                "original reported-output slot differs",
            )
        return original

    def _initial_task_authority(self, binding, rows, *, historical=False):
        origin, selected, _source, _chain = assignments.committed_chain(rows, binding, historical=historical)
        by_id = {row["id"]: row for row in rows}
        # Historical selection never excuses a corrupt newer decision or sink.
        for row in rows:
            if row["kind"] != "assignment-committed":
                continue
            _require(self._assignment_validator is not None, "original assignment decision validator missing")
            self._assignment_validator(row["body"])
            ack = by_id[row["body"]["parent_commit_id"]]["body"]
            check_parent_sink_identity(
                ack["sink_identity"], expected_path=self.run / "state.json", byte_count=ack["sink_identity"]["bytes"]
            )
        return selected if selected is not origin else None

    @_technical_hold
    def writer_history_authority(self, binding):
        """Historical writer identity from its own original causal assignment."""
        rows = self._chain()[0]
        ordinary = {key: binding[key] for key in ("contract", "task", "settings", "source", "runtime_sha256")}
        origin, selected, source, _chain = assignments.committed_chain(rows, ordinary, historical=True)
        self._initial_task_authority(ordinary, rows, historical=True)
        task = binding["task"]
        grants = assignments.writer_paths(binding["contract"]["body"])
        _require(
            task["affected_paths"]
            and all(path in grants for path in task["affected_paths"])
            and all(not (self.workspace / path).is_dir() for path in grants),
            "writer history broadened approved leaves or included a directory",
        )
        _require(
            task.get("kind") == "implement"
            and binding["source"] == binding["assignment_source"] == source
            and binding["policy"] == scope.POLICY
            and binding["manifest_body"] == binding["settings"]["design_manifest"]["body"],
            "writer history changed original task source, manifest or policy",
        )
        pins = self._journal_pins([row for row in rows if row["sequence"] <= selected["sequence"]])
        if selected is not origin:
            pins.update(selected["body"]["pins"])
        self._pins(pins)
        return {
            "binding": deepcopy(binding),
            "approval_id": origin["id"],
            "evidence_hashes": pins,
            "protected_refs": list(pins),
        }

    @_technical_hold
    def rework_writer_authority(self, state: RunState, *, snapshot):
        rows, prepared, _finished = self._idle()
        self._source_continuity(snapshot)
        binding = self._binding(state, snapshot)
        selected = self._initial_task_authority(binding, rows)
        _require(
            selected is not None
            and selected["body"]["decision"]["status"] == "REWORK"
            and cast(dict, state["current_task"])["kind"] == "implement"
            and not state.get("active_stage")
            and not any(row["body"]["binding"]["task"] == binding["task"] for row in prepared.values()),
            "repair writer needs its untouched actual committed REWORK task",
        )
        return self.writer_history_authority(
            {
                **binding,
                "policy": scope.POLICY,
                "manifest_body": deepcopy(state["settings"]["design_manifest"]["body"]),
                "assignment_source": deepcopy(selected["body"]["binding"]["source"]),
            }
        )

    @_technical_hold
    def require_task_authority(self, state: RunState, *, snapshot):
        """Original caller MUST invoke before provider/SDK/native setup.

        First-task identity comes from the actual operator/sink journal, never
        a task ID or current saved dictionary alone. Later causal tasks remain
        held until a concrete original assignment/WriterAdmissions seam exists.
        """
        rows, _prepared, _finished = self._idle()
        self._source_continuity(snapshot)
        binding = self._binding(state, snapshot)
        return self._initial_task_authority(binding, rows)

    @_technical_hold
    def first_writer_authority(self, state: RunState, *, snapshot):
        """Fresh original first-implement authority, never provider permission.

        Replay the actual approval preparation, normalized parent intent/ACK
        and committed assignment. No saved binding, token or current snapshot
        can replace those original rows. This bounded API deliberately stops
        before a first writer preparation, including on reopened native runs.
        """
        return self._writer_origin(state, snapshot=snapshot, untouched=True)

    @_technical_hold
    def writer_origin_authority(self, state: RunState, *, snapshot):
        """Reauthenticate original approval while its exact first writer proceeds.

        This is not dispatch authority: the concrete adapter must separately
        join pending handles, original effective isolation, ordinary durable
        admission, and the actual native root/stop. No later owner is admitted.
        """
        return self._writer_origin(state, snapshot=snapshot, untouched=False)

    def _writer_origin(self, state: RunState, *, snapshot, untouched):
        rows, prepared = (self._idle() if untouched else self._chain())[:2]
        self._ownership()
        cursor, _goal = source_transition.cursor(rows)
        _require(snapshot == cursor == self._fresh_snapshot(rows), "source escaped original first writer chain")
        binding = self._binding(state, snapshot)
        self._initial_task_authority(binding, rows)
        _require(
            (
                not untouched
                or (
                    not any(row["body"]["binding"]["task"] is not None for row in prepared.values())
                    and not state.get("active_stage")
                )
            )
            and not any(
                state.get(key)
                for key in (
                    "active_runner_check",
                    "uncertain_artifacts",
                    "pending_report_repair",
                    "parent_run",
                    "parent_batch",
                    "orchestration_batch",
                    "orchestration_history",
                    "progressive",
                )
            ),
            "only the untouched original first writer is supported",
        )
        ledger = self.run / "visual-writer-admissions"
        if untouched:
            _require(
                not ledger.is_symlink()
                and (
                    not ledger.exists()
                    or ledger.is_dir()
                    and not any(path.name != "ledger.lock" for path in ledger.iterdir())
                ),
                "retained first-writer intent cannot be retried without causal recovery",
            )
        outcomes = self.run / "native-parent-outcomes"
        _require(
            not outcomes.is_symlink()
            and (not outcomes.exists() or (outcomes.is_dir() and not any(outcomes.iterdir()))),
            "retained parent failure cannot supply first-writer authority",
        )
        original = next(
            row
            for row in rows
            if row["kind"] == "approval-committed" and row["body"]["contract"] == binding["contract"]
        )
        approved = original["body"]
        task, goal = binding["task"], binding["contract"]
        manifest = state["settings"]["design_manifest"]["body"]
        admitted = scope.admit(goal["body"], manifest)
        _require(
            admitted is not None
            and task.get("kind") == "implement"
            and not task.get("milestone_ids")
            and task.get("milestone_id") == admitted["milestone_ids"][0]
            and (not untouched or snapshot == approved["snapshot"]),
            "first writer differs from its original assignment inventory or owner",
        )
        check_initial_task_declaration(
            goal, approved["decision"], task, approved["snapshot"], state.get("findings_ledger", [])
        )
        criteria = task.get("criterion_ids", task.get("acceptance_criteria"))
        _require(
            criteria == goal["body"]["milestones"][0]["acceptance_criteria"]
            and (
                "criterion_ids" not in task
                or "acceptance_criteria" not in task
                or task["criterion_ids"] == task["acceptance_criteria"]
            ),
            "first writer must retain the complete original owner criteria",
        )
        # Derivation validates exact case/path ownership, not image acceptance.
        scope.derive(
            goal["body"], manifest, task, accepted_milestones=(), accepted_case_input_paths={}, changed_paths=[]
        )
        approval = next(row for row in rows if row["id"] == approved["prepared_id"])
        intent = approval["body"]
        planning = [
            row
            for row in rows
            if row["kind"] == "planning-committed"
            and row["sequence"] < approval["sequence"]
            and row["body"]["contract"] == intent["draft_contract"]
            and row["body"]["settings"] == intent["settings"]
            and row["body"]["source"] == intent["snapshot"]
            and row["body"]["token"] == intent["selected"]
        ]
        _require(len(planning) == 1, "missing original reviewed planning publication")
        # Original approval proof stays byte-stable as later causal rows append.
        pins = {
            **self._journal_pins([row for row in rows if row["sequence"] <= original["sequence"]]),
            **planning[0]["body"]["pins"],
        }
        self._pins(pins)
        _require(
            self._rows() == rows and self._binding(state, snapshot) == binding,
            "original first-writer facts changed during authentication",
        )
        return {
            "binding": {
                **binding,
                "source": deepcopy(approved["snapshot"]),
                "policy": scope.POLICY,
                "manifest_body": deepcopy(manifest),
                "assignment_source": deepcopy(approved["snapshot"]),
            },
            "approval_id": original["id"],
            "evidence_hashes": pins,
            "protected_refs": list(pins),
        }

    def _original_readonly_plan_pins(self, rows, approval):
        selected = next(row for row in rows if row["id"] == approval["body"]["prepared_id"])["body"]
        planning = [
            row
            for row in rows
            if row["kind"] == "planning-committed"
            and row["sequence"] < approval["sequence"]
            and row["body"]["contract"] == selected["draft_contract"]
            and row["body"]["settings"] == selected["settings"]
            and row["body"]["source"] == selected["snapshot"]
            and row["body"]["token"] == selected["selected"]
        ]
        _require(len(planning) == 1, "missing original reviewed plan publication")
        return planning[0]["body"]["pins"]

    @_technical_hold
    def readonly_task_authority(self, state: RunState, *, snapshot, review_record=None):
        binding = self._binding(state, snapshot)
        rows, prepared, finished = self._idle()
        self._source_continuity(snapshot)
        assignment = self._initial_task_authority(binding, rows)
        if cast(dict, state["current_task"])["kind"] == "validate" and assignment is None:
            return self.initial_validate_authority(state, snapshot=snapshot, review_record=review_record)
        assigned = [
            (p, finished[ident])
            for ident, p in prepared.items()
            if finished[ident]["kind"] == "finished"
            and p["body"]["binding"]["contract"] == binding["contract"]
            and isinstance(p["body"]["binding"]["task"], dict)
        ]
        builders = [(p, f) for p, f in assigned if p["body"]["record_identity"]["stage"] == "terra"]
        lineage = assignments.readonly_lineage(rows, binding)
        planning_pins = {}
        if lineage["kind"] == "native_builder":
            _require(builders, "readonly source needs an original stopped first Builder")
            builder, stopped = builders[-1]
            actual = stopped["body"]["record"]
            _require(
                builder["body"]["binding"]["task"] == lineage["task"]
                and stopped["body"]["after"] == snapshot
                and actual["exit_code"] == 0
                and not any(
                    actual.get(key)
                    for key in (
                        "timed_out",
                        "interrupted",
                        "rejected",
                        "report_only",
                        "report_repaired",
                        "repaired_by",
                        "dry_run",
                        "truncated_output",
                    )
                ),
                "readonly source lost its latest actual successful bounded Builder",
            )
        else:
            _require(
                not builders and not any(row["kind"] == "prepared" and row["body"]["grants"] for row in rows),
                "operator readonly lineage has prior writer provenance",
            )
            approved = lineage["approval"]["body"]
            check_initial_task_declaration(
                binding["contract"], approved["decision"], lineage["task"], snapshot, state.get("findings_ledger", [])
            )
            planning_pins = self._original_readonly_plan_pins(rows, lineage["approval"])
        reviews = [
            (p, f)
            for p, f in assigned
            if p["body"]["binding"]["task"] == binding["task"] and p["body"]["record_identity"]["stage"] == "sol"
        ]
        _require(len(reviews) <= 1, "multiple original Sol reviews for one task are unsupported")
        active = state.get("active_stage")
        if reviews:
            _require(
                isinstance(review_record, dict)
                and history.record_identity(review_record) == reviews[0][1]["body"]["record"]
                and review_record.get("exit_code") == 0
                and not any(
                    review_record.get(key)
                    for key in (
                        "timed_out",
                        "interrupted",
                        "rejected",
                        "report_only",
                        "report_repaired",
                        "repaired_by",
                        "dry_run",
                        "truncated_output",
                    )
                )
                and (not active or history.record_identity(active) == history.record_identity(review_record)),
                "readonly replay needs its exact original collected Sol review",
            )
        else:
            _require(review_record is None and not active, "readonly acquisition overlaps an active stage")
        pins = {**self._journal_pins(rows), **planning_pins}
        if lineage["kind"] == "native_builder":
            _require(self._writer_delta_validator is not None, "original stopped writer delta validator missing")
            self._writer_delta_validator(state, snapshot=snapshot, builder=builder, stopped=stopped)
            pins.update(stopped["body"]["pins"])
        self._pins(pins)
        if lineage["kind"] == "operator_supplied_source":
            _require(
                self._binding(state, snapshot) == binding and self._rows() == rows,
                "readonly validation lineage changed during original authentication",
            )
        return {
            "binding": binding,
            "approval_id": next(row["id"] for row in rows if row["kind"] == "approval-committed"),
            "pins": pins,
        }

    @_technical_hold
    def initial_validate_authority(self, state: RunState, *, snapshot, review_record=None):
        """Original first readonly assignment; no Builder or saved PASS substitute."""
        rows, prepared, _finished = self._idle()
        self._source_continuity(snapshot)
        binding = self._binding(state, snapshot)
        self._initial_task_authority(binding, rows)
        assigned = [row for row in prepared.values() if row["body"]["binding"]["task"] is not None]
        active = state.get("active_stage")
        if assigned:
            _require(isinstance(review_record, dict), "readonly replay requires its original finished review")
            _original_rows, original, stopped = self._finished_for(history.record_identity(review_record))
            _require(
                len(assigned) == 1
                and original["id"] == assigned[0]["id"]
                and original["body"]["binding"] == binding
                and original["body"]["record_identity"]["stage"] == "sol"
                and original["body"]["record_identity"]["role"] == "sol"
                and original["body"]["grants"] == []
                and stopped["body"]["after"] == snapshot
                and review_record.get("exit_code") == 0
                and not any(
                    review_record.get(key)
                    for key in (
                        "timed_out",
                        "interrupted",
                        "rejected",
                        "report_only",
                        "report_repaired",
                        "repaired_by",
                        "dry_run",
                    )
                )
                and (not active or history.record_identity(active) == history.record_identity(review_record)),
                "readonly replay lost its original collected fresh Sol review",
            )
            self._pins(stopped["body"]["pins"])
        else:
            _require(review_record is None and not active, "missing original independent review launch")
        _require(
            not any(
                state.get(key)
                for key in (
                    "active_runner_check",
                    "uncertain_artifacts",
                    "pending_report_repair",
                    "parent_run",
                    "parent_batch",
                    "orchestration_batch",
                    "orchestration_history",
                    "progressive",
                )
            ),
            "only original initial validation supports owned readonly checks",
        )
        outcomes = self.run / "native-parent-outcomes"
        _require(
            not outcomes.is_symlink()
            and (not outcomes.exists() or (outcomes.is_dir() and not any(outcomes.iterdir()))),
            "unresolved parent outcome",
        )
        approval = next(
            row
            for row in rows
            if row["kind"] == "approval-committed" and row["body"]["contract"] == binding["contract"]
        )
        task, goal = binding["task"], binding["contract"]
        criteria = [item["id"] for item in goal["body"]["acceptance_criteria"]]
        _require(
            goal["body"]["initial_task"]["kind"] == task["kind"] == "validate"
            and not task.get("milestone_ids")
            and snapshot == approval["body"]["snapshot"]
            and set(task.get("criterion_ids", task.get("acceptance_criteria", []))) == set(criteria)
            and (
                "criterion_ids" not in task
                or "acceptance_criteria" not in task
                or task["criterion_ids"] == task["acceptance_criteria"]
            ),
            "initial validate must retain complete approved source and criteria",
        )
        check_initial_task_declaration(
            goal, approval["body"]["decision"], task, snapshot, state.get("findings_ledger", [])
        )
        pins = {**self._journal_pins(rows), **self._original_readonly_plan_pins(rows, approval)}
        self._pins(pins)
        _require(
            self._binding(state, snapshot) == binding and self._rows() == rows,
            "initial validation changed during original authentication",
        )
        return {"binding": binding, "approval_id": approval["id"], "pins": pins}

    @_technical_hold
    def verify_original_image_configuration(self, record, context):
        """Bind retained image configuration to its genuine finished launch."""
        _rows, original, stopped = self._finished_for(history.record_identity(record))
        body = original["body"]
        descriptor = body["native_isolation"]
        _require(
            body["grants"] == []
            and body["boundary"]["allow_write"] is False
            and body["record_identity"]["stage"] == body["record_identity"]["role"] == "sol"
            and stopped["body"]["record"]["exit_code"] == 0
            and context.get("native_isolation") == descriptor
            and body["command_sha256"] == util.digest(context["command"])
            and body["environment_sha256"] == context["environment_sha256"]
            and context["native_child_identity"] == descriptor["child_identity"],
            "image context differs from actual original readonly Sol launch",
        )
        path = self._safe(context["launch_manifest"], file=True)
        _require(
            body["pins"].get(str(path)) == context["launch_sha256"] == util.file_hash(path)
            and _json(path.read_bytes())
            == {key: value for key, value in context.items() if key not in ("launch_manifest", "launch_sha256")},
            "image ready context lacks original pre-dispatch protection",
        )
        self._pins(body["pins"])
        self._isolation_pins(descriptor)
        result = {"descriptor": deepcopy(descriptor), "boundary": deepcopy(body["boundary"])}
        if descriptor.get("backend") == "kilocode":
            sealed = [row for row in _rows if row["kind"] == "sealed" and row["body"]["prepared_id"] == original["id"]]
            _require(len(sealed) == 1, "original Kilo image launch has no unique actual held-root seal")
            result["source_launch_seal"] = {
                "prepared_sha256": util.digest(body),
                "prepared_id": original["id"],
                "root": deepcopy(sealed[0]["body"]["root"]),
                "command_sha256": body["command_sha256"],
                "environment_sha256": body["environment_sha256"],
            }
        return result

    def _check_boundary(self, body):
        boundary = body["boundary"]
        if body["purpose"] == "initial_validate_capture":
            return capture_policy.verify_boundary(self.workspace, self.run, body)
        _require(
            set(boundary)
            in (
                {
                    "scratch",
                    "tree",
                    "profile",
                    "profile_sha256",
                    "setup",
                    "runtime_roots",
                    "executables",
                    "copy",
                    "output",
                    "result_slots",
                },
                {
                    "scratch",
                    "tree",
                    "profile",
                    "profile_sha256",
                    "setup",
                    "runtime_roots",
                    "executables",
                    "copy",
                    "output",
                    "result_slots",
                    "node_runtime",
                },
            ),
            "unknown check boundary field",
        )
        scratch = self._safe(boundary["scratch"])
        tree = self._safe(boundary["tree"])
        control = scratch.parent
        _require(
            control.parent == self.workspace / ".autocode"
            and control.name.startswith("tool-containment-")
            and scratch.name == "scratch"
            and tree == scratch / "verification"
            and tree.is_dir()
            and body["cwd"] == str(tree)
            and type(boundary["setup"]) is bool,
            "check does not own this exact clean-copy boundary",
        )
        expected_roots = sorted({str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve())})
        runtime = check_runtime.inputs(self.workspace, body["binding"]["contract"]["body"], setup=boundary["setup"])
        _require(
            boundary.get("node_runtime") == runtime and ("node_runtime" in boundary) is (runtime is not None),
            "check Node runtime differs from original approved inputs",
        )
        native = copies.native_git_setup(scratch) if boundary["setup"] else None
        if native is not None:
            expected_roots = sorted({*expected_roots, native["executable"]})
            _require(
                body["command"][-1] in [shlex.join(args) for args in native["commands"]]
                and body["environment_sha256"] == util.digest(native["environment"]),
                "native setup needs its fixed exact Git argv and environment",
            )
        _require(
            boundary["runtime_roots"] == expected_roots
            and all(not Path(name).is_relative_to(self.workspace) for name in expected_roots),
            "unsupported interpreter dependency authority",
        )
        binaries = [
            linux_check.kernel.executable() if sys.platform == "linux" else Path("/usr/bin/sandbox-exec"),
            Path("/bin/bash"),
            Path(sys.executable).resolve(),
        ]
        if sys.platform != "linux":
            binaries.append(Path("/usr/bin/git"))
        if native is not None:
            binaries.append(Path(native["executable"]))
        if sys.platform == "linux":
            binaries.append(Path("/bin/sh"))
        _require(
            boundary["executables"] == {str(p): util.file_hash(p) for p in binaries}, "native check executable changed"
        )
        # Every copied source byte is immutable, including ignored approved files.
        inputs = source_snapshot.inventory(self.workspace, paths=body["binding"]["source"].get("source_paths", []))
        for name, identity in inputs.items():
            _require(
                not identity.startswith(("symlink:", "submodule:", "uninitialized-submodule")),
                "unsupported copied source link/submodule",
            )
            _require(copies._identity(tree / name) == identity, "copied check input differs from approved source")
        if boundary["copy"] is not None:
            _require(not boundary["setup"], "setup cannot impersonate a sealed copy")
            actual_tree, _identity = copies.execution(
                boundary["copy"]["manifest"], boundary["copy"]["sha256"], self.workspace
            )
            _require(actual_tree == tree, "foreign verification copy")
        else:
            _require(boundary["setup"], "final check requires a sealed copy")
        profile = self._safe(boundary["profile"], file=True)
        _require(
            profile.parent == control and profile.suffix == (".json" if sys.platform == "linux" else ".sb"),
            "foreign check policy path",
        )
        output = self._safe(boundary["output"])
        _require(output.is_relative_to(self.run) and output.suffix == ".log", "foreign check output slot")
        _require(
            isinstance(boundary["result_slots"], list)
            and all(self._safe(name).parent == scratch / "writable" for name in boundary["result_slots"]),
            "foreign structured result slot",
        )
        if sys.platform == "linux":
            try:
                linux_check.verify(boundary, body, runtime=runtime)
            except (ValueError, OSError) as error:
                raise util.Paused(
                    "PAUSED_VISUAL_EVIDENCE", "Native check kernel policy changed or widened: " + str(error)
                ) from error
            return
        writable = tree / ".git" if boundary["setup"] else scratch / "writable"
        expected = containment._render_policy(
            self.workspace,
            scratch,
            read_workspace=False,
            read_roots=expected_roots,
            denied_read_paths=(self.workspace,),
            read_exceptions=(scratch,),
            write_roots=(writable,),
            denied_write_paths=(() if boundary["setup"] else (tree,)),
            write_files=(output,),
        )
        expected += check_runtime.policy(runtime)
        _require(
            profile.read_text() == expected and util.file_hash(profile) == boundary["profile_sha256"],
            "check kernel policy changed or widened",
        )
        _require(
            len(body["command"]) == 8
            and body["command"][:7]
            == ["/usr/bin/sandbox-exec", "-f", str(profile), "/bin/bash", "--noprofile", "--norc", "-c"]
            and body["command_sha256"] == util.digest(body["command"]),
            "command bypasses exact check sandbox",
        )

    @_technical_hold
    def prepare_check(
        self, state: RunState, snapshot, *, purpose, command, environment, cwd, boundary, review_record=None
    ):
        facts = self.readonly_task_authority(state, snapshot=snapshot, review_record=review_record)
        _require(purpose in ("verification_copy_setup", "initial_validate_check"), "unsupported check purpose")
        _require((purpose == "verification_copy_setup") is boundary["setup"], "wrong check setup purpose")
        body = {
            "binding": facts["binding"],
            "approval_id": facts["approval_id"],
            "purpose": purpose,
            "command": list(command),
            "command_sha256": util.digest(command),
            "environment_sha256": util.digest(environment),
            "cwd": str(cwd),
            "boundary": deepcopy(boundary),
            "pins": facts["pins"],
        }
        self._check_boundary(body)
        row = self._append("check-prepared", body)
        handle = PreparedCheck(self, uuid.uuid4().hex, row["id"], _bytes(body))
        self._pending_checks[handle.nonce] = handle
        return handle

    @_technical_hold
    def prepare_capture(self, state: RunState, snapshot, boundary):
        """Admit one concrete original lease, never an operator capture bundle."""
        facts = self.readonly_task_authority(state, snapshot=snapshot)
        _require(
            type(boundary) is capture_policy.CaptureBoundary
            and boundary.workspace == self.workspace
            and boundary.run == self.run
            and boundary.snapshot == snapshot,
            "foreign capture owner",
        )
        boundary.require_current()
        body = {
            "binding": facts["binding"],
            "approval_id": facts["approval_id"],
            "purpose": "initial_validate_capture",
            "user_events": deepcopy(state.get("user_events", [])),
            "command": list(boundary.command),
            "command_sha256": util.digest(boundary.command),
            "environment_sha256": util.digest(boundary.environment),
            "cwd": str(boundary.tree),
            "boundary": boundary.descriptor(),
            "pins": facts["pins"],
        }
        self._check_boundary(body)
        row = self._append("check-prepared", body)
        handle = PreparedCheck(self, uuid.uuid4().hex, row["id"], _bytes(body))
        self._pending_checks[handle.nonce] = handle
        self._capture_boundaries[handle.nonce] = boundary
        return handle

    @_technical_hold
    def require_prepared_check(self, handle):
        self._ownership()
        _require(
            type(handle) is PreparedCheck
            and handle.owner is self
            and self._pending_checks.get(handle.nonce) is handle
            and not self._active_checks,
            "missing original pending check",
        )
        rows = self._chain()[0]
        pending = [
            row
            for row in rows
            if row["kind"] == "check-prepared"
            and not any(end["kind"] == "check-finished" and end["body"]["prepared_id"] == row["id"] for end in rows)
        ]
        _require(
            len(pending) == 1
            and pending[0]["id"] == handle.row_id
            and _bytes(pending[0]["body"]) == handle.body
            and not any(
                row["kind"] in ("check-sealed", "check-released") and row["body"]["prepared_id"] == handle.row_id
                for row in rows
            ),
            "check handle cannot adopt sealed/released/foreign history",
        )
        body = _json(handle.body)
        if body["purpose"] == "initial_validate_capture":
            boundary = self._capture_boundaries.get(handle.nonce)
            _require(
                type(boundary) is capture_policy.CaptureBoundary and boundary.descriptor() == body["boundary"],
                "missing original live capture lease",
            )
            boundary.require_current()
        current_source, _goal = source_transition.cursor(rows)
        _require(
            body["binding"]["source"] == current_source == self._fresh_snapshot(rows)
            and body["binding"]["runtime_sha256"] == util.digest(history.runtime_files(self.runtime)),
            "check source/runtime changed before release",
        )
        self._pins(body["pins"])
        self._check_boundary(body)

    @_technical_hold
    def release_check(self, handle, barrier, child, tree, release_token):
        self.require_prepared_check(handle)
        _require(
            type(barrier) is barriers.WriterBarrier
            and type(tree) is processes.ProcessTree
            and barrier._child is child
            and tree.pid == child.pid,
            "foreign original check process owner",
        )
        body = _json(handle.body)

        def seal(actual_child, actual_tree, facts):
            self.require_prepared_check(handle)
            root = actual_tree.capture_root()
            _require(
                actual_child is child
                and actual_tree is tree
                and root["pid"] == child.pid
                and root["group"] == child.pid
                and root.get("birth_identity", 0) > 0
                and facts["target_command_sha256"] == body["command_sha256"]
                and facts["target_environment_sha256"] == body["environment_sha256"]
                and facts == barrier.execution_facts(child, tree),
                "check envelope changed before seal",
            )
            self._append(
                "check-sealed", {"prepared_id": handle.row_id, "root": deepcopy(root), "barrier": deepcopy(facts)}
            )

        barrier.release_after_seal(child, tree, seal, release_token)
        _require(barrier._released, "check did not release after original seal")
        self._append("check-released", {"prepared_id": handle.row_id})
        self._active_checks[handle.nonce] = (handle, child, tree)
        del self._pending_checks[handle.nonce]

    @_technical_hold
    def finish_check(self, handle, actual_result, *, after):
        _require(self._active_checks.get(handle.nonce, (None,))[0] is handle, "missing actual active check")
        _, child, tree = self._active_checks[handle.nonce]
        body = _json(handle.body)
        _require(
            type(child.returncode) is int
            and actual_result["exit_code"] == child.returncode
            and not processes.live_processes(list(tree.known.values()))
            and after == body["binding"]["source"] == self._fresh_snapshot(),
            "check has no exact collected readonly terminal",
        )
        _require(
            all(key in actual_result for key in command_receipts.OWNERSHIP_FIELDS)
            and command_receipts.completed(actual_result),
            "native check ownership incomplete",
        )
        metadata = actual_result["supervision"]
        root = next(
            row["body"]["root"]
            for row in self._rows()
            if row["kind"] == "check-sealed" and row["body"]["prepared_id"] == handle.row_id
        )
        _require(
            metadata["provider"]["pid"] == child.pid
            and metadata["provider"]["birth_identity"] == root["birth_identity"],
            "foreign check terminal birth",
        )
        self._check_boundary(body)
        capture = body["purpose"] == "initial_validate_capture"
        _require(
            actual_result["output"] == body["boundary"]["output"]
            and actual_result["command"] == (shlex.join(body["command"]) if capture else body["command"][-1]),
            "check output/command differs from launch",
        )
        if capture:
            owner = self._capture_boundaries[handle.nonce]
            owner.require_current(live=False)
            _require(
                actual_result["exit_code"] == 0 and not actual_result["timed_out"], "capture acquisition did not finish"
            )
            broker_receipt = owner.control / "broker.json"
            _require(
                _json(broker_receipt.read_bytes())
                == {
                    "nonce": owner.broker.nonce,
                    "listener": owner.broker.identity,
                    "responses": owner.broker.complete(),
                },
                "missing original parent-served HTTP evidence",
            )
            slots = [str(owner.output / name) for name in ("candidate.png", "browser.json")]
            slots.append(str(broker_receipt))
            policy_path = str(owner.policy_path)
        else:
            slots = [name for name in body["boundary"]["result_slots"] if Path(name).exists()]
            policy_path = body["boundary"]["profile"]
        actual_result["native_result_files"] = {name: util.file_hash(self._safe(name, file=True)) for name in slots}
        pins = {
            name: util.file_hash(self._safe(name, file=True))
            for name in (actual_result["output"], metadata["receipt"], policy_path, *slots)
        }
        row = self._append(
            "check-finished",
            {"prepared_id": handle.row_id, "after": deepcopy(after), "result": deepcopy(actual_result), "pins": pins},
        )
        del self._active_checks[handle.nonce]
        return {"prepared_id": handle.row_id, "finished_id": row["id"]}

    @_technical_hold
    def authenticate_check(self, receipt, *, state: RunState, snapshot, review_record=None):
        facts = self.readonly_task_authority(state, snapshot=snapshot, review_record=review_record)
        rows = self._rows()
        selector = receipt.get("native_check")
        _require(
            isinstance(selector, dict) and set(selector) == {"prepared_id", "finished_id"},
            "missing native check receipt selector",
        )
        original = next((row for row in rows if row["id"] == selector["prepared_id"]), None)
        final = next((row for row in rows if row["id"] == selector["finished_id"]), None)
        _require(original is not None and final is not None, "foreign check journal selector")
        assert original is not None and final is not None  # _require above refused a missing row
        result = final["body"]["result"]
        _require(
            original["kind"] == "check-prepared"
            and final["kind"] == "check-finished"
            and final["body"]["prepared_id"] == original["id"]
            and original["body"]["purpose"] == "initial_validate_check"
            and original["body"]["binding"] == facts["binding"]
            and all(receipt.get(key) == value for key, value in result.items()),
            "receipt differs from original actual executed check",
        )
        self._pins(final["body"]["pins"])
        return {
            "source": deepcopy(snapshot),
            "prepared_id": original["id"],
            "finished_id": final["id"],
            "pins": {**facts["pins"], **final["body"]["pins"]},
        }

    @_technical_hold
    def retain_capture(self, state: RunState, *, snapshot, handle, capture):
        """Bind parent-promoted bytes to this exact original acquisition."""
        facts = self.readonly_task_authority(state, snapshot=snapshot)
        _require(
            type(handle) is PreparedCheck
            and handle.owner is self
            and type(self._capture_boundaries.get(handle.nonce)) is capture_policy.CaptureBoundary,
            "capture promotion lost its original owner",
        )
        original = _json(handle.body)
        rows = self._rows()
        finished = [
            row for row in rows if row["kind"] == "check-finished" and row["body"]["prepared_id"] == handle.row_id
        ]
        _require(
            original["purpose"] == "initial_validate_capture"
            and original["binding"] == facts["binding"]
            and len(finished) == 1
            and not any(
                row["kind"] == "capture-published" and row["body"]["prepared_id"] == handle.row_id for row in rows
            ),
            "capture is not a fresh original completed acquisition",
        )
        selected = {"prepared_id": handle.row_id, "finished_id": finished[0]["id"]}
        path = self._safe(capture["capture_ref"], file=True)
        _require(util.file_hash(path) == capture["capture_sha256"], "promoted capture manifest changed")
        body = _json(path.read_bytes())
        _require(body.get("native_check") == selected, "capture manifest lacks exact original execution")
        boundary = self._capture_boundaries[handle.nonce]
        for key, name in (("candidate", "candidate.png"), ("browser", "browser.json")):
            target = self._safe(self.workspace / body["artifacts"][key]["path"], file=True)
            _require(
                util.file_hash(target)
                == finished[0]["body"]["result"]["native_result_files"][str(boundary.output / name)],
                "promoted capture differs from actual acquired bytes",
            )
        verified, refs = capture_policy.evidence.verify(state, str(path), capture["capture_sha256"], current=snapshot)
        pins = {ref: util.file_hash(self._safe(ref, file=True)) for ref in refs}
        row = self._append(
            "capture-published",
            {
                "binding": facts["binding"],
                **selected,
                "capture": deepcopy(capture),
                "case": verified["case"],
                "pins": pins,
            },
        )
        return row["id"]

    @_technical_hold
    def authenticate_captures(self, state: RunState, *, snapshot, captures):
        """No operator bundle, saved READY dictionary or callback grants provenance."""
        self.require_task_authority(state, snapshot=snapshot)
        binding = self._binding(state, snapshot)
        rows = self._rows()
        cases = state["settings"]["design_manifest"]["body"]["cases"]
        _require(
            isinstance(captures, dict) and set(captures) == {case["id"] for case in cases},
            "native capture authority requires every approved reference case",
        )
        pins = {}
        for case in cases:
            capture = captures[case["id"]]
            selected = {key: capture[key] for key in ("capture_ref", "capture_sha256")}
            matches = [
                row
                for row in rows
                if row["kind"] == "capture-published"
                and row["body"]["binding"] == binding
                and row["body"]["capture"] == selected
                and row["body"]["case"] == capture_policy.evidence.case_binding(case)
            ]
            _require(len(matches) == 1, "capture has no original native parent publication")
            body = matches[0]["body"]
            prepared = next((row for row in rows if row["id"] == body["prepared_id"]), None)
            finished = next((row for row in rows if row["id"] == body["finished_id"]), None)
            _require(
                prepared is not None
                and finished is not None
                and prepared["kind"] == "check-prepared"
                and finished["kind"] == "check-finished"
                and prepared["body"]["purpose"] == "initial_validate_capture"
                and prepared["body"]["binding"] == binding
                and finished["body"]["prepared_id"] == prepared["id"],
                "capture ownership join is incomplete",
            )
            assert prepared is not None and finished is not None  # _require above refused a missing row
            self._check_boundary(prepared["body"])
            self._pins(finished["body"]["pins"])
            self._pins(body["pins"])
            capture_policy.evidence.verify(
                state, selected["capture_ref"], selected["capture_sha256"], case=case, current=snapshot
            )
            pins.update(body["pins"])
            pins.update(finished["body"]["pins"])
        pins.update(self._journal_pins(rows))
        return pins

    @_technical_hold
    def prepare_launch(
        self,
        state: RunState,
        snapshot,
        record,
        *,
        boundary,
        command,
        environment,
        native_role_isolation,
        writer_execution=None,
    ):
        rows, _prepared, _finished = self._idle()
        self._source_continuity(snapshot)
        binding = self._launch_binding(state, snapshot)
        approvals = [
            row
            for row in rows
            if row["kind"] == "approval-committed"
            and row["body"]["contract"] == binding["contract"]
            and route_fit.same(row["body"]["settings"], binding["settings"])
            and row["body"]["runtime_sha256"] == binding["runtime_sha256"]
        ]
        _require(
            (len(approvals) == 1 if _contract_approved(state) else not approvals and boundary["allow_write"] is False),
            "unapproved writer or missing original committed exact operator approval",
        )
        if _contract_approved(state):
            self._initial_task_authority(binding, rows)
        pins, descriptor = self._native_config(
            boundary,
            command,
            environment,
            state,
            record,
            native_role_isolation,
            **({"writer_execution": writer_execution} if boundary["allow_write"] else {}),
        )
        pins.update(self._planning_input_pins(binding, record))
        grants = (
            containment.writer_footprint(self.workspace, boundary["write_paths"]) if boundary["allow_write"] else []
        )
        _require(
            not boundary["allow_write"]
            or grants
            == containment.writer_footprint(self.workspace, cast(dict, state["current_task"])["affected_paths"]),
            "writer grants differ from actual owned task",
        )
        kinds = deepcopy(boundary.get("writer_path_kinds", {}))
        _require(set(kinds) == set(grants), "grant kinds are not the one measured map")
        body = {
            "policy": POLICY,
            "binding": binding,
            "record_identity": self._launch_record_identity(record, binding, command),
            "command_sha256": util.digest(command),
            "environment_sha256": util.digest(environment),
            "boundary": deepcopy(boundary),
            "grants": grants,
            "grant_kinds": kinds,
            "pins": pins,
            "native_isolation": deepcopy(descriptor),
        }
        row = self._append("prepared", body)
        handle = PreparedLaunch(self, uuid.uuid4().hex, row["id"], _bytes(body))
        self._pending[handle.nonce] = handle
        if boundary["allow_write"]:
            _require(writer_execution is self._preparing_writer, "writer preparation changed before journal append")
            self._writer_executions[handle.nonce] = writer_execution
        return handle

    @_technical_hold
    def release_launch(self, handle, barrier, child, tree, release_token):
        """Original on_root hook. No fake child/tree or externally supplied seal."""
        _require(
            type(handle) is PreparedLaunch and handle.owner is self and self._pending.get(handle.nonce) is handle,
            "foreign/replayed actual launch handle",
        )
        _require(
            type(barrier) is barriers.WriterBarrier
            and type(tree) is processes.ProcessTree
            and barrier._child is child
            and tree.pid == child.pid,
            "not original held bootstrap/supervisor",
        )
        body = _json(handle.body)
        writer = self._writer_executions.get(handle.nonce)
        _require(
            not body["grants"] or writer is not None and writer.native is handle,
            "writable native preparation lost the original domain admission",
        )
        if body["binding"]["task"] is not None:
            with self._replay_before(handle):
                self._initial_task_authority(body["binding"], self._rows())

        def seal(actual_child, actual_tree, facts):
            self._ownership()
            self._pins(body["pins"])
            self._recheck_planning_inputs(body)
            self._isolation_pins(body["native_isolation"])
            if body["native_isolation"].get("backend") == "kilocode":
                try:
                    from .autocode_kilo_isolation import verify_boundary
                except ImportError:
                    from autocode_kilo_isolation import verify_boundary
                verify_boundary(body["boundary"])
            else:
                containment.verify(body["boundary"])
            root = actual_tree.capture_root()
            _require(
                actual_child is child
                and actual_tree is tree
                and root
                and root["pid"] == child.pid
                and root["group"] == child.pid
                and root.get("birth_identity", 0) > 0
                and facts["target_command_sha256"] == body["command_sha256"]
                and facts["target_environment_sha256"] == body["environment_sha256"]
                and facts == barrier.execution_facts(actual_child, actual_tree),
                "held-root target/birth binding changed",
            )
            writer_facts = {}
            if writer is not None:
                writer.seal(barrier, actual_child, actual_tree)
                writer_facts = {
                    "writer_admission_id": writer.admission["id"],
                    "writer_dispatch_sha256": util.digest(writer.admission["body"]["dispatch"]),
                }
            self._append(
                "sealed",
                {"prepared_id": handle.row_id, "root": deepcopy(root), "barrier": deepcopy(facts), **writer_facts},
            )

        barrier.release_after_seal(child, tree, seal, release_token)
        _require(barrier._released, "guarded release did not complete")
        self._append("released", {"prepared_id": handle.row_id})
        self._active[handle.nonce] = (handle, child, tree)
        del self._pending[handle.nonce]

    @_technical_hold
    def finish_launch(self, handle, record, after):
        _require(self._active.get(handle.nonce, (None,))[0] is handle, "finish lacks original active child")
        _handle, child, tree = self._active[handle.nonce]
        _require(
            type(child.returncode) is int and not processes.live_processes(list(tree.known.values())),
            "original supervisor has not quiesced its actual root/descendants",
        )
        body = _json(handle.body)
        self._recheck_planning_inputs(body)
        check_finished_record(
            body["record_identity"],
            history.record_identity(record),
            **({"source_after": after} if body["boundary"]["allow_write"] else {}),
        )
        _require(
            after == self._fresh_snapshot() and record["exit_code"] == child.returncode,
            "stopped source/exit differs from actual child",
        )
        _require(
            record["source_revision"] == after["revision"]
            and _json(self._safe(record["before_ref"], file=True).read_bytes()) == body["binding"]["source"]
            and _json(self._safe(record["after_ref"], file=True).read_bytes()) == after
            and (
                record.get("reported_output") is None
                or record["reported_output"]
                == str(Path(body["record_identity"]["output"]).with_suffix(".reported.json"))
            ),
            "final source/snapshot/report slots differ from the original actual stage",
        )
        changed = util.changed_paths(body["binding"]["source"], after)
        _require(
            all(_within(name, body["grants"], body["grant_kinds"]) for name in changed),
            "source changed outside exact enforced grants",
        )
        pins = {
            name: util.file_hash(self._safe(name, file=True))
            for name in [
                record[key]
                for key in ("events", "output", "reported_output", "before_ref", "after_ref")
                if record.get(key)
            ]
        }
        row = self._append(
            "finished",
            {
                "prepared_id": handle.row_id,
                "record": history.record_identity(record),
                "after": deepcopy(after),
                "possible_writes": possible_write_paths(body["grants"], changed),
                "pins": pins,
            },
        )
        del self._active[handle.nonce]
        writer = self._writer_executions.get(handle.nonce)
        if writer is not None:
            try:
                writer.finish(row)
            except Exception:
                writer.failed = True  # Retain both journals; no later authority.
                raise
        return row["id"]

    @contextmanager
    def _replay_before(self, handle):
        """Replay earlier evidence only inside one original owned operation.

        This never replaces idle admission, source freshness or dispatch. The
        complete chain is checked, then earlier immutable evidence is replayed
        through the prefix before this exact pending original launch. Foreign
        gaps, checks and reopened handles remain held.
        """
        _require(
            type(handle) is PreparedLaunch
            and handle.owner is self
            and (self._pending.get(handle.nonce) is handle or self._active.get(handle.nonce, (None,))[0] is handle),
            "historical replay needs the actual original pending/active handle",
        )
        rows, prepared, _sealed, _released, finished = self._chain()
        pending = set(prepared) - set(finished)
        _require(
            pending == {handle.row_id}
            and prepared[handle.row_id]["body"] == _json(handle.body)
            and sum(row["kind"] == "check-prepared" for row in rows)
            == sum(row["kind"] == "check-finished" for row in rows),
            "historical replay cannot excuse another unfinished operation",
        )
        prefix = rows[: prepared[handle.row_id]["sequence"]]
        selected = (handle, prefix, {key: value for key, value in prepared.items() if key != handle.row_id}, finished)
        previous = self._historical_replay
        _require(previous is None or previous[0] is handle, "overlapping historical replay owner")
        self._historical_replay = selected
        try:
            yield
        finally:
            self._historical_replay = previous

    def _finished_for(self, record):
        replay = self._historical_replay
        if replay is None:
            rows, prepared, finished = self._idle()
        else:
            handle, rows, prepared, finished = replay
            _require(
                self._pending.get(handle.nonce) is handle or self._active.get(handle.nonce, (None,))[0] is handle,
                "original replay operation ended or changed owner",
            )
        matches = [
            (prepared[ident], row)
            for ident, row in finished.items()
            if row["kind"] == "finished" and row["body"]["record"] == record
        ]
        _require(len(matches) == 1, "missing/ambiguous original native stage launch")
        return rows, matches[0][0], matches[0][1]

    def _journal_pins(self, rows):
        return {
            str(self.root / (f"{row['sequence']:08d}-" + row["id"] + ".json")): hashlib.sha256(_bytes(row)).hexdigest()
            for row in rows
        }

    @_technical_hold
    def measured_source_delta(self, *, state: RunState, snapshot):
        binding = self._binding(state, snapshot)
        self._source_continuity(snapshot)
        rows, prepared, finished = self._idle()
        self._initial_task_authority(binding, rows)
        writers = [
            (prepared[ident], row)
            for ident, row in finished.items()
            if row["kind"] == "finished"
            and isinstance(prepared[ident]["body"]["binding"]["task"], dict)
            and prepared[ident]["body"]["binding"]["contract"] == binding["contract"]
            and prepared[ident]["body"]["record_identity"]["stage"] == "terra"
        ]
        if not writers:
            lineage = assignments.readonly_lineage(rows, binding)
            _require(
                lineage["kind"] == "operator_supplied_source", "missing original approved source or causal writer chain"
            )
            return {"binding_sha256": util.digest(binding), "changed_paths": []}
        _require(writers[-1][1]["body"]["after"] == snapshot, "latest original writer source is stale")
        paths: list = []
        for original, stopped in writers:
            old = original["body"]["binding"]
            _require(
                route_fit.same(old["settings"], binding["settings"])
                and old["runtime_sha256"] == binding["runtime_sha256"]
                and old["task"]["milestone_id"] == binding["task"]["milestone_id"],
                "writer chain changed routes, limits, runtime or owner",
            )
            paths = possible_write_paths(paths, stopped["body"]["possible_writes"])
        return {"binding_sha256": util.digest(binding), "changed_paths": paths}

    def _publication(self, selector, query):
        self._safe(selector["path"])
        _require(
            selector == query["selector"] and query["namespace"] == str(self.run / "visual-native-history"),
            "foreign native original namespace",
        )
        rows, origin, stopped = self._finished_for(query["original_record"])
        _require(
            origin["body"]["binding"] == query["binding"]
            and origin["body"]["grants"] == []
            and origin["body"]["boundary"]["allow_write"] is False,
            "native review/Completion original binding or readonly grant differs from actual launch",
        )
        self._join_producer(selector, query, rows, origin, stopped)
        return rows, origin, stopped

    def _join_producer(self, selector, query, rows, review_launch, review_finish):
        if selector["kind"] == "checkpoints":
            _require(
                query["original_producer"] is None
                and query["producer_task"] is None
                and query["operator_source"] is None,
                "Completion publication has unsupported direct producer fields",
            )
            # NativeHistory replays the exact committed review_selector and its
            # producer proof. A Completion record cannot replace that review.
            return None
        _require(selector["kind"] in ("reviews", "rejections"), "unknown publication producer kind")
        binding = query["binding"]
        if query["original_producer"] is not None:
            _require(
                query["operator_source"] is None and isinstance(query["producer_task"], dict),
                "native Builder and operator producer cannot be mixed",
            )
            _all_rows, builder_launch, builder_finish = self._finished_for(query["original_producer"])
            body = builder_launch["body"]
            actual = builder_finish["body"]["record"]
            _require(
                actual["stage"] == actual["role"] == "terra"
                and actual["engine"] == "opencode"
                and actual["route_role"] in (None, "terra")
                and actual["exit_code"] == 0
                and not any(
                    actual.get(name)
                    for name in (
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
                )
                and body["binding"]["task"] == query["producer_task"]
                and body["binding"]["contract"] == binding["contract"]
                and route_fit.same(body["binding"]["settings"], binding["settings"])
                and body["binding"]["runtime_sha256"] == binding["runtime_sha256"]
                and body["boundary"]["allow_write"] is True
                and body["grants"]
                and builder_finish["body"]["after"] == binding["source"]
                and builder_finish["sequence"] < review_launch["sequence"],
                "producer differs from original actual bounded Builder task/contract/source/launch",
            )
            _require(
                not any(
                    row["kind"] == "finished" and row["body"]["possible_writes"]
                    for row in rows[builder_finish["sequence"] + 1 : review_launch["sequence"]]
                ),
                "another possible source writer superseded this original Builder producer",
            )
            self._pins(builder_finish["body"]["pins"])
            return None
        supplied = query["operator_source"]
        _require(
            query["producer_task"] is None
            and isinstance(supplied, dict)
            and supplied["kind"] == "operator_supplied_source",
            "native image review lacks actual producer provenance",
        )
        task, goal = binding["task"], binding["contract"]
        initial = goal["body"]["initial_task"]
        criteria = {row["id"] for row in goal["body"]["acceptance_criteria"]}
        _require(
            initial["kind"] == task["kind"] == "validate"
            and initial["milestone_id"] == task["milestone_id"]
            and set(initial["acceptance_criteria"])
            == set(task.get("criterion_ids", task.get("acceptance_criteria", [])))
            == criteria
            and (
                "criterion_ids" not in task
                or "acceptance_criteria" not in task
                or task["criterion_ids"] == task["acceptance_criteria"]
            )
            and supplied["model"] is None
            and supplied["session_id"] is None
            and supplied["task_id"] == task["id"]
            and supplied["contract_hash"] == goal["hash"]
            and supplied["contract_revision"] == goal["revision"]
            and supplied["source_revision"] == binding["source"]["revision"]
            and supplied["source_files_sha256"] == util.digest(binding["source"]["files"])
            and supplied["approval_event_sha256"] == util.digest(goal["approval_event"]),
            "operator producer changed actual first validation/source/approval identity",
        )
        # Historical review replay uses only its original preceding causal
        # chain. The owner authenticates whole current assignments separately;
        # recursively selecting a later decision here would borrow the future.
        prefix = rows[: review_launch["sequence"]]
        lineage = assignments.readonly_lineage(prefix, binding)
        approval = lineage["approval"]
        original_approval = next((row for row in prefix if row["id"] == approval["body"]["prepared_id"]), None)
        _require(
            lineage["kind"] == "operator_supplied_source"
            and original_approval is not None
            and original_approval["kind"] == "approval-prepared"
            and rows[0]["body"]["source"] == original_approval["body"]["snapshot"]
            and not any(
                row["kind"] == "prepared"
                and (row["body"]["grants"] or row["body"]["record_identity"]["stage"] == "terra")
                for row in rows[: review_launch["sequence"]]
            ),
            "operator source lacks original committed first-task approval or has prior writer provenance",
        )
        return None

    @_technical_hold
    def verify_namespace(self, *, query, selector):
        rows, origin, stopped = self._publication(selector, query)
        phase = query["phase"]
        _require(
            phase
            in (
                "before-parent-publication",
                "after-parent-publication",
                "before-native-replay",
                "after-native-replay",
                "after-checkpoint-replay",
            ),
            "unknown native publication phase",
        )
        committed = [
            row for row in rows if row["kind"] == "publication-committed" and row["body"]["selector"] == selector
        ]
        if phase in ("before-native-replay", "after-native-replay", "after-checkpoint-replay"):
            _require(
                len(committed) == 1
                and committed[0]["body"]["query_identity"]
                == util.digest({key: value for key, value in query.items() if key != "phase"}),
                "native sidecar has no original guarded durable parent commit",
            )
        else:
            # A physical copy may be prepared under the concrete native boundary,
            # but its presence must not become committed/scheduler authority.
            _require(not committed, "native publication already committed")
            if phase == "before-parent-publication":
                _require(
                    not any(_json(handle.body)["selector"] == selector for handle in self._publications.values()),
                    "replayed pending parent publication",
                )
                self.prepare_parent_publication(query=query, selector=selector)
            else:
                _require(
                    any(_json(handle.body)["selector"] == selector for handle in self._publications.values()),
                    "publication lacks original process-local preparation",
                )
                _require(
                    util.file_hash(self._safe(selector["path"], file=True)) == selector["sha256"],
                    "physical original sidecar differs before parent commit",
                )
        pins = self._journal_pins(rows)
        through = next((row["id"] for row in reversed(rows) if row["kind"] == "finished"), None)
        _require(through, "missing actual protected through-launch epoch")
        return {
            "kind": query["kind"],
            "query_sha256": util.digest(query),
            "evidence_hashes": pins,
            "protected_refs": list(pins),
            "original_launch_id": stopped["id"],
            "through_launch_id": through,
        }

    @_technical_hold
    def writes_since_review(self, *, query, selector):
        rows, origin, stopped = self._finished_for(query["original_record"])
        _require(origin["body"]["binding"] == query["original_binding"], "original source/write epoch differs")
        _require(
            any(row["kind"] == "publication-committed" and row["body"]["selector"] == selector for row in rows),
            "source freshness cannot borrow an uncommitted sidecar",
        )
        through_row = next(row for row in reversed(rows) if row["kind"] == "finished")
        _require(
            through_row["body"]["after"] == query["current_binding"]["source"] == self._fresh_snapshot(rows),
            "current source escaped protected launch continuity",
        )
        paths = []
        for row in rows[stopped["sequence"] + 1 :]:
            if row["kind"] == "finished":
                paths = possible_write_paths(paths, row["body"]["possible_writes"])
        pins = self._journal_pins(rows)
        through = next(row["id"] for row in reversed(rows) if row["kind"] == "finished")
        return {
            "kind": query["kind"],
            "query_sha256": util.digest(query),
            "changed_paths": paths,
            "evidence_hashes": pins,
            "protected_refs": list(pins),
            "original_launch_id": stopped["id"],
            "through_launch_id": through,
        }

    @_technical_hold
    def prepare_parent_publication(self, *, query, selector):
        """After actual native semantics pass; before original guarded commit.

        Caller integration must invoke this only at the existing accepted native
        review/Completion boundary. A handle never labels the sidecar accepted.
        """
        self._publication(selector, query)
        body = {
            "selector": deepcopy(selector),
            "query_identity": util.digest({key: value for key, value in query.items() if key != "phase"}),
        }
        row = self._append("publication-prepared", body)
        handle = PreparedParentPublication(self, uuid.uuid4().hex, row["id"], _bytes(body))
        self._publications[handle.nonce] = handle
        return handle

    @_technical_hold
    def commit_parent_publication(self, handle, *, parent_commit, committed_candidate, snapshot):
        """CODE hook AFTER original durable parent commit, not a model flag.

        Until actual caller wiring can establish that ordering, this method
        must not be called. Restarts never reconstruct a prepared handle.
        The journal refuses unresolved copies rather than auto-promoting them.
        """
        _require(
            type(handle) is PreparedParentPublication
            and handle.owner is self
            and self._publications.get(handle.nonce) is handle,
            "missing original parent commit handle",
        )
        body = _json(handle.body)
        self._parent_ack(parent_commit, committed_candidate, snapshot, publication=handle)
        selector = body["selector"]
        _require(
            util.file_hash(self._safe(selector["path"], file=True)) == selector["sha256"],
            "prepared original sidecar changed before parent publication",
        )
        self._idle()
        row = self._append("publication-committed", {**body, "parent_commit_id": parent_commit.row_id})
        del self._publications[handle.nonce]
        return row["id"]

    @_technical_hold
    def committed_selectors(self, kind):
        """Only committed publications are eligible for history replay.

        Integrate this at NativeReviewHistory._selectors. Pending physical
        copies remain preserved; unknown copies are refused, never adopted.
        """
        _require(kind in ("reviews", "rejections", "checkpoints"), "unknown native publication kind")
        rows, _prepared, _finished = self._idle()
        known = [
            row["body"]["selector"]
            for row in rows
            if row["kind"] == "publication-prepared" and row["body"]["selector"]["kind"] == kind
        ]
        committed = [
            row["body"]["selector"]
            for row in rows
            if row["kind"] == "publication-committed" and row["body"]["selector"]["kind"] == kind
        ]
        directory = self.run / "visual-native-history" / kind
        physical = list(directory.glob("*/manifest.json"))
        _require(
            all(any(item["path"] == str(path) for item in known) for path in physical),
            "unknown physical native sidecar is not an original publication",
        )
        _require(len({item["path"] for item in committed}) == len(committed), "duplicate committed native sidecar")
        for item in committed:
            _require(
                util.file_hash(self._safe(item["path"], file=True)) == item["sha256"],
                "committed native sidecar disappeared or changed",
            )
        return deepcopy(committed)

    def _publication_matches(self, handle, candidate, snapshot):
        if type(handle) is assignments.PreparedAssignment:
            _require(
                handle.owner is self and self._assignments.get(handle.nonce) is handle,
                "foreign pending native assignment",
            )
            body = _json(handle.body)
            assignments.check_candidate(body, candidate, snapshot)
            self._assignment_validator(body)
            return {"kind": "assignments", "id": handle.row_id}
        _require(
            type(handle) is PreparedParentPublication
            and handle.owner is self
            and self._publications.get(handle.nonce) is handle,
            "foreign pending publication",
        )
        selector = _json(handle.body)["selector"]
        raw = self._safe(selector["path"], file=True).read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == selector["sha256"], "pending native copy changed")
        metadata = _json(raw)
        _require(
            metadata["binding"] == self._binding(candidate, snapshot)
            and any(
                history.record_identity(row) == history.record_identity(metadata["record"])
                for row in candidate.get("stages", [])
            ),
            "final parent candidate changed native stage/task binding",
        )
        if selector["kind"] in ("reviews", "rejections"):
            validation = candidate["validation"]
            original = metadata["functional"]
            _require(
                all(validation.get(key) == value for key, value in original.items() if key != "evidence_hashes")
                and all(
                    validation["evidence_hashes"].get(name) == pin for name, pin in original["evidence_hashes"].items()
                )
                and (
                    selector["kind"] == "rejections"
                    or metadata["receipt"] in candidate.get("visual_acceptance_receipts", [])
                ),
                "final persisted functional/image projection differs",
            )
        else:
            progress = candidate["milestone_progress"][
                candidate["goal_contract"]["hash"] + ":" + metadata["milestone_id"]
            ]
            _require(
                progress["accepted"] is True
                and progress["contract_hash"] == candidate["goal_contract"]["hash"]
                and progress["accepted_source_revision"] == snapshot["revision"],
                "final persisted checkpoint projection differs",
            )
        return selector

    @_technical_hold
    def prepare_assignment_publication(self, body):
        self._idle()
        self._source_continuity(body["binding"]["source"])
        _require(self._assignment_validator is not None, "original assignment validator missing")
        self._assignment_validator(body)
        row = self._append("assignment-prepared", deepcopy(body))
        value = assignments.PreparedAssignment(self, uuid.uuid4().hex, row["id"], _bytes(body))
        self._assignments[value.nonce] = value
        return value

    @_technical_hold
    def commit_assignment_publication(self, handle, *, parent_commit, committed_candidate, snapshot):
        _require(
            type(handle) is assignments.PreparedAssignment
            and handle.owner is self
            and self._assignments.get(handle.nonce) is handle,
            "foreign assignment commit",
        )
        self._parent_ack(parent_commit, committed_candidate, snapshot, publication=handle)
        body = _json(handle.body)
        ident = self._append(
            "assignment-committed", {**body, "prepared_id": handle.row_id, "parent_commit_id": parent_commit.row_id}
        )["id"]
        del self._assignments[handle.nonce]
        del self._commit_receipts[parent_commit.nonce]
        self._last_parent_ack = None
        return ident

    @_technical_hold
    def prepare_parent_commit(self, candidate, *, snapshot, publications=(), approval=None, planning_record=None):
        """Bind the actual normalized value AT the original atomic JSON sink.

        The CODE sink adapter must call this for its final value, rather than
        a pre-status/pre-intervention clone. On mismatch it preserves the valid
        parent write, retains an orphan native proposal, and publishes nothing.
        """
        self._ownership()
        self._commit_source(snapshot, candidate, approval)
        publications = tuple(publications)
        selectors = [self._publication_matches(handle, candidate, snapshot) for handle in publications]
        _require(len({handle.nonce for handle in publications}) == len(publications), "repeated pending native handle")
        if approval is not None:
            self._approved_candidate(approval, candidate, snapshot)
        planning = self._planning_matches(planning_record, candidate, snapshot) if planning_record is not None else None
        _require(
            publications or approval is not None or planning is not None,
            "no original native/operator publication at this sink",
        )
        raw = (json.dumps(candidate, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        body = {
            "candidate_sha256": hashlib.sha256(raw).hexdigest(),
            "source": deepcopy(snapshot),
            "contract": deepcopy(candidate["goal_contract"]),
            "task": deepcopy(candidate.get("current_task")),
            "settings_sha256": util.digest(candidate["settings"]),
            "publications": selectors,
            "publication_nonces": [handle.nonce for handle in publications],
            "approval_nonce": approval.nonce if approval is not None else None,
            "approval_prepared_id": approval.row_id if approval is not None else None,
            "planning": planning,
            "runtime_sha256": util.digest(history.runtime_files(self.runtime)),
        }
        row = self._append("parent-commit-prepared", body)
        handle = PreparedParentCommit(self, uuid.uuid4().hex, row["id"], _bytes(body))
        self._commits[handle.nonce] = handle
        return handle

    @_technical_hold
    def capture_parent_commit(self, intent, *, committed_candidate, serialized_bytes, sink_identity, snapshot):
        """Called ONLY by the existing sink after file/replace/dir fsync.

        No private state file is opened/read. The explicit util hook provides
        actual fstat identity and exact bytes it wrote. This method is not an
        independently discoverable persist observer; caller integration is
        required and its ACK is not image/native acceptance.
        """
        self._ownership()
        _require(
            type(intent) is PreparedParentCommit and intent.owner is self and self._commits.get(intent.nonce) is intent,
            "foreign/reconstructed parent commit intent",
        )
        body = _json(intent.body)
        approval = self._approvals.get(body.get("approval_nonce"))
        _require(
            body.get("approval_nonce") is None or approval is not None, "parent sink lost original approval handle"
        )
        self._commit_source(snapshot, committed_candidate, approval)
        expected = (json.dumps(committed_candidate, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        _require(
            type(serialized_bytes) is bytes
            and serialized_bytes == expected
            and hashlib.sha256(serialized_bytes).hexdigest() == body["candidate_sha256"]
            and body["source"] == snapshot
            and body["contract"] == committed_candidate["goal_contract"]
            and body["task"] == committed_candidate.get("current_task")
            and body["settings_sha256"] == util.digest(committed_candidate["settings"])
            and body["runtime_sha256"] == util.digest(history.runtime_files(self.runtime)),
            "actual parent sink wrote a different final candidate",
        )
        check_parent_sink_identity(
            sink_identity, expected_path=self.run / "state.json", byte_count=len(serialized_bytes)
        )
        row = self._append(
            "parent-commit-acknowledged", {**body, "intent_id": intent.row_id, "sink_identity": deepcopy(sink_identity)}
        )
        # A successful original sink ACK consumes this exact intent once.
        del self._commits[intent.nonce]
        receipt = ParentCommitReceipt(self, intent.nonce, row["id"], _bytes(row["body"]))
        self._commit_receipts[receipt.nonce] = receipt
        self._last_parent_ack = receipt
        return receipt

    @_technical_hold
    def invalidate_parent_ack(self):
        """CODE sink hook calls this on EVERY later/intermediate parent write.

        A mismatch must not throw/rollback valid intervention consumption; it
        merely makes an earlier matching ACK ineligible for native publication.
        """
        previous = self._last_parent_ack
        self._last_parent_ack = None
        self._ownership()
        if previous is not None:
            self._append("parent-commit-invalidated", {"parent_commit_id": previous.row_id})

    def _parent_ack(self, receipt, candidate, snapshot, *, publication=None, approval=None):
        _require(
            type(receipt) is ParentCommitReceipt
            and receipt.owner is self
            and self._commit_receipts.get(receipt.nonce) is receipt
            and self._last_parent_ack is receipt,
            "missing final original parent sink ACK",
        )
        body = _json(receipt.body)
        raw = (json.dumps(candidate, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        _require(
            body["candidate_sha256"] == hashlib.sha256(raw).hexdigest()
            and body["source"] == snapshot == source_scope.snapshot(self.workspace, candidate)
            and body["runtime_sha256"] == util.digest(history.runtime_files(self.runtime)),
            "final parent changed after sink ACK/boundary side effects",
        )
        if publication is not None:
            _require(publication.nonce in body["publication_nonces"], "sink ACK did not commit this exact sidecar")
            self._publication_matches(publication, candidate, snapshot)
        if approval is not None:
            _require(approval.nonce == body["approval_nonce"], "sink ACK did not commit the original operator action")
            self._approved_candidate(approval, candidate, snapshot)
        else:
            self._source_continuity(snapshot)

    def _planning_matches(self, record, candidate, snapshot):
        is_repair = bool(
            record.get("report_only") and record.get("original_stage", record.get("stage")) == "plan_finalize"
        )
        stage_valid = (
            (record.get("original_stage") == "plan_finalize" or record["stage"] == "plan_finalize")
            if is_repair
            else record["stage"] == "plan_finalize" and not record.get("report_only")
        )
        _require(
            candidate["settings"]["planning_flow"] == "v2"
            and not _contract_approved(candidate)
            and candidate.get("current_task") is None
            and stage_valid
            and record["exit_code"] == 0
            and not any(
                record.get(key) for key in ("report_repaired", "timed_out", "interrupted", "dry_run", "rejected")
            ),
            "initial planning authority needs the original successful v2 final native stage",
        )
        launch_record = dict(record)
        if is_repair:
            launch_record.pop("applied_original_events", None)
        _rows, original, stopped = self._finished_for(history.record_identity(launch_record))
        _require(
            original["body"]["grants"] == []
            and original["body"]["binding"]["source"] == snapshot == stopped["body"]["after"]
            and route_fit.same(original["body"]["binding"]["settings"], candidate["settings"]),
            "final native planning stage lost original readonly source/settings",
        )
        if is_repair:
            failed = [
                row
                for row in _rows
                if row["kind"] == "finished"
                and row["body"]["record"]["stage"] == "plan_finalize"
                and row["body"]["record"].get("rejected") is True
            ]
            _require(len(failed) >= 1, "repaired final native planning stage has no original failed launch in journal")
            original_failed = failed[-1]
            _require(
                original_failed["body"]["after"] == snapshot,
                "original failed final planning stage had different source",
            )
        self._pins(stopped["body"]["pins"])
        report = _json(self._safe(record["output"], file=True).read_bytes())
        events = self._safe(record["events"], file=True).read_bytes()
        raw_report = _json(self._safe(record.get("reported_output") or record["output"], file=True).read_bytes())
        session = record.get("expected_session") or record.get("thread_id")
        _require(isinstance(session, str) and session, "planning final native session is missing")
        _require(raw_report == report, "final planning authority cannot borrow a repaired/normalized report")
        native = acceptance._terminal(events, raw_report, session)
        goal = candidate["goal_contract"]
        final = candidate["planning_final"]
        _require(
            report["contract"] == goal["body"]
            and final["final_token"] == contracts.token(goal) == candidate["planning"]["final_token"],
            "actual final native planning report differs from displayed draft",
        )
        pins = {}
        for key in ("artifact", "delta", "graph"):
            item = final[key]
            path = self._safe(self.run / item["path"], file=True)
            _require(
                path.is_relative_to(self.run / "planning")
                and _sha(item["sha256"])
                and util.file_hash(path) == item["sha256"],
                "final planning handoff changed before parent sink",
            )
            pins[str(path)] = item["sha256"]
        return {
            "contract": deepcopy(goal),
            "settings": deepcopy(candidate["settings"]),
            "source": deepcopy(snapshot),
            "record": history.record_identity(record),
            "original_launch_id": stopped["id"],
            "native": native,
            "pins": pins,
            "token": final["final_token"],
        }

    @_technical_hold
    def commit_planning_publication(self, parent_commit, committed_candidate, *, snapshot):
        self._parent_ack(parent_commit, committed_candidate, snapshot)
        body = _json(parent_commit.body)["planning"]
        _require(isinstance(body, dict), "final parent sink has no original reviewed planning preparation")
        matches = [row for row in committed_candidate["stages"] if history.record_identity(row) == body["record"]]
        _require(len(matches) == 1, "final parent requires exactly one original reviewed planning record")
        _require(
            body == self._planning_matches(matches[0], committed_candidate, snapshot),
            "final parent sink did not commit this original reviewed draft",
        )
        _require(
            not any(
                row["kind"] == "planning-committed" and row["body"]["parent_commit_id"] == parent_commit.row_id
                for row in self._rows()
            ),
            "original planning sink ACK was already published",
        )
        ident = self._append("planning-committed", {"parent_commit_id": parent_commit.row_id, **body})["id"]
        del self._commit_receipts[parent_commit.nonce]
        self._last_parent_ack = None
        return ident

    @_technical_hold
    def prepared_publication(self, selector):
        """Return only the exact handle formed at native history's real guard."""
        handles = [handle for handle in self._publications.values() if _json(handle.body)["selector"] == selector]
        _require(len(handles) == 1, "missing original same-process native publication preparation")
        return handles[0]

    @_technical_hold
    def prepare_initial_approval(self, draft_state, selected, *, snapshot):
        """Called from the actual parsed CLI approval action, before _approve.

        This forms a process-local operator intent, not a committed approval,
        native image PASS or permission to launch. The original lifecycle still
        performs its full reviewed-plan/question/obligation gates.
        """
        rows, _prepared, _finished = self._idle()
        goal = draft_state["goal_contract"]
        _require(
            snapshot == util.snapshot(self.workspace)
            and draft_state.get("current_task") is None
            and draft_state["status"] in ("AWAITING_GOAL_APPROVAL", "PAUSED_GOAL_UNAPPROVED")
            and not any(
                draft_state.get(key) for key in ("active_stage", "uncertain_artifacts", "pending_report_repair")
            )
            and contracts.sealed(goal)
            and selected == contracts.token(goal)
            and draft_state.get("displayed_goal") == selected
            and not goal["body"]["open_blocking_questions"],
            "not the actual displayed idle operator approval",
        )
        _require(
            not any(
                row["kind"] == "approval-committed" and row["body"]["contract"]["hash"] == goal["hash"] for row in rows
            ),
            "this contract already has an original operator delivery",
        )
        admission = scope.admit(goal["body"], draft_state["settings"]["design_manifest"]["body"])
        _require(
            admission is not None
            and draft_state["settings"].get("joint_planning") is True
            and draft_state.get("planning", {}).get("final_token") == selected,
            "initial task lacks its exact reviewed serial visual plan",
        )
        reviewed = [
            row
            for row in rows
            if row["kind"] == "planning-committed"
            and row["body"]["contract"] == goal
            and route_fit.same(row["body"]["settings"], draft_state["settings"])
            and row["body"]["source"] == snapshot
            and row["body"]["token"] == selected
        ]
        _require(len(reviewed) == 1, "saved final token lacks its original native reviewed-plan parent publication")
        self._pins(reviewed[0]["body"]["pins"])
        initial = goal["body"]["initial_task"]
        decision = {
            "status": "CONTINUE",
            "next_objective": initial["objective"],
            "affected_paths": list(goal["body"]["milestones"][0]["affected_paths"])
            if initial["affected_paths"] or initial["kind"] == "validate"
            else [],
            "next_task": {key: value for key, value in initial.items() if key not in ("objective", "affected_paths")},
        }
        prospective = source_snapshot.snapshot(self.workspace, paths=source_scope.declared_paths(goal["body"]))
        _require(snapshot == util.snapshot(self.workspace), "source changed while preparing approved inventory")
        source_transition.selected(prospective, goal["body"])
        body = {
            "selected": selected,
            "draft_contract": deepcopy(goal),
            "settings": deepcopy(draft_state["settings"]),
            "snapshot": deepcopy(snapshot),
            "decision": decision,
            "selected_snapshot": prospective,
            "runtime_sha256": util.digest(history.runtime_files(self.runtime)),
        }
        row = self._append("approval-prepared", body)
        handle = PreparedApproval(self, uuid.uuid4().hex, row["id"], _bytes(body))
        self._approvals[handle.nonce] = handle
        return handle

    def _approved_candidate(self, handle, state: RunState, snapshot, *, require_task=True):
        _require(
            type(handle) is PreparedApproval and handle.owner is self and self._approvals.get(handle.nonce) is handle,
            "foreign/restarted/replayed operator intent",
        )
        body = _json(handle.body)
        goal, draft = cast(dict, state["goal_contract"]), body["draft_contract"]
        _require(
            _contract_approved(state)
            and all(goal[key] == draft[key] for key in ("task_id", "revision", "hash", "body"))
            and goal["approval_event"]["token"] == body["selected"]
            and goal["approval_event"]["kind"] == "goal_approval"
            and goal["approval_event"]["actor"] == "user_cli"
            and route_fit.same(state["settings"], body["settings"])
            and body["snapshot"] == util.snapshot(self.workspace)
            and snapshot
            == source_transition.selected(body["selected_snapshot"], draft["body"])
            == source_scope.snapshot(self.workspace, state)
            and body["runtime_sha256"] == util.digest(history.runtime_files(self.runtime)),
            "operator candidate changed exact source/contract/settings/runtime",
        )
        if require_task:
            check_initial_task_declaration(
                goal, body["decision"], state.get("current_task"), snapshot, state.get("findings_ledger", [])
            )
        else:
            _require(state.get("current_task") is None, "initial assignment preparation cannot replace a task")
        return body

    @_technical_hold
    def initial_assignment_proof(self, handle, approved_candidate, *, snapshot):
        """Only a tentative pure first assignment inside the actual CLI action.

        The first paid/native writer is separately refused until AFTER original
        commit_user_action returns and commit_initial_approval seals delivery.
        """
        body = self._approved_candidate(handle, approved_candidate, snapshot, require_task=False)
        _require(approved_candidate.get("current_task") is None, "initial proof cannot replace a task")
        return checkpoint.VerifiedInitialAssignment(
            checkpoint.binding(approved_candidate, snapshot, initial=True),
            snapshot["revision"],
            body["runtime_sha256"],
            checkpoint.assignment_identity(body["decision"]),
        )

    @_technical_hold
    def commit_initial_approval(self, handle, committed_candidate, *, snapshot, parent_commit):
        """Mandatory CODE hook after the original durable CLI parent commit.

        Not callable on a reconstructed token/event. Exact existing action and
        persist ordering must be wired by the parent; no private state is read.
        """
        body = self._approved_candidate(handle, committed_candidate, snapshot)
        self._parent_ack(parent_commit, committed_candidate, snapshot, approval=handle)
        row = self._append(
            "approval-committed",
            {
                "prepared_id": handle.row_id,
                "contract": deepcopy(committed_candidate["goal_contract"]),
                "settings": body["settings"],
                "snapshot": body["selected_snapshot"],
                "decision": body["decision"],
                "runtime_sha256": body["runtime_sha256"],
                "current_task": deepcopy(committed_candidate.get("current_task")),
                "parent_commit_id": parent_commit.row_id,
            },
        )
        del self._approvals[handle.nonce]
        del self._commit_receipts[parent_commit.nonce]
        self._last_parent_ack = None
        return row["id"]

    @_technical_hold
    def verify_initial_approval(self, *, query):
        rows, _prepared, _finished = self._idle()
        approvals = [
            row
            for row in rows
            if row["kind"] == "approval-committed"
            and all(row["body"][key] == query[key] for key in ("contract", "settings", "snapshot", "decision"))
        ]
        _require(
            len(approvals) == 1
            and query["snapshot"] == self._fresh_snapshot(rows)
            and approvals[0]["body"]["runtime_sha256"] == util.digest(history.runtime_files(self.runtime)),
            "missing original guarded operator delivery/source/runtime; flags and tokens are insufficient",
        )
        pins = self._journal_pins(rows)
        return {
            "kind": query["kind"],
            "query_sha256": util.digest(query),
            "evidence_hashes": pins,
            "protected_refs": list(pins),
        }
