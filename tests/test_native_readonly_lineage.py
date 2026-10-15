"""Real ordinary task/archive/typed sink, explicitly controlled review evidence.

The original journal, operator approval, assign_task UUID/archive and durable
ParentSink are real. Model verdicts/decision provenance are controlled hooks,
not native model/image authority. No provider, browser or held target runs.
"""

import json
import unittest
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_contract_identity as contracts
import autocode_goal_lifecycle as lifecycle
import autocode_native_assignment as assignments
import autocode_native_parent as parent
import autocode_source_scope as source_scope
import autocode_util as util
import autocode_visual_checkpoint as checkpoint
import autocode_visual_protection as protection
import autocode_visual_scope as scope

import tests.test_native_assignment as pure_fixtures
import tests.test_native_writer as fixtures


class OriginalReadonlyLineageControls(unittest.TestCase):
    prepare = fixtures.FirstWriterAuthorityTests.prepare
    acknowledge = fixtures.FirstWriterAuthorityTests.acknowledge
    commit = fixtures.FirstWriterAuthorityTests.commit

    def setUp(self):
        fixtures.FirstWriterAuthorityTests.setUp(self)
        goal = self.state["goal_contract"]
        goal["body"]["initial_task"]["kind"] = "validate"
        goal["body"]["constraints"].append('VISUAL_WRITER_PATHS=["ignored.txt","future.txt"]')
        goal["hash"] = util.digest({key: goal[key] for key in ("task_id", "revision", "body")})
        self.token = contracts.token(goal)
        self.state["displayed_goal"] = self.state["planning"]["final_token"] = self.token
        self.owner._append(
            "planning-committed",
            {
                "contract": deepcopy(goal),
                "settings": deepcopy(self.state["settings"]),
                "source": deepcopy(self.raw),
                "token": self.token,
                "pins": {str(self.anchor): util.file_hash(self.anchor)},
            },
        )
        self.state, self.current = self.commit()
        self.original = deepcopy(self.state["current_task"])
        self.sink = parent.ParentSink(
            self.run,
            factory=self.factory,
            ordinary_write=util.atomic_json,
            snapshot=lambda value: source_scope.snapshot(self.root, value),
        )
        self.factory.bind_parent_sink(self.sink, parent.ParentSink)
        self.reviews = {}
        self.pins = {str(self.anchor): util.file_hash(self.anchor)}

    def decision(self):
        spec = self.state["goal_contract"]["body"]["initial_task"]
        return {
            "status": "CONTINUE",
            "next_objective": "Fresh full-scope independent validation",
            "next_task": {
                key: deepcopy(value) for key, value in spec.items() if key not in ("objective", "affected_paths")
            },
            "affected_paths": [],
            "evidence": [],
            "user_request": {"kind": "none"},
        }

    @contextmanager
    def controlled_reviews(self):
        # The actual decision bytes are retained, but native launch/model
        # provenance and Sol verdict are explicit controlled boundary facts.
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(self.factory, "current_decision_review", side_effect=lambda *args, **kwargs: self.selected)
            )
            stack.enter_context(
                patch.object(self.factory, "decision_review_proof", side_effect=lambda *args, **kwargs: self.proof)
            )
            stack.enter_context(
                patch.object(
                    self.factory,
                    "verify_decision_review",
                    side_effect=lambda selected: deepcopy(self.reviews[selected["sha256"]]),
                )
            )
            stack.enter_context(
                patch.object(
                    self.factory,
                    "_decision_evidence",
                    side_effect=lambda bound, record, decision: (deepcopy(self.pins), util.digest(decision)),
                )
            )
            stack.enter_context(patch.object(self.factory.history, "_retained_pins", return_value=self.pins))
            yield

    def handoff(self, *, publish=True):
        decision = self.decision()
        ordinal = len(self.reviews)
        record = {
            "stage": "astra_review",
            "role": "astra_review",
            "output": str(self.run / f"controlled-decision-{ordinal}.json"),
        }
        util.atomic_json(record["output"], decision)
        self.state.setdefault("stages", []).append(record)
        bound = self.owner._binding(self.state, self.current)
        self.selected = {
            "kind": "reviews",
            "path": str(self.run / f"controlled-review-{ordinal}.json"),
            "sha256": util.digest({"ordinal": ordinal}),
        }
        self.reviews[self.selected["sha256"]] = {
            "metadata": {"binding": bound, "receipt": {"scope_verdict": "PASS", "scope_all_accepted": True}}
        }
        derived = scope.derive(
            bound["contract"]["body"],
            self.state["settings"]["design_manifest"]["body"],
            bound["task"],
            accepted_milestones=(),
            accepted_case_input_paths={},
        )
        self.proof = checkpoint.VerifiedCheckpoint(
            checkpoint.binding(self.state, self.current),
            self.current["revision"],
            bound["runtime_sha256"],
            json.dumps(derived, sort_keys=True, separators=(",", ":")),
            (),
            (),
            True,
            "d" * 64,
        )
        before = deepcopy(self.state)
        self.factory.assign_task(lifecycle.assign_task, self.state, decision, self.current, record)
        self.assertEqual(self.state["task_archive"], [*before.get("task_archive", []), before["current_task"]])
        self.assertNotEqual(self.state["current_task"]["id"], before["current_task"]["id"])
        self.assertEqual(self.state["goal_contract"], before["goal_contract"])
        self.assertEqual(self.state["settings"], before["settings"])
        handles = self.factory.evidence_publications()
        self.assertEqual(len(handles), 1)
        if publish:
            with self.sink.publication(publications=handles):
                self.sink.write(self.run / "state.json", self.state)
            self.sink.publish(self.state, publications=handles)
        return before, handles

    def test_real_operator_approval_and_one_ordinary_durable_validation_handoff(self):
        with self.controlled_reviews():
            before, handles = self.handoff()
            facts = self.owner.readonly_task_authority(self.state, snapshot=self.current)
            self.assertEqual(facts["binding"]["task"], self.state["current_task"])
            self.assertEqual(
                self.owner.measured_source_delta(state=self.state, snapshot=self.current)["changed_paths"], []
            )
            self.assertFalse(any(row["kind"] == "prepared" and row["body"]["grants"] for row in self.owner._rows()))
        with self.assertRaises(util.Paused):
            self.owner.require_task_authority(self.state, snapshot=self.current)

    def test_repeated_real_uuid_archive_handoffs_keep_original_operator_source(self):
        with self.controlled_reviews():
            self.handoff()
            self.handoff()
            facts = self.owner.readonly_task_authority(self.state, snapshot=self.current)
            lineage = assignments.readonly_lineage(self.owner._rows(), facts["binding"])
            self.assertEqual(lineage["kind"], "operator_supplied_source")
            self.assertEqual(lineage["task"], self.original)
            self.assertEqual(len(self.state["task_archive"]), 2)
            self.assertEqual(len({task["id"] for task in [*self.state["task_archive"], self.state["current_task"]]}), 3)

    def test_operator_review_join_replays_only_its_original_committed_prefix(self):
        with self.controlled_reviews():
            self.handoff()
            self.handoff()
            bound = self.owner._binding(self.state, self.current)
            supplied = {
                "kind": "operator_supplied_source",
                "model": None,
                "session_id": None,
                "task_id": bound["task"]["id"],
                "contract_hash": bound["contract"]["hash"],
                "contract_revision": bound["contract"]["revision"],
                "source_revision": self.current["revision"],
                "source_files_sha256": util.digest(self.current["files"]),
                "approval_event_sha256": util.digest(bound["contract"]["approval_event"]),
            }
            query = {"binding": bound, "original_producer": None, "producer_task": None, "operator_source": supplied}
            rows = self.owner._rows()
            self.owner._join_producer({"kind": "reviews"}, query, rows, {"sequence": len(rows)}, {})
            query["operator_source"]["source_revision"] = "stale"
            with self.assertRaises(util.Paused):
                self.owner._join_producer({"kind": "reviews"}, query, rows, {"sequence": len(rows)}, {})

    def test_failed_review_cannot_authorize_validation_lineage(self):
        with self.controlled_reviews():
            self.handoff()
            self.reviews[self.selected["sha256"]]["metadata"]["receipt"]["scope_verdict"] = "FAIL"
            with self.assertRaises(util.Paused):
                self.owner.readonly_task_authority(self.state, snapshot=self.current)

    def test_original_planning_pins_remain_mandatory_after_handoff(self):
        with self.controlled_reviews():
            self.handoff()
            self.anchor.write_text("tampered original planning evidence\n")
            with self.assertRaises(util.Paused):
                self.owner.readonly_task_authority(self.state, snapshot=self.current)

    def test_external_source_change_still_refuses_causal_operator_validation(self):
        with self.controlled_reviews():
            self.handoff()
            (self.root / "ignored.txt").write_text("unauthorized changed source\n")
            current = source_scope.snapshot(self.root, self.state)
            with self.assertRaises(util.Paused):
                self.owner.readonly_task_authority(self.state, snapshot=current)

    def test_unfinished_assignment_ack_and_foreign_task_do_not_gain_readonly_authority(self):
        with self.controlled_reviews():
            self.handoff(publish=False)
            with self.assertRaises(util.Paused):
                self.owner.readonly_task_authority(self.state, snapshot=self.current)
            foreign = deepcopy(self.state)
            foreign["current_task"]["milestone_id"] = "M2"
            with self.assertRaises(util.Paused):
                self.owner.readonly_task_authority(foreign, snapshot=self.current)


