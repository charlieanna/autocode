"""A mistyped identity echo on a correctly bound review launch costs no report repair (#2135).

Fake provider and saved responses only; no model calls. The reproduction follows the reported
Completion Reviewer rejection (`$.contract_hash: invalid enum`, bugfix-iso-weeks, glm-mimo-kilo,
master eeb64352f). That run's rejected report has no public bundle reference, so the response
below is a new deterministic reproduction, not the original report.
"""

import contextlib
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_goals as goals
import autocode_report_identity as report_identity
import autocode_run_actions as run_actions

from . import test_autocode as base

runner, support = base.runner, base.s

HASH = "a" * 64
MISTYPED = "a" * 63 + "b"  # one character off, as the provider copied it
OLD_HASH = "c" * 64
TASK = "task-0123456789ab"


class Fixture(unittest.TestCase):
    setUp_base = base.RetrofitTest.setUp

    def setUp(self):
        self.setUp_base()
        self.state.update(
            next_stage="astra_review",
            goal_contract={"revision": 2, "hash": HASH},
            contract_history=[{"revision": 1, "hash": OLD_HASH}],
            current_task={"id": TASK, "contract_revision": 2, "contract_hash": HASH},
            task_archive=[{"id": "task-ffffffffffff"}],
        )
        self.schema = self.run / "astra-v3.schema.json"
        legacy = support.read(runner.SCHEMA_DIR / "v2/astra-decision.schema.json")
        self.schema.write_text(json.dumps(goals.role_schema(legacy, "astra")))

    def report(self, **identity):
        return {
            "status": "CONTINUE",
            "acceptance_criteria": [
                {"id": "C1", "criterion": "Contract holds", "status": "verified", "evidence": str(self.evidence)}
            ],
            "evidence": [str(self.evidence)],
            "next_objective": "next coherent batch",
            "blocker": "",
            "plan": ["batch"],
            "affected_paths": [],
            "contract_revision": 2,
            "contract_hash": HASH,
            "task_id": TASK,
            "user_request": {
                "kind": "none",
                "discovered": "",
                "impact": "",
                "decision_needed": "",
                "options": [],
                "proposed_delta": "",
            },
            "deferred_backlog": [],
            "next_task": {
                "kind": "none",
                "milestone_id": "",
                "requirements": [],
                "acceptance_criteria": [],
                "validation_plan": [],
            },
            "agreed_limitations": [],
            **identity,
        }


class LaunchBoundIdentityTests(Fixture):
    def review(self, report, *, during=None, revisions=("r", "r")):
        """Launch the Completion Reviewer against a fake provider that writes `report`."""
        state = self.state
        launches = []

        class Child:
            pid = 987654321

            def __init__(child, command, **kwargs):
                launches.append(command)
                if during:
                    during(state)
                Path(command[command.index("-o") + 1]).write_text(json.dumps(report))
                kwargs["stdout"].write('{"type":"thread.started","thread_id":"a-session"}\n')
                kwargs["stdout"].write('{"type":"turn.completed","usage":{"input_tokens":9,"output_tokens":2}}\n')

            def wait(child):
                return 0

        @contextlib.contextmanager
        def launch(command, *, checkpoint, **kwargs):
            for key in ("receipt_path", "timeout"):
                kwargs.pop(key)
            child = Child(command, **kwargs)
            checkpoint({"owner": {}, "provider": {"pid": child.pid}})
            yield child

        snapshots = iter([{"head": "h", "files": {}, "revision": revision} for revision in revisions] * 4)
        with (
            patch.object(runner.supervision, "launch", launch),
            patch.object(support, "snapshot", side_effect=lambda *a, **k: next(snapshots)),
            patch.object(runner.subprocess, "run"),
            patch.object(runner.processes, "preflight", return_value=None),
            patch.object(runner.processes, "wait_for_stage", return_value=(0, False)),
        ):
            result = runner.run_role(
                role="astra",
                prompt="review the batch",
                sandbox="read-only",
                workspace=self.root,
                run_dir=self.run,
                state=state,
                schema=self.schema,
                model="model-astra",
                allow_write=False,
                dry_run=False,
            )
        self.assertEqual(1, len(launches))
        return result

    def assert_refused(self, report, **options):
        with self.assertRaises((runner.ReportRepairQueued, support.Paused)) as caught:
            self.review(report, **options)
        rejected = [row for row in self.state["stages"] if row.get("rejected")]
        if isinstance(caught.exception, runner.ReportRepairQueued):
            self.assertIn("invalid enum", self.state["pending_report_repair"]["error"])
        self.assertFalse(any("identity_corrections" in row for row in self.state["stages"]))
        return caught.exception, rejected

    def test_mistyped_contract_hash_on_a_bound_launch_needs_no_repair_and_keeps_a_receipt(self):
        value, record = self.review(self.report(contract_hash=MISTYPED))
        self.assertEqual(HASH, value["contract_hash"])
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual(
            [{"field": "contract_hash", "reported": MISTYPED, "adopted": HASH}], record["identity_corrections"]
        )
        self.assertEqual((2, HASH, TASK), (record["contract_revision"], record["contract_hash"], record["task_id"]))
        self.assertEqual(MISTYPED, support.read(record["reported_output"])["contract_hash"])
        self.assertEqual(value, support.read(record["output"]))
        self.assertEqual(
            {**value, "contract_hash": MISTYPED}, support.read(record["reported_output"]), "only the identity changed"
        )

    def test_mistyped_revision_and_task_id_are_adopted_from_the_launch(self):
        value, record = self.review(self.report(contract_revision=3, task_id="task-0123456789ac"))
        self.assertEqual((2, TASK), (value["contract_revision"], value["task_id"]))
        self.assertEqual(["contract_revision", "task_id"], [row["field"] for row in record["identity_corrections"]])

    def test_exact_echo_is_unchanged_and_writes_no_receipt(self):
        value, record = self.review(self.report())
        self.assertEqual(self.report(), value)
        self.assertNotIn("identity_corrections", record)
        self.assertNotIn("reported_output", record)

    def test_contract_changed_during_the_stage_is_refused(self):
        def revise(state):
            state["goal_contract"] = {"revision": 3, "hash": "d" * 64}

        self.assert_refused(self.report(contract_hash=MISTYPED), during=revise)

    def test_report_for_another_task_is_refused(self):
        def reassign(state):
            state["current_task"] = {"id": "task-999999999999"}

        self.assert_refused(self.report(contract_hash=MISTYPED), during=reassign)

    def test_echo_of_an_earlier_contract_or_task_is_stale_not_a_typo(self):
        for identity in ({"contract_hash": OLD_HASH}, {"contract_revision": 1}, {"task_id": "task-ffffffffffff"}):
            with self.subTest(identity=identity):
                self.setUp()
                self.assert_refused(self.report(**identity))

    def test_source_change_during_review_still_invalidates_the_report(self):
        error, _ = self.assert_refused(self.report(contract_hash=MISTYPED), revisions=("r", "changed"))
        self.assertEqual("PAUSED_STALE_VALIDATION", error.status)


