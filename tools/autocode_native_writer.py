"""Authenticate one original first writer against both retained journals.

No controller, private state reader, allowance or provider launch. A same-owner bounded REWORK must retain its actual assignment and Resolver;
new owners remain held. Historical evidence is rejoined to the actual native
seal/finish; a ledger row alone never authenticates a source transition.
"""

from __future__ import annotations

import os
import stat
import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_native_writer_execution as execution
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
    from . import autocode_visual_protection as protection
    from . import autocode_visual_writer_admission as admissions
except ImportError:
    import autocode_native_writer_execution as execution
    import autocode_source_scope as source_scope
    import autocode_util as util
    import autocode_visual_protection as protection
    import autocode_visual_writer_admission as admissions


class _WriterHistoryBatch:
    """Cold native facts for one original ledger recheck, never next-launch authority."""

    def __init__(self, authority, operation, chain):
        self.authority, self.owner = authority, authority._owner
        self.execution, self.operation = authority._operation(), operation
        authority.admissions._context(operation)
        self.facts = {}
        self.chain = chain.initial()
        self.chain_payload = chain.payload
        self.rows = protection._bytes(self.chain[0])
        self.pins = self.owner._journal_pins(protection._json(self.rows))
        copies = []
        for row in protection._json(self.rows):
            body = row["body"]
            self._add_pins(body.get("pins", {}))
            record = body.get("record", {})
            for name in ("before_ref", "after_ref"):
                if record.get(name) is not None:
                    path = self.owner._safe(record[name], file=True)
                    self._add_pins({str(path): util.file_hash(path)})
            if row["kind"] == "check-prepared":
                boundary = body["boundary"]
                copies.append(self.owner._safe(boundary["tree"]))
                self._add_pins(boundary.get("pins", {}))
                self._add_pins(boundary.get("executables", {}))
                if body["purpose"] == "initial_validate_capture":
                    self._add_pins(boundary["runtime"]["files"])
                    path = self.owner._safe(self.owner.workspace / boundary["config_ref"], file=True)
                    self._add_pins({str(path): util.file_hash(path)})
                else:
                    self._add_pins({boundary["profile"]: boundary["profile_sha256"]})
                    if boundary["copy"] is not None:
                        self._add_pins({boundary["copy"]["manifest"]: boundary["copy"]["sha256"]})
            descriptor = body.get("native_isolation")
            if descriptor is not None:
                self._add_pins(descriptor["files"])
                self._add_pins(
                    {
                        descriptor["manifest"]: descriptor["manifest_sha256"],
                        descriptor["child_identity"]["executable"]: descriptor["child_identity"]["executable_sha256"],
                    }
                )
        self.runtime = protection.history.runtime_files(self.owner.runtime)
        protection._require(
            util.digest(self.runtime) == self.execution.binding["runtime_sha256"],
            "writer batch runtime differs from its exact prepared binding",
        )
        self._add_pins({str(self.owner.runtime / name): pin for name, pin in self.runtime.items()})
        self.namespaces = (
            self.owner.root,
            self.owner.run / "visual-native-history",
            self.owner.run / "native-parent-outcomes",
            *copies,
        )
        self.members = self._members()
        for path in self.members:
            if path.is_file():
                protection._require(
                    path.name != "state.json" and not path.is_symlink(), "unsafe writer batch namespace member"
                )
                self._add_pins({str(path): util.file_hash(path)})
        self.source = deepcopy(self.execution.before)
        paths = {Path(name) for name in self.pins} | self.members | set(self.namespaces)
        for name in [*self.source["files"], *self.source.get("source_paths", ())]:
            path = self.owner.workspace / name
            paths.add(path)
            paths.update(parent for parent in path.parents if parent.is_relative_to(self.owner.workspace))
        # The actual ACK file is observed by metadata only; never read state.json.
        paths.add(self.owner.run / "state.json")
        self.stamps = {path: self._stamp(path) for path in paths}
        parents = {parent for path in paths for parent in path.parents}
        self.ancestors = {parent: self._ancestor(parent) for parent in parents}
        self.signature = self._signature()
        self._hashes()
        self.check()

    @staticmethod
    def _observe(path, observations):
        # One observation serves membership, type and identity in this check.
        # The next before/after check always starts a new observation map.
        if path not in observations:
            try:
                observations[path] = path.lstat()
            except FileNotFoundError:
                observations[path] = None
        return observations[path]

    @classmethod
    def _stamp(cls, path, observations=None):
        value = cls._observe(path, {} if observations is None else observations)
        if value is None:
            return None
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

    @classmethod
    def _ancestor(cls, path, observations=None):
        value = cls._observe(path, {} if observations is None else observations)
        if value is None:
            return None  # Approved future leaves can have missing nested parents.
        protection._require(stat.S_ISDIR(value.st_mode), "writer batch authority ancestor was aliased")
        # Shared run/.autocode and external runtime ancestors may host keeper
        # updates. Their timestamps are not immutable authority or a write log.
        return (value.st_dev, value.st_ino, value.st_mode)

    def _add_pins(self, pins):
        for name, pin in pins.items():
            prior = self.pins.get(name)
            protection._require(
                protection._sha(pin) and (prior is None or prior == pin), "conflicting original writer batch evidence"
            )
            self.pins[name] = pin

    def _members(self, observations=None):
        observations = {} if observations is None else observations
        result = set()
        directories = []
        for root in self.namespaces:
            value = self._observe(root, observations)
            protection._require(value is None or not stat.S_ISLNK(value.st_mode), "writer batch namespace was aliased")
            if value is None:
                continue
            protection._require(stat.S_ISDIR(value.st_mode), "writer batch namespace replaced")
            directories.append(root)
        visited = set()
        while directories:
            directory = directories.pop()
            if directory in visited:
                continue
            visited.add(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    value = self._observe(path, observations)
                    protection._require(
                        value is not None
                        and path.name != "state.json"
                        and (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode)),
                        "unsafe or nonregular writer batch namespace",
                    )
                    result.add(path)
                    if stat.S_ISDIR(value.st_mode):
                        directories.append(path)
        return result

    def _signature(self):
        value = self.execution
        value.sink.require_ready()
        protection._require(
            self.authority._execution is value
            and not value.failed
            and self.owner._writer_executions.get(value.native.nonce) is value
            and self.owner._pending.get(value.native.nonce) is value.native
            and value.state is self.authority._state
            and value.state.get("active_stage") is value.record
            and value.sink.factory.protection is self.owner,
            "writer batch lost its concrete original pending operation",
        )
        return protection._bytes(
            {
                "interpreter": [
                    str(Path(sys.prefix).resolve()),
                    str(Path(sys.base_prefix).resolve()),
                    str(Path(sys.executable).resolve()),
                ],
                "state": value.state,
                "binding": value.binding,
                "record": value.record,
                "candidate": value.sink.durable_candidate,
                "boundary": value.boundary,
                "command": value.command,
                "environment": value.environment,
                "sink_blocked": value.sink.blocked,
                "sink_error": str(value.sink.error),
                "sink_ack": value.sink.ack,
            }
        )

    def _hashes(self):
        for name, expected in self.pins.items():
            path = Path(name)
            protection._require(
                path.name != "state.json"
                and path.resolve() == path
                and path.is_file()
                and not path.is_symlink()
                and util.file_hash(path) == expected,
                "complete original writer batch pin changed",
            )

    def check(self):
        self.authority.admissions._context(self.operation)
        self.owner._ownership()
        observations = {}
        protection._require(
            self._signature() == self.signature and self._members(observations) == self.members,
            "writer batch task, durable source or protected namespace changed",
        )
        protection._require(
            all(self._ancestor(path, observations) == saved for path, saved in self.ancestors.items()),
            "writer batch authority ancestor changed",
        )
        protection._require(
            all(self._stamp(path, observations) == saved for path, saved in self.stamps.items()),
            "writer batch evidence/source was replaced or edited, even if restored",
        )

    def original(self, binding):
        self.check()
        key = protection._bytes(binding)
        if key not in self.facts:
            facts = self.owner.writer_history_authority(binding)
            self.check()
            protection._require(
                all(self.pins.get(name) == pin for name, pin in facts["evidence_hashes"].items()),
                "writer batch facts escaped complete cold custody",
            )
            protection._bytes(facts)  # Preserve the original strict JSON validation.
            # Canonical JSON is the binding key, not the cold fact value: sorting
            # evidence keys would reorder its original protected_refs proof.
            self.facts[key] = deepcopy(facts)
        return deepcopy(self.facts[key])

    def finish(self):
        self.check()
        protection._require(
            protection._bytes(self.owner._chain_cold()) == protection._bytes(self.chain),
            "complete newer native writer chain changed during recheck",
        )
        protection._require(
            protection.history.runtime_files(self.owner.runtime) == self.runtime
            and source_scope.snapshot(self.owner.workspace, self.authority._state) == self.source,
            "writer batch source or complete runtime changed during recheck",
        )
        self._hashes()
        self.check()

    def close(self):
        self.facts.clear()
        self.chain = None


