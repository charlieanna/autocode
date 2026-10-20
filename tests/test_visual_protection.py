"""Controlled native-protection guard fixtures, NOT native qualification.

These proposed unit tests exercise path/chain/sink refusal deterministically.
The prepared sink intent fixture supplies NO image, owner, approval or launch
authority. No test runs another test or invokes a native/provider/controller.
"""

import hashlib
import json
import os  # noqa: F401 - preserve existing import behavior
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_util as util
import autocode_visual_factory as factory
import autocode_visual_protection as protection
from autocode_verified_json_cache import VerifiedJSONCache


class VerifiedJSONCacheTests(unittest.TestCase):
    def test_identical_bytes_validate_once_but_both_returned_values_are_isolated(self):
        validate = Mock(side_effect=json.loads)
        cache = VerifiedJSONCache(validate)
        first = cache.validated(b'{"nested":[{"value":1}]}')
        first["nested"][0]["value"] = 9
        second = cache.validated(b'{"nested":[{"value":1}]}')
        self.assertEqual({"nested": [{"value": 1}]}, second)
        second["nested"].clear()
        self.assertEqual({"nested": [{"value": 1}]}, cache.validated(b'{"nested":[{"value":1}]}'))
        self.assertEqual(1, validate.call_count)

    def test_changed_bytes_are_validated_and_failures_are_never_retained(self):
        validate = Mock(side_effect=json.loads)
        cache = VerifiedJSONCache(validate)
        self.assertEqual({"value": 1}, cache.validated(b'{"value":1}'))
        self.assertEqual({"value": 2}, cache.validated(b'{"value":2}'))
        for _ in range(2):
            with self.assertRaises(ValueError):
                cache.validated(b'{"value":')
        self.assertEqual(4, validate.call_count)

    def test_a_full_byte_budget_preserves_its_prefix_without_retaining_new_values(self):
        validate = Mock(side_effect=json.loads)
        cache = VerifiedJSONCache(validate, max_bytes=2)
        self.assertEqual({}, cache.validated(b"{}"))
        for _ in range(2):
            self.assertEqual([], cache.validated(b"[]"))
        self.assertEqual({}, cache.validated(b"{}"))
        self.assertEqual(3, validate.call_count)

    def test_a_zero_budget_disables_reuse(self):
        validate = Mock(side_effect=json.loads)
        cache = VerifiedJSONCache(validate, max_bytes=0)
        for _ in range(2):
            self.assertEqual({}, cache.validated(b"{}"))
        self.assertEqual(2, validate.call_count)

    def test_instances_cannot_share_another_validators_success(self):
        accepted = VerifiedJSONCache(json.loads)
        rejected = VerifiedJSONCache(Mock(side_effect=ValueError("different policy")))
        self.assertEqual({}, accepted.validated(b"{}"))
        with self.assertRaisesRegex(ValueError, "different policy"):
            rejected.validated(b"{}")

    def test_strict_canonical_row_validation_is_preserved_on_cache_misses(self):
        row = {"version": 1, "sequence": 0, "previous": None, "kind": "origin", "body": {}}
        row["id"] = util.digest(row)
        raw = protection._bytes(row)
        cache = VerifiedJSONCache(protection.NativeProtection._validated_row)
        self.assertEqual(row, cache.validated(raw))
        for invalid in (
            raw + b" ",
            raw.replace(b'"version":1', b'"version":2'),
            raw.replace(b'"body":{}', b'"body":{},"body":{}'),
            raw.replace(b'"body":{}', b'"body":{"value":NaN}'),
        ):
            with self.subTest(raw=invalid), self.assertRaises(util.Paused):
                cache.validated(invalid)


class PossibleWriteBoundTests(unittest.TestCase):
    def test_directory_grant_covers_restored_and_future_descendants(self):
        self.assertEqual(protection.possible_write_paths(["src"], []), ["src"])
        self.assertEqual(protection.possible_write_paths(["src"], ["src/new.py"]), ["src", "src/new.py"])

    def test_measured_changes_cannot_erase_or_duplicate_possible_grants(self):
        self.assertEqual(protection.possible_write_paths(["a.py", "src"], ["a.py", "b.py"]), ["a.py", "src", "b.py"])

    def test_nonliteral_and_private_grants_are_refused(self):
        for name in ("src/*", "../other", "/outside", "src/../other", ".autocode/runs/x", ".git/config"):
            with self.subTest(name=name), self.assertRaises(util.Paused):
                protection.possible_write_paths([name], [])

    def test_malformed_measured_inputs_are_refused(self):
        for measured in (None, [""], ["/outside"], ["src\\other"], ["src/./other"]):
            with self.subTest(measured=measured), self.assertRaises(util.Paused):
                protection.possible_write_paths([], measured)


class OwnedGuardFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name).resolve()
        self.run = self.workspace / ".autocode" / "runs" / "controlled"
        self.run.mkdir(parents=True)
        self.run_handle = (self.run / "writer.lock").open("a+")
        self.checkout_handle = (self.workspace / ".autocode" / "writer.lock").open("a+")
        self.addCleanup(self.run_handle.close)
        self.addCleanup(self.checkout_handle.close)
        self.source = {"head": "fixture-head", "files": {"src/a.py": "fixture-file"}}
        self.source["revision"] = util.digest(self.source)
        self.current = deepcopy(self.source)
        self.runtime = {"fixture.py": "0" * 64}
        self.snapshot_patch = patch.object(util, "snapshot", side_effect=lambda _path: deepcopy(self.current))
        self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)
        self.runtime_patch = patch.object(protection.history, "runtime_files", return_value=self.runtime)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)
        self.start = {"settings": {}, "goal_contract": None, "stages": []}
        self.owner = self.make_owner()

    def make_owner(self, **changes):
        start = {**self.start, **changes}
        return protection.NativeProtection(
            self.workspace,
            self.run,
            run_handle=self.run_handle,
            checkout_handle=self.checkout_handle,
            start_state=start,
            snapshot=self.current,
        )

    def sink_intent(self, candidate, *, planning=None):
        # Controlled transaction fixture only: it can test ACK identity but is
        # not a paid launch, independent image receipt or operator approval.
        raw = (json.dumps(candidate, indent=2, sort_keys=True) + "\n").encode()
        body = {
            "candidate_sha256": hashlib.sha256(raw).hexdigest(),
            "source": deepcopy(self.current),
            "contract": deepcopy(candidate["goal_contract"]),
            "task": deepcopy(candidate.get("current_task")),
            "settings_sha256": util.digest(candidate["settings"]),
            "publications": [],
            "publication_nonces": [],
            "approval_nonce": None,
            "planning": planning,
            "runtime_sha256": util.digest(self.runtime),
        }
        intent = protection.PreparedParentCommit(self.owner, "controlled-intent", "1" * 64, protection._bytes(body))
        self.owner._commits[intent.nonce] = intent
        sink = {"path": str(self.run / "state.json"), "device": 1, "inode": 2, "bytes": len(raw), "mode": 0o600}
        return intent, raw, sink

    def test_warm_validation_does_not_adopt_modified_file_bytes(self):
        self.assertEqual([], self.owner.committed_selectors("reviews"))
        original = next(self.owner.root.iterdir())
        original.chmod(0o600)
        original.write_bytes(original.read_bytes() + b" ")
        with self.assertRaises(util.Paused):
            self.owner.committed_selectors("reviews")

    def test_warm_validation_still_checks_sequence_filenames(self):
        self.assertEqual([], self.owner.committed_selectors("reviews"))
        original = next(self.owner.root.iterdir())
        original.rename(original.with_name("00000003-" + original.name.split("-", 1)[1]))
        with self.assertRaises(util.Paused):
            self.owner.committed_selectors("reviews")

    def test_warm_validation_still_requires_the_original_open_lock(self):
        self.assertEqual([], self.owner.committed_selectors("reviews"))
        self.checkout_handle.close()
        with self.assertRaises(util.Paused):
            self.owner.committed_selectors("reviews")

    def test_same_locked_owner_can_reopen_original_namespace(self):
        restarted = self.make_owner()
        self.assertEqual(restarted.root, self.owner.root)

    def test_closed_original_lock_refuses_owner(self):
        self.checkout_handle.close()
        with self.assertRaises(util.Paused):
            self.make_owner()

    def test_source_drift_after_origin_refuses_reopening(self):
        self.current = {"head": "fixture-head", "files": {"src/a.py": "changed"}}
        self.current["revision"] = util.digest(self.current)
        with self.assertRaises(util.Paused):
            self.make_owner()

    def test_unknown_journal_member_is_not_adopted(self):
        (self.owner.root / "unowned.json").write_text("{}")
        with self.assertRaises(util.Paused):
            self.make_owner()

    def test_sequence_filename_change_is_refused_even_with_same_bytes(self):
        original = next(self.owner.root.iterdir())
        original.rename(original.with_name("00000003-" + original.name.split("-", 1)[1]))
        with self.assertRaises(util.Paused):
            self.make_owner()

    def test_state_file_authority_path_is_explicitly_refused(self):
        with self.assertRaises(util.Paused):
            self.owner._safe(self.run / "state.json")

    def test_foreign_sink_receipt_cannot_be_reconstructed(self):
        candidate = {"goal_contract": {"id": "fixture"}, "settings": {}, "current_task": None}
        intent, raw, sink = self.sink_intent(candidate)
        copy = protection.PreparedParentCommit(intent.owner, intent.nonce, intent.row_id, intent.body)
        with self.assertRaises(util.Paused):
            self.owner.capture_parent_commit(
                copy, committed_candidate=candidate, serialized_bytes=raw, sink_identity=sink, snapshot=self.current
            )

    def test_changed_final_task_cannot_use_prepared_sink_intent(self):
        candidate = {"goal_contract": {"id": "fixture"}, "settings": {}, "current_task": {"id": "original"}}
        intent, raw, sink = self.sink_intent(candidate)
        changed = {**candidate, "current_task": {"id": "queued-intervention"}}
        with self.assertRaises(util.Paused):
            self.owner.capture_parent_commit(
                intent, committed_candidate=changed, serialized_bytes=raw, sink_identity=sink, snapshot=self.current
            )

    def test_exact_sink_ack_is_invalidated_by_a_later_original_write(self):
        candidate = {"goal_contract": {"id": "fixture"}, "settings": {}, "current_task": None}
        intent, raw, sink = self.sink_intent(candidate)
        receipt = self.owner.capture_parent_commit(
            intent, committed_candidate=candidate, serialized_bytes=raw, sink_identity=sink, snapshot=self.current
        )
        self.owner._parent_ack(receipt, candidate, self.current)
        self.owner.invalidate_parent_ack()
        with self.assertRaises(util.Paused):
            self.owner._parent_ack(receipt, candidate, self.current)

    def test_boundary_side_effect_candidate_drift_refuses_publication_ack(self):
        candidate = {"goal_contract": {"id": "fixture"}, "settings": {}, "current_task": None}
        intent, raw, sink = self.sink_intent(candidate)
        receipt = self.owner.capture_parent_commit(
            intent, committed_candidate=candidate, serialized_bytes=raw, sink_identity=sink, snapshot=self.current
        )
        changed = {**candidate, "validation": {"verdict": "FAIL"}}
        with self.assertRaises(util.Paused):
            self.owner._parent_ack(receipt, changed, self.current)

    def test_foreign_sink_path_size_and_malformed_permission_mode_are_refused(self):
        candidate = {"goal_contract": {"id": "fixture"}, "settings": {}, "current_task": None}
        for change in ({"path": "/outside/state.json"}, {"bytes": 0}, {"mode": 0o100600}):
            intent, raw, sink = self.sink_intent(candidate)
            with self.subTest(change=change), self.assertRaises(util.Paused):
                self.owner.capture_parent_commit(
                    intent,
                    committed_candidate=candidate,
                    serialized_bytes=raw,
                    sink_identity={**sink, **change},
                    snapshot=self.current,
                )

    def test_exact_original_sink_intent_is_consumed_by_first_ack(self):
        candidate = {"goal_contract": {"id": "fixture"}, "settings": {}, "current_task": None}
        intent, raw, sink = self.sink_intent(candidate)
        self.owner.capture_parent_commit(
            intent, committed_candidate=candidate, serialized_bytes=raw, sink_identity=sink, snapshot=self.current
        )
        with self.assertRaises(util.Paused):
            self.owner.capture_parent_commit(
                intent, committed_candidate=candidate, serialized_bytes=raw, sink_identity=sink, snapshot=self.current
            )

    def test_planning_publication_cannot_replay_the_same_original_ack(self):
        # Controlled sink replay fixture. _planning_matches is deliberately a
        # fake shape result, NOT actual planning/native/operator authority.
        record = {"stage": "plan_finalize"}
        planning = {"record": protection.history.record_identity(record)}
        candidate = {"goal_contract": {"id": "fixture"}, "settings": {}, "current_task": None, "stages": [record]}
        intent, raw, sink = self.sink_intent(candidate, planning=planning)
        receipt = self.owner.capture_parent_commit(
            intent, committed_candidate=candidate, serialized_bytes=raw, sink_identity=sink, snapshot=self.current
        )
        with patch.object(self.owner, "_planning_matches", return_value=planning):
            self.owner.commit_planning_publication(receipt, candidate, snapshot=self.current)
            with self.assertRaises(util.Paused):
                self.owner.commit_planning_publication(receipt, candidate, snapshot=self.current)

    def test_none_contract_origin_and_preapproval_binding_preserve_original_state(self):
        original = deepcopy(self.start)
        bound = self.owner._launch_binding(self.start, self.current)
        self.assertIsNone(bound["contract"])
        self.assertIsNone(bound["task"])
        self.assertEqual(self.start, original)

    def test_absent_contract_reopens_and_binds_preapproval_without_authority(self):
        state = {key: value for key, value in self.start.items() if key != "goal_contract"}
        original = deepcopy(state)
        reopened = protection.NativeProtection(
            self.workspace,
            self.run,
            run_handle=self.run_handle,
            checkout_handle=self.checkout_handle,
            start_state=state,
            snapshot=self.current,
        )
        self.assertIsNone(reopened._launch_binding(state, self.current)["contract"])
        self.assertEqual(state, original)

    def test_malformed_present_contract_is_refused_at_constructor_and_launch_binding(self):
        for value in ([], "malformed", False, 1):
            state = {**self.start, "goal_contract": value}
            with self.subTest(value=value), self.assertRaises(util.Paused):
                self.make_owner(goal_contract=value)
            with self.subTest(bound=value), self.assertRaises(util.Paused):
                self.owner._launch_binding(state, self.current)

    def test_planning_publication_missing_original_record_is_typed_hold(self):
        record = {"stage": "plan_finalize", "role": "plan_reviewer"}
        planning = {"record": protection.history.record_identity(record)}
        candidate = {"goal_contract": None, "settings": {}, "current_task": None, "stages": []}
        intent, raw, sink = self.sink_intent(candidate, planning=planning)
        ack = self.owner.capture_parent_commit(
            intent, committed_candidate=candidate, serialized_bytes=raw, sink_identity=sink, snapshot=self.current
        )
        with patch.object(self.owner, "_planning_matches") as native_replay, self.assertRaises(util.Paused):
            self.owner.commit_planning_publication(ack, candidate, snapshot=self.current)
        native_replay.assert_not_called()

    def test_planning_publication_duplicate_original_record_is_typed_hold(self):
        record = {"stage": "plan_finalize", "role": "plan_reviewer"}
        planning = {"record": protection.history.record_identity(record)}
        candidate = {"goal_contract": None, "settings": {}, "current_task": None, "stages": [record, deepcopy(record)]}
        intent, raw, sink = self.sink_intent(candidate, planning=planning)
        ack = self.owner.capture_parent_commit(
            intent, committed_candidate=candidate, serialized_bytes=raw, sink_identity=sink, snapshot=self.current
        )
        with patch.object(self.owner, "_planning_matches") as native_replay, self.assertRaises(util.Paused):
            self.owner.commit_planning_publication(ack, candidate, snapshot=self.current)
        native_replay.assert_not_called()

    def test_planning_matches_admits_repaired_plan_finalize_record(self):
        repair_record = {
            "stage": "plan_finalize_report_repair",
            "original_stage": "plan_finalize",
            "report_only": True,
            "exit_code": 0,
        }
        contract = {"task_id": "t1", "revision": 1, "body": {}, "approval_status": "draft"}
        contract["hash"] = util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        candidate = {
            "settings": {"planning_flow": "v2"},
            "goal_contract": contract,
            "current_task": None,
        }
        with patch.object(self.owner, "_finished_for") as finished_for:
            finished_for.side_effect = util.Paused("PAUSED_TEST", "reached finished_for")
            with self.assertRaises(util.Paused) as caught:
                self.owner._planning_matches(repair_record, candidate, self.current)
            self.assertEqual("reached finished_for", str(caught.exception))

    def test_planning_matches_rejects_repaired_non_finalize_record(self):
        repair_record = {
            "stage": "plan_review_report_repair",
            "original_stage": "plan_review",
            "report_only": True,
            "exit_code": 0,
        }
        contract = {"task_id": "t1", "revision": 1, "body": {}, "approval_status": "draft"}
        contract["hash"] = util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        candidate = {
            "settings": {"planning_flow": "v2"},
            "goal_contract": contract,
            "current_task": None,
        }
        with self.assertRaises(util.Paused) as caught:
            self.owner._planning_matches(repair_record, candidate, self.current)
        self.assertIn(
            "initial planning authority needs the original successful v2 final native stage", str(caught.exception)
        )

    def test_planning_matches_repaired_finalize_requires_failed_launch_in_journal(self):
        repair_record = {
            "stage": "plan_finalize_report_repair",
            "original_stage": "plan_finalize",
            "report_only": True,
            "exit_code": 0,
        }
        contract = {"task_id": "t1", "revision": 1, "body": {}, "approval_status": "draft"}
        contract["hash"] = util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        candidate = {
            "settings": {"planning_flow": "v2"},
            "goal_contract": contract,
            "current_task": None,
        }
        original = {"body": {"grants": [], "binding": {"source": self.current, "settings": candidate["settings"]}}}
        stopped = {"id": "stop1", "body": {"after": self.current, "pins": {}}}
        with patch.object(self.owner, "_finished_for", return_value=([], original, stopped)):
            with self.assertRaises(util.Paused) as caught:
                self.owner._planning_matches(repair_record, candidate, self.current)
            self.assertIn(
                "repaired final native planning stage has no original failed launch in journal", str(caught.exception)
            )