class SavedReportBindingTests(Fixture):
    """The loader alone: superseded attempts, unbound launches and older records."""

    def saved(self, report, **record):
        directory = self.run / "iterations/005"
        directory.mkdir(parents=True, exist_ok=True)
        output, events = directory / "completion-reviewer-01.json", directory / "completion-reviewer-01.jsonl"
        output.write_text(json.dumps(report))
        events.write_text("")
        schema = directory / "completion-reviewer-01.schema.json"
        schema.write_text(
            json.dumps(support.review_generation_schema(support.read(self.schema), self.state, "astra_review"))
        )
        record = {
            "stage": "astra_review",
            "role": "astra",
            "iteration": 5,
            "output": str(output),
            "events": str(events),
            "schema": str(schema),
            "contract_revision": 2,
            "contract_hash": HASH,
            "task_id": TASK,
            **record,
        }
        return record

    def test_the_active_attempt_is_corrected_and_reloads_unchanged(self):
        record = self.saved(self.report(contract_hash=MISTYPED))
        self.state["active_stage"] = copy.deepcopy(record)
        value = runner.load_stage_report(record, state=self.state)
        self.assertEqual(HASH, value["contract_hash"])
        self.assertEqual(value, runner.load_stage_report(record, state=self.state))
        self.assertEqual(MISTYPED, support.read(record["reported_output"])["contract_hash"])

    def test_superseded_attempt_without_an_active_binding_is_refused(self):
        record = self.saved(self.report(contract_hash=MISTYPED))
        for active in (None, {**record, "output": str(self.run / "iterations/005/completion-reviewer-02.json")}):
            with self.subTest(active=bool(active)):
                self.state["active_stage"] = active
                with self.assertRaisesRegex(ValueError, "contract_hash: invalid enum"):
                    runner.load_stage_report(copy.deepcopy(record), state=self.state)

    def test_record_without_a_launch_identity_is_not_fabricated(self):
        record = self.saved(self.report(contract_hash=MISTYPED))
        for key in ("contract_hash", "contract_revision"):
            with self.subTest(missing=key):
                unbound = {k: v for k, v in record.items() if k != key}
                self.state["active_stage"] = copy.deepcopy(unbound)
                with self.assertRaisesRegex(ValueError, "contract_hash: invalid enum"):
                    runner.load_stage_report(copy.deepcopy(unbound), state=self.state)

    def test_schema_not_bound_at_launch_keeps_the_report_as_written(self):
        record = self.saved(self.report(contract_hash=MISTYPED), schema=str(self.schema))
        self.state["active_stage"] = copy.deepcopy(record)
        value = runner.load_stage_report(record, state=self.state)
        self.assertEqual(MISTYPED, value["contract_hash"])
        self.assertNotIn("identity_corrections", record)
        self.assertTrue(run_actions.stale_result(self.state, value, "r"), "the stale-result guard still refuses it")

    def test_other_stages_and_corrected_reports_keep_their_freshness_checks(self):
        report = self.report(contract_hash=MISTYPED)
        for stage in ("terra", "astra_plan"):
            record = {"stage": stage, "output": "x", "events": "y", "contract_hash": HASH, "contract_revision": 2}
            self.state["active_stage"] = record
            self.assertEqual((report, []), report_identity.bind(report, {}, record, self.state))
        corrected = {**self.report(), "source_revision": "old"}
        self.assertTrue(run_actions.stale_result(self.state, corrected, "new"))


if __name__ == "__main__":
    unittest.main()