class NativeWriterAuthority:
    @protection._technical_hold
    def __init__(self, owner, state: RunState, *, snapshot):
        protection._require(
            type(owner) is protection.NativeProtection, "writer authority needs the original concrete native owner"
        )
        self._owner, self._state = owner, state
        ordinary = owner._binding(state, snapshot)
        assigned = owner._initial_task_authority(ordinary, owner._idle()[0])
        facts = (
            owner.first_writer_authority(state, snapshot=snapshot)
            if assigned is None
            else owner.rework_writer_authority(state, snapshot=snapshot)
        )
        self._binding = protection._bytes(facts["binding"])
        self._execution = None
        self._history_batch = None
        self.admissions = admissions.WriterAdmissions(
            owner.workspace, owner.run, authenticate=self.authenticate, verification_scope=self._recheck_scope
        )

    def _fresh(self):
        current = source_scope.snapshot(self._owner.workspace, self._state)
        # Fresh launch readiness authenticates live task/runtime and original
        # approval evidence; historical replay alone cannot authorize a launch.
        ordinary = self._owner._binding(self._state, current)
        assigned = self._owner._initial_task_authority(ordinary, self._owner._idle()[0])
        facts = (
            self._owner.first_writer_authority(self._state, snapshot=current)
            if assigned is None
            else self._owner.rework_writer_authority(self._state, snapshot=current)
        )
        protection._require(current == facts["binding"]["source"], "writer source changed before preparation")
        protection._require(
            protection._bytes(facts["binding"]) == self._binding, "original writer binding changed; never rebind it"
        )
        return facts

    @classmethod
    @protection._technical_hold
    def historical(cls, owner, state, *, snapshot):
        protection._require(type(owner) is protection.NativeProtection, "original native writer owner required")
        value = object.__new__(cls)
        value._owner, value._state, value._execution = owner, state, None
        value._historical = True
        value._history_batch = None
        rows, prepared, _sealed, _released, finished = owner._chain()
        roots = [
            row
            for ident, row in prepared.items()
            if ident in finished and row["body"]["record_identity"]["stage"] == "terra" and row["body"]["grants"]
        ]
        protection._require(roots, "historical writer needs the actual original stopped writable launch")
        body = roots[0]["body"]["binding"]
        bound = {
            **deepcopy(body),
            "assignment_source": deepcopy(body["source"]),
            "policy": admissions.visual_scope.POLICY,
            "manifest_body": deepcopy(body["settings"]["design_manifest"]["body"]),
        }
        facts = owner.writer_history_authority(bound)
        value._binding = protection._bytes(facts["binding"])
        value.admissions = admissions.WriterAdmissions(
            owner.workspace, owner.run, authenticate=value.authenticate, verification_scope=value._recheck_scope
        )
        return value

    @contextmanager
    def _recheck_scope(self, operation):
        protection._require(self._history_batch is None, "overlapping original writer history operation")
        with self._owner._writer_chain_scope(self._operation(), operation) as chain:
            batch = _WriterHistoryBatch(self, operation, chain)
            self._history_batch = batch
            try:
                chain.activate(batch)
                yield
                chain.deactivate()
                batch.finish()  # Cold native exit before the ledger publishes ready.
            finally:
                self._history_batch = None
                batch.close()

    def _history_facts(self, binding):
        batch = self._history_batch
        return self._owner.writer_history_authority(binding) if batch is None else batch.original(binding)

    def _original(self):
        facts = self._history_facts(protection._json(self._binding))
        protection._require(protection._bytes(facts["binding"]) == self._binding, "original writer approval changed")
        return facts

    def _operation(self):
        value = self._execution
        protection._require(
            type(value) is execution.NativeWriterExecution
            and value.authority is self
            and value.owner is self._owner
            and not value.failed,
            "missing concrete original writer execution",
        )
        return value

    def _native_admission(self, admission):
        owner = self._owner
        rows, prepared, sealed, released, _finished = owner._chain()
        matches = [
            (p, sealed[ident])
            for ident, p in prepared.items()
            if ident in sealed
            and ident in released
            and p["body"]["record_identity"]["stage"] == p["body"]["record_identity"]["role"] == "terra"
            and p["body"]["binding"]
            == {
                key: admission["body"]["binding"][key]
                for key in ("contract", "task", "settings", "source", "runtime_sha256")
            }
            and p["body"]["command_sha256"] == admission["body"]["command_sha256"]
            and p["body"]["environment_sha256"] == admission["body"]["environment_sha256"]
        ]
        protection._require(len(matches) == 1, "writer admission lacks its exact original native seal/release")
        original, seal = matches[0]
        launch = admission["body"]["launch"]
        protection._require(
            launch["root"] == seal["body"]["root"]
            and launch.get("transport") == seal["body"]["barrier"]
            and seal["body"].get("writer_admission_id") == admission["id"]
            and seal["body"].get("writer_dispatch_sha256") == util.digest(admission["body"]["dispatch"]),
            "writer ledger birth/barrier differs from original native root",
        )
        return rows, original

    @protection._technical_hold
    def binding(self):
        return deepcopy(self._fresh()["binding"])

    @protection._technical_hold
    def authenticate(self, kind, query, selector):
        """Exact operation queries; no dictionary, callback or saved flag grants."""
        value = self._execution
        if (
            type(value) is execution.NativeWriterExecution
            and value.native is not None
            and (
                self._owner._pending.get(value.native.nonce) is value.native
                or self._owner._active.get(value.native.nonce, (None,))[0] is value.native
            )
        ):
            with self._owner._replay_before(value.native):
                return self._authenticate(kind, query, selector)
        return self._authenticate(kind, query, selector)

    def _authenticate(self, kind, query, selector):
        protection._require(
            query.get("workspace") == str(self._owner.workspace) and query.get("run_dir") == str(self._owner.run),
            "foreign writer query",
        )
        if kind in ("historical-launch", "builder-delta"):
            facts = self._history_facts(query["admission"]["body"]["binding"])
        elif kind == "bounded-rework":
            facts = self._history_facts(query["next_binding"])
        else:
            facts = (
                self._original()
                if self._execution is not None or getattr(self, "_historical", False)
                else self._fresh()
            )
        binding = facts["binding"]
        pins = deepcopy(facts["evidence_hashes"])
        identity = {
            "approval_id": facts["approval_id"],
            "task_id": binding["task"]["id"],
            "milestone_id": binding["task"]["milestone_id"],
            "assignment_source_revision": binding["assignment_source"]["revision"],
            "runtime_sha256": binding["runtime_sha256"],
        }
        if kind == "approval-runtime":
            expected = {"binding": binding, "workspace": str(self._owner.workspace), "run_dir": str(self._owner.run)}
            protection._require(
                query == expected
                and selector == {key: binding[key] for key in ("contract", "runtime_sha256", "settings")},
                "changed original writer authentication query",
            )
        elif kind == "containment":
            value = self._operation()
            expected = {
                "workspace": str(self._owner.workspace),
                "run_dir": str(self._owner.run),
                "ledger": str(self.admissions.root),
                "boundary": value.boundary,
                "command_sha256": util.digest(value.command),
                "environment_sha256": util.digest(value.environment),
            }
            protection._require(query == expected and selector == value.boundary, "foreign writer containment")
            actual_pins, _ = self._owner._native_config(
                value.boundary,
                value.command,
                value.environment,
                self._state,
                value.record,
                value.isolation,
                writer_execution=value,
            )
            pins.update(
                {
                    name: pin
                    for name, pin in actual_pins.items()
                    if protection.Path(name).is_relative_to(self._owner.run)
                }
            )
        elif kind == "dispatch":
            value = self._operation()
            protection._require(
                value.native is not None
                and value.prepared is not None
                and self._owner._writer_executions.get(value.native.nonce) is value
                and self._owner._pending.get(value.native.nonce) is value.native
                and value.sink.durable_candidate is not None
                and self._state.get("active_stage") is value.record
                and value.sink.durable_candidate.get("active_stage") == value.record,
                "ordinary dispatch did not durably admit this exact active writer",
            )
            value.sink.require_ready()
            expected = {
                "prepared_id": value.prepared.id,
                "nonce": admissions.json.loads(value.prepared.payload)["nonce"],
                "binding": binding,
                "command_sha256": util.digest(value.command),
                "environment_sha256": util.digest(value.environment),
                "workspace": str(self._owner.workspace),
                "run_dir": str(self._owner.run),
            }
            selected = {
                "native_prepared_id": value.native.row_id,
                "record_sha256": util.digest(value.record),
                "candidate_sha256": util.digest(value.sink.durable_candidate),
            }
            protection._require(query == expected and selector == selected, "changed actual ordinary dispatch")
            identity["authorization_sha256"] = util.digest(
                {
                    "approval_id": facts["approval_id"],
                    "active_stage": value.record,
                    "settings": binding["settings"],
                    "candidate": selected["candidate_sha256"],
                }
            )
        elif kind in ("historical-launch", "builder-delta"):
            admission = query["admission"]
            protection._require(
                admission["body"]["binding"] == binding, "historical writer changed its own exact causal binding"
            )
            rows, original = self._native_admission(admission)
            if kind == "historical-launch":
                expected = {
                    "admission": admission,
                    "prepared": query["prepared"],
                    "workspace": str(self._owner.workspace),
                    "run_dir": str(self._owner.run),
                }
                protection._require(
                    query == expected
                    and selector == admission["body"]["launch"]
                    and query["prepared"]["binding"] == binding
                    and query["prepared"]["command_sha256"] == original["body"]["command_sha256"]
                    and query["prepared"]["environment_sha256"] == original["body"]["environment_sha256"],
                    "historical writer launch changed",
                )
            else:
                stopped = [
                    row for row in rows if row["kind"] == "finished" and row["body"]["prepared_id"] == original["id"]
                ]
                protection._require(len(stopped) == 1, "missing actual stopped original writer")
                stopped = stopped[0]
                expected = {
                    "admission": admission,
                    "before": binding["source"],
                    "after": stopped["body"]["after"],
                    "changed_paths": util.changed_paths(binding["source"], stopped["body"]["after"]),
                    "workspace": str(self._owner.workspace),
                    "run_dir": str(self._owner.run),
                }
                protection._require(
                    query == expected
                    and selector == {"native_prepared_id": original["id"], "native_finished_id": stopped["id"]},
                    "stopped writer source/selector changed",
                )
                identity["write_paths"] = deepcopy(stopped["body"]["possible_writes"])
                pins.update(stopped["body"]["pins"])
            if kind == "builder-delta":
                pins.update(self._owner._journal_pins([row for row in rows if row["sequence"] <= stopped["sequence"]]))
            else:
                pins.update(
                    self._owner._journal_pins(
                        [
                            row
                            for row in rows
                            if row["sequence"]
                            <= next(
                                row["sequence"]
                                for row in rows
                                if row["kind"] == "released" and row["body"]["prepared_id"] == original["id"]
                            )
                        ]
                    )
                )
        elif kind == "bounded-rework":
            rows = self._owner._chain()[0]
            selected = [
                row
                for row in rows
                if row["id"] == selector.get("assignment_id") and row["kind"] == "assignment-committed"
            ]
            protection._require(len(selected) == 1, "bounded writer lacks its actual committed assignment")
            body = selected[0]["body"]
            self._owner._assignment_validator(body)
            protection._require(
                body["new_task"] == binding["task"]
                and body["binding"]["source"] == query["source"] == binding["source"]
                and body["decision"]["status"] == "REWORK"
                and body["record"]["stage"] == "astra_resolve"
                and query["next_binding"] == binding
                and query["root_admission"]["body"]["binding"]["task"]["milestone_id"]
                == binding["task"]["milestone_id"],
                "repair decision lost its exact same-owner task, source or status",
            )
            identity = {
                **identity,
                "stage": body["record"]["stage"],
                "status": "REWORK",
                "next_task_sha256": util.digest(body["new_task"]),
                "decision_sha256": body["decision_sha256"],
            }
            pins.update(body["pins"])
            pins.update(self._owner._journal_pins([row for row in rows if row["sequence"] <= selected[0]["sequence"]]))
        else:
            protection._require(False, "writer " + str(kind) + " authority remains held")
        self._owner._pins(pins)
        return {
            "kind": kind,
            "query_sha256": util.digest(query),
            "identity": identity,
            "evidence_hashes": pins,
            "protected_refs": list(pins),
        }