class DecoratorAndActualSinkTests(unittest.TestCase):
    def test_technical_hold_constructs_once_and_preserves_wrapped_function(self):
        @protection._technical_hold
        def malformed(value):
            return value["required"]

        self.assertEqual(malformed.__name__, "malformed")
        self.assertEqual(malformed({"required": "present"}), "present")
        with self.assertRaises(util.Paused):
            malformed({})

    def test_actual_atomic_json_sink_uses_permission_bits_and_detached_value(self):
        # Actual reviewed utility boundary on an ordinary throwaway JSON path.
        # No controller, native provider or private run state file is accessed.
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "ordinary.json"
            receipts = []
            value = {"tuple_becomes_list": ("a", "b"), "nested": {"ok": True}}
            util.atomic_json(target, value, after_commit=lambda **receipt: receipts.append(receipt))
            self.assertEqual(len(receipts), 1)
            receipt = receipts[0]
            self.assertEqual(receipt["value"], {"tuple_becomes_list": ["a", "b"], "nested": {"ok": True}})
            self.assertEqual(json.loads(receipt["serialized_bytes"]), receipt["value"])
            self.assertEqual(receipt["sink_identity"]["mode"], target.stat().st_mode & 0o7777)
            self.assertLessEqual(receipt["sink_identity"]["mode"], 0o7777)
            self.assertIsNone(
                protection.check_parent_sink_identity(
                    receipt["sink_identity"], expected_path=target, byte_count=len(receipt["serialized_bytes"])
                )
            )
            with self.assertRaises(util.Paused):
                protection.check_parent_sink_identity(
                    {**receipt["sink_identity"], "mode": 0o100600},
                    expected_path=target,
                    byte_count=len(receipt["serialized_bytes"]),
                )


