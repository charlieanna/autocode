"""Controlled public admission-ledger tests; no process/native image authority.

Snapshot, containment and original-native authentication seams are explicit
controlled fakes. These deterministic tests cannot replace TaskRun/browser/live
negative controls or a native image finding/repair/current-review qualification.
"""
from copy import deepcopy
from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import autocode_util as util
import autocode_visual_scope as scope
import autocode_visual_writer_admission as admission


class ControlledTree:
    def __init__(self, pid):
        self.pid = pid

    def capture_root(self):
        return {"pid": self.pid, "group": self.pid, "birth_time": 1000.0,
                "birth_identity": 1000.0, "started": "controlled-test-birth"}


class WriterAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        self.run = self.project / ".autocode" / "runs" / "owned"
        self.run.mkdir(parents=True)
        self.anchor = self.run / "controlled-original-proof.txt"
        self.anchor.write_text("Controlled proof seam; not native evidence.\n")
        self.pin = util.file_hash(self.anchor)
        self.mapping = {"home": ["C1"], "detail": ["C2"], "final": ["C3"]}
        self.manifest = {"version": 1, "cases": [
            {"id": "home", "implementation_paths": ["ui/home.py", "ui/shared.css"]},
            {"id": "detail", "implementation_paths": ["ui/detail.py", "ui/shared.css"]},
            {"id": "final", "implementation_paths": ["ui/final.py"]}]}
        self.body = {"constraints": [scope.MARKER + scope.POLICY, scope.CASE_MARKER + json.dumps(self.mapping)],
            "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}, {"id": "C3"}],
            "milestones": [{"id": "M1", "acceptance_criteria": ["C1"], "depends_on": [],
                            "affected_paths": ["ui/home.py", "ui/shared.css"]},
                           {"id": "M2", "acceptance_criteria": ["C2"], "depends_on": ["M1"],
                            "affected_paths": ["ui/detail.py", "ui/shared.css"]},
                           {"id": "M3", "acceptance_criteria": ["C3"], "depends_on": ["M2"],
                            "affected_paths": ["ui/final.py"]}], "initial_task": {"milestone_id": "M1"}}
        self.contract = {"task_id": "product", "revision": 1, "body": self.body}
        self.contract["hash"] = util.digest(self.contract)
        self.current = self.snapshot({"ui/home.py": "a" * 64, "ui/detail.py": "b" * 64,
                                      "ui/shared.css": "c" * 64, "ui/final.py": "d" * 64})
        self.original_source = deepcopy(self.current)
        self.settings = {"roles": {"terra": {"engine": "opencode", "model": "glm-approved", "reasoning_effort": "high", "model_pinned": True}},
                         "limits": {"max_seconds": 0, "stage_timeout_seconds": 0},
                         "builder_retry": {"ordinary_retries": 3}}
        self.command = ["opencode", "run", "--model", "glm-approved", "--variant", "high"]
        control = self.project / ".autocode" / ("tool-containment-" + "a" * 32)
        self.boundary = {"version": 1, "workspace": str(self.project), "allow_write": True,
                         "tools": ["bash"], "scratch": str(control / "scratch"), "shell": str(control / "shell")}
        self.env = {"PATH": "/controlled/no-provider", "SHELL": self.boundary["shell"],
                    "AUTOCODE_TOOL_CONTAINMENT": json.dumps(self.boundary), "CONTROLLED_SECRET": "not-retained"}
        self.queries, self.false_kind, self.delta_writes = [], None, None
        self.delta_write_facts = {}  # Controlled original facts, never native authority.
        self.retry_ordinal, self.retry_kind = 1, "ordinary_retry"
        self.allowance_mutation, self.dispatch_mutation = None, None
        self.retry_exhausted = False
        self.rework_stage, self.rework_status = "astra_review", "REWORK"
        self.patches = [patch.object(admission.util, "snapshot", side_effect=lambda _: deepcopy(self.current)),
                        patch.object(admission.containment, "verify", side_effect=lambda value: deepcopy(value)),
                        patch.object(admission.processes, "ProcessTree", ControlledTree)]
        for mock in self.patches:
            mock.start()
            self.addCleanup(mock.stop)
        self.ledger = admission.WriterAdmissions(self.project, self.run, authenticate=self.authenticate)

    def snapshot(self, files):
        row = {"head": "e" * 40, "files": dict(files)}
        return {**row, "revision": util.digest(row),
                "source_paths": admission.source_scope.declared_paths(self.body)}

    def binding(self, owner="M2", identity="task-m2"):
        row = next(row for row in self.body["milestones"] if row["id"] == owner)
        return {"policy": scope.POLICY, "contract": deepcopy(self.contract), "manifest_body": deepcopy(self.manifest),
                "task": {"id": identity, "kind": "implement", "milestone_id": owner,
                         "criterion_ids": list(row["acceptance_criteria"]), "affected_paths": list(row["affected_paths"]),
                         "contract_hash": self.contract["hash"], "contract_revision": 1,
                         "source_revision": self.current["revision"]},
                "settings": deepcopy(self.settings), "runtime_sha256": "f" * 64,
                "assignment_source": deepcopy(self.current), "source": deepcopy(self.current)}

    def authenticate(self, kind, query, selector):
        self.queries.append((kind, deepcopy(query)))
        if kind == self.false_kind:
            return True  # Explicitly fails: a truth-valued hook is not evidence.
        identity = {"controlled": "not-native-authority"}
        if kind == "prerequisite":
            if query["mode"] == "current" and query["milestone_id"] == "M2":
                raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Controlled M2 current image proof absent")
            if query["mode"] == "current" and query["binding"]["source"] != self.original_source:
                raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Controlled M1 current image proof stale")
            identity = {"milestone_id": query["milestone_id"], "case_ids": query["case_ids"],
                        "visual_receipt_id": "controlled-receipt", "delivery_attempt_id": "controlled-delivery",
                        "native_session_id": "controlled-session", "native_finish_id": "controlled-finish",
                        "report_sha256": self.pin, "stage_record_sha256": "1" * 64}
        elif kind == "dispatch":
            identity = {"authorization_sha256": "2" * 64}
            if isinstance(selector, dict) and "retry_identity" in selector:
                original = selector["retry_identity"]
                identity.update(retry_allowance_sha256=original["allowance_sha256"],
                    attempt_ordinal=original["attempt_ordinal"], stopped_admission_id=original["stopped_admission_id"])
            if self.dispatch_mutation:
                self.dispatch_mutation(identity)
        elif kind == "retry-allowance":
            if self.retry_exhausted and query["mode"] == "current":
                raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Controlled original retry allowance exhausted")
            bound = query["next_binding"]
            stopped, delta = query["stopped_admission"], query["stopped_delta"]
            identity = {"allowance_kind": selector["kind"],
                "allowance_sha256": util.digest({"stopped": stopped["id"], "ordinal": selector["ordinal"], "kind": selector["kind"]}),
                "original_outcome_sha256": "4" * 64, "failure_evidence_sha256": "5" * 64,
                "attempt_ordinal": selector["ordinal"], "permitted_attempts": 1,
                "stopped_admission_id": stopped["id"], "stopped_delta_id": delta["id"],
                "task_sha256": util.digest(bound["task"]),
                "assignment_source_revision": bound["assignment_source"]["revision"],
                "operation_source_revision": bound["source"]["revision"],
                "settings_sha256": util.digest(bound["settings"]), "runtime_sha256": bound["runtime_sha256"]}
            if self.allowance_mutation:
                self.allowance_mutation(identity)
        elif kind == "bounded-rework":
            identity = {"stage": self.rework_stage, "status": self.rework_status, "decision_sha256": "3" * 64,
                        "next_task_sha256": util.digest(query["next_binding"]["task"])}
        elif kind == "builder-delta":
            # A later stopped attempt must not rewrite an earlier attempt's
            # retained native-fact fake. Bind the first observed writes to the
            # full original query and selector, including admission identity.
            key = util.digest({"query": query, "selector": selector})
            if key not in self.delta_write_facts:
                self.delta_write_facts[key] = {
                    "admission_id": query["admission"]["id"],
                    "write_paths": list(self.delta_writes if self.delta_writes is not None else query["changed_paths"])}
            identity = {"write_paths": list(self.delta_write_facts[key]["write_paths"])}
        return {"kind": kind, "query_sha256": util.digest(query), "identity": identity,
                "evidence_hashes": {str(self.anchor): self.pin}, "protected_refs": [str(self.anchor)]}

    def prepare(self, binding=None, selectors=None):
        binding = binding or self.binding()
        if selectors is None:
            selectors = [{"milestone_id": "M1", "controlled_selector": "not-native"}]
        return self.ledger.prepare_new(binding, selectors, boundary=self.boundary, command=self.command, environment=self.env)

    def launch(self, prepared, binding):
        body = json.loads(prepared.payload)
        dispatch = {"controlled_charged_allowance": "not-a-grant"}
        if body["lineage"] is not None and body["lineage"]["kind"] == "retry":
            dispatch["retry_identity"] = body["lineage"]["allowance"]["identity"]
        self.ledger.recheck(prepared, binding, boundary=self.boundary, command=self.command,
                            environment=self.env, dispatch_selector=dispatch)
        child = SimpleNamespace(pid=44, args=list(self.command))
        return self.ledger.seal_launch(prepared, child, ControlledTree(44), command=self.command,
                                      environment=self.env, launched_at="2026-10-06T16:00:00+00:00")

    def original_delta(self):
        binding = self.binding()
        original = self.launch(self.prepare(binding), binding)
        self.current = self.snapshot({**self.current["files"], "ui/shared.css": "9" * 64})
        delta = self.ledger.record_delta(original["id"], binding["source"], self.current, {"controlled_stopped_builder": "not-native"})
        return original, delta

    def test_preparation_has_no_actual_launch_authority_and_secret_is_not_retained(self):
        prepared = self.prepare()
        with self.assertRaisesRegex(util.Paused, "missing actual admission"):
            self.ledger.authenticate_history(prepared.id)
        texts = "".join(path.read_text() for path in self.ledger.root.glob("*.json"))
        self.assertNotIn("not-retained", texts)
        with self.assertRaisesRegex(util.Paused, "not an actual launch"):
            self.ledger.seal_launch(prepared, SimpleNamespace(pid=44, args=self.command), ControlledTree(44),
                command=self.command, environment=self.env, launched_at="2026-10-06T16:00:00+00:00")

    def test_exact_actual_launch_can_be_authenticated_only_as_history(self):
        binding = self.binding()
        row = self.launch(self.prepare(binding), binding)
        self.assertEqual(row, self.ledger.authenticate_history(row["id"]))
        self.assertNotIn("current_visual_pass", row)
        self.assertEqual(1000.0, row["body"]["launch"]["root"]["birth_identity"])

    def test_an_unclosed_actual_writer_refuses_another_prelaunch_dispatch(self):
        binding = self.binding()
        first, second = self.prepare(binding), self.prepare(binding)
        self.launch(first, binding)
        with self.assertRaisesRegex(util.Paused, "previous admitted writer"):
            self.ledger.recheck(second, binding, boundary=self.boundary, command=self.command,
                               environment=self.env, dispatch_selector={})
        with self.assertRaisesRegex(util.Paused, "previous admitted writer"):
            self.prepare(self.binding("M3", "task-m3"), selectors=[{"milestone_id": "M1"}, {"milestone_id": "M2"}])

    def test_foreign_handle_restart_and_replay_cannot_reuse_prelaunch_permission(self):
        binding = self.binding()
        prepared = self.prepare(binding)
        forged = admission.Prepared(prepared.id, prepared.payload)
        second = admission.WriterAdmissions(self.project, self.run, authenticate=self.authenticate)
        for ledger, handle in ((self.ledger, forged), (second, prepared)):
            with self.subTest(ledger=ledger, handle=handle), self.assertRaisesRegex(util.Paused, "foreign/replayed"):
                ledger.recheck(handle, binding, boundary=self.boundary, command=self.command,
                               environment=self.env, dispatch_selector={})
        self.launch(prepared, binding)
        with self.assertRaisesRegex(util.Paused, "foreign/replayed"):
            self.ledger.recheck(prepared, binding, boundary=self.boundary, command=self.command,
                                environment=self.env, dispatch_selector={})

    def test_final_environment_mutation_and_pid_birth_mismatch_refuse_sealing(self):
        binding = self.binding()
        prepared = self.prepare(binding)
        with self.assertRaisesRegex(util.Paused, "Popen argv/environment changed"):
            self.ledger.recheck(prepared, binding, boundary=self.boundary, command=self.command,
                environment={**self.env, "AUTOCODE_OUTPUT_ATTEMPT": "late-mutation"}, dispatch_selector={})
        self.ledger.recheck(prepared, binding, boundary=self.boundary, command=self.command,
                            environment=self.env, dispatch_selector={})
        with self.assertRaisesRegex(util.Paused, "actual supervisor tree"):
            self.ledger.seal_launch(prepared, SimpleNamespace(pid=44, args=self.command), ControlledTree(99),
                command=self.command, environment=self.env, launched_at="2026-10-06T16:00:00+00:00")
        tree = ControlledTree(44)
        tree.capture_root = lambda: None
        with self.assertRaisesRegex(util.Paused, "birth/session identity"):
            self.ledger.seal_launch(prepared, SimpleNamespace(pid=44, args=self.command), tree,
                command=self.command, environment=self.env, launched_at="2026-10-06T16:00:00+00:00")

    def test_missing_native_prerequisite_and_truth_valued_authenticator_fail_closed(self):
        with self.assertRaisesRegex(util.Paused, "prerequisite native proof"):
            self.prepare(selectors=[])
        self.false_kind = "prerequisite"
        with self.assertRaisesRegex(util.Paused, "forged/foreign prerequisite"):
            self.prepare()

    def test_source_drift_after_preparation_and_actual_argv_substitution_refuse(self):
        binding = self.binding()
        prepared = self.prepare(binding)
        self.current = self.snapshot({**self.current["files"], "ui/shared.css": "8" * 64})
        with self.assertRaisesRegex(util.Paused, "source changed"):
            self.ledger.recheck(prepared, binding, boundary=self.boundary, command=self.command,
                                environment=self.env, dispatch_selector={})
        self.current = deepcopy(binding["source"])
        self.ledger.recheck(prepared, binding, boundary=self.boundary, command=self.command,
                            environment=self.env, dispatch_selector={})
        with self.assertRaisesRegex(util.Paused, "actual Popen arguments differ"):
            self.ledger.seal_launch(prepared, SimpleNamespace(pid=44, args=["another-provider"]), ControlledTree(44),
                command=self.command, environment=self.env, launched_at="2026-10-06T16:00:00+00:00")

    def test_shared_write_stales_current_m1_but_allows_only_authenticated_same_owner_repair(self):
        original, delta = self.original_delta()
        repaired = self.binding(identity="task-m2-repair")
        prepared = self.ledger.prepare_rework(repaired, original["id"], [delta["id"]], {"controlled_accepted_rework": "not-native"},
            boundary=self.boundary, command=self.command, environment=self.env)
        result = self.launch(prepared, repaired)
        self.assertEqual(original["id"], result["body"]["lineage"]["root_admission"])
        self.current = self.snapshot({**self.current["files"], "ui/shared.css": "6" * 64})
        self.ledger.record_delta(result["id"], repaired["source"], self.current, {"controlled_stopped_repair": "not-native"})
        with self.assertRaisesRegex(util.Paused, "M1 current image proof stale"):
            # A legitimate repair launch is not current checkpoint/image
            # acceptance and cannot release the next owner.
            self.prepare(self.binding("M3", "task-m3"), selectors=[{"milestone_id": "M1"}, {"milestone_id": "M2"}])

    def test_repair_history_reauthenticates_original_delta_not_only_root_and_decision(self):
        original, delta = self.original_delta()
        repaired = self.binding(identity="task-m2-repair")
        prepared = self.ledger.prepare_rework(repaired, original["id"], [delta["id"]], {},
            boundary=self.boundary, command=self.command, environment=self.env)
        launched = self.launch(prepared, repaired)
        self.false_kind = "builder-delta"
        with self.assertRaisesRegex(util.Paused, "forged/foreign builder-delta"):
            self.ledger.authenticate_history(launched["id"])

    def test_rework_refuses_cross_owner_contract_runtime_paths_routes_retry_and_caps(self):
        original, delta = self.original_delta()
        changes = [lambda row: row["task"].__setitem__("milestone_id", "M3"),
                   lambda row: row["task"]["criterion_ids"].append("C1"),
                   lambda row: row["task"]["affected_paths"].append("outside.py"),
                   lambda row: row.__setitem__("runtime_sha256", "0" * 64),
                   lambda row: row["settings"]["roles"]["terra"].__setitem__("reasoning_effort", "max"),
                   lambda row: row["settings"]["builder_retry"].__setitem__("ordinary_retries", 4),
                   lambda row: row["settings"]["limits"].__setitem__("max_seconds", 7200),
                   lambda row: row["contract"].__setitem__("revision", 2)]
        for mutate in changes:
            row = self.binding(identity="task-m2-repair")
            mutate(row)
            with self.subTest(mutate=mutate), self.assertRaises((util.Paused, ValueError)):
                self.ledger.prepare_rework(row, original["id"], [delta["id"]], {},
                    boundary=self.boundary, command=self.command, environment=self.env)

    def test_missing_delta_missing_decision_and_restored_outside_write_refuse(self):
        original, delta = self.original_delta()
        with self.assertRaisesRegex(util.Paused, "missing/truncated/reordered"):
            self.ledger.prepare_rework(self.binding(identity="task-m2-repair"), original["id"], [], {},
                boundary=self.boundary, command=self.command, environment=self.env)
        self.false_kind = "bounded-rework"
        with self.assertRaisesRegex(util.Paused, "forged/foreign bounded-rework"):
            self.ledger.prepare_rework(self.binding(identity="task-m2-repair"), original["id"], [delta["id"]], {},
                boundary=self.boundary, command=self.command, environment=self.env)
        self.false_kind = None
        repaired = self.binding(identity="task-m2-repair")
        prepared = self.ledger.prepare_rework(repaired, original["id"], [delta["id"]], {},
            boundary=self.boundary, command=self.command, environment=self.env)
        repair_launch = self.launch(prepared, repaired)
        after = self.snapshot({**self.current["files"], "ui/shared.css": "7" * 64})
        self.current = after
        self.delta_writes = ["ui/shared.css", "outside-restored.txt"]
        self.assertNotIn("outside-restored.txt", after["files"])
        before = sorted(path.name for path in self.ledger.root.glob("*.json"))
        self.queries.clear()
        with self.assertRaisesRegex(util.Paused, "unaccounted/out-of-scope"):
            self.ledger.record_delta(repair_launch["id"], repaired["source"], after, {})
        # Historical provenance passed before the current restored write was
        # refused; neither guard is replaced by a broad exception match.
        delta_queries = [query for kind, query in self.queries if kind == "builder-delta"]
        self.assertEqual([original["id"], repair_launch["id"]],
                         [query["admission"]["id"] for query in delta_queries])
        original_facts = next(row for row in self.delta_write_facts.values()
                              if row["admission_id"] == original["id"])
        self.assertEqual(["ui/shared.css"], original_facts["write_paths"])
        self.assertEqual(before, sorted(path.name for path in self.ledger.root.glob("*.json")))

    def test_changed_original_delta_write_provenance_refuses_repair_history(self):
        original, delta = self.original_delta()
        repaired = self.binding(identity="task-m2-repair")
        prepared = self.ledger.prepare_rework(repaired, original["id"], [delta["id"]], {},
            boundary=self.boundary, command=self.command, environment=self.env)
        repair_launch = self.launch(prepared, repaired)
        original_facts = next(row for row in self.delta_write_facts.values()
                              if row["admission_id"] == original["id"])
        # Deliberately corrupt only the controlled authenticator's ORIGINAL
        # retained facts. Both paths are owned; the provenance equality guard,
        # rather than a new-delta/outside-path guard, must reject this change.
        original_facts["write_paths"] = ["ui/shared.css", "ui/home.py"]
        before = sorted(path.name for path in self.ledger.root.glob("*.json"))
        self.queries.clear()
        with self.assertRaisesRegex(util.Paused, "retained delta provenance or observed writes changed"):
            self.ledger.authenticate_history(repair_launch["id"])
        self.assertEqual([original["id"]], [query["admission"]["id"] for kind, query in self.queries
                                          if kind == "builder-delta"])
        self.assertEqual(before, sorted(path.name for path in self.ledger.root.glob("*.json")))

    def test_ledger_tamper_changed_original_report_and_symlink_authority_refuse(self):
        binding = self.binding()
        row = self.launch(self.prepare(binding), binding)
        self.anchor.write_text("tampered original protected proof\n")
        with self.assertRaisesRegex(util.Paused, "evidence changed"):
            self.ledger.authenticate_history(row["id"])
        self.anchor.write_text("Controlled proof seam; not native evidence.\n")
        path = next(self.ledger.root.glob("*-" + row["id"] + ".json"))
        raw = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(raw.replace(b'"admission"', b'"prepared"', 1))
        with self.assertRaisesRegex(util.Paused, "forged ledger chain"):
            self.ledger.authenticate_history(row["id"])
        path.write_bytes(raw)
        other = self.project / "another-run"
        other.mkdir()
        bad_run = self.run.parent / "symlink"
        bad_run.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(util.Paused, "owned run"):
            admission.WriterAdmissions(self.project, bad_run, authenticate=self.authenticate)


    def retry_binding(self, launched):
        bound = deepcopy(launched["body"]["binding"])
        bound["source"] = deepcopy(self.current)
        return bound

    def retry_prepare(self, bound, root, delta_ids, *, kind="ordinary_retry", ordinal=1):
        return self.ledger.prepare_retry(bound, root["id"], delta_ids,
            {"kind": kind, "ordinal": ordinal, "controlled_original_allowance": "not-native"},
            boundary=self.boundary, command=self.command, environment=self.env)

    def test_same_task_retry_keeps_assigned_source_after_retained_shared_write(self):
        original, delta = self.original_delta()
        retry = self.retry_binding(original)
        self.assertNotEqual(retry["assignment_source"], retry["source"])
        self.assertEqual(original["body"]["binding"]["task"], retry["task"])
        prepared = self.retry_prepare(retry, original, [delta["id"]])
        launched = self.launch(prepared, retry)
        self.assertEqual("retry", launched["body"]["lineage"]["kind"])
        self.assertEqual(original["id"], launched["body"]["lineage"]["previous_admission_id"])
        self.assertEqual(launched, self.ledger.authenticate_history(launched["id"]))
        modes = {query["mode"] for kind, query in self.queries if kind == "retry-allowance"}
        self.assertEqual({"current", "historical"}, modes)
        self.assertFalse(any(kind == "bounded-rework" for kind, _ in self.queries))

    def test_retry_preserves_ordinary_explicit_and_provider_startup_allowances(self):
        original, delta = self.original_delta()
        latest, deltas = original, [delta["id"]]
        for kind in ("ordinary_retry", "explicit_builder_retry", "provider_startup_retry"):
            with self.subTest(kind=kind):
                retry = self.retry_binding(latest)
                prepared = self.retry_prepare(retry, original, deltas, kind=kind)
                latest = self.launch(prepared, retry)
                self.assertEqual(kind, latest["body"]["lineage"]["allowance"]["identity"]["allowance_kind"])
                self.assertEqual(original["body"]["binding"]["task"], latest["body"]["binding"]["task"])
                self.assertEqual(latest, self.ledger.authenticate_history(latest["id"]))
                stopped = self.ledger.record_delta(latest["id"], self.current, self.current, {})
                deltas.append(stopped["id"])

    def test_retry_can_follow_zero_change_stopped_attempt_without_new_assignment(self):
        bound = self.binding()
        original = self.launch(self.prepare(bound), bound)
        delta = self.ledger.record_delta(original["id"], self.current, self.current, {})
        retry = self.retry_binding(original)
        launched = self.launch(self.retry_prepare(retry, original, [delta["id"]]), retry)
        self.assertEqual(bound["task"], launched["body"]["binding"]["task"])
        self.assertEqual([], delta["body"]["write_paths"])

    def test_retry_refuses_missing_native_allowance_or_boolean_hook(self):
        original, delta = self.original_delta()
        self.false_kind = "retry-allowance"
        with self.assertRaisesRegex(util.Paused, "forged/foreign retry-allowance"):
            self.retry_prepare(self.retry_binding(original), original, [delta["id"]])

    def test_exhausted_existing_allowance_is_not_replaced_or_refunded(self):
        original, delta = self.original_delta()
        self.retry_exhausted = True
        before = sorted(path.name for path in self.ledger.root.glob("*.json"))
        with self.assertRaisesRegex(util.Paused, "original retry allowance exhausted"):
            self.retry_prepare(self.retry_binding(original), original, [delta["id"]])
        self.assertEqual(before, sorted(path.name for path in self.ledger.root.glob("*.json")))

    def test_provider_startup_allowance_refuses_source_change_or_restored_write(self):
        original, delta = self.original_delta()
        with self.assertRaisesRegex(util.Paused, "unchanged attempt with no observed writes"):
            self.retry_prepare(self.retry_binding(original), original, [delta["id"]], kind="provider_startup_retry")
        retry = self.retry_binding(original)
        latest = self.launch(self.retry_prepare(retry, original, [delta["id"]]), retry)
        self.delta_writes = ["ui/shared.css"]
        stopped = self.ledger.record_delta(latest["id"], self.current, self.current, {})
        with self.assertRaisesRegex(util.Paused, "unchanged attempt with no observed writes"):
            self.retry_prepare(self.retry_binding(latest), original, [delta["id"], stopped["id"]], kind="provider_startup_retry")

    def test_retry_refuses_changed_task_anchor_scope_routes_runtime_and_caps(self):
        original, delta = self.original_delta()
        changes = [lambda row: row["task"].__setitem__("id", "new-task"),
                   lambda row: row["task"].__setitem__("milestone_id", "M3"),
                   lambda row: row["task"]["criterion_ids"].append("C1"),
                   lambda row: row["task"]["affected_paths"].append("outside.py"),
                   lambda row: row.__setitem__("assignment_source", deepcopy(row["source"])),
                   lambda row: row.__setitem__("runtime_sha256", "0" * 64),
                   lambda row: row["settings"]["roles"]["terra"].__setitem__("model", "unapproved"),
                   lambda row: row["settings"]["limits"].__setitem__("max_seconds", 7200),
                   lambda row: row["settings"]["builder_retry"].__setitem__("ordinary_retries", 4)]
        for mutate in changes:
            bound = self.retry_binding(original)
            mutate(bound)
            with self.subTest(mutate=mutate), self.assertRaises((util.Paused, ValueError)):
                self.retry_prepare(bound, original, [delta["id"]])

    def test_retry_requires_exact_single_existing_stopped_outcome_identity(self):
        original, delta = self.original_delta()
        for key, value in (("permitted_attempts", 2), ("attempt_ordinal", True),
                           ("allowance_kind", "new_grant"), ("stopped_admission_id", "9" * 64),
                           ("stopped_delta_id", "8" * 64), ("task_sha256", "7" * 64),
                           ("original_outcome_sha256", None), ("failure_evidence_sha256", None)):
            self.allowance_mutation = lambda identity, key=key, value=value: identity.update({key: value})
            with self.subTest(key=key), self.assertRaisesRegex(util.Paused, "exact original stopped outcome"):
                self.retry_prepare(self.retry_binding(original), original, [delta["id"]])

    def test_retry_recheck_and_history_reauthenticate_allowance_and_full_prefix(self):
        original, delta = self.original_delta()
        retry = self.retry_binding(original)
        with self.assertRaisesRegex(util.Paused, "missing/truncated/reordered"):
            self.retry_prepare(retry, original, [])
        prepared = self.retry_prepare(retry, original, [delta["id"]])
        self.false_kind = "retry-allowance"
        with self.assertRaisesRegex(util.Paused, "forged/foreign retry-allowance"):
            self.launch(prepared, retry)
        self.false_kind = None
        launched = self.launch(prepared, retry)
        self.false_kind = "builder-delta"
        with self.assertRaisesRegex(util.Paused, "forged/foreign builder-delta"):
            self.ledger.authenticate_history(launched["id"])
        self.false_kind = "retry-allowance"
        with self.assertRaisesRegex(util.Paused, "forged/foreign retry-allowance"):
            self.ledger.authenticate_history(launched["id"])

    def test_retry_dispatch_must_bind_exact_allowance_and_cannot_replay_it(self):
        original, delta = self.original_delta()
        retry = self.retry_binding(original)
        prepared = self.retry_prepare(retry, original, [delta["id"]])
        self.dispatch_mutation = lambda identity: identity.update(retry_allowance_sha256="0" * 64)
        with self.assertRaisesRegex(util.Paused, "consume this exact existing"):
            self.launch(prepared, retry)
        self.dispatch_mutation = None
        launched = self.launch(prepared, retry)
        stopped = self.ledger.record_delta(launched["id"], self.current, self.current, {})
        previous_identity = launched["body"]["lineage"]["allowance"]["identity"]
        self.allowance_mutation = lambda identity: identity.update(allowance_sha256=previous_identity["allowance_sha256"])
        with self.assertRaisesRegex(util.Paused, "already admitted"):
            self.retry_prepare(self.retry_binding(launched), original, [delta["id"], stopped["id"]])

    def test_retry_chain_uses_latest_task_and_never_releases_stale_prior_images(self):
        original, delta = self.original_delta()
        rework = self.binding(identity="task-m2-repair")
        prepared = self.ledger.prepare_rework(rework, original["id"], [delta["id"]], {},
            boundary=self.boundary, command=self.command, environment=self.env)
        repair = self.launch(prepared, rework)
        self.current = self.snapshot({**self.current["files"], "ui/shared.css": "6" * 64})
        repaired_delta = self.ledger.record_delta(repair["id"], rework["source"], self.current, {})
        retry = self.retry_binding(repair)
        retried = self.launch(self.retry_prepare(retry, original, [delta["id"], repaired_delta["id"]]), retry)
        self.assertEqual(repair["body"]["binding"]["task"], retried["body"]["binding"]["task"])
        final_delta = self.ledger.record_delta(retried["id"], self.current, self.current, {})
        with self.assertRaisesRegex(util.Paused, "missing/truncated/reordered"):
            self.retry_prepare(self.retry_binding(retried), original, [repaired_delta["id"], final_delta["id"]])
        with self.assertRaisesRegex(util.Paused, "M1 current image proof stale"):
            self.prepare(self.binding("M3", "task-m3"), selectors=[{"milestone_id": "M1"}, {"milestone_id": "M2"}])

    def test_modern_resolver_rework_is_allowed_but_legacy_continue_is_not_admitted(self):
        original, delta = self.original_delta()
        self.rework_stage = "astra_resolve"
        self.rework_status = "REWORK"
        rework = self.binding(identity="task-resolver-repair")
        self.ledger.prepare_rework(rework, original["id"], [delta["id"]], {},
            boundary=self.boundary, command=self.command, environment=self.env)
        self.rework_status = "CONTINUE"
        with self.assertRaisesRegex(util.Paused, "accepted Completion/Resolver bounded repair"):
            self.ledger.prepare_rework(rework, original["id"], [delta["id"]], {},
                boundary=self.boundary, command=self.command, environment=self.env)

    def install_native_observer(self):
        """Controlled callback-count and original-file seam, never native proof.

        The finite chain models three ordinary, three explicit, three startup
        allowances and three accepted REWORKs; no live allowance is granted.
        A deterministic callback ceiling makes an exponential regression fail
        early, without a wall-clock timeout, sleep or spawned test process.
        """
        original = self.ledger.authenticate
        self.observed_calls = Counter()
        self.observed_refs = {}
        self.observed_refusals = set()
        self.observed_after = None
        self.observed_total = 0

        def observed(kind, query, selector):
            identity = (query["admission"]["id"] if kind in ("historical-launch", "builder-delta")
                        else util.digest(query))
            key = (kind, identity)
            self.observed_calls[key] += 1
            self.observed_total += 1
            if self.observed_total > 128:
                raise AssertionError("Finite causal operation repeated native authentication over its bound")
            if key in self.observed_refusals:
                return True  # A refused original proof cannot become cached authority.
            proof = original(kind, query, selector)
            if kind in ("historical-launch", "builder-delta"):
                if key not in self.observed_refs:
                    path = self.run / ("controlled-native-" + util.digest(key) + ".txt")
                    path.write_text("Controlled immutable original " + kind + " " + identity + "\n")
                    self.observed_refs[key] = (path, util.file_hash(path))
                path, pin = self.observed_refs[key]
                proof["evidence_hashes"][str(path)] = pin
                proof["protected_refs"].append(str(path))
            if self.observed_after:
                self.observed_after(kind, query)
            return proof

        self.ledger.authenticate = observed

    def observed_operation(self, function, *args, **kwargs):
        self.observed_calls.clear()
        self.observed_total = 0
        self.queries.clear()
        return function(*args, **kwargs)

    def observed_mixed_chain(self):
        self.install_native_observer()
        original, delta = self.observed_operation(self.original_delta)
        latest, deltas, launches = original, [delta["id"]], [original]
        kinds = ("ordinary_retry", "provider_startup_retry", "rework", "explicit_builder_retry") * 3
        for ordinal, kind in enumerate(kinds, 1):
            if kind == "rework":
                bound = self.binding(identity="task-observed-rework-" + str(ordinal))
                prepared = self.observed_operation(self.ledger.prepare_rework, bound, original["id"], deltas, {},
                    boundary=self.boundary, command=self.command, environment=self.env)
            else:
                bound = self.retry_binding(latest)
                prepared = self.observed_operation(self.retry_prepare, bound, original, deltas,
                                                  kind=kind, ordinal=ordinal)
            latest = self.observed_operation(self.launch, prepared, bound)
            launches.append(latest)
            # A startup retry is permitted only after a genuinely zero-write
            # stopped attempt. These controlled later deltas are all unchanged.
            stopped = self.observed_operation(self.ledger.record_delta, latest["id"], self.current, self.current, {})
            deltas.append(stopped["id"])
        return original, latest, deltas, launches

    def test_finite_mixed_history_reads_ledger_once_and_authenticates_each_native_identity_once(self):
        original, latest, deltas, launches = self.observed_mixed_chain()
        with patch.object(self.ledger, "_rows", wraps=self.ledger._rows) as reads:
            actual = self.observed_operation(self.ledger.authenticate_history, latest["id"])
        self.assertEqual(latest, actual)
        self.assertEqual(1, reads.call_count)
        history = {identity: count for (kind, identity), count in self.observed_calls.items()
                   if kind == "historical-launch"}
        delta = {identity: count for (kind, identity), count in self.observed_calls.items()
                 if kind == "builder-delta"}
        self.assertEqual({row["id"]: 1 for row in launches}, history)
        self.assertEqual({row["id"]: 1 for row in launches[:-1]}, delta)
        # 13 admissions + 12 preceding deltas + 9 existing allowances +
        # 3 accepted REWORK decisions + 1 original historical prerequisite.
        self.assertEqual(38, self.observed_total)
        self.assertEqual(3 * len(launches) - 1, self.observed_total)
        self.assertTrue(all(query["mode"] == "historical" for kind, query in self.queries
                            if kind in ("retry-allowance", "prerequisite")))
        self.assertEqual(original["id"], actual["body"]["lineage"]["root_admission"])
        self.assertNotIn("current_visual_pass", actual)

    def test_next_operation_rechecks_early_and_latest_original_evidence_and_native_refusal(self):
        original, latest, _, _ = self.observed_mixed_chain()
        self.assertEqual(latest, self.observed_operation(self.ledger.authenticate_history, latest["id"]))
        for row in (original, latest):
            key = ("historical-launch", row["id"])
            path, _ = self.observed_refs[key]
            raw = path.read_bytes()
            with self.subTest(admission=row["id"]):
                path.write_text("Changed original retained evidence BETWEEN operations\n")
                try:
                    with self.assertRaisesRegex(util.Paused, "retained authentication evidence changed"):
                        self.observed_operation(self.ledger.authenticate_history, latest["id"])
                    self.assertEqual(1, self.observed_calls[key])
                finally:
                    path.write_bytes(raw)
                self.observed_refusals.add(key)
                with self.assertRaisesRegex(util.Paused, "forged/foreign historical-launch"):
                    self.observed_operation(self.ledger.authenticate_history, latest["id"])
                self.assertEqual(1, self.observed_calls[key])
                self.observed_refusals.remove(key)
                self.assertEqual(latest, self.observed_operation(self.ledger.authenticate_history, latest["id"]))

    def test_operation_end_rechecks_a_completed_early_evidence_pin(self):
        original, latest, _, _ = self.observed_mixed_chain()
        path, _ = self.observed_refs[("historical-launch", original["id"])]
        raw = path.read_bytes()

        def after(kind, query):
            if kind == "retry-allowance" and query["next_binding"] == latest["body"]["binding"]:
                self.observed_after = None
                path.write_text("Changed after the completed early admission was memoized\n")

        self.observed_after = after
        try:
            with self.assertRaisesRegex(util.Paused, "evidence changed during operation"):
                self.observed_operation(self.ledger.authenticate_history, latest["id"])
        finally:
            path.write_bytes(raw)
            self.observed_after = None
        self.assertEqual(latest, self.observed_operation(self.ledger.authenticate_history, latest["id"]))

    def test_failed_operation_end_recheck_never_publishes_a_ready_launch(self):
        original, latest, deltas, _ = self.observed_mixed_chain()
        bound = self.retry_binding(latest)
        prepared = self.observed_operation(self.retry_prepare, bound, original, deltas, ordinal=13)
        body = json.loads(prepared.payload)
        path, _ = self.observed_refs[("historical-launch", original["id"])]
        raw = path.read_bytes()

        def after(kind, query):
            if kind == "dispatch":
                self.observed_after = None
                path.write_text("Original evidence changed after dispatch authentication\n")

        self.observed_after = after
        try:
            with self.assertRaisesRegex(util.Paused, "evidence changed during operation"):
                self.observed_operation(self.ledger.recheck, prepared, bound, boundary=self.boundary,
                    command=self.command, environment=self.env,
                    dispatch_selector={"retry_identity": body["lineage"]["allowance"]["identity"]})
            with self.assertRaisesRegex(util.Paused, "not an actual launch"):
                self.ledger.seal_launch(prepared, SimpleNamespace(pid=44, args=self.command), ControlledTree(44),
                    command=self.command, environment=self.env, launched_at="2026-10-06T16:00:00+00:00")
        finally:
            path.write_bytes(raw)
            self.observed_after = None

    def test_mixed_chain_rejects_missing_reordered_and_truncated_prefix_before_preparation(self):
        original, latest, deltas, _ = self.observed_mixed_chain()
        before = sorted(path.name for path in self.ledger.root.glob("*.json"))
        for bad in ([], deltas[1:], list(reversed(deltas)), deltas[:-1]):
            with self.subTest(prefix=bad), self.assertRaisesRegex(util.Paused, "missing/truncated/reordered"):
                self.observed_operation(self.retry_prepare, self.retry_binding(latest), original, bad, ordinal=13)
            self.assertEqual(before, sorted(path.name for path in self.ledger.root.glob("*.json")))

    def controlled_semantic_rows(self):
        """Copy the owned fixture only; production ledger hashing stays intact.

        Graph-defense tests inject a dishonest validated-reader seam. A true
        SHA256 cycle would require a cryptographic fixed point, so pretending
        these rows are a valid native ledger would misstate the test evidence.
        The public history operation must reject them even after that seam.
        """
        return [json.loads(path.read_bytes()) for path in sorted(self.ledger.root.glob("*.json"))]

    def test_active_cycle_and_forward_edges_refuse_before_recursive_native_authentication(self):
        original, latest, deltas, launches = self.observed_mixed_chain()
        for cycle in (True, False):
            rows = self.controlled_semantic_rows()
            target_delta = deltas[-2] if cycle else deltas[0]
            target_launch = latest if cycle else launches[1]
            row = next(row for row in rows if row["id"] == target_delta)
            row["body"]["admission_id"] = target_launch["id"]
            reason = "cyclic historical admission" if cycle else "forward admission dependency"
            with self.subTest(cycle=cycle), patch.object(self.ledger, "_rows", return_value=rows) as reads:
                with self.assertRaisesRegex(util.Paused, reason):
                    self.observed_operation(self.ledger.authenticate_history, latest["id"])
                self.assertEqual(1, reads.call_count)
            # A cycle hits the already active latest admission; a forward edge
            # refuses before ever asking native authentication about its target.
            key = ("historical-launch", target_launch["id"])
            self.assertEqual(1 if cycle else 0, self.observed_calls[key])

    def test_historical_reconstruction_rejects_reordered_and_truncated_full_prefix(self):
        _, latest, _, _ = self.observed_mixed_chain()
        prefix = latest["body"]["lineage"]["delta_ids"]
        for bad in (list(reversed(prefix)), prefix[1:], prefix[:-1]):
            rows = self.controlled_semantic_rows()
            for row in rows:
                if row["id"] in (latest["id"], latest["body"]["prepared_id"]):
                    row["body"]["lineage"]["delta_ids"] = list(bad)
            with self.subTest(prefix=bad), patch.object(self.ledger, "_rows", return_value=rows):
                with self.assertRaisesRegex(util.Paused, "missing/truncated/reordered"):
                    self.observed_operation(self.ledger.authenticate_history, latest["id"])


if __name__ == "__main__":
    unittest.main()
