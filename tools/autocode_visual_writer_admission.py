"""Protected causal writer-admission history; never a second controller.

WriterAdmissions._append is the sole retained-ledger writer. prepare_* creates
only PREPARED history. recheck creates short-lived in-memory launch permission;
seal_launch records actual Popen arguments and ProcessTree root birth. Readers
authenticate history/retained deltas for the admitted owner's bounded REWORK
or an already authorized SAME-TASK retry; no new allowance is granted.

The injected authenticate(kind, query, selector) is a runtime trust boundary,
not a model hook. It must independently verify original approval, native
receipt/delivery/report, containment, dispatch and accepted decision artifacts.
This module verifies their exact query identity and retained pins, not native
transport semantics. A checksum, saved owner, accepted bool or chmod is not
authority. Missing authentication fails closed. No state.json is read/written.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import uuid
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_design_inventory as inventory
    from . import autocode_native_locators as native_locators
    from . import autocode_process as processes
    from . import autocode_route_fit as route_fit
    from . import autocode_source_scope as source_scope
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_tool_containment as containment
    from . import autocode_util as util
    from . import autocode_visual_scope as visual_scope
    from . import autocode_writer_barrier as barriers
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_design_inventory as inventory
    import autocode_native_locators as native_locators
    import autocode_process as processes
    import autocode_route_fit as route_fit
    import autocode_source_scope as source_scope
    import autocode_source_snapshot as source_snapshot
    import autocode_tool_containment as containment
    import autocode_util as util
    import autocode_visual_scope as visual_scope
    import autocode_writer_barrier as barriers


_RECORD = re.compile(r"([0-9]{8})-([0-9a-f]{64})\.json")
_BINDING = frozenset(
    ("policy", "contract", "manifest_body", "task", "settings", "runtime_sha256", "assignment_source", "source")
)


def _require(condition, reason):
    if not condition:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Writer admission: " + reason)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _json(value):
    # Canonical bytes are immutable in Prepared; nested dictionaries never
    # remain caller-mutable authority. Reject non-JSON and non-finite values.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _ids(value, label, *, empty=False):
    _require(
        isinstance(value, list)
        and (value or empty)
        and all(isinstance(item, str) and item.strip() for item in value)
        and len(value) == len(set(value)),
        label + " needs exact unique IDs",
    )
    return list(value)


def _paths(value, label, *, empty=False):
    values = _ids(value, label, empty=empty)
    for name in values:
        path = PurePosixPath(name)
        _require(
            not path.is_absolute()
            and ".." not in path.parts
            and "\\" not in name
            and "\x00" not in name
            and name != "."
            and str(path) == name.rstrip("/")
            and not set(path.parts) & {".git", ".autocode", ".autocode-ui"},
            label + " needs portable project paths",
        )
    return values


def _within(name, declared):
    if any(char in name for char in "*?["):
        return name in declared  # Never narrow a glob merely by intersection.
    return any(
        inventory.path_intersects(name, owned)
        and (
            any(char in owned for char in "*?[")
            or name.rstrip("/") == owned.rstrip("/")
            or name.rstrip("/").startswith(owned.rstrip("/") + "/")
        )
        for owned in declared
    )


def _snapshot(value):
    _require(
        isinstance(value, dict)
        and set(value) in ({"head", "files", "revision"}, {"head", "files", "revision", "source_paths"})
        and ("source_paths" not in value or source_snapshot.is_canonical_paths(value["source_paths"]))
        and isinstance(value["head"], str)
        and value["head"]
        and isinstance(value["files"], dict)
        and all(isinstance(name, str) and isinstance(pin, str) for name, pin in value["files"].items())
        and value["revision"] == util.digest({key: value[key] for key in ("head", "files")}),
        "missing or changed complete source snapshot",
    )
    _paths(list(value["files"]), "Source inventory", empty=True)
    return deepcopy(value)


def _task(binding):
    _require(
        isinstance(binding, dict)
        and set(binding) == _BINDING
        and binding["policy"] == visual_scope.POLICY
        and _sha(binding["runtime_sha256"]),
        "unsupported exact writer binding",
    )
    saved, task = binding["contract"], binding["task"]
    _require(isinstance(saved, dict) and contracts.sealed(saved), "contract seal changed")
    facts = visual_scope.admit(saved["body"], binding["manifest_body"])
    _require(facts is not None and isinstance(binding["settings"], dict), "missing approved serial policy/settings")
    _require(
        isinstance(task, dict)
        and isinstance(task.get("id"), str)
        and task["id"]
        and task.get("kind") == "implement"
        and not task.get("milestone_ids")
        and task.get("contract_hash") == saved["hash"]
        and task.get("contract_revision") == saved["revision"],
        "writer task changed contract/kind or uses a batch",
    )
    owner = next((row for row in saved["body"]["milestones"] if row["id"] == task.get("milestone_id")), None)
    _require(owner is not None, "unknown writer owner")
    criteria = task.get("criterion_ids", task.get("acceptance_criteria"))
    _require(
        _ids(criteria, "Writer criteria") == owner["acceptance_criteria"],
        "writer must retain the complete owner's exact approved CIDs",
    )
    if "criterion_ids" in task and "acceptance_criteria" in task:
        _require(task["criterion_ids"] == task["acceptance_criteria"], "conflicting task criterion fields")
    paths = _paths(task.get("affected_paths"), "Writer paths")
    _require(all(_within(name, owner["affected_paths"]) for name in paths), "writer expands owner paths")
    assignment = _snapshot(binding["assignment_source"])
    source = _snapshot(binding["source"])
    _require(
        task.get("source_revision") == assignment["revision"] and assignment["head"] == source["head"],
        "task assignment anchor changed or operation source changed HEAD",
    )
    _json(binding)
    return facts, owner, criteria, paths


@dataclass(frozen=True)
class Prepared:
    """A process-local preparation handle, explicitly not a launch receipt."""

    id: str
    payload: bytes


class _Verification:
    """Private, one-operation traversal; never retained scheduling authority.

    The original protected ledger is read once under its existing exclusive
    lock. Canonical row bytes and lookup metadata are immutable. Only entries
    whose native/header/lineage checks have ALL completed enter the memo.
    Nothing is shared with the next operation, including failure state.
    """

    def __init__(self, owner, rows):
        self.owner = owner
        self.rows = tuple(_json(row) for row in rows)
        self.index = MappingProxyType(
            {row["id"]: (row["sequence"], row["kind"], raw) for row, raw in zip(rows, self.rows, strict=False)}
        )
        _require(len(self.index) == len(rows), "duplicate immutable ledger identity")
        self.completed = {}
        self.completed_deltas = {}
        self.active = set()
        self.evidence = {}
        self.appended = False
        self.live = True

    def close(self):
        self.live = False
        self.completed.clear()
        self.completed_deltas.clear()
        self.active.clear()
        self.evidence.clear()


class WriterAdmissions:
    def __init__(self, workspace, run_dir, *, authenticate, verification_scope=None):
        _require(callable(authenticate), "missing code-owned native/decision authenticator")
        self.workspace = Path(workspace).absolute()
        self.run = Path(run_dir).absolute()
        self.root = self.run / "visual-writer-admissions"
        self.authenticate = authenticate
        _require(verification_scope is None or callable(verification_scope), "invalid code-owned verification scope")
        self.verification_scope = verification_scope
        self._pending = {}
        self._ready = {}
        self._verification = None
        self._layout()

    def _layout(self):
        _require(
            self.workspace == self.workspace.resolve()
            and self.run == self.run.resolve()
            and self.run.parent == self.workspace / ".autocode" / "runs"
            and self.run.is_dir(),
            "ledger must belong to this exact existing owned run",
        )
        _require(
            not any(path.is_symlink() for path in (self.root, self.run, *self.run.parents)),
            "ledger authority traverses a symlink",
        )

    def _boundary(self, boundary, command, environment, *, operation):
        self._layout()
        containment.verify(boundary)  # Includes original native conformance/policy/shell pins.
        _require(
            boundary.get("allow_write") is True
            and boundary.get("tools") == ["bash"]
            and Path(boundary["workspace"]) == self.workspace
            and not self.root.is_relative_to(Path(boundary["scratch"])),
            "writer ledger is not outside the exact contained tool scratch",
        )
        _require(
            isinstance(command, list)
            and command
            and all(isinstance(word, str) and word for word in command)
            and isinstance(environment, dict)
            and all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()),
            "missing exact final Popen argv/environment",
        )
        _require(
            native_locators.boundary_matches(environment.get("AUTOCODE_TOOL_CONTAINMENT", "null"), boundary)
            and environment.get("SHELL") == boundary["shell"],
            "final child lost its native boundary",
        )
        # A retained conformance manifest alone is not the effective tool route.
        # The callback rechecks actual shell-only config/denies, no alternate
        # provider tools/plugins, executable identity and private-ancestor deny.
        return self._proof(
            "containment",
            {
                "workspace": str(self.workspace),
                "ledger": str(self.root),
                "boundary": deepcopy(boundary),
                "command_sha256": util.digest(command),
                "environment_sha256": util.digest(environment),
            },
            boundary,
            operation=operation,
        )

    def _file(self, value, *, protected=False):
        _require(isinstance(value, str) and value, "missing retained evidence path")
        path = Path(value)
        path = path if path.is_absolute() else self.workspace / path
        _require(
            ".." not in path.parts
            and path.is_relative_to(self.workspace)
            and path.name != "state.json"
            and path.is_file()
            and not any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(self.workspace)),
            "unsafe/missing retained evidence",
        )
        _require(not protected or path.is_relative_to(self.run), "authority evidence belongs to another run")
        return path

    def _pins(self, proof, *, operation):
        self._context(operation)
        pins, protected = proof["evidence_hashes"], proof["protected_refs"]
        _require(
            isinstance(pins, dict) and pins and all(_sha(pin) for pin in pins.values()),
            "authentication lacks original evidence pins",
        )
        _ids(protected, "Protected authority references")
        _require(set(protected) <= set(pins), "protected authority is not pinned")
        for name, expected in pins.items():
            path = self._file(name, protected=name in protected)
            _require(util.file_hash(path) == expected, "retained authentication evidence changed")
            prior = operation.evidence.get(str(path))
            _require(prior is None or prior[0] == expected, "conflicting original evidence pins")
            operation.evidence[str(path)] = (expected, name in protected or bool(prior and prior[1]))

    def _evidence_current(self, operation):
        """Memoized history cannot hide a changed retained file at operation end."""
        self._context(operation)
        for name, (expected, protected) in operation.evidence.items():
            _require(
                util.file_hash(self._file(name, protected=protected)) == expected,
                "retained authentication evidence changed during operation",
            )

    def _proof(self, kind, query, selector, *, operation):
        query = {**query, "workspace": str(self.workspace), "run_dir": str(self.run)}
        proof = self.authenticate(kind, deepcopy(query), deepcopy(selector))
        _require(
            isinstance(proof, dict)
            and set(proof) == {"kind", "query_sha256", "identity", "evidence_hashes", "protected_refs"}
            and proof["kind"] == kind
            and proof["query_sha256"] == util.digest(query)
            and isinstance(proof["identity"], dict)
            and proof["identity"],
            "missing/forged/foreign " + kind + " authentication",
        )
        self._pins(proof, operation=operation)
        _json(proof)
        return deepcopy(proof)

    @contextmanager
    def _locked(self):
        import fcntl

        self._layout()
        self.root.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(self.root / "ledger.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _context(self, operation):
        _require(
            isinstance(operation, _Verification)
            and operation.owner is self
            and operation.live
            and self._verification is operation,
            "foreign or expired private verification context",
        )

    @contextmanager
    def _operation(self):
        _require(self._verification is None, "reentrant ledger verification operation")
        # The CODE adapter/caller must also retain the original native runtime,
        # workspace, admission and artifact locks. This ledger lock cannot
        # manufacture native immutability or effective containment authority.
        with self._locked():
            operation = _Verification(self, self._rows())
            self._verification = operation
            try:
                yield operation
                self._evidence_current(operation)
            finally:
                operation.close()
                self._verification = None

    def _operation_rows(self, operation):
        self._context(operation)
        return [json.loads(raw) for raw in operation.rows]

    def _rows(self):
        self._layout()
        if not self.root.exists():
            return []
        rows, previous = [], None
        for path in sorted(self.root.iterdir()):
            if path.name == "ledger.lock":
                _require(not path.is_symlink(), "ledger lock was replaced")
                continue
            match = _RECORD.fullmatch(path.name)
            _require(match is not None and int(match[1]) == len(rows), "missing or unknown ledger entry")
            raw = self._file(str(path), protected=True).read_bytes()
            _require(raw.endswith(b"\n"), "truncated immutable ledger entry")
            value = json.loads(raw)
            _require(
                isinstance(value, dict)
                and set(value) == {"version", "sequence", "previous", "kind", "body", "id"}
                and value["version"] == 1
                and value["sequence"] == len(rows)
                and value["previous"] == previous
                and value["kind"] in ("prepared", "admission", "delta")
                and value["id"] == match[2] == util.digest({key: item for key, item in value.items() if key != "id"})
                and raw == _json(value) + b"\n",
                "changed or forged ledger chain",
            )
            rows.append(value)
            previous = value["id"]
        return rows

    def _append(self, kind, body, *, operation):
        """Sole writer: exclusive-create+fsync, never overwrite an existing receipt.

        Mode0700/0400 are hygiene only. Native shell-only private-ancestor deny
        and original artifact authentication establish the model boundary.
        """
        self._context(operation)
        _require(not operation.appended, "one ledger write per verification operation")
        rows = self._operation_rows(operation)
        if kind == "admission":
            # Preparation can be repeated; actual launches cannot replay
            # a nonce or overlap an unclosed admitted writer. The caller's
            # existing launch locks still cover Popen and this append.
            closed = {row["body"]["admission_id"] for row in rows if row["kind"] == "delta"}
            launches = [row for row in rows if row["kind"] == "admission"]
            _require(
                not any(row["body"]["prepared_id"] == body["prepared_id"] for row in launches),
                "actual preparation was already admitted",
            )
            _require(
                all(row["id"] in closed for row in launches), "previous admitted writer has no sealed stopped delta"
            )
            if body["lineage"] is None:
                binding = body["binding"]
                _require(
                    not any(
                        row["body"]["binding"]["contract"]["hash"] == binding["contract"]["hash"]
                        and row["body"]["binding"]["task"]["milestone_id"] == binding["task"]["milestone_id"]
                        for row in launches
                    ),
                    "owner already launched; original causal continuation is required",
                )
            if body["lineage"] is not None and body["lineage"]["kind"] == "retry":
                allowance = body["lineage"]["allowance"]["identity"]["allowance_sha256"]
                _require(
                    not any(
                        row["body"]["lineage"] is not None
                        and row["body"]["lineage"]["kind"] == "retry"
                        and row["body"]["lineage"]["allowance"]["identity"]["allowance_sha256"] == allowance
                        for row in launches
                    ),
                    "existing retry allowance was already admitted",
                )
        value = {
            "version": 1,
            "sequence": len(rows),
            "previous": rows[-1]["id"] if rows else None,
            "kind": kind,
            "body": deepcopy(body),
        }
        value["id"] = util.digest(value)
        path = self.root / (f"{len(rows):08d}-" + value["id"] + ".json")
        self._evidence_current(operation)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), "wb") as sink:
            sink.write(_json(value) + b"\n")
            sink.flush()
            os.fsync(sink.fileno())
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        operation.appended = True
        return deepcopy(value)

    def _get(self, identity, kind, *, operation, before_sequence=None):
        self._context(operation)
        _require(_sha(identity), "invalid retained receipt selector")
        item = operation.index.get(identity)
        _require(item is not None and item[1] == kind, "missing actual " + kind + " receipt")
        _require(before_sequence is None or item[0] < before_sequence, "cyclic or forward " + kind + " dependency")
        return json.loads(item[2])

    def _idle(self, *, operation):
        rows = self._operation_rows(operation)
        closed = {row["body"]["admission_id"] for row in rows if row["kind"] == "delta"}
        _require(
            all(row["id"] in closed for row in rows if row["kind"] == "admission"),
            "previous admitted writer has no sealed stopped delta",
        )

    def _current(self, binding, *, operation):
        _task(binding)
        proof = self._proof(
            "approval-runtime",
            {"binding": binding},
            {
                "contract": binding["contract"],
                "runtime_sha256": binding["runtime_sha256"],
                "settings": binding["settings"],
            },
            operation=operation,
        )
        _require(
            source_snapshot.snapshot(self.workspace, paths=source_scope.declared_paths(binding["contract"]["body"]))
            == binding["source"],
            "source changed before writer operation",
        )
        return proof

    def _prerequisites(self, binding, selectors, *, mode, operation):
        facts, owner, _, _ = _task(binding)
        # The approved serial chain makes earlier owners transitive
        # prerequisites. Require their current complete cases, so M3 cannot
        # borrow M2 admission after a shared write made M1 scheduling stale.
        dependencies = facts["milestone_ids"][: facts["milestone_ids"].index(owner["id"])]
        _require(
            isinstance(selectors, list)
            and all(isinstance(row, dict) for row in selectors)
            and [row.get("milestone_id") for row in selectors] == dependencies,
            "missing or reordered prerequisite native proof",
        )
        proofs = []
        for selector in selectors:
            mid = selector["milestone_id"]
            cases = [cid for cid in facts["case_ids"] if facts["case_owners"][cid] == mid]
            query = {"mode": mode, "binding": binding, "milestone_id": mid, "case_ids": cases}
            proof = self._proof("prerequisite", query, selector, operation=operation)
            identity = proof["identity"]
            _require(
                identity.get("milestone_id") == mid
                and identity.get("case_ids") == cases
                and all(
                    isinstance(identity.get(key), str) and identity[key]
                    for key in ("visual_receipt_id", "delivery_attempt_id", "native_session_id", "native_finish_id")
                )
                and all(_sha(identity.get(key)) for key in ("report_sha256", "stage_record_sha256")),
                "prerequisite lost complete-case receipt/report/native delivery identity",
            )
            proofs.append(proof)
        return proofs

    def _prepare(self, binding, selectors, lineage, *, boundary, command, environment, operation):
        self._idle(operation=operation)  # Native quiescence is authenticated separately.
        authority = self._current(binding, operation=operation)
        boundary_proof = self._boundary(boundary, command, environment, operation=operation)
        prerequisites = (
            self._prerequisites(binding, selectors, mode="current", operation=operation) if lineage is None else []
        )
        body = {
            "nonce": uuid.uuid4().hex,
            "binding": deepcopy(binding),
            "selectors": deepcopy(selectors),
            "authority": authority,
            "containment": boundary_proof,
            "prerequisites": prerequisites,
            "command_sha256": util.digest(command),
            "environment_sha256": util.digest(environment),
            "lineage": deepcopy(lineage),
            "admitted": False,
        }
        row = self._append("prepared", body, operation=operation)
        prepared = Prepared(row["id"], _json(body))
        self._pending[prepared.id] = prepared
        return prepared

    def prepare_new(self, binding, prerequisite_selectors, *, boundary, command, environment):
        with self._operation() as operation:
            return self._prepare_new(
                binding,
                prerequisite_selectors,
                boundary=boundary,
                command=command,
                environment=environment,
                operation=operation,
            )

    def _prepare_new(self, binding, prerequisite_selectors, *, boundary, command, environment, operation):
        """Prepare new-owner intent, no Popen/retry charge/admission authority."""
        _task(binding)
        _require(
            binding["assignment_source"] == binding["source"], "a new task needs its exact current assignment snapshot"
        )
        _require(
            not any(
                row["kind"] == "admission"
                and row["body"]["binding"]["task"]["milestone_id"] == binding["task"]["milestone_id"]
                and row["body"]["binding"]["contract"]["hash"] == binding["contract"]["hash"]
                for row in self._operation_rows(operation)
            ),
            "owner already launched; continuation needs original causal authority",
        )
        return self._prepare(
            binding,
            prerequisite_selectors,
            None,
            boundary=boundary,
            command=command,
            environment=environment,
            operation=operation,
        )

    def authenticate_history(self, admission_id):
        """Fresh historical operation; no cached authority accepted from callers."""
        with self._operation() as operation:
            return self._history(admission_id, operation=operation)

    def _history(self, admission_id, *, operation, before_sequence=None):
        """Historical causal authority only, never current scheduling PASS."""
        self._context(operation)
        _require(_sha(admission_id), "invalid retained receipt selector")
        _require(admission_id not in operation.active, "cyclic historical admission dependency")
        row = self._get(admission_id, "admission", operation=operation, before_sequence=before_sequence)
        # The exact canonical row bytes come from this one locked ledger read;
        # an ID, caller cache or previous operation cannot release authority.
        key = operation.index[admission_id][2]
        if key in operation.completed:
            return json.loads(operation.completed[key])
        operation.active.add(admission_id)
        try:
            body = row["body"]
            preparation = self._get(
                body["prepared_id"], "prepared", operation=operation, before_sequence=row["sequence"]
            )
            prepared = preparation["body"]
            _require(
                body["binding"] == prepared["binding"]
                and body["lineage"] == prepared["lineage"]
                and body["command_sha256"] == prepared["command_sha256"]
                and body["environment_sha256"] == prepared["environment_sha256"],
                "admission differs from original prepared identity",
            )
            _task(body["binding"])
            self._proof(
                "historical-launch", {"admission": row, "prepared": prepared}, body["launch"], operation=operation
            )
            if body["lineage"] is None:
                self._prerequisites(body["binding"], prepared["selectors"], mode="historical", operation=operation)
            else:
                self._revalidate_lineage(
                    body["binding"],
                    body["lineage"],
                    before_sequence=row["sequence"],
                    mode="historical",
                    operation=operation,
                )
            # Completed-only: exceptions cannot memoize a partial native proof,
            # a header match or an unvalidated descendant. Returned values copy.
            operation.completed[key] = key
            return deepcopy(row)
        finally:
            operation.active.remove(admission_id)

    def _chain(self, root_id, delta_ids, *, before_sequence=None, operation):
        root = self._history(root_id, operation=operation, before_sequence=before_sequence)
        _require(root["body"]["lineage"] is None, "REWORK must anchor the original new-owner launch")
        recorded = [
            row
            for row in self._operation_rows(operation)
            if row["kind"] == "delta"
            and row["body"]["root_admission"] == root_id
            and (before_sequence is None or row["sequence"] < before_sequence)
        ]
        _require(
            delta_ids == [row["id"] for row in recorded] and recorded,
            "missing/truncated/reordered retained Builder delta chain",
        )
        source, previous = root["body"]["binding"]["source"], None
        _, _, _, paths = _task(root["body"]["binding"])
        for row in recorded:
            body = row["body"]
            launch = self._history(body["admission_id"], operation=operation, before_sequence=row["sequence"])
            launch_root = launch["body"]["lineage"]["root_admission"] if launch["body"]["lineage"] else launch["id"]
            _require(
                body["before"] == source
                and body["previous_delta"] == previous
                and launch["sequence"] < row["sequence"]
                and launch["body"]["binding"]["source"] == body["before"]
                and launch_root == root_id,
                "broken or cross-owner causal source chain",
            )
            _snapshot(body["before"])
            _snapshot(body["after"])
            _require(
                all(_within(name, paths) for name in body["write_paths"]), "retained write expanded original scope"
            )
            key = (operation.index[row["id"]][2], operation.index[launch["id"]][2])
            if key not in operation.completed_deltas:
                proof = self._proof(
                    "builder-delta",
                    {
                        "admission": launch,
                        "before": body["before"],
                        "after": body["after"],
                        "changed_paths": util.changed_paths(body["before"], body["after"]),
                    },
                    body["selector"],
                    operation=operation,
                )
                _require(
                    proof == body["proof"] and proof["identity"].get("write_paths") == body["write_paths"],
                    "retained delta provenance or observed writes changed",
                )
                operation.completed_deltas[key] = _json(proof)
            source, previous = body["after"], row["id"]
        return root, source

    def _lineage(self, binding, original_id, delta_ids, decision_selector, *, before_sequence=None, operation):
        root, source = self._chain(original_id, delta_ids, before_sequence=before_sequence, operation=operation)
        original = root["body"]["binding"]
        _task(binding)
        _require(
            binding["source"] == source
            and binding["assignment_source"] == source
            and all(
                binding[key] == original[key]
                for key in ("policy", "contract", "manifest_body", "settings", "runtime_sha256")
            ),
            "REWORK changed source/assignment/contract/references/runtime/routes/retry policy/caps",
        )
        latest_delta = self._get(delta_ids[-1], "delta", operation=operation, before_sequence=before_sequence)
        latest = self._history(
            latest_delta["body"]["admission_id"], operation=operation, before_sequence=latest_delta["sequence"]
        )
        old, new = original["task"], binding["task"]
        _require(
            new["id"] != latest["body"]["binding"]["task"]["id"]
            and not any(
                row["kind"] == "admission"
                and row["body"]["binding"]["contract"]["hash"] == binding["contract"]["hash"]
                and row["body"]["binding"]["task"]["id"] == new["id"]
                and (before_sequence is None or row["sequence"] < before_sequence)
                for row in self._operation_rows(operation)
            )
            and new["milestone_id"] == old["milestone_id"]
            and new.get("criterion_ids", new.get("acceptance_criteria"))
            == old.get("criterion_ids", old.get("acceptance_criteria"))
            and all(_within(name, old["affected_paths"]) for name in new["affected_paths"]),
            "REWORK borrows another owner or expands original task CIDs/write scope",
        )
        query = {"root_admission": root, "delta_ids": delta_ids, "source": source, "next_binding": binding}
        decision = self._proof("bounded-rework", query, decision_selector, operation=operation)
        identity = decision["identity"]
        _require(
            (identity.get("stage"), identity.get("status")) in (("astra_review", "REWORK"), ("astra_resolve", "REWORK"))
            and identity.get("next_task_sha256") == util.digest(new)
            and _sha(identity.get("decision_sha256")),
            "not an accepted Completion/Resolver bounded repair",
        )
        return {
            "kind": "rework",
            "root_admission": original_id,
            "delta_ids": list(delta_ids),
            "decision_selector": deepcopy(decision_selector),
            "decision": decision,
        }

    def prepare_rework(self, binding, original_id, delta_ids, decision_selector, *, boundary, command, environment):
        with self._operation() as operation:
            return self._prepare_rework(
                binding,
                original_id,
                delta_ids,
                decision_selector,
                boundary=boundary,
                command=command,
                environment=environment,
                operation=operation,
            )

    def _prepare_rework(
        self, binding, original_id, delta_ids, decision_selector, *, boundary, command, environment, operation
    ):
        """Continue only an admitted owner; no current prerequisite eligibility is inferred."""
        lineage = self._lineage(binding, original_id, delta_ids, decision_selector, operation=operation)
        return self._prepare(
            binding, [], lineage, boundary=boundary, command=command, environment=environment, operation=operation
        )

    def _retry_lineage(
        self, binding, original_id, delta_ids, allowance_selector, *, before_sequence=None, mode="current", operation
    ):
        """Authenticate an existing allowance; never create, charge or enlarge it.

        The CODE adapter verifies current unused eligibility at preparation and
        prelaunch, and immutable original consumption/provenance in history.
        A saved action, generic checksum or task ID is not an allowance.
        """
        _require(mode in ("current", "historical"), "invalid retry authentication mode")
        root, source = self._chain(original_id, delta_ids, before_sequence=before_sequence, operation=operation)
        _task(binding)
        latest_delta = self._get(delta_ids[-1], "delta", operation=operation, before_sequence=before_sequence)
        latest = self._history(
            latest_delta["body"]["admission_id"], operation=operation, before_sequence=latest_delta["sequence"]
        )
        previous = latest["body"]["binding"]
        _require(
            binding["source"] == source
            and all(
                binding[key] == previous[key]
                for key in (
                    "policy",
                    "contract",
                    "manifest_body",
                    "task",
                    "settings",
                    "runtime_sha256",
                    "assignment_source",
                )
            ),
            "same-task retry changed assignment/task/CIDs/paths/source/routes/retry policy/caps",
        )
        _require(
            all(
                binding[key] == root["body"]["binding"][key]
                for key in ("policy", "contract", "manifest_body", "settings", "runtime_sha256")
            ),
            "same-task retry borrowed a different original admission",
        )
        query = {
            "mode": mode,
            "root_admission": root,
            "delta_ids": list(delta_ids),
            "stopped_admission": latest,
            "stopped_delta": latest_delta,
            "next_binding": binding,
        }
        allowance = self._proof("retry-allowance", query, allowance_selector, operation=operation)
        identity = allowance["identity"]
        required = {
            "allowance_kind",
            "allowance_sha256",
            "original_outcome_sha256",
            "failure_evidence_sha256",
            "attempt_ordinal",
            "permitted_attempts",
            "stopped_admission_id",
            "stopped_delta_id",
            "task_sha256",
            "assignment_source_revision",
            "operation_source_revision",
            "settings_sha256",
            "runtime_sha256",
        }
        _require(
            set(identity) == required
            and identity["allowance_kind"] in ("ordinary_retry", "explicit_builder_retry", "provider_startup_retry")
            and all(
                _sha(identity[key])
                for key in (
                    "allowance_sha256",
                    "original_outcome_sha256",
                    "failure_evidence_sha256",
                    "task_sha256",
                    "assignment_source_revision",
                    "operation_source_revision",
                    "settings_sha256",
                    "runtime_sha256",
                )
            )
            and type(identity["attempt_ordinal"]) is int
            and identity["attempt_ordinal"] > 0
            and type(identity["permitted_attempts"]) is int
            and identity["permitted_attempts"] == 1
            and identity["stopped_admission_id"] == latest["id"]
            and identity["stopped_delta_id"] == latest_delta["id"]
            and identity["task_sha256"] == util.digest(binding["task"])
            and identity["assignment_source_revision"] == binding["assignment_source"]["revision"]
            and identity["operation_source_revision"] == source["revision"]
            and identity["settings_sha256"] in route_fit.digests(binding["settings"])
            and identity["runtime_sha256"] == binding["runtime_sha256"],
            "retry lacks the exact original stopped outcome and existing single dispatch allowance",
        )
        if identity["allowance_kind"] == "provider_startup_retry":
            _require(
                latest_delta["body"]["before"] == latest_delta["body"]["after"]
                and latest_delta["body"]["write_paths"] == [],
                "provider startup retry requires an unchanged attempt with no observed writes",
            )
        # An immutable spent grant is still authentic history, never current
        # permission. Prevent preparation reuse after an actual admitted launch.
        if mode == "current":
            _require(
                not any(
                    row["kind"] == "admission"
                    and row["body"]["lineage"] is not None
                    and row["body"]["lineage"]["kind"] == "retry"
                    and row["body"]["lineage"]["allowance"]["identity"]["allowance_sha256"]
                    == identity["allowance_sha256"]
                    for row in self._operation_rows(operation)
                ),
                "existing retry allowance was already admitted",
            )
        return {
            "kind": "retry",
            "root_admission": original_id,
            "delta_ids": list(delta_ids),
            "previous_admission_id": latest["id"],
            "previous_delta_id": latest_delta["id"],
            "allowance_selector": deepcopy(allowance_selector),
            "allowance": allowance,
        }

    def prepare_retry(self, binding, original_id, delta_ids, allowance_selector, *, boundary, command, environment):
        with self._operation() as operation:
            return self._prepare_retry(
                binding,
                original_id,
                delta_ids,
                allowance_selector,
                boundary=boundary,
                command=command,
                environment=environment,
                operation=operation,
            )

    def _prepare_retry(
        self, binding, original_id, delta_ids, allowance_selector, *, boundary, command, environment, operation
    ):
        """Prepare only an already permitted same-task ordinary/operator/startup retry."""
        lineage = self._retry_lineage(binding, original_id, delta_ids, allowance_selector, operation=operation)
        return self._prepare(
            binding, [], lineage, boundary=boundary, command=command, environment=environment, operation=operation
        )

    def _revalidate_lineage(self, binding, lineage, *, before_sequence=None, mode="current", operation):
        _require(
            isinstance(lineage, dict) and lineage.get("kind") in ("rework", "retry"),
            "unknown original continuation lineage",
        )
        root = self._get(lineage["root_admission"], "admission", operation=operation, before_sequence=before_sequence)
        _require(
            root["body"]["lineage"] is None and (before_sequence is None or root["sequence"] < before_sequence),
            "continuation does not follow its original new-owner admission",
        )
        if lineage["kind"] == "rework":
            actual = self._lineage(
                binding,
                lineage["root_admission"],
                lineage["delta_ids"],
                lineage["decision_selector"],
                before_sequence=before_sequence,
                operation=operation,
            )
            _require(actual == lineage, "original accepted bounded REWORK decision or retained delta changed")
        else:
            actual = self._retry_lineage(
                binding,
                lineage["root_admission"],
                lineage["delta_ids"],
                lineage["allowance_selector"],
                before_sequence=before_sequence,
                mode=mode,
                operation=operation,
            )
            _require(
                {key: value for key, value in actual.items() if key != "allowance"}
                == {key: value for key, value in lineage.items() if key != "allowance"}
                and all(
                    actual["allowance"][key] == lineage["allowance"][key]
                    for key in ("kind", "identity", "evidence_hashes", "protected_refs")
                ),
                "original existing retry allowance, stopped outcome or retained delta changed",
            )
            if mode == "current":
                _require(actual["allowance"] == lineage["allowance"], "retry preparation authentication changed")

    def recheck(self, prepared, binding, *, boundary, command, environment, dispatch_selector):
        _require(self._pending.get(prepared.id) is prepared, "foreign/replayed preparation handle")
        self._ready.pop(prepared.id, None)
        with self._operation() as operation:
            scope = self.verification_scope(operation) if self.verification_scope is not None else nullcontext()
            with scope:
                dispatch = self._recheck(
                    prepared,
                    binding,
                    boundary=boundary,
                    command=command,
                    environment=environment,
                    dispatch_selector=dispatch_selector,
                    operation=operation,
                )
        # A failed operation-end evidence recheck must leave NO ready handle.
        # The fresh dispatch result is published only after context completion;
        # it is not a historical memo or a next-operation verification cache.
        self._ready[prepared.id] = dispatch

    def _recheck(self, prepared, binding, *, boundary, command, environment, dispatch_selector, operation):
        """Immediately before Popen inside existing admission/workspace/run locks.

        Dispatch authentication rechecks the caller's existing charged ordinary
        allowance or explicit retry grant. This module never charges/grants it.
        """
        _require(self._pending.get(prepared.id) is prepared, "foreign/replayed preparation handle")
        self._ready.pop(prepared.id, None)
        self._idle(operation=operation)
        body = json.loads(prepared.payload)
        _require(
            self._get(prepared.id, "prepared", operation=operation)["body"] == body and binding == body["binding"],
            "prepared task/contract/source/policy changed",
        )
        _require(
            util.digest(command) == body["command_sha256"] and util.digest(environment) == body["environment_sha256"],
            "final Popen argv/environment changed",
        )
        self._current(binding, operation=operation)
        _require(
            self._boundary(boundary, command, environment, operation=operation) == body["containment"],
            "containment authority changed",
        )
        if body["lineage"] is None:
            _require(
                self._prerequisites(binding, body["selectors"], mode="current", operation=operation)
                == body["prerequisites"],
                "prerequisite proof changed before launch",
            )
        else:
            self._revalidate_lineage(binding, body["lineage"], operation=operation)
        query = {
            "prepared_id": prepared.id,
            "nonce": body["nonce"],
            "binding": binding,
            "command_sha256": body["command_sha256"],
            "environment_sha256": body["environment_sha256"],
        }
        dispatch = self._proof("dispatch", query, dispatch_selector, operation=operation)
        _require(_sha(dispatch["identity"].get("authorization_sha256")), "missing existing dispatch/retry authority")
        if body["lineage"] is not None and body["lineage"]["kind"] == "retry":
            allowance = body["lineage"]["allowance"]["identity"]
            _require(
                dispatch["identity"].get("retry_allowance_sha256") == allowance["allowance_sha256"]
                and dispatch["identity"].get("attempt_ordinal") == allowance["attempt_ordinal"]
                and dispatch["identity"].get("stopped_admission_id") == allowance["stopped_admission_id"],
                "dispatch did not consume this exact existing retry allowance",
            )
        return _json(dispatch)

    def seal_launch(self, prepared, child, owned_tree, *, command, environment, launched_at, barrier=None):
        with self._operation() as operation:
            return self._seal_launch(
                prepared,
                child,
                owned_tree,
                command=command,
                environment=environment,
                launched_at=launched_at,
                barrier=barrier,
                operation=operation,
            )

    def _seal_launch(self, prepared, child, owned_tree, *, command, environment, launched_at, operation, barrier=None):
        """Seal actual birth before any ordinary child.poll() can reap the root.

        Caller passes the final Popen argv/env, not earlier worker_context env.
        The original supervisor's tree is retained; no process/controller is
        created here. A fast exit with no native birth capture fails closed.
        """
        _require(
            self._pending.get(prepared.id) is prepared and prepared.id in self._ready,
            "preparation/recheck alone is not an actual launch",
        )
        body = json.loads(prepared.payload)
        transport = None
        if barrier is not None:
            _require(type(barrier) is barriers.WriterBarrier, "missing original writer barrier")
            transport = barrier.execution_facts(child, owned_tree)
            _require(
                transport["target_command_sha256"] == util.digest(command)
                and transport["target_environment_sha256"] == util.digest(environment),
                "held transport differs from the approved target",
            )
        else:
            _require(
                child.args == command and not hasattr(child, "_autocode_launch_envelope"),
                "actual Popen arguments differ",
            )
        _require(
            util.digest(command) == body["command_sha256"] and util.digest(environment) == body["environment_sha256"],
            "actual Popen arguments differ",
        )
        _require(
            isinstance(owned_tree, processes.ProcessTree) and owned_tree.pid == child.pid,
            "launch lacks the actual supervisor tree",
        )
        root = owned_tree.capture_root()
        try:
            timestamp = dt.datetime.fromisoformat(launched_at)
        except (TypeError, ValueError):
            _require(False, "actual launch needs an unambiguous UTC timestamp")
        _require(timestamp.utcoffset() == dt.timedelta(0), "actual launch needs an unambiguous UTC timestamp")
        _require(
            type(child.pid) is int
            and child.pid > 0
            and isinstance(root, dict)
            and root.get("pid") == child.pid
            and root.get("group") == child.pid
            and type(root.get("birth_identity")) in (int, float)
            and math.isfinite(root["birth_identity"])
            and root["birth_identity"] > 0
            and type(root.get("birth_time")) in (int, float)
            and math.isfinite(root["birth_time"])
            and root["birth_time"] > 0
            and isinstance(launched_at, str)
            and launched_at,
            "actual child birth/session identity is missing",
        )
        row = self._append(
            "admission",
            {
                "prepared_id": prepared.id,
                "binding": body["binding"],
                "lineage": body["lineage"],
                "command_sha256": body["command_sha256"],
                "environment_sha256": body["environment_sha256"],
                "dispatch": json.loads(self._ready[prepared.id]),
                "launch": {
                    "root": deepcopy(root),
                    "command_sha256": util.digest(command),
                    "environment_sha256": util.digest(environment),
                    "launched_at": launched_at,
                    **({"transport": transport} if transport is not None else {}),
                },
            },
            operation=operation,
        )
        del self._ready[prepared.id]
        del self._pending[prepared.id]
        return deepcopy(row)

    def record_delta(self, admission_id, before, after, stage_selector):
        with self._operation() as operation:
            return self._record_delta(admission_id, before, after, stage_selector, operation=operation)

    def _record_delta(self, admission_id, before, after, stage_selector, *, operation):
        """Retain a stopped Builder's exact before/after plus all native observed writes.

        Authenticator must verify quiescence and original stage/source/events;
        before/after alone cannot detect an out-of-scope write later restored.
        """
        admission = self._history(admission_id, operation=operation)
        body = admission["body"]
        before, after = _snapshot(before), _snapshot(after)
        _require(
            before == body["binding"]["source"]
            and before["head"] == after["head"]
            and source_snapshot.snapshot(
                self.workspace, paths=source_scope.declared_paths(body["binding"]["contract"]["body"])
            )
            == after,
            "Builder source/HEAD differs from retained launch",
        )
        root_id = body["lineage"]["root_admission"] if body["lineage"] else admission_id
        history = [
            row
            for row in self._operation_rows(operation)
            if row["kind"] == "delta" and row["body"]["root_admission"] == root_id
        ]
        _require(
            not any(row["body"]["admission_id"] == admission_id for row in history), "Builder delta is already sealed"
        )
        _require(not history or history[-1]["body"]["after"] == before, "retained Builder deltas do not chain")
        changed = util.changed_paths(before, after)
        query = {"admission": admission, "before": before, "after": after, "changed_paths": changed}
        proof = self._proof("builder-delta", query, stage_selector, operation=operation)
        writes = _paths(proof["identity"].get("write_paths"), "All observed Builder writes", empty=True)
        _, _, _, owned = _task(body["binding"])
        _require(
            set(changed) <= set(writes) and all(_within(name, owned) for name in writes),
            "Builder made an unaccounted/out-of-scope write, including restored writes",
        )
        return self._append(
            "delta",
            {
                "root_admission": root_id,
                "admission_id": admission_id,
                "previous_delta": history[-1]["id"] if history else None,
                "before": before,
                "after": after,
                "write_paths": writes,
                "selector": deepcopy(stage_selector),
                "proof": proof,
            },
            operation=operation,
        )