class FinishedRecordJoinTests(unittest.TestCase):
    def record(self):
        return {
            "stage": "terra",
            "role": "terra",
            "route_role": None,
            "engine": "opencode",
            "launch_route": {"engine": "opencode", "provider": "saved", "model": "saved", "reasoning_effort": "high"},
            "source_revision": None,
            "contract_revision": 1,
            "contract_hash": "contract",
            "task_id": "task-original",
            "events": "/owned/events.jsonl",
            "output": "/owned/output.json",
            "reported_output": None,
            "before_ref": "/owned/builder.before.json",
            "after_ref": "/owned/builder.after.json",
            "expected_session": None,
        }

    def test_legitimate_final_only_postwrite_source_and_session_are_not_frozen(self):
        original = self.record()
        final = {
            **deepcopy(original),
            "source_revision": "actual-postwrite",
            "thread_id": "actual-session",
            "finished_at": "actual-finish",
            "exit_code": 0,
            "reported_output": "/owned/output.reported.json",
        }
        self.assertIsNone(protection.check_finished_record(original, final))
        # Actual finish still independently compares postwrite snapshot/slot to
        # the original supervisor/source; this pure join grants no such proof.

    def test_stable_record_fields_cannot_be_swapped_for_an_old_stage(self):
        original = self.record()
        for key in (
            "stage",
            "role",
            "engine",
            "launch_route",
            "contract_revision",
            "contract_hash",
            "task_id",
            "events",
            "output",
            "before_ref",
            "after_ref",
        ):
            with self.subTest(key=key), self.assertRaises(util.Paused):
                protection.check_finished_record(original, {**deepcopy(original), key: "swapped"})

    def test_route_fallback_cannot_be_changed_when_original_route_role_is_null(self):
        original = self.record()
        with self.assertRaises(util.Paused):
            protection.check_finished_record(original, {**original, "route_role": "sol"})

    def test_existing_session_and_source_anchor_cannot_change(self):
        original = {**self.record(), "expected_session": "original-session", "source_revision": "known-before"}
        for change in ({"expected_session": "borrowed"}, {"source_revision": "relabelled"}):
            with self.subTest(change=change), self.assertRaises(util.Paused):
                protection.check_finished_record(original, {**original, **change})


class InitialTaskDeclarationTests(unittest.TestCase):
    def fixture(self):
        goal = {"revision": 1, "hash": "original-contract"}
        decision = {
            "status": "CONTINUE",
            "next_objective": "Original first slice",
            "affected_paths": ["src/a.py"],
            "next_task": {
                "kind": "implement",
                "milestone_id": "M1",
                "requirements": ["Original requirement"],
                "acceptance_criteria": ["AC1"],
                "validation_plan": ["Original validation"],
                "findings": [],
            },
        }
        source = {"revision": "original-source"}
        ledger = [{"id": "F1", "status": "open"}, {"id": "F2", "status": "resolved"}]
        task = {
            **deepcopy(decision["next_task"]),
            "id": "task-0123456789ab",
            "assigned_at": "original-time",
            "objective": decision["next_objective"],
            "affected_paths": ["src/a.py"],
            "findings": ["F1"],
            "contract_revision": 1,
            "contract_hash": "original-contract",
            "source_revision": "original-source",
            "decision": "CONTINUE",
        }
        return goal, decision, task, source, ledger

    def test_original_normalized_first_task_including_automatic_findings_matches(self):
        self.assertIsNone(protection.check_initial_task_declaration(*self.fixture()))

    def test_same_id_changed_kind_criteria_paths_or_objective_is_not_the_approved_task(self):
        goal, decision, task, source, ledger = self.fixture()
        for change in (
            {"kind": "validate"},
            {"acceptance_criteria": ["AC2"]},
            {"affected_paths": ["src/b.py"]},
            {"objective": "Changed scope"},
            {"source_revision": "changed-source"},
            {"extra_scope": True},
        ):
            with self.subTest(change=change), self.assertRaises(util.Paused):
                protection.check_initial_task_declaration(goal, decision, {**task, **change}, source, ledger)

    def test_validate_first_task_has_no_automatic_writer_findings(self):
        goal, decision, task, source, ledger = self.fixture()
        decision["next_task"]["kind"] = task["kind"] = "validate"
        task["findings"] = []
        self.assertIsNone(protection.check_initial_task_declaration(goal, decision, task, source, ledger))
        with self.assertRaises(util.Paused):
            protection.check_initial_task_declaration(goal, decision, {**task, "findings": ["F1"]}, source, ledger)


