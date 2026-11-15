"""Planning and public usage read one settled-attempt ledger, without launching a provider."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_planning_calls as planning
import autocode_run_view as run_view
import autocode_usage as usage


class SettledAttemptTests(unittest.TestCase):
    def setUp(self):
        self.spawn = self.enterContext(patch.object(subprocess, "Popen", side_effect=AssertionError("no process")))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.events = self.root / "requirements-01.jsonl"
        self.events.write_text("offline provider exited without a report\n")

    def tearDown(self):
        self.spawn.assert_not_called()

    def record(self, stage="requirements_gather", **extra):
        return {
            "stage": stage,
            "role": "requirements",
            "engine": "opencode",
            "provider": "fixture",
            "started_at": "2026-11-15T16:04:35+00:00",
            "finished_at": "2026-11-15T16:04:36+00:00",
            "exit_code": 1,
            "accounted": True,
            "events": str(self.events),
            "output": str(self.events.with_suffix(".json")),
            **extra,
        }

    def state(self, **extra):
        return {
            "status": "WAITING_FOR_USER",
            "run_dir": str(self.root),
            "settings": {"limits": {"planning_calls": 1}},
            "next_stage": "requirements_gather",
            **extra,
        }

    def test_public_status_counts_retained_failures_across_planning_names_and_backends(self):
        stages = (
            "requirements_gather",
            "draft_plan",
            "review_plan",
            "revise_plan",
            "finalize_plan",
            "requirements",
            "plan",
            "plan_review",
            "plan_revise",
            "plan_finalize",
        )
        for stage in stages:
            for engine, provider in (
                ("codex", None),
                ("opencode", "opencode"),
                ("opencode", "kilocode"),
                ("opencode", "claude"),
            ):
                for finish in ({}, {"timed_out": True, "exit_code": -15}, {"rejected": True, "exit_code": 0}):
                    with self.subTest(stage=stage, engine=engine, provider=provider, finish=finish):
                        record = self.record(stage, engine=engine, provider=provider, **finish)
                        state = self.state(active_stage=record)
                        before = copy.deepcopy(state)
                        shown = run_view.view(state)
                        self.assertEqual({"used": 1, "cap": 1, "renewed_at": None}, shown["planning_calls"])
                        self.assertEqual(1, shown["usage"]["accounting"]["attempt_count"])
                        self.assertEqual(0, shown["usage"]["accounting"]["active_attempts"])
                        self.assertFalse(shown["usage"]["accounting"]["provider_requests"]["complete"])
                        self.assertIsNone(shown["usage"]["accounting"]["provider_requests"]["value"])
                        self.assertEqual(before, state, "reading an attempt grants no reconciliation authority")

    def test_an_accounted_legacy_attempt_without_a_finish_timestamp_counts(self):
        record = self.record()
        record.pop("finished_at")
        state = self.state(active_stage=record)
        self.assertEqual(1, planning.used(state))
        self.assertEqual([record], usage.finished_records(state))
        self.assertIsNone(usage.running_stage(state))

    def test_unfinished_and_pre_exec_placeholders_do_not_charge_their_own_admission(self):
        record = self.record(runner_calls=1, pid=78, supervision={"provider": {"pid": 78, "birth_identity": 3.0}})
        record.pop("finished_at")
        for accounted in (None, False, "false"):
            with self.subTest(accounted=accounted):
                record["accounted"] = accounted
                state = self.state(active_stage=record)
                self.assertEqual(0, planning.used(state))
                self.assertIsNone(planning.reached(state))
                self.assertEqual([], usage.finished_records(state))
                self.assertIs(record, usage.running_stage(state))

    def test_saved_active_and_history_copies_are_one_attempt_after_json_roundtrip(self):
        record = self.record()
        state = json.loads(json.dumps(self.state(stages=[record], history=[record], active_stage=record)))
        self.assertEqual(1, planning.used(state))
        self.assertEqual(1, len(usage.finished_records(state)))
        state.pop("active_stage")
        self.assertEqual(1, planning.used(state))

    def test_duplicate_saved_artifact_rows_are_not_two_calls_or_two_costs(self):
        record = self.record(metrics={"provider_cost_usd": 0.25})
        state = self.state(stages=[record, copy.deepcopy(record)], history=[copy.deepcopy(record)])
        self.assertEqual(1, planning.used(state))
        self.assertEqual(1, usage.summary(state)["stages"])
        self.assertEqual(0.25, usage.summary(state)["cost_usd"]["reported"])

    def test_explicit_archive_paths_deduplicate_and_use_the_kept_artifact(self):
        original = self.record()
        archived_events = self.root / "archived" / self.events.name
        archived_events.parent.mkdir()
        archived_events.write_bytes(self.events.read_bytes())
        kept = self.record(events=str(archived_events), output=str(archived_events.with_suffix(".json")))
        kept["archived_paths"] = {original[key]: kept[key] for key in ("events", "output")}
        state = self.state(stages=[original, kept], active_stage=copy.deepcopy(original))
        self.assertEqual(1, planning.used(state))
        self.assertEqual([kept], usage.finished_records(state))
        self.assertEqual("events:" + str(archived_events.resolve()), usage.attempt_identity(kept))
        self.assertEqual(1, run_view.view(state)["usage"]["accounting"]["attempt_count"])

    def test_archive_alias_chains_only_merge_the_recorded_attempt(self):
        records = [self.record(events=str(self.root / name)) for name in ("old.jsonl", "middle.jsonl", "kept.jsonl")]
        for old, kept in zip(records[:-1], records[1:], strict=True):
            kept["archived_paths"] = {old["events"]: kept["events"]}
        state = self.state(stages=records)
        self.assertEqual(1, planning.used(state))
        self.assertEqual([records[-1]], usage.finished_records(state))

    def test_malformed_conflicting_and_cyclic_archive_maps_do_not_guess_equivalence(self):
        first, second = self.record(), self.record(events=str(self.root / "other.jsonl"))
        for invalid in (None, [], {first["events"]: None}, {first["events"]: first["events"]}):
            with self.subTest(invalid=invalid):
                second["archived_paths"] = invalid
                self.assertEqual(2, planning.used(self.state(stages=[first, second])))
        first["archived_paths"] = {second["events"]: first["events"]}
        second["archived_paths"] = {first["events"]: second["events"]}
        self.assertEqual(2, planning.used(self.state(stages=[first, second])))
        third = self.record(events=str(self.root / "third.jsonl"), archived_paths={first["events"]: "different.jsonl"})
        first.pop("archived_paths")
        self.assertEqual(3, planning.used(self.state(stages=[first, second, third])))

    def test_similar_names_roles_and_reports_do_not_merge_distinct_attempts(self):
        records = [self.record(events=str(self.root / folder / self.events.name)) for folder in ("one", "two")]
        records += [
            self.record(
                "requirements_gather_report_repair",
                events=str(self.root / "repair.jsonl"),
                original_stage="requirements_gather",
                report_only=True,
            )
        ]
        self.assertEqual(3, planning.used(self.state(stages=records)))
        anonymous = {"stage": "requirements_gather", "started_at": None}
        self.assertEqual(2, planning.used(self.state(stages=[anonymous, copy.deepcopy(anonymous)])))
        self.assertEqual(2, planning.used(self.state(stages=[anonymous, anonymous])))
        anonymous["accounted"] = True
        self.assertEqual(1, planning.used(self.state(stages=[anonymous], active_stage=anonymous)))

    def test_identity_spelling_and_relative_run_namespace_are_preserved(self):
        for record, namespace, expected in (
            ({"events": str(self.events)}, "", "events:" + str(self.events.resolve())),
            ({"output": "reply.json"}, str(self.root), "output:" + str((self.root / "reply.json").resolve())),
            ({"worker_attempt": "worker-owned"}, "run", "run:worker_attempt:worker-owned"),
            ({"attempt_id": "001/planner-01"}, "run", "run:attempt_id:001/planner-01"),
            ({}, "run", "run:unidentified:4"),
        ):
            with self.subTest(record=record):
                self.assertEqual(expected, usage.attempt_identity(record, namespace=namespace, index=4))
        self.assertEqual(str(self.root.resolve()), usage.run_identity(str(self.root)))
        self.assertEqual("legacy-run", usage.run_identity("legacy-run"))
        self.assertIsNone(usage.run_identity(None))

    def test_history_alone_and_malformed_lists_are_not_new_attempt_authority(self):
        record = self.record()
        for stages in (None, True, "missing", {"stage": "requirements_gather"}):
            with self.subTest(stages=stages):
                state = self.state(stages=stages, history=[record])
                self.assertEqual(0, planning.used(state))
                self.assertEqual([], usage.finished_records(state))
                state["active_stage"] = record
                self.assertEqual(1, planning.used(state))

    def test_public_readers_keep_the_run_namespace_for_relative_artifacts(self):
        records = [self.record(events=name) for name in ("first.jsonl", "second.jsonl")]
        state = self.state(stages=records)
        shown = run_view.view(state)
        self.assertEqual(2, shown["planning_calls"]["used"])
        self.assertEqual(2, shown["usage"]["accounting"]["attempt_count"])

    def test_retained_attempt_respects_existing_feedback_epoch_and_runner_exclusions(self):
        record = self.record()
        event = {"kind": "answer", "actor": "user_cli", "at": "2026-11-15T16:04:37+00:00"}
        state = self.state(active_stage=record, user_events=[event])
        self.assertEqual(0, planning.used(state))
        event["actor"] = "runner"
        self.assertEqual(1, planning.used(state))
        record["started_at"] = "unreadable"
        event["actor"] = "user_cli"
        self.assertEqual(1, planning.used(state), "an unknown time cannot erase a call")
        for excluded in ("runner_owned", "dry_run"):
            with self.subTest(excluded=excluded):
                record[excluded] = True
                self.assertEqual(0, planning.used(state))
                record.pop(excluded)

    def test_the_hard_twenty_call_boundary_uses_the_same_finished_attempts(self):
        records = [self.record(events=str(self.root / f"plan-{index}.jsonl")) for index in range(19)]
        state = self.state(stages=records, active_stage=self.record(), settings={"limits": {"planning_calls": 20}})
        self.assertEqual((20, 20), planning.reached(state))
        state["active_stage"].pop("finished_at")
        state["active_stage"].pop("accounted")
        self.assertEqual(19, planning.used(state))
        self.assertIsNone(planning.reached(state))
        self.assertEqual(20, state["settings"]["limits"]["planning_calls"])

    def test_deduplicated_usage_stays_unknown_or_partial_instead_of_zero_or_complete(self):
        for metrics in ({}, {"provider_cost_usd": 0.25, "provider_tokens_partial": True}):
            with self.subTest(metrics=metrics):
                record = self.record(metrics=metrics)
                state = self.state(stages=[record, copy.deepcopy(record)], active_stage=copy.deepcopy(record))
                before = copy.deepcopy(state)
                shown = run_view.view(state)
                self.assertEqual(1, shown["planning_calls"]["used"])
                self.assertFalse(shown["usage"]["cost_usd"]["complete"])
                self.assertEqual(1, shown["usage"]["accounting"]["attempt_count"])
                self.assertIsNone(shown["usage"]["accounting"]["provider_requests"]["value"])
                self.assertEqual(before, state)
