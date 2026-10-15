"""Assemble fresh visual domain facts from concrete original native proof.

No global registry, model hook, controller import, state-file read or authority
from stored accepted flags. Scope 3 threads original planning/first-approval
hooks; incomplete later routes remain held and qualification is still required.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_native_assignment as assignments
    from . import autocode_native_writer as writer
    from . import autocode_native_writer_execution as writer_execution
    from . import autocode_util as util
    from . import autocode_visual_checkpoint as checkpoint
    from . import autocode_visual_history as history
    from . import autocode_visual_protection as protection
    from . import autocode_visual_scope as scope
except ImportError:
    import autocode_native_assignment as assignments
    import autocode_native_writer as writer
    import autocode_native_writer_execution as writer_execution
    import autocode_util as util
    import autocode_visual_checkpoint as checkpoint
    import autocode_visual_history as history
    import autocode_visual_protection as protection
    import autocode_visual_scope as scope


def _require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native factory: " + reason)


def _scope_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class NativeVisualFactory:
    @protection._technical_hold
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
        self.protection = protection.NativeProtection(
            workspace,
            run_dir,
            run_handle=run_handle,
            checkout_handle=checkout_handle,
            start_state=start_state,
            snapshot=snapshot,
            activation_intent=activation_intent,
            **(
                {"planning_abandonment_intent": planning_abandonment_intent}
                if planning_abandonment_intent is not None
                else {}
            ),
        )
        self.history = history.NativeReviewHistory(workspace, run_dir, protection=self.protection)
        self.workspace = self.protection.workspace
        self.runtime = self.protection.runtime
        self._checkpoint_publications = {}
        self._parent_sink = self._parent_sink_type = None
        self.protection._writer_delta_validator = self.verify_first_writer_delta
        self.protection._assignment_validator = self.verify_assignment

    def bind_parent_sink(self, sink, sink_type):
        _require(
            self._parent_sink is None and type(sink) is sink_type and sink.factory is self,
            "original native parent sink may be bound only once",
        )
        self._parent_sink, self._parent_sink_type = sink, sink_type

    @protection._technical_hold
    def verify_first_writer_delta(self, state: RunState, *, snapshot, builder, stopped):
        authority = writer.NativeWriterAuthority.historical(self.protection, state, snapshot=snapshot)
        with authority.admissions._operation() as operation:
            rows = authority.admissions._operation_rows(operation)
            original = [row for row in rows if row["kind"] == "admission" and row["body"]["lineage"] is None]
            deltas = [row for row in rows if row["kind"] == "delta"]
            _require(
                len(original) == 1
                and deltas
                and deltas[-1]["body"]["selector"]
                == {"native_prepared_id": builder["id"], "native_finished_id": stopped["id"]},
                "missing exact original admission and complete stopped delta chain",
            )
            _root, after = authority.admissions._chain(
                original[0]["id"], [row["id"] for row in deltas], operation=operation
            )
            _require(after == snapshot, "complete stopped writer delta chain is not current source")
        return original[0]["id"]

    def verify_decision_review(self, selector):
        return (
            self.history.verify_rejection(selector)
            if selector["kind"] == "rejections"
            else self.history.verify_review(selector)
        )

    @protection._technical_hold
    def current_decision_review(self, state: RunState, *, snapshot):
        self.protection.require_task_authority(state, snapshot=snapshot)
        bound = self.protection._binding(state, snapshot)
        selected = []
        for kind in ("reviews", "rejections"):
            for selector in self.protection.committed_selectors(kind):
                if self.verify_decision_review(selector)["metadata"]["binding"] == bound:
                    selected.append(selector)
        _require(len(selected) == 1, "exactly one current original passing review or retained rejection is required")
        return selected[0]

    @protection._technical_hold
    def decision_review_proof(self, state: RunState, *, snapshot, selector):
        original = self.verify_decision_review(selector)
        _require(
            original["metadata"]["binding"] == self.protection._binding(state, snapshot),
            "decision review is not the actual current task/source",
        )
        if selector["kind"] == "reviews":
            return self.checkpoint_proof(state, snapshot=snapshot, review_selector=selector)
        facts = self.history.fresh_facts(state=state, snapshot=snapshot)
        derived = self.scope_authority(state=state, snapshot=snapshot)
        return checkpoint.VerifiedCheckpoint(
            checkpoint.binding(state, snapshot),
            snapshot["revision"],
            facts["current_binding"]["runtime_sha256"],
            _scope_json(derived),
            tuple(facts["historical_milestone_ids"]),
            tuple(facts["eligible_milestone_ids"]),
            False,
            None,
        )

    @protection._technical_hold
    def require_resolver_request(self, state: RunState, *, snapshot):
        bound = self.protection._binding(state, snapshot)
        request = state.get("resolution_request") or {}
        records = [
            row
            for row in state.get("stages", [])
            if row.get("output") == request.get("source_output") and row.get("stage") == "astra_review"
        ]
        _require(len(records) == 1, "Resolver needs the genuine current ordinary Completion request")
        self._resolver_request_evidence(bound, request, records[0])
        return deepcopy(records[0])

    def _resolver_request_evidence(self, bound, request, record):
        _require(
            request.get("task_id") == bound["task"]["id"]
            and request.get("contract_hash") == bound["contract"]["hash"]
            and request.get("source_revision") == bound["source"]["revision"]
            and request.get("source_stage") == "astra_review"
            and request.get("provenance") == "completion_review_decision"
            and request.get("source_output") == request.get("review_output") == record["output"],
            "Resolver cannot borrow another task, source or routing packet",
        )
        report = protection._json(self.protection._safe(record["output"], file=True).read_bytes())
        _require(
            report.get("status") in ("REWORK", "BLOCKED") and request.get("review") == report,
            "Resolver packet changed the genuine nonpassing Completion decision",
        )
        self._decision_evidence(bound, record, report)
        _require(
            all(
                request.get("evidence_hashes", {}).get(record[key])
                == util.file_hash(self.protection._safe(record[key], file=True))
                for key in ("output", "events")
            ),
            "ordinary Resolver packet omitted its original Completion artifacts",
        )
        self.protection._pins(request["evidence_hashes"])

    def _decision_evidence(self, binding, record, decision):
        rows, original, stopped = self.protection._finished_for(history.record_identity(record))
        _require(
            record.get("stage") == record.get("role")
            and record["stage"] in ("astra_review", "astra_resolve")
            and original["body"]["binding"] == binding
            and stopped["body"]["after"] == binding["source"]
            and original["body"]["grants"] == []
            and record.get("exit_code") == 0
            and not any(
                record.get(key)
                for key in (
                    "report_only",
                    "report_repaired",
                    "repaired_by",
                    "applied_original_events",
                    "timed_out",
                    "interrupted",
                    "rejected",
                    "dry_run",
                    "truncated_output",
                )
            ),
            "assignment needs its actual successful readonly Completion/Resolver",
        )
        route = binding["settings"]["roles"][protection.record_route(record)]
        _require(
            record.get("engine") == (route.get("engine") or binding["settings"].get("engine")) == "opencode"
            and (record["launch_route"].get("provider") or record["engine"]) == (route.get("provider") or "opencode")
            and record["launch_route"]["model"] == route["model"]
            and record["launch_route"].get("reasoning_effort") == route.get("reasoning_effort"),
            "assignment decision changed its saved route or reasoning",
        )
        pins = {
            **stopped["body"]["pins"],
            **self.protection._journal_pins([row for row in rows if row["sequence"] <= stopped["sequence"]]),
        }
        self.protection._pins(pins)
        read = lambda name: self.protection._safe(name, file=True).read_bytes()
        report = protection._json(read(record["output"]))
        raw_report = protection._json(read(record.get("reported_output") or record["output"]))
        _require(
            report == raw_report
            and all(
                decision.get(key) == report.get(key)
                for key in (
                    "status",
                    "next_task",
                    "next_objective",
                    "affected_paths",
                    "evidence",
                    "task_id",
                    "contract_hash",
                    "contract_revision",
                    "user_request",
                )
            ),
            "assignment changed the actual accepted original decision",
        )
        for key in ("before_ref", "after_ref"):
            _require(
                protection._json(read(record[key])) == binding["source"], "decision was not readonly on its own source"
            )
        protection.acceptance._terminal(
            read(record["events"]), raw_report, record.get("expected_session") or record.get("thread_id")
        )
        return pins, util.digest(report)

    @protection._technical_hold
    def verify_assignment(self, body):
        bound = body["binding"]
        old = {"goal_contract": bound["contract"], "settings": bound["settings"], "current_task": bound["task"]}
        selected = self.verify_decision_review(body["review_selector"])
        _require(selected["metadata"]["binding"] == bound, "assignment borrowed a foreign or stale original review")
        normalized = assignments.normalized(old, body["accepted_decision"])
        _require(normalized == body["decision"], "retained handoff changed normalized owner scope")
        pins, report_hash = self._decision_evidence(bound, body["record"], body["accepted_decision"])
        pins.update(self.history._retained_pins(body["review_selector"], selected["metadata"]))
        _require(
            pins == body["pins"] and report_hash == body["decision_sha256"],
            "original accepted decision/review pins changed",
        )
        if body["decision"]["status"] == "CONTINUE":
            _require(
                body["review_selector"]["kind"] == "reviews"
                and selected["metadata"]["receipt"]["scope_verdict"] == "PASS"
                and selected["metadata"]["receipt"]["scope_all_accepted"] is True,
                "final validate assignment requires current complete-case passing evidence",
            )
        else:
            _require(
                body["record"]["stage"] == "astra_resolve"
                and body["decision"]["next_task"]["kind"] == "implement"
                and bool(body["decision"]["evidence"]),
                "bounded repair requires the actual independent Resolver REWORK",
            )
        before = {**old, "task_archive": body["task_archive"][:-1], "findings_ledger": body["findings_before"]}
        after = {
            **old,
            "current_task": body["new_task"],
            "task_archive": body["task_archive"],
            "affected_paths": body["decision"]["affected_paths"],
        }
        assignments.capture(before, after, body["decision"], bound["source"])
        if body["decision"]["status"] == "REWORK":
            self._resolver_request_evidence(bound, body["resolution_request"], body["resolver_source_record"])
        else:
            _require(
                body["resolution_request"] is None and body["resolver_source_record"] is None,
                "final validation cannot carry a correction grant",
            )
        self.protection._pins(pins)
        return selected

    @protection._technical_hold
    def assign_task(self, operation, state: RunState, decision, snapshot, record):
        selected = self.current_decision_review(state, snapshot=snapshot)
        normalized = assignments.normalized(state, decision)
        bound = self.protection._binding(state, snapshot)
        proof = self.decision_review_proof(state, snapshot=snapshot, selector=selected)
        proof = replace(proof, authorized_assignment=checkpoint.assignment_identity(normalized))
        before = deepcopy(state)
        resolver_source = (
            self.require_resolver_request(state, snapshot=snapshot) if normalized["status"] == "REWORK" else None
        )
        pins, report_hash = self._decision_evidence(bound, record, decision)
        original = self.verify_decision_review(selected)
        pins.update(self.history._retained_pins(selected, original["metadata"]))
        # Ordinary lifecycle owns all prerequisite gates, UUID, source anchor,
        # previous task archival, findings and exact normalized task fields.
        kind = operation(state, normalized, snapshot, visual_proof=proof)
        task = assignments.capture(before, state, normalized, snapshot)
        body = {
            "binding": bound,
            "accepted_decision": deepcopy(decision),
            "decision": normalized,
            "record": deepcopy(record),
            "review_selector": selected,
            "new_task": task,
            "task_archive": deepcopy(state["task_archive"]),
            "findings_before": deepcopy(before.get("findings_ledger", [])),
            "resolution_request": deepcopy(before.get("resolution_request")) if resolver_source is not None else None,
            "resolver_source_record": resolver_source,
            "decision_sha256": report_hash,
            "pins": pins,
        }
        self.protection.prepare_assignment_publication(body)
        return kind

    @protection._technical_hold
    def scope_authority(self, *, state: RunState, snapshot):
        # NativeReviewHistory freshly authenticates immutable original image,
        # config, functional, native delivery and actual protected launch bytes.
        # The callback shape is not a trust input: this is the concrete method.
        return self.history.scope_authority(state=state, snapshot=snapshot)

    @protection._technical_hold
    def checkpoint_proof(self, state: RunState, *, snapshot, review_selector=None, prepared_checkpoint=None):
        facts = self.history.fresh_facts(state=state, snapshot=snapshot)
        derived = self.scope_authority(state=state, snapshot=snapshot)
        accepted, receipt_hash = False, None
        if review_selector is not None:
            original = self.history.verify_review(review_selector)
            old = original["metadata"]
            _require(
                old["binding"] == facts["current_binding"], "own current review differs from exact full current binding"
            )
            receipt = old["receipt"]
            _require(
                receipt["binding"]["visual_scope"] == derived,
                "own current review scope differs from freshly authenticated derivation",
            )
            accepted = receipt["scope_verdict"] == "PASS" and receipt["scope_all_accepted"] is True
            receipt_hash = util.digest(receipt) if accepted else None
        authorized_acceptance = None
        if prepared_checkpoint is not None:
            _require(
                type(prepared_checkpoint) is history.PreparedCheckpoint
                and prepared_checkpoint.owner is self.history
                and self.history._prepared.get(prepared_checkpoint.nonce) is prepared_checkpoint,
                "missing exact original native Completion preparation",
            )
            _require(
                prepared_checkpoint.authorized_acceptance == checkpoint.binding(state, snapshot)
                and prepared_checkpoint.binding_json == history._canonical(facts["current_binding"])
                and accepted,
                "prepared native commit lost current review/source/settings/runtime",
            )
            # The native history constructor verified the original Completion
            # report, actual stage/source/session/delivery and all earlier
            # current eligible owners before creating this exact handle.
            authorized_acceptance = prepared_checkpoint.authorized_acceptance
        return checkpoint.VerifiedCheckpoint(
            checkpoint.binding(state, snapshot),
            snapshot["revision"],
            facts["current_binding"]["runtime_sha256"],
            _scope_json(derived),
            tuple(facts["historical_milestone_ids"]),
            tuple(facts["eligible_milestone_ids"]),
            accepted,
            receipt_hash,
            authorized_acceptance=authorized_acceptance,
        )

    @protection._technical_hold
    def prepare_checkpoint(self, state: RunState, *, review_selector, completion_record, accepted_decision, snapshot):
        facts = self.history.fresh_facts(state=state, snapshot=snapshot)
        admission = scope.admit(
            cast(dict, state["goal_contract"])["body"], state["settings"]["design_manifest"]["body"]
        )
        owner = cast(dict, state["current_task"])["milestone_id"]
        prior = admission["milestone_ids"][: admission["milestone_ids"].index(owner)]
        selectors = {mid: facts["checkpoint_selectors"][mid] for mid in prior}
        return self.history.prepare_checkpoint(
            state,
            review_selector=review_selector,
            completion_record=completion_record,
            accepted_decision=accepted_decision,
            prerequisite_selectors=selectors,
            snapshot=snapshot,
        )

    @protection._technical_hold
    def prepare_checkpoint_publication(self, prepared, accepted_candidate, *, snapshot):
        """AFTER pure guarded accept; BEFORE task archive or parent persistence.

        Existing history.commit_checkpoint writes a protected copied sidecar;
        protection refuses replay/scheduler eligibility until original parent
        persist succeeds and commit_checkpoint_publication runs. The public
        name makes the still-tentative durability semantics explicit.
        """
        selector = self.history.commit_checkpoint(prepared, accepted_candidate, snapshot=snapshot)
        handle = self.protection.prepared_publication(selector)
        self._checkpoint_publications[handle.nonce] = handle
        return handle

    @protection._technical_hold
    def commit_checkpoint_publication(self, handle, *, parent_commit, committed_candidate, snapshot):
        _require(
            self._checkpoint_publications.get(handle.nonce) is handle, "foreign/restarted checkpoint parent commit"
        )
        ident = self.protection.commit_parent_publication(
            handle, parent_commit=parent_commit, committed_candidate=committed_candidate, snapshot=snapshot
        )
        del self._checkpoint_publications[handle.nonce]
        return ident

    @protection._technical_hold
    def review_publications(self):
        """Only process-local publications born at actual native retain_review."""
        return tuple(
            handle
            for handle in self.protection._publications.values()
            if protection._json(handle.body)["selector"]["kind"] == "reviews"
        )

    @protection._technical_hold
    def evidence_publications(self):
        """Exact local review/checkpoint handles; no disk/restart reconstruction."""
        handles = (*self.protection._publications.values(), *self.protection._assignments.values())
        for handle in handles:
            if type(handle) is assignments.PreparedAssignment:
                _require(
                    handle.owner is self.protection and self.protection._assignments.get(handle.nonce) is handle,
                    "foreign native assignment handle",
                )
                continue
            _require(
                type(handle) is protection.PreparedParentPublication
                and handle.owner is self.protection
                and self.protection._publications.get(handle.nonce) is handle,
                "foreign native evidence publication",
            )
            kind = protection._json(handle.body)["selector"]["kind"]
            _require(
                kind in ("reviews", "rejections")
                or (kind == "checkpoints" and self._checkpoint_publications.get(handle.nonce) is handle),
                "unsupported or foreign native evidence publication",
            )
        return handles

    @protection._technical_hold
    def commit_evidence_publication(self, handle, *, parent_commit, committed_candidate, snapshot):
        _require(
            any(original is handle for original in self.evidence_publications()),
            "missing exact local native evidence publication",
        )
        if type(handle) is assignments.PreparedAssignment:
            return self.protection.commit_assignment_publication(
                handle, parent_commit=parent_commit, committed_candidate=committed_candidate, snapshot=snapshot
            )
        if protection._json(handle.body)["selector"]["kind"] in ("reviews", "rejections"):
            return self.commit_review_publication(
                handle, parent_commit=parent_commit, committed_candidate=committed_candidate, snapshot=snapshot
            )
        return self.commit_checkpoint_publication(
            handle, parent_commit=parent_commit, committed_candidate=committed_candidate, snapshot=snapshot
        )

    @protection._technical_hold
    def commit_review_publication(self, handle, *, parent_commit, committed_candidate, snapshot):
        _require(
            protection._json(handle.body)["selector"]["kind"] in ("reviews", "rejections"),
            "wrong native review publication kind",
        )
        return self.protection.commit_parent_publication(
            handle, parent_commit=parent_commit, committed_candidate=committed_candidate, snapshot=snapshot
        )

    @protection._technical_hold
    def prepare_parent_commit(self, candidate, *, snapshot, publications=(), approval=None, planning_record=None):
        return self.protection.prepare_parent_commit(
            candidate, snapshot=snapshot, publications=publications, approval=approval, planning_record=planning_record
        )

    @protection._technical_hold
    def capture_parent_commit(self, intent, *, committed_candidate, serialized_bytes, sink_identity, snapshot):
        return self.protection.capture_parent_commit(
            intent,
            committed_candidate=committed_candidate,
            serialized_bytes=serialized_bytes,
            sink_identity=sink_identity,
            snapshot=snapshot,
        )

    @protection._technical_hold
    def invalidate_parent_ack(self):
        return self.protection.invalidate_parent_ack()

    @protection._technical_hold
    def commit_planning_publication(self, parent_commit, committed_candidate, *, snapshot):
        return self.protection.commit_planning_publication(parent_commit, committed_candidate, snapshot=snapshot)

    @protection._technical_hold
    def prepare_initial_approval(self, draft_state, selected, *, snapshot):
        return self.protection.prepare_initial_approval(draft_state, selected, snapshot=snapshot)

    @protection._technical_hold
    def initial_assignment_proof(self, handle, approved_candidate, *, snapshot):
        return self.protection.initial_assignment_proof(handle, approved_candidate, snapshot=snapshot)

    @protection._technical_hold
    def commit_initial_approval(self, handle, committed_candidate, *, snapshot, parent_commit):
        return self.protection.commit_initial_approval(
            handle, committed_candidate, snapshot=snapshot, parent_commit=parent_commit
        )

    @protection._technical_hold
    def prepare_launch(
        self, state: RunState, snapshot, record, *, boundary, command, environment, native_role_isolation
    ):
        if not boundary["allow_write"]:
            return self.protection.prepare_launch(
                state,
                snapshot,
                record,
                boundary=boundary,
                command=command,
                environment=environment,
                native_role_isolation=native_role_isolation,
            )
        _require(self._parent_sink is not None, "writable native launch needs its original caller parent sink")
        authority = self.first_writer_owner(state, snapshot=snapshot)
        value = writer_execution.NativeWriterExecution(
            authority,
            state,
            snapshot=snapshot,
            record=record,
            boundary=boundary,
            command=command,
            environment=environment,
            isolation=native_role_isolation,
            sink=self._parent_sink,
            sink_type=self._parent_sink_type,
        )
        _require(self.protection._preparing_writer is None, "overlapping original writer preparation")
        self.protection._preparing_writer = value
        try:
            _require(native_role_isolation._writer_execution is None, "isolation already belongs to another writer")
            native_role_isolation._writer_execution = value
            value.prepare()
            handle = self.protection.prepare_launch(
                state,
                snapshot,
                record,
                boundary=boundary,
                command=command,
                environment=environment,
                native_role_isolation=native_role_isolation,
                writer_execution=value,
            )
            value.attach(handle)
            return handle
        except Exception:
            value.failed = True
            raise
        finally:
            self.protection._preparing_writer = None

    @protection._technical_hold
    def require_task_authority(self, state: RunState, *, snapshot):
        return self.protection.require_task_authority(state, snapshot=snapshot)

    @protection._technical_hold
    def first_writer_proof(self, state: RunState, *, snapshot):
        """Original first-task scope only; no dispatch or image acceptance."""
        binding = self.protection._binding(state, snapshot)
        assigned = self.protection._initial_task_authority(binding, self.protection._idle()[0])
        facts = (
            self.protection.first_writer_authority(state, snapshot=snapshot)
            if assigned is None
            else self.protection.rework_writer_authority(state, snapshot=snapshot)
        )
        bound = facts["binding"]
        derived = scope.derive(
            bound["contract"]["body"],
            bound["manifest_body"],
            bound["task"],
            accepted_milestones=(),
            accepted_case_input_paths={},
            changed_paths=[],
        )
        return checkpoint.VerifiedCheckpoint(
            checkpoint.binding(state, snapshot),
            snapshot["revision"],
            bound["runtime_sha256"],
            _scope_json(derived),
            (),
            (),
            False,
            None,
            authorized_launch=checkpoint.launch_identity(bound["task"]),
        )

    @protection._technical_hold
    def first_writer_owner(self, state: RunState, *, snapshot):
        """Concrete authenticator; unavailable execution evidence stays held."""
        return writer.NativeWriterAuthority(self.protection, state, snapshot=snapshot)

    @protection._technical_hold
    def require_planning_origin(self, state: RunState, *, snapshot):
        return self.protection.require_planning_origin(state, snapshot=snapshot)

    @protection._technical_hold
    def release_launch(self, handle, barrier, child, tree, release_token):
        return self.protection.release_launch(handle, barrier, child, tree, release_token)

    @protection._technical_hold
    def finish_launch(self, handle, record, after):
        return self.protection.finish_launch(handle, record, after)

    @protection._technical_hold
    def require_final_integration(self, state: RunState, *, snapshot, review_selector):
        original = self.history.verify_review(review_selector)
        derived = self.scope_authority(state=state, snapshot=snapshot)
        receipt = original["metadata"]["receipt"]
        # verify_review freshly replays actual native/source/delivery authority.
        # This pure recheck validates the REAL immutable v2 receipt schema; it
        # grants no authority from booleans or a saved derived summary.
        protection.acceptance._check_scoped_receipt(receipt)
        cases = state["settings"]["design_manifest"]["body"]["cases"]
        case_ids = [case["id"] for case in cases]
        _require(
            original["metadata"]["binding"]
            == self.history.fresh_facts(state=state, snapshot=snapshot)["current_binding"]
            and receipt["binding"]["visual_scope"] == derived
            and receipt["binding"]["cases"] == cases
            and receipt["binding"]["case_criteria"] == derived["case_criteria"]
            and [row["id"] for row in receipt["cases"]] == case_ids
            and derived["mode"] == "integration"
            and derived["required_case_ids"] == case_ids
            and derived["out_of_scope_case_ids"] == []
            and receipt["scope_verdict"] == receipt["verdict"] == "PASS"
            and receipt["scope_all_accepted"] is True
            and all(row["verdict"] == "PASS" for row in receipt["cases"]),
            "final completion requires one fresh matching all-case native integration receipt",
        )
        # This is an additional native gate. Caller still executes the original
        # functional/full canonical/completion/human acceptance rules unchanged.
        return True