class FirstTaskAuthorityGuardTests(unittest.TestCase):
    def fixture(self):
        # Structural matching only, not an original operator journal/sink ACK.
        owner = object.__new__(protection.NativeProtection)
        task = {
            "id": "same-id",
            "kind": "implement",
            "milestone_id": "M1",
            "acceptance_criteria": ["AC1"],
            "affected_paths": ["src/a.py"],
        }
        source = {"revision": "controlled-source", "files": {"src/a.py": "pinned"}}
        binding = {
            "contract": {"hash": "original-contract"},
            "settings": {},
            "runtime_sha256": "runtime",
            "task": task,
            "source": source,
        }
        rows = [
            {
                "id": "controlled-approval",
                "sequence": 0,
                "kind": "approval-committed",
                "body": {
                    "contract": binding["contract"],
                    "settings": {},
                    "runtime_sha256": "runtime",
                    "current_task": deepcopy(task),
                    "snapshot": deepcopy(source),
                },
            }
        ]
        return owner, binding, rows

    def test_original_complete_first_task_declaration_matches(self):
        owner, binding, rows = self.fixture()
        self.assertIsNone(owner._initial_task_authority(binding, rows))

    def test_same_id_modified_declaration_or_later_owner_has_no_first_task_authority(self):
        owner, binding, rows = self.fixture()
        for change in (
            {"kind": "validate"},
            {"acceptance_criteria": ["AC2"]},
            {"affected_paths": ["src/b.py"]},
            {"milestone_id": "M2"},
            {"id": "later-task"},
        ):
            with self.subTest(change=change), self.assertRaises(util.Paused):
                owner._initial_task_authority({**binding, "task": {**binding["task"], **change}}, rows)

    def test_preparation_refuses_later_assignment_before_native_config(self):
        owner, binding, rows = self.fixture()
        changed = {**binding, "task": {**binding["task"], "milestone_id": "M2"}}
        with (
            patch.object(owner, "_idle", return_value=(rows, {}, {})),
            patch.object(owner, "_source_continuity"),
            patch.object(owner, "_launch_binding", return_value=changed),
            patch.object(protection.contracts, "approved", return_value=True),
            patch.object(owner, "_native_config") as native_setup,
        ):
            with self.assertRaises(util.Paused):
                owner.prepare_launch(
                    {}, {}, {}, boundary={"allow_write": True}, command=[], environment={}, native_role_isolation=None
                )
            native_setup.assert_not_called()

    def test_matching_first_task_reaches_the_real_preparation_seam(self):
        owner, binding, rows = self.fixture()

        class SetupReached(RuntimeError):
            pass

        with (
            patch.object(owner, "_idle", return_value=(rows, {}, {})),
            patch.object(owner, "_source_continuity"),
            patch.object(owner, "_launch_binding", return_value=binding),
            patch.object(protection.contracts, "approved", return_value=True),
            patch.object(owner, "_native_config", side_effect=SetupReached) as native_setup,
        ):
            with self.assertRaises(SetupReached):
                owner.prepare_launch(
                    {}, {}, {}, boundary={"allow_write": True}, command=[], environment={}, native_role_isolation=None
                )
            native_setup.assert_called_once()


class FactoryTypedContextTests(unittest.TestCase):
    def test_missing_checkpoint_owner_is_a_typed_hold_not_index_error(self):
        owner = object.__new__(factory.NativeVisualFactory)
        owner.history = Mock()
        owner.history.fresh_facts.return_value = {"checkpoint_selectors": {}}
        state = {
            "goal_contract": {"body": {}},
            "settings": {"design_manifest": {"body": {}}},
            "current_task": {"milestone_id": "unknown"},
        }
        with (
            patch.object(factory.scope, "admit", return_value={"milestone_ids": ["M1"]}),
            self.assertRaises(util.Paused),
        ):
            owner.prepare_checkpoint(state, review_selector={}, completion_record={}, accepted_decision={}, snapshot={})
        owner.history.prepare_checkpoint.assert_not_called()

    def test_missing_prerequisite_selector_is_a_typed_hold_before_checkpoint_preparation(self):
        owner = object.__new__(factory.NativeVisualFactory)
        owner.history = Mock()
        owner.history.fresh_facts.return_value = {"checkpoint_selectors": {}}
        state = {
            "goal_contract": {"body": {}},
            "settings": {"design_manifest": {"body": {}}},
            "current_task": {"milestone_id": "M2"},
        }
        with (
            patch.object(factory.scope, "admit", return_value={"milestone_ids": ["M1", "M2"]}),
            self.assertRaises(util.Paused),
        ):
            owner.prepare_checkpoint(state, review_selector={}, completion_record={}, accepted_decision={}, snapshot={})
        owner.history.prepare_checkpoint.assert_not_called()


