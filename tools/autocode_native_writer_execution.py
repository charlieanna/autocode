"""One original first-writer preparation, dual seal and stopped-delta join.

This lower adapter starts no process and imports no factory/controller/owner.
Its concrete authority and parent sink are supplied by the original factory;
the original held launcher remains the sole transport and token releaser.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_source_scope as source_scope
    import autocode_util as util


def _require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native writer: " + reason)


class NativeWriterExecution:
    def __init__(
        self,
        authority,
        state: RunState,
        *,
        snapshot,
        record,
        boundary,
        command,
        environment,
        isolation,
        sink,
        sink_type,
    ):
        self.authority, self.owner, self.state = authority, authority._owner, state
        _require(
            type(sink) is sink_type and sink.factory.protection is self.owner,
            "original concrete durable parent sink is required",
        )
        self.sink = sink
        self.isolation = isolation
        self.before = deepcopy(snapshot)
        self.record = record
        self.boundary, self.command, self.environment = deepcopy(boundary), deepcopy(command), deepcopy(environment)
        _require(
            record.get("stage") == record.get("role") == "terra"
            and boundary.get("allow_write") is True
            and cast(dict, state["current_task"])["kind"] == "implement",
            "only the original first implement Builder is enabled",
        )
        self.binding = authority.binding()
        self.native = self.prepared = self.admission = self.delta = None
        self._sealed_root = None
        self.failed = False
        authority._execution = self

    def require_isolation(self, isolation, *, state: RunState, command, environment, boundary):
        _require(
            (
                self.owner._preparing_writer is self
                or self.native is not None
                and self.owner._writer_executions.get(self.native.nonce) is self
            )
            and isolation is self.isolation
            and state is self.state
            and command == self.command
            and environment == self.environment
            and boundary == self.boundary
            and self.authority._execution is self
            and not self.failed,
            "foreign or changed original writable isolation preparation",
        )

    def prepare(self):
        _require(self.prepared is None and self.native is None and not self.failed, "repeated writer preparation")
        ledger = self.authority.admissions
        with ledger._operation() as operation:
            rows = ledger._operation_rows(operation)
            roots = [row for row in rows if row["kind"] == "admission" and row["body"]["lineage"] is None]
            deltas = [row["id"] for row in rows if row["kind"] == "delta"]
        if roots:
            _require(len(roots) == 1 and deltas, "repair requires the complete original stopped delta chain")
            ordinary = {key: self.binding[key] for key in ("contract", "task", "settings", "source", "runtime_sha256")}
            assigned = self.owner._initial_task_authority(ordinary, self.owner._idle()[0])
            _require(assigned is not None, "repair cannot borrow initial approval")
            self.prepared = ledger.prepare_rework(
                self.binding,
                roots[0]["id"],
                deltas,
                {"assignment_id": assigned["id"]},
                boundary=self.boundary,
                command=self.command,
                environment=self.environment,
            )
        else:
            self.prepared = ledger.prepare_new(
                self.binding, [], boundary=self.boundary, command=self.command, environment=self.environment
            )

    def attach(self, native):
        _require(
            self.native is None
            and self.prepared is not None
            and native.owner is self.owner
            and self.owner._pending.get(native.nonce) is native,
            "foreign native writer preparation",
        )
        self.native = native

    def recheck(self):
        self.sink.require_ready()
        _require(
            not self.failed
            and self.native is not None
            and self.prepared is not None
            and self.owner._writer_executions.get(self.native.nonce) is self
            and self.owner._pending.get(self.native.nonce) is self.native,
            "original writer preparation is absent or already consumed",
        )
        self.owner.require_prepared_source(self.native)
        _require(
            source_scope.snapshot(self.owner.workspace, self.state) == self.before,
            "source changed before actual first-writer launch",
        )
        active = self.state.get("active_stage")
        _require(
            active is self.record
            and self.sink.durable_candidate is not None
            and self.sink.durable_candidate == self.state
            and self.sink.durable_candidate.get("active_stage") == active
            and self.sink.durable_candidate.get("current_task") == self.state["current_task"]
            and self.sink.durable_candidate.get("settings") == self.state["settings"]
            and self.sink.durable_candidate.get("goal_contract") == self.state["goal_contract"],
            "writer needs its actual ordinary durable active stage and saved limits",
        )
        selected = {
            "native_prepared_id": self.native.row_id,
            "record_sha256": util.digest(active),
            "candidate_sha256": util.digest(self.sink.durable_candidate),
        }
        self.authority.admissions.recheck(
            self.prepared,
            self.binding,
            boundary=self.boundary,
            command=self.command,
            environment=self.environment,
            dispatch_selector=selected,
        )

    def seal(self, barrier, child, tree):
        self.recheck()
        _require(self.admission is None, "writer already admitted")
        # Domain admission writes evidence only. The original protection caller
        # appends its own seal and invokes the ONE original barrier token.
        self.admission = self.authority.admissions.seal_launch(
            self.prepared,
            child,
            tree,
            command=self.command,
            environment=self.environment,
            launched_at=util.now(),
            barrier=barrier,
        )
        self._sealed_root = deepcopy(tree.capture_root())

    def finish(self, row):
        _require(
            self.admission is not None
            and self.delta is None
            and row["kind"] == "finished"
            and row["body"]["prepared_id"] == self.native.row_id,
            "stopped writer lost the actual original native finish",
        )
        selected = {"native_prepared_id": self.native.row_id, "native_finished_id": row["id"]}
        self.delta = self.authority.admissions.record_delta(
            self.admission["id"], self.before, row["body"]["after"], selected
        )
        return self.delta
