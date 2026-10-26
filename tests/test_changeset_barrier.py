"""The core's durable set-approval fence; no provider or supervised command is launched."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_changeset as changeset
import autocode_changeset_barrier as barrier
import autocode_changeset_store as store
import autocode_contract_identity as identity
import autocode_resolver_human as human
import autocode_run_view as run_view
import autocode_util as util


class ChangesetBarrierTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="changeset-barrier-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "home")})
        environment.start()
        self.addCleanup(environment.stop)
        self.a, self.b = (self.state(name) for name in ("api", "consumer"))
        record = store.reserve(
            source_workspace=self.a["workspace"],
            source_run_dir=self.a["run_dir"],
            target="consumer",
            workspace=self.b["workspace"],
            brief="Consume it",
        )
        store.bind(record["id"], "consumer", self.b["run_dir"])
        self.save(self.a)
        self.save(self.b)

    def state(self, name):
        workspace = self.root / name
        run_dir = workspace / ".autocode" / "runs" / "one"
        run_dir.mkdir(parents=True)
        body = {
            "open_blocking_questions": [],
            "acceptance_criteria": [],
            "constraints": [],
            "permission_boundaries": [],
        }
        contract = {"task_id": name, "revision": 1, "body": body, "origin": "test"}
        contract["hash"] = util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        token = identity.token(contract)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": token}
        contract.update(approval_status="approved", approval_event=event)
        return {
            "workspace": str(workspace),
            "run_dir": str(run_dir),
            "task": name,
            "goal_contract": contract,
            "user_events": [event],
            "displayed_goal": token,
            "status": "RUNNING",
            "phase": "READY_TO_EXECUTE",
            "next_stage": "terra",
        }

    def save(self, state):
        path = Path(state["run_dir"]) / "state.json"
        with barrier.checkpoint(path, state):
            util.atomic_json(path, state)

    def begin(self):
        record = store.find(self.a["run_dir"])
        rows = [{"run_dir": s["run_dir"], "plan_token": identity.token(s["goal_contract"])} for s in (self.a, self.b)]
        token = changeset.set_token(rows)
        store.begin_approval(
            self.a["run_dir"], token, {r["run_dir"]: r["plan_token"] for r in rows}, store.members(record)
        )
        return token

    def accept(self, state, token):
        barrier.approve(state, identity.token(state["goal_contract"]), token)
        self.save(state)

    def release(self):
        token = self.begin()
        self.accept(self.a, token)
        self.accept(self.b, token)
        store.release_approval(self.a["run_dir"], token)
        return token

    def test_partial_approval_stays_held_and_retry_releases_every_member(self):
        token = self.begin()
        self.accept(self.a, token)
        self.assertTrue(barrier.refusal(self.a))
        self.assertEqual("approve_plan", run_view.action(self.a)["needs"]["kind"])
        with self.assertRaisesRegex(store.ChangesetError, "not every member"):
            store.release_approval(self.a["run_dir"], token)
        retry = self.begin()
        self.assertEqual(token, retry)
        self.accept(self.a, retry)
        self.accept(self.b, retry)
        store.release_approval(self.a["run_dir"], retry)
        self.assertIsNone(barrier.refusal(self.a))
        self.assertIsNone(barrier.refusal(self.b))

    def test_contract_change_withdraws_the_release_before_checkpoint_commit(self):
        token = self.release()
        changed = copy.deepcopy(self.b)
        changed["goal_contract"]["approval_status"] = "draft"
        path = Path(changed["run_dir"]) / "state.json"
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            with barrier.checkpoint(path, changed):
                self.assertTrue(barrier.refusal(self.a), "hold is durable before the new state can commit")
                raise RuntimeError("interrupted")
        self.assertTrue(barrier.refusal(self.a))
        with self.assertRaisesRegex(store.ChangesetError, "not every member"):
            store.release_approval(self.a["run_dir"], token)

    def test_new_member_withdraws_release_and_stale_set_token_cannot_release(self):
        token = self.release()
        store.reserve(
            source_workspace=self.b["workspace"],
            source_run_dir=self.b["run_dir"],
            target="history",
            workspace=self.root / "history",
            brief="Record it",
        )
        self.assertTrue(barrier.refusal(self.a))
        with self.assertRaisesRegex(store.ChangesetError, "membership or plans changed"):
            store.release_approval(self.a["run_dir"], token)

    def test_standalone_approval_releases_only_that_exact_plan_and_turn(self):
        self.accept(self.a, self.begin())
        barrier.approve(self.a, identity.token(self.a["goal_contract"]), None)
        self.save(self.a)
        self.assertIsNone(barrier.refusal(self.a))
        self.assertTrue(barrier.refusal(self.b))
        self.a["task"] = "A new follow-up"
        self.save(self.a)
        self.assertTrue(barrier.refusal(self.a))

    def test_reapproval_cannot_bypass_another_pause_or_a_new_planning_turn(self):
        self.accept(self.a, self.begin())
        for status in ("PAUSED_COST_LIMIT", "PAUSED_PERMISSION", "PAUSED_UNCERTAIN_STAGE", "TASK_COMPLETE"):
            self.a["status"] = status
            self.assertFalse(barrier.held_approval(self.a), status)
        self.a.update(status="RUNNING", phase="PLANNING")
        self.assertFalse(barrier.held_approval(self.a))

    def test_same_text_followup_is_a_different_approval_turn(self):
        token = self.release()
        self.a["turns"] = [{"task": self.a["task"]}]
        self.save(self.a)
        self.assertTrue(barrier.refusal(self.b))
        with self.assertRaisesRegex(store.ChangesetError, "not every member"):
            store.release_approval(self.a["run_dir"], token)

    def test_parallel_worker_obeys_parent_release_without_republishing_it(self):
        self.release()
        child = copy.deepcopy(self.a)
        child["parent_run"] = self.a["run_dir"]
        child["run_dir"] = str(Path(self.a["run_dir"]) / "orchestration" / "child")
        self.assertIsNone(barrier.refusal(child))
        record = store.find(self.a["run_dir"])
        with barrier.checkpoint(Path(child["run_dir"]) / "state.json", child):
            pass
        self.assertEqual(record, store.find(self.a["run_dir"]))
        self.b["goal_contract"]["approval_status"] = "draft"
        self.save(self.b)
        self.assertTrue(barrier.refusal(child))

    def test_missing_set_during_release_is_a_clean_refusal(self):
        token = self.begin()
        store.path().unlink()
        with self.assertRaisesRegex(store.ChangesetError, "membership or plans changed"):
            store.release_approval(self.a["run_dir"], token)

    def test_repeated_retry_receives_an_ordinary_authenticated_goal_request(self):
        token = self.begin()
        self.accept(self.a, token)
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                self.begin()  # the prior preparation stopped before approving the second member
                human.queue(
                    self.a,
                    "goal_approval",
                    barrier.reapproval_origin(self.a),
                    status="AWAITING_GOAL_APPROVAL",
                    next_stage="terra",
                )
                self.assertEqual("escalate", human.evaluate(self.a))
                request = human.current(self.a)
                self.assertEqual("goal_approval", request["scope"])
                barrier.approve(self.a, identity.token(self.a["goal_contract"]), token)
                runner.finish_human_action(self.a, request)
                self.save(self.a)

    def test_resolver_reapproval_cannot_replace_an_unrelated_stop(self):
        self.accept(self.a, self.begin())
        for status in ("PAUSED_COST_LIMIT", "PAUSED_PERMISSION_DENIED", "TASK_COMPLETE"):
            with self.subTest(status=status):
                candidate = copy.deepcopy(self.a)
                candidate["status"] = status
                human.queue(
                    candidate,
                    "goal_approval",
                    {"stage": "changeset_reapproval"},
                    status="AWAITING_GOAL_APPROVAL",
                    next_stage="terra",
                )
                self.assertEqual("reject", human.evaluate(candidate))
                self.assertIsNone(human.current(candidate))


if __name__ == "__main__":
    unittest.main()