class ProducerJoinGuardTests(unittest.TestCase):
    def builder_fixture(self):
        # Deliberately controlled structural lookup, NOT a native launch/image
        # receipt. Production _finished_for replays protected actual journals.
        owner = object.__new__(protection.NativeProtection)
        task = {
            "id": "original-task",
            "kind": "implement",
            "milestone_id": "M1",
            "source_revision": "assignment-before-writes",
            "acceptance_criteria": ["AC1"],
            "affected_paths": ["src/a.py"],
        }
        binding = {
            "task": deepcopy(task),
            "contract": {"hash": "original-contract"},
            "settings": {},
            "runtime_sha256": "runtime",
            "source": {"revision": "reviewed-after-writes"},
        }
        launch = {
            "sequence": 1,
            "body": {
                "binding": {**deepcopy(binding), "source": {"revision": "before-writes"}},
                "boundary": {"allow_write": True},
                "grants": ["src/a.py"],
            },
        }
        record = {
            "stage": "terra",
            "role": "terra",
            "engine": "opencode",
            "route_role": None,
            "exit_code": 0,
            "source_revision": "reviewed-after-writes",
        }
        finish = {
            "sequence": 2,
            "kind": "finished",
            "body": {"record": record, "after": binding["source"], "pins": {}, "possible_writes": ["src/a.py"]},
        }
        review = {"sequence": 4}
        rows = [{"kind": "origin"}, launch, finish, {"kind": "readonly-gap"}, review]
        query = {
            "binding": binding,
            "original_producer": deepcopy(record),
            "producer_task": deepcopy(task),
            "operator_source": None,
        }
        return owner, query, rows, launch, finish, review

    def test_builder_assignment_source_stays_prewrite_while_actual_finish_is_reviewed_postwrite(self):
        owner, query, rows, launch, finish, review = self.builder_fixture()
        with patch.object(owner, "_finished_for", return_value=(rows, launch, finish)), patch.object(owner, "_pins"):
            self.assertIsNone(owner._join_producer({"kind": "reviews"}, query, rows, review, {}))
        self.assertEqual(query["producer_task"]["source_revision"], "assignment-before-writes")

    def test_swapped_same_id_builder_task_or_postwrite_snapshot_cannot_supply_current_review(self):
        owner, query, rows, launch, finish, review = self.builder_fixture()
        variants = [
            ({**query, "producer_task": {**query["producer_task"], "affected_paths": ["src/b.py"]}}, finish),
            (query, {**finish, "body": {**finish["body"], "after": {"revision": "borrowed-source"}}}),
        ]
        for supplied, stopped in variants:
            with (
                self.subTest(supplied=supplied),
                patch.object(owner, "_finished_for", return_value=(rows, launch, stopped)),
                patch.object(owner, "_pins"),
                self.assertRaises(util.Paused),
            ):
                owner._join_producer({"kind": "reviews"}, supplied, rows, review, {})

    def test_later_restored_writer_grant_supersedes_original_builder_even_with_same_after_bytes(self):
        owner, query, rows, launch, finish, review = self.builder_fixture()
        rows[3] = {"kind": "finished", "body": {"possible_writes": ["src/a.py"]}}
        with (
            patch.object(owner, "_finished_for", return_value=(rows, launch, finish)),
            patch.object(owner, "_pins"),
            self.assertRaises(util.Paused),
        ):
            owner._join_producer({"kind": "reviews"}, query, rows, review, {})

    def test_missing_original_finished_builder_is_a_hold_not_record_metadata_authority(self):
        owner, query, rows, launch, finish, review = self.builder_fixture()
        with (
            patch.object(
                owner, "_finished_for", side_effect=util.Paused("PAUSED_VISUAL_EVIDENCE", "missing actual finish")
            ),
            self.assertRaises(util.Paused),
        ):
            owner._join_producer({"kind": "reviews"}, query, rows, review, {})

    def operator_fixture(self):
        owner = object.__new__(protection.NativeProtection)
        source = {"revision": "original-source", "files": {"src/a.py": "pinned"}}
        task = {
            "id": "first-task",
            "kind": "validate",
            "milestone_id": "M1",
            "acceptance_criteria": ["AC1"],
            "affected_paths": ["src/a.py"],
        }
        goal = {
            "hash": "contract",
            "revision": 1,
            "approval_event": {"original": "approval"},
            "body": {
                "initial_task": {"kind": "validate", "milestone_id": "M1", "acceptance_criteria": ["AC1"]},
                "acceptance_criteria": [{"id": "AC1"}],
                "milestones": [{"id": "M1", "affected_paths": ["src/a.py"], "acceptance_criteria": ["AC1"]}],
            },
        }
        binding = {"task": task, "contract": goal, "settings": {}, "runtime_sha256": "runtime", "source": source}
        supplied = {
            "kind": "operator_supplied_source",
            "model": None,
            "session_id": None,
            "task_id": task["id"],
            "contract_hash": goal["hash"],
            "contract_revision": goal["revision"],
            "source_revision": source["revision"],
            "source_files_sha256": util.digest(source["files"]),
            "approval_event_sha256": util.digest(goal["approval_event"]),
        }
        # Complete structural journal rows exercise the original causal join;
        # this controlled fixture still grants no operator or image authority.
        rows = [
            {"id": "controlled-origin", "sequence": 0, "kind": "origin", "body": {"source": source}},
            {
                "id": "controlled-prepared-approval",
                "sequence": 1,
                "kind": "approval-prepared",
                "body": {"snapshot": source},
            },
            {
                "id": "controlled-committed-approval",
                "sequence": 2,
                "kind": "approval-committed",
                "body": {
                    "prepared_id": "controlled-prepared-approval",
                    "contract": goal,
                    "current_task": task,
                    "settings": {},
                    "runtime_sha256": "runtime",
                    "snapshot": source,
                },
            },
        ]
        query = {"binding": binding, "original_producer": None, "producer_task": None, "operator_source": supplied}
        return owner, query, rows, {"sequence": len(rows)}

    def test_operator_shape_requires_original_first_validation_source_and_committed_approval(self):
        owner, query, rows, review = self.operator_fixture()
        self.assertIsNone(owner._join_producer({"kind": "reviews"}, query, rows, review, {}))
        changed = deepcopy(query)
        changed["operator_source"]["approval_event_sha256"] = "borrowed-approval"
        with self.assertRaises(util.Paused):
            owner._join_producer({"kind": "reviews"}, changed, rows, review, {})

    def test_prior_writer_even_without_net_source_change_refuses_operator_source(self):
        owner, query, rows, review = self.operator_fixture()
        rows.append(
            {
                "id": "controlled-prior-writer",
                "sequence": len(rows),
                "kind": "prepared",
                "body": {"grants": ["src/a.py"], "record_identity": {"stage": "terra"}},
            }
        )
        review["sequence"] = len(rows)
        with self.assertRaises(util.Paused):
            owner._join_producer({"kind": "reviews"}, query, rows, review, {})


class PlanningRouteIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name).resolve()
        self.run = self.workspace / ".autocode" / "runs" / "route-fixture"
        self.run.mkdir(parents=True)
        # Shape-only record lookup; no native supervisor, approval or config
        # receipt is fabricated. Real _native_config separately verifies them.
        self.owner = object.__new__(protection.NativeProtection)
        self.owner.workspace, self.owner.run = self.workspace, self.run
        self.source = {"revision": "controlled-original-source"}
        self.roles = {
            key: {
                "engine": "opencode",
                "provider": "saved-provider",
                "model": "saved/" + key,
                "reasoning_effort": "high",
            }
            for key in ("requirements", "glm", "plan_reviewer")
        }

    def record(self, stage, role, *, explicit=None):
        before = self.run / (stage + ".before.json")
        before.write_text(json.dumps(self.source))
        route = self.roles[explicit or role]
        command = ["/controlled/native", "run", "--model", route["model"], "--variant", "high"]
        record = {
            "stage": stage,
            "role": role,
            "engine": "opencode",
            "command": command,
            "launch_route": deepcopy(route),
            "before_ref": str(before),
            "events": str(self.run / (stage + ".events.jsonl")),
            "output": str(self.run / (stage + ".output.json")),
        }
        if explicit is not None:
            record["route_role"] = explicit
        binding = {"settings": {"roles": deepcopy(self.roles)}, "source": self.source, "task": None}
        return record, binding, command

    def test_ordinary_planning_roles_reach_record_binding_without_stage_route_keys(self):
        # These are actual V2_STAGE_ROLES shapes, plus the documented recognizer
        # requirements route. Saved role keys intentionally contain no stages.
        for stage, role in (
            ("requirements", "requirements"),
            ("plan", "glm"),
            ("plan_revise", "glm"),
            ("plan_review", "plan_reviewer"),
            ("plan_finalize", "plan_reviewer"),
            ("recognize_workflow", "requirements"),
        ):
            with self.subTest(stage=stage):
                record, binding, command = self.record(stage, role)
                original = self.owner._launch_record_identity(record, binding, command)
                self.assertEqual(original["stage"], stage)
                self.assertEqual(protection.record_route(original), role)
                self.assertEqual(original["launch_route"], self.roles[role])

    def test_explicit_route_and_stage_identity_remain_independent_and_exact(self):
        record, binding, command = self.record("plan_finalize", "plan_reviewer", explicit="glm")
        original = self.owner._launch_record_identity(record, binding, command)
        self.assertEqual(protection.record_route(original), "glm")
        for change in ({"route_role": "plan_reviewer"}, {"stage": "plan_review"}):
            with self.subTest(change=change), self.assertRaises(util.Paused):
                protection.check_finished_record(original, {**original, **change})
        with self.assertRaises(util.Paused):
            self.owner._launch_record_identity({**record, "route_role": "plan_reviewer"}, binding, command)

    def test_saved_model_or_high_effort_record_cannot_be_substituted(self):
        record, binding, command = self.record("plan", "glm")
        for change in ({"model": "other/model"}, {"reasoning_effort": "low"}):
            supplied = {**record, "launch_route": {**record["launch_route"], **change}}
            with self.subTest(change=change), self.assertRaises(util.Paused):
                self.owner._launch_record_identity(supplied, binding, command)

    def test_malformed_explicit_route_is_not_a_fallback_to_saved_role_or_stage(self):
        for value in ("", " ", [], {}, False):
            with self.subTest(value=value), self.assertRaises(util.Paused):
                protection.record_route({"stage": "plan_finalize", "role": "plan_reviewer", "route_role": value})


