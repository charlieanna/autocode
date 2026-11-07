"""Stable current attention identity, independent of polling and acknowledgement."""

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_attention as attention
import autocode_attention_cli as cli
import autocode_chat_view as chat
import autocode_client_actions as actions
import autocode_replay_view as replay
import autocode_run_view as run_view
import autocode_taskrun as taskrun

WORKSPACE = Path("/tmp/attention-project")
RUN = WORKSPACE / ".autocode" / "runs" / "one"


def value(*, context="plan-6", done=False, turns=None, created="2026-10-24T00:00:00Z", need=None):
    state = {"created_at": created, "turns": turns or [], "task_id": "saved-task-uuid"}
    public = {"done": done, "needs": need or {"kind": "approve_plan", "token": context}}
    return attention.project(state, public, workspace=WORKSPACE, run_dir=RUN, context=context)


def offered(**kwargs):
    return attention.with_acknowledgement(value(**kwargs), {"available": True, "state": "unread"})


class AttentionIdentity(unittest.TestCase):
    def test_repeated_reads_have_the_same_item_and_do_not_mutate_inputs(self):
        state = {"created_at": "now", "turns": []}
        public = {"status": "AWAITING_GOAL_APPROVAL", "needs": {"kind": "approve_plan", "token": "r6"}}
        before = copy.deepcopy((state, public))
        context = chat.context(state, public)
        one = attention.project(state, public, workspace=WORKSPACE, run_dir=RUN, context=context)
        public["observation"] = {"sampled_at": "a later read"}
        two = attention.project(state, public, workspace=WORKSPACE, run_dir=RUN, context=chat.context(state, public))
        self.assertEqual(one, two)
        public.pop("observation")
        self.assertEqual(before, (state, public))
        self.assertEqual("plan_approval", one["item"]["kind"])

    def test_revised_question_or_plan_is_a_new_item_not_a_transferred_acknowledgement(self):
        six, seven = offered(context="plan-6"), offered(context="plan-7")
        self.assertNotEqual(six["item"]["id"], seven["item"]["id"])
        self.assertEqual("unread", seven["acknowledgement"]["state"])

    def test_successive_identical_same_chat_completions_use_the_existing_turn_receipt(self):
        initial = value(done=True)
        second = value(done=True, turns=[{"event_id": "feedback-first", "say": "same text"}])
        third = value(
            done=True, turns=[{"event_id": "feedback-first"}, {"event_id": "feedback-second", "say": "same text"}]
        )
        self.assertEqual(3, len({row["item"]["id"] for row in (initial, second, third)}))
        self.assertEqual(second, value(done=True, context="later-status-read", turns=[{"event_id": "feedback-first"}]))

    def test_legacy_turn_position_is_stable_and_missing_incarnation_is_unknown(self):
        one, two = value(done=True, turns=[{}]), value(done=True, turns=[{}, {}])
        self.assertNotEqual(one["item"]["id"], two["item"]["id"])
        self.assertIsNone(attention.project({}, {"done": True}, workspace=WORKSPACE, run_dir=RUN, context="x")["item"])

    def test_another_run_or_recreated_directory_cannot_inherit_the_old_outcome(self):
        self.assertNotEqual(value(done=True)["item"]["id"], value(done=True, created="a new incarnation")["item"]["id"])
        other = attention.project(
            {"created_at": "now"}, {"done": True}, workspace=WORKSPACE, run_dir=RUN.with_name("two"), context="x"
        )
        self.assertNotEqual(value(done=True)["item"]["id"], other["item"]["id"])

    def test_routine_work_and_dependencies_create_no_human_alert(self):
        for kind in ("continue", "dependency"):
            self.assertIsNone(value(need={"kind": kind})["item"])
        self.assertEqual("clarification", value(need={"kind": "answer"})["item"]["kind"])
        self.assertEqual(
            "decision", value(need={"kind": "answer", "resolver_scope": "operational_exhaustion"})["item"]["kind"]
        )

    def test_acknowledgement_adds_no_task_authority_and_snippets_exclude_provider_text(self):
        current = offered(need={"kind": "answer", "reason": "SECRET-PROVIDER-OUTPUT"})
        self.assertNotIn("SECRET-PROVIDER-OUTPUT", str(current))
        before = copy.deepcopy(current)
        command = actions.attention_acknowledgement(current, WORKSPACE, RUN)
        self.assertEqual(["attention", "ack"], command.argv[:2])
        self.assertFalse(actions.advancing(command))
        self.assertEqual(before, current)
        for forbidden in ("--approve-goal", "--resume-paused", "--answer"):
            bad = copy.deepcopy(current)
            bad["acknowledge"]["next_command"].append(forbidden)
            with self.assertRaises(actions.ClientGap):
                actions.attention_acknowledgement(bad, WORKSPACE, RUN)
        with self.assertRaises(actions.ClientGap):
            actions.attention_acknowledgement(current, WORKSPACE, RUN.with_name("other"))

    def test_malformed_optional_ack_metadata_is_a_client_gap_not_a_crash(self):
        for bad in ([1], {"item": [1]}, {"acknowledge": [1]}, {"acknowledgement": [1]}, {**offered(), "version": True}):
            with self.subTest(value=bad), self.assertRaises(actions.ClientGap):
                actions.attention_acknowledgement(bad, WORKSPACE, RUN)

    def test_recorded_or_invalid_replay_never_produces_a_completion_alert_in_inventory(self):
        for settings in ({"replay_recording": {}}, {"replay_recording": {"checks": "recorded"}}):
            state = {"status": "TASK_COMPLETE", "settings": settings, "created_at": "now"}
            self.assertFalse(replay.completion_policy(state)["completion_allowed"])
            self.assertFalse(run_view.view(state, workspace=WORKSPACE, run_dir=RUN)["done"])

    def test_taskrun_carries_only_the_named_acknowledgement_and_preserves_advancing_receipts(self):
        run = taskrun.TaskRun(WORKSPACE, RUN)
        import json
        import subprocess

        item = offered()["item"]
        receipt = {"item_id": item["id"], "workspace": item["workspace"], "run_dir": item["run_dir"]}
        reply = subprocess.CompletedProcess([], 0, json.dumps({"acknowledged": True, "receipt": receipt}), "")
        with patch.object(taskrun, "run_process", return_value=reply) as process:
            result = run.acknowledge_attention({"attention": offered()})
            self.assertTrue(result["acknowledged"])
            self.assertFalse(process.call_args.kwargs["advancing"])
            self.assertIsNone(run.last_advance)
            self.assertEqual(offered()["acknowledge"]["next_command"][1:], process.call_args.args[0][2:])

    def test_taskrun_refuses_malformed_or_mismatched_acknowledgement_receipts(self):
        import json
        import subprocess

        run = taskrun.TaskRun(WORKSPACE, RUN)
        for reply in ([], {"acknowledged": True, "receipt": {"item_id": "another-alert"}}):
            process = subprocess.CompletedProcess([], 0, json.dumps(reply), "")
            with patch.object(taskrun, "run_process", return_value=process), self.assertRaises(taskrun.TaskRunError):
                run.acknowledge_attention({"attention": offered()})

    def test_superseded_cli_alert_is_refused_without_writing_an_ack_or_advancing(self):
        import contextlib
        import io
        import json

        with (
            patch.object(cli.store, "inspect", return_value={"state": "unread"}),
            patch.object(cli, "TaskRun") as driver,
            patch.object(cli.store, "acknowledge") as write,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            driver.return_value.status.return_value = {"attention": offered(context="new-plan")}
            code = cli.cli(
                [
                    "ack",
                    "--workspace",
                    str(WORKSPACE),
                    "--run-dir",
                    str(RUN),
                    "--item-id",
                    offered(context="old-plan")["item"]["id"],
                ]
            )
            self.assertEqual(2, code)
            self.assertFalse(json.loads(output.getvalue())["acknowledged"])
            write.assert_not_called()