class PureReadonlyProducerControls(unittest.TestCase):
    """Synthetic row chronology, actual ordinary UUID/archive normalization."""

    def fixture(self):
        state, source, decision = pure_fixtures.fixture()
        bound = {
            "contract": state["goal_contract"],
            "settings": state["settings"],
            "task": deepcopy(state["current_task"]),
            "source": source,
            "runtime_sha256": "c" * 64,
        }
        rows = [
            {
                "id": "origin",
                "sequence": 0,
                "kind": "approval-committed",
                "body": {
                    "contract": bound["contract"],
                    "settings": bound["settings"],
                    "current_task": bound["task"],
                    "snapshot": source,
                    "runtime_sha256": bound["runtime_sha256"],
                },
            }
        ]
        for _ in range(2):
            derived = scope.derive(
                state["goal_contract"]["body"],
                state["settings"]["design_manifest"]["body"],
                state["current_task"],
                accepted_milestones=(),
                accepted_case_input_paths={},
            )
            normalized = assignments.normalized(state, decision)
            proof = checkpoint.VerifiedCheckpoint(
                checkpoint.binding(state, source),
                source["revision"],
                "c" * 64,
                json.dumps(derived, sort_keys=True, separators=(",", ":")),
                (),
                (),
                True,
                "d" * 64,
                authorized_assignment=checkpoint.assignment_identity(normalized),
            )
            before = deepcopy(state)
            lifecycle.assign_task(state, normalized, source, visual_proof=proof)
            task = assignments.capture(before, state, normalized, source)
            body = {"binding": deepcopy(bound), "new_task": task, "decision": normalized}

            def append(kind, value):
                row = {"id": str(len(rows)), "sequence": len(rows), "kind": kind, "body": deepcopy(value)}
                rows.append(row)
                return row

            prepared = append("assignment-prepared", body)
            intent = append(
                "parent-commit-prepared",
                {
                    "publications": [{"kind": "assignments", "id": prepared["id"]}],
                    "task": task,
                    "source": source,
                    "contract": bound["contract"],
                    "settings_sha256": util.digest(bound["settings"]),
                    "runtime_sha256": bound["runtime_sha256"],
                },
            )
            ack = append(
                "parent-commit-acknowledged",
                {
                    **intent["body"],
                    "intent_id": intent["id"],
                    "sink_identity": {
                        "path": "/controlled/state.json",
                        "device": 1,
                        "inode": 1,
                        "bytes": 1,
                        "mode": 0o600,
                    },
                },
            )
            append("assignment-committed", {**body, "prepared_id": prepared["id"], "parent_commit_id": ack["id"]})
            bound = {**bound, "task": task}
        return state, source, rows, bound

    def test_repeated_validate_selects_the_true_original_implement_producer(self):
        state, source, rows, bound = self.fixture()
        lineage = assignments.readonly_lineage(rows, bound)
        self.assertEqual(lineage["kind"], "native_builder")
        self.assertEqual(lineage["task"], state["task_archive"][0])
        self.assertNotEqual(lineage["task"]["id"], state["task_archive"][-1]["id"])

    def guard_fixture(self):
        state, source, rows, bound = self.fixture()
        builder_task = state["task_archive"][0]
        builder = {
            "id": "builder",
            "body": {
                "binding": {**deepcopy(bound), "task": builder_task},
                "record_identity": {"stage": "terra"},
                "grants": ["ui/chat.py", "ui/work.py"],
            },
        }
        stopped = {"kind": "finished", "body": {"record": {"exit_code": 0}, "after": source, "pins": {}}}
        owner = object.__new__(protection.NativeProtection)
        owner.run = Path("/controlled")
        owner._binding = Mock(return_value=bound)
        owner._idle = Mock(return_value=(rows, {"builder": builder}, {"builder": stopped}))
        owner._source_continuity = Mock()
        # This pure producer caller controls durable/review authentication;
        # real original sink authenticity is covered by the fixture above.
        owner._initial_task_authority = Mock(return_value=rows[-1])
        owner._pins = Mock()
        owner._journal_pins = Mock(return_value={})
        owner._writer_delta_validator = Mock()
        return state, source, owner, builder, stopped

    def test_original_guard_uses_the_actual_stopped_builder_through_readonly_suffix(self):
        state, source, owner, builder, stopped = self.guard_fixture()
        facts = owner.readonly_task_authority(state, snapshot=source)
        self.assertEqual(facts["binding"]["task"], state["current_task"])
        owner._writer_delta_validator.assert_called_once_with(state, snapshot=source, builder=builder, stopped=stopped)

    def test_changed_readonly_source_or_rework_cannot_masquerade_as_validation(self):
        for defect in ("source", "rework", "criteria", "owner", "scope"):
            state, source, rows, bound = self.fixture()
            if defect == "source":
                bound["source"] = {**source, "revision": "changed"}
            elif defect == "rework":
                rows[-1]["body"]["decision"]["status"] = "REWORK"
                rows[-4]["body"]["decision"]["status"] = "REWORK"
            else:
                key, value = {
                    "criteria": ("acceptance_criteria", []),
                    "owner": ("milestone_id", "M2"),
                    "scope": ("affected_paths", ["foreign.py"]),
                }[defect]
                bound["task"][key] = value
            with self.subTest(defect=defect), self.assertRaises(util.Paused):
                assignments.readonly_lineage(rows, bound)


if __name__ == "__main__":
    unittest.main()