class FinalIntegrationReceiptTests(unittest.TestCase):
    def fixture(self):
        # Exact immutable v2 receipt schema, with controlled declared hashes and
        # paths only. Mocked history does NOT grant native/source/image authority.
        # The production boundary still calls verify_review on one selector.
        cases = [
            {
                "id": name,
                "artifacts": {"screenshot": {"path": name + ".png", "sha256": util.digest("reference-" + name)}},
            }
            for name in ("empty", "populated")
        ]
        mapping = {"empty": ["C1"], "populated": ["C2"]}
        derived = {
            "policy": "complete-cases-v1",
            "mode": "integration",
            "milestone_ids": ["M2"],
            "required_case_ids": ["empty", "populated"],
            "current_case_ids": ["populated"],
            "recheck_case_ids": ["empty"],
            "out_of_scope_case_ids": [],
            "case_criteria": mapping,
            "case_owners": {"empty": "M1", "populated": "M2"},
            "task_criterion_ids": ["C1", "C2"],
            "affected_paths": ["app.html"],
            "changed_paths": [],
            "accepted_milestone_ids": ["M1"],
            "case_input_paths": {"empty": ["app.html"]},
        }
        bound = {
            "task_id": "current-task",
            "contract_hash": util.digest("contract"),
            "contract_revision": 1,
            "source_revision": util.digest("source"),
            "runtime_hash": util.digest("runtime"),
            "criteria": [{"id": "C1"}, {"id": "C2"}],
            "case_criteria": mapping,
            "cases": cases,
            "reviewer": {"provider": "opencode", "model": "saved/reviewer-high"},
            "manifest_body_hash": util.digest(cases),
            "manifest_file_sha256": util.digest("manifest-file"),
            "visual_scope": deepcopy(derived),
        }
        manifest_ref = "/controlled/manifest.json"
        pins = {manifest_ref: bound["manifest_file_sha256"]}
        rows, images = [], []
        for case in cases:
            name = case["id"]
            row = {
                "id": name,
                "verdict": "PASS",
                "criterion_ids": mapping[name],
                "capture_ref": "/controlled/" + name + "-capture.json",
                "capture_sha256": util.digest("capture-" + name),
                "candidate_ref": "/controlled/" + name + "-candidate.png",
                "candidate_sha256": util.digest("candidate-" + name),
                "comparison_ref": "/controlled/" + name + "-comparison.json",
                "comparison_sha256": util.digest("comparison-" + name),
                "reference_sha256": case["artifacts"]["screenshot"]["sha256"],
            }
            rows.append(row)
            for ref, sha in (
                ("capture_ref", "capture_sha256"),
                ("candidate_ref", "candidate_sha256"),
                ("comparison_ref", "comparison_sha256"),
            ):
                pins[row[ref]] = row[sha]
            images.extend(
                [
                    {"case_id": name, "kind": "reference", "sha256": row["reference_sha256"], "mime": "image/png"},
                    {"case_id": name, "kind": "candidate", "sha256": row["candidate_sha256"], "mime": "image/png"},
                ]
            )
        native = {
            "events_ref": "/controlled/events.jsonl",
            "report_ref": "/controlled/report.json",
            "session_id": "current-native-session",
            "message_id": "current-message",
            "finish_id": "current-finish",
        }
        delivery = {
            "version": 1,
            "kind": "final_request_image_delivery",
            "image_capable": True,
            "completed": True,
            "after_final_transform": True,
            "binding_sha256": util.digest(bound),
            "images": images,
            **bound["reviewer"],
            **{key: native[key] for key in ("session_id", "message_id", "finish_id")},
            "request_id": "current-request",
            "response_id": "current-response",
            "events_sha256": util.digest("events"),
            "report_sha256": util.digest("report"),
            "evidence_ref": "/controlled/delivery.json",
            "evidence_sha256": util.digest("delivery"),
        }
        pins.update(
            {
                native["events_ref"]: delivery["events_sha256"],
                native["report_ref"]: delivery["report_sha256"],
                delivery["evidence_ref"]: delivery["evidence_sha256"],
            }
        )
        accepted_at = "2026-10-06T12:00:00+00:00"
        reviewer = {
            "stage": "sol",
            "accepted": True,
            "read_only": True,
            "independent": True,
            "source_full_gate": True,
            **bound["reviewer"],
            "runtime_hash": bound["runtime_hash"],
            "session_id": native["session_id"],
            "producer_model": "saved/builder-high",
            "producer_session_id": "different-builder-session",
            "finished_at": accepted_at,
        }
        receipt = {
            "version": 2,
            "kind": "visual_acceptance",
            "binding": bound,
            "cases": rows,
            "verdict": "PASS",
            "accepted_at": accepted_at,
            "manifest_ref": manifest_ref,
            "reviewer": reviewer,
            "delivery": delivery,
            "evidence_hashes": pins,
            "native_evidence": native,
            "scope_verdict": "PASS",
            "scope_all_accepted": True,
            "id": util.digest(
                {
                    "events": native["events_ref"],
                    **{key: native[key] for key in ("session_id", "message_id", "finish_id")},
                }
            ),
        }
        self.rehash(receipt)
        state = {"settings": {"design_manifest": {"body": {"cases": deepcopy(cases)}}}}
        current_binding = {
            "contract": "exact-current-contract",
            "task": "exact-current-task",
            "settings": deepcopy(state["settings"]),
            "source": "exact-current-source",
            "runtime_sha256": "exact-runtime",
        }
        original = {"metadata": {"binding": deepcopy(current_binding), "receipt": receipt}}
        owner = object.__new__(factory.NativeVisualFactory)
        owner.history = Mock()
        owner.history.verify_review.return_value = original
        owner.history.fresh_facts.return_value = {"current_binding": current_binding}
        owner.scope_authority = Mock(return_value=deepcopy(derived))
        selector = {
            "kind": "reviews",
            "manifest_ref": "/controlled/original-review.json",
            "sha256": util.digest("original-review"),
        }
        return owner, state, selector, original, derived

    def rehash(self, receipt):
        receipt["receipt_sha256"] = util.digest(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        )

    def test_one_fresh_actual_schema_integration_receipt_reaches_final_native_gate(self):
        owner, state, selector, original, derived = self.fixture()
        self.assertTrue(owner.require_final_integration(state, snapshot={}, review_selector=selector))
        owner.history.verify_review.assert_called_once_with(selector)

    def test_all_case_slice_pass_still_cannot_claim_final_completion(self):
        owner, state, selector, original, derived = self.fixture()
        original["metadata"]["receipt"]["binding"]["visual_scope"]["mode"] = derived["mode"] = "slice"
        original["metadata"]["receipt"]["delivery"]["binding_sha256"] = util.digest(
            original["metadata"]["receipt"]["binding"]
        )
        owner.scope_authority.return_value = deepcopy(derived)
        self.rehash(original["metadata"]["receipt"])
        with self.assertRaises(util.Paused):
            owner.require_final_integration(state, snapshot={}, review_selector=selector)

    def test_failed_required_case_cannot_use_saved_success_booleans(self):
        owner, state, selector, original, derived = self.fixture()
        receipt = original["metadata"]["receipt"]
        receipt["cases"][0]["verdict"] = receipt["verdict"] = receipt["scope_verdict"] = "FAIL"
        receipt["scope_all_accepted"] = False
        self.rehash(receipt)
        state["current_all_accepted"] = state["scope_all_accepted"] = True
        with self.assertRaises(util.Paused):
            owner.require_final_integration(state, snapshot={}, review_selector=selector)

    def test_outside_not_verified_or_missing_full_case_roster_cannot_complete(self):
        for mutation in ("outside", "omitted"):
            owner, state, selector, original, derived = self.fixture()
            receipt = original["metadata"]["receipt"]
            if mutation == "outside":
                # A valid second slice retains the complete roster but may not
                # claim the earlier outside case as inspected or complete.
                retained_scope = receipt["binding"]["visual_scope"]
                retained_scope.update(
                    mode="slice",
                    required_case_ids=["populated"],
                    recheck_case_ids=[],
                    out_of_scope_case_ids=["empty"],
                    task_criterion_ids=["C2"],
                )
                owner.scope_authority.return_value = deepcopy(retained_scope)
                receipt["cases"][0].update(
                    verdict="NOT_VERIFIED",
                    capture_ref="",
                    capture_sha256=None,
                    candidate_ref="",
                    candidate_sha256=None,
                    comparison_ref="",
                    comparison_sha256=None,
                    reference_sha256=None,
                )
                receipt["verdict"] = "NOT_VERIFIED"
                receipt["delivery"]["images"] = receipt["delivery"]["images"][2:]
                receipt["delivery"]["binding_sha256"] = util.digest(receipt["binding"])
            else:
                receipt["cases"].pop()
            self.rehash(receipt)
            with self.subTest(mutation=mutation), self.assertRaises(util.Paused):
                owner.require_final_integration(state, snapshot={}, review_selector=selector)

    def test_foreign_current_binding_or_scope_drift_refuses_final_gate(self):
        for mutation in ("source", "scope"):
            owner, state, selector, original, derived = self.fixture()
            if mutation == "source":
                original["metadata"]["binding"]["source"] = "stale-original-source"
            else:
                owner.scope_authority.return_value["affected_paths"] = ["different.html"]
            with self.subTest(mutation=mutation), self.assertRaises(util.Paused):
                owner.require_final_integration(state, snapshot={}, review_selector=selector)

    def test_historical_slice_union_and_extra_summary_field_cannot_replace_one_receipt(self):
        owner, state, selector, original, derived = self.fixture()
        slices = []
        for index, identity in enumerate(("empty", "populated")):
            receipt = deepcopy(original["metadata"]["receipt"])
            current = receipt["binding"]["visual_scope"]
            current.update(
                mode="slice",
                milestone_ids=["M1" if index == 0 else "M2"],
                required_case_ids=[identity],
                current_case_ids=[identity],
                recheck_case_ids=[],
                out_of_scope_case_ids=["populated" if index == 0 else "empty"],
                task_criterion_ids=["C1" if index == 0 else "C2"],
                accepted_milestone_ids=[] if index == 0 else ["M1"],
                case_input_paths={} if index == 0 else {"empty": ["app.html"]},
            )
            outside = receipt["cases"][1 if index == 0 else 0]
            outside.update(
                verdict="NOT_VERIFIED",
                capture_ref="",
                capture_sha256=None,
                candidate_ref="",
                candidate_sha256=None,
                comparison_ref="",
                comparison_sha256=None,
                reference_sha256=None,
            )
            receipt["verdict"] = "NOT_VERIFIED"
            receipt["delivery"]["images"] = receipt["delivery"]["images"][2 * index : 2 * index + 2]
            for key in ("session_id", "message_id", "finish_id"):
                receipt["native_evidence"][key] += "-slice-" + str(index)
                receipt["delivery"][key] = receipt["native_evidence"][key]
            receipt["reviewer"]["session_id"] = receipt["native_evidence"]["session_id"]
            receipt["delivery"]["binding_sha256"] = util.digest(receipt["binding"])
            native = receipt["native_evidence"]
            receipt["id"] = util.digest(
                {
                    "events": native["events_ref"],
                    **{key: native[key] for key in ("session_id", "message_id", "finish_id")},
                }
            )
            self.rehash(receipt)
            slices.append(receipt)
        # Complementary scope PASS receipts cover both cases collectively, but
        # neither is a fresh all-case integration request; the gate reads one.
        original["metadata"]["receipt"] = deepcopy(slices[1])
        state["visual_acceptance_receipts"] = slices
        state["current_all_accepted"] = True
        with self.assertRaises(util.Paused):
            owner.require_final_integration(state, snapshot={}, review_selector=selector)
        owner.history.verify_review.assert_called_once_with(selector)
        owner, state, selector, original, derived = self.fixture()
        original["metadata"]["receipt"]["current_all_accepted"] = True
        self.rehash(original["metadata"]["receipt"])
        with self.assertRaises(util.Paused):
            owner.require_final_integration(state, snapshot={}, review_selector=selector)


if __name__ == "__main__":
    unittest.main()
