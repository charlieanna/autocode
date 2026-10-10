"""Offline crash recovery and competing-server tests; no real provider is called."""

import hashlib
import json
import select
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOOLS / "dashboard"), str(TOOLS)]
import dashboard_continuous as continuous
from dashboard_continuous import ContinuousConversationStore


class QueuedPool:
    """Model a process that saved dispatches but crashed before its workers ran."""

    def __init__(self, *args, **kwargs):
        self.jobs = []

    def submit(self, callback, *args):
        self.jobs.append((callback, args))
        return Future()

    def shutdown(self, **kwargs):
        pass


def structured(messages, route, workdir):
    user = next(row for row in reversed(messages) if row["role"] == "user")
    return json.dumps(
        {
            "contract_version": 1,
            "kind": "autocode.planner-structured-draft",
            "goal": "Preserve a reliable conversation",
            "requirements": ["Keep the exact saved turn"],
            "milestones": ["Implement and verify"],
            "parallelism": [],
            "unresolved_questions": [],
            "source_revision": {
                "requirements_revision": sum(row["role"] == "user" for row in messages),
                "logical_turn_id": user["logical_turn_id"],
            },
            "attribution": {"role": "planner", "model": route["model"], "reasoning_effort": route["reasoning_effort"]},
            "freshness": {"state": "fresh", "updated_at": "2026-09-30T00:00:00+00:00"},
        }
    )


class ContinuousRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.calls = []

    def seed(self):
        with patch.object(continuous, "ThreadPoolExecutor", QueuedPool):
            store = ContinuousConversationStore(root=self.root, provider=self.gatherer, planner=self.planner)
            result = store.create("Preserve the saved human turn", request_id="stable-request")
        # Fault injection: process exit releases OS leases, leaving its durable
        # pre-dispatch document unchanged. No worker in QueuedPool ever ran.
        for lease in store._leases.values():
            lease.close()
        store.close()
        path = self.root / (result["id"] + ".json")
        return path, json.loads(path.read_text())

    def gatherer(self, messages, model, workdir):
        self.calls.append(("gatherer", messages[-1]["id"], model))
        return "The exact saved message is retained."

    def planner(self, messages, route, workdir):
        self.calls.append(("planner", messages[-1]["id"], route["model"]))
        return structured(messages, route, workdir)

    def open(self, **kwargs):
        store = ContinuousConversationStore(root=self.root, provider=self.gatherer, planner=self.planner, **kwargs)
        self.addCleanup(store.close)
        return store

    def capture_both(self, document, corrupt=False):
        turn = document["_active_logical_turn"]
        text = "Saved reply from before the crash."
        gatherer = document["_dispatches"][turn]
        gatherer.update(
            state="RESULT_CAPTURED",
            result={"text": text, "sha256": "broken" if corrupt else hashlib.sha256(text.encode()).hexdigest()},
        )
        planner = document["_planner_dispatches"][turn]
        raw = structured(document["messages"], planner["route"], self.root)
        planner.update(
            state="RESULT_CAPTURED", result={"text": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
        )

    def test_captured_replies_commit_once_without_provider_replay(self):
        path, document = self.seed()
        self.capture_both(document)
        path.write_text(json.dumps(document))
        store = self.open()
        store.close()
        public = store.get(document["id"])
        self.assertEqual("ready", public["status"])
        self.assertEqual(1, sum(row["role"] == "assistant" for row in public["messages"]))
        self.assertEqual(1, sum(row["status"] == "current" for row in public["plan_drafts"]))
        again = self.open()
        again.close()
        self.assertEqual(public["messages"], again.get(document["id"])["messages"])
        self.assertEqual([], self.calls)

    def test_indeterminate_dispatch_survives_restart_without_retry(self):
        path, document = self.seed()
        turn = document["_active_logical_turn"]
        document["_dispatches"][turn]["state"] = "PROCESS_STARTING"
        document["_planner_dispatches"][turn]["state"] = "PROCESS_STARTED"
        path.write_text(json.dumps(document))
        store = self.open()
        self.assertEqual("uncertain", store.get(document["id"])["status"])
        with self.assertRaisesRegex(ValueError, "uncertain"):
            store.retry(document["id"])
        with self.assertRaisesRegex(ValueError, "uncertain"):
            store.send(document["id"], "A duplicate?", request_id="new-request")
        store.close()
        self.assertEqual([], self.calls)

    def test_safe_pre_dispatch_retry_preserves_user_identity(self):
        path, document = self.seed()
        store = self.open()
        # Drain only the recovery Planner before explicitly retrying the saved
        # human turn; these futures perform injected in-process work only.
        store.pool.submit(lambda: None).result(timeout=5)
        store.retry(document["id"])
        store.close()
        public = store.get(document["id"])
        users = [row for row in public["messages"] if row["role"] == "user"]
        self.assertEqual([document["messages"][0]["id"]], [row["id"] for row in users])
        self.assertEqual("ready", public["status"])
        self.assertEqual(1, sum(role == "gatherer" for role, *_ in self.calls))
        self.assertEqual(1, sum(role == "planner" for role, *_ in self.calls))

    def test_corrupt_capture_does_not_replay_or_prevent_store_opening(self):
        path, document = self.seed()
        self.capture_both(document, corrupt=True)
        path.write_text(json.dumps(document))
        store = self.open()
        public = store.get(document["id"])
        self.assertEqual("uncertain", public["status"])
        self.assertIn("integrity", public["error"])
        self.assertFalse(any(row["role"] == "assistant" for row in public["messages"]))
        with self.assertRaisesRegex(ValueError, "integrity"):
            store.retry(document["id"])
        store.close()
        self.assertEqual([], self.calls)

    def test_captured_planner_result_survives_commit_failure_and_restarts(self):
        path, document = self.seed()
        self.capture_both(document)
        path.write_text(json.dumps(document))
        with patch.object(
            ContinuousConversationStore, "_commit_structured", side_effect=OSError("Injected commit failure")
        ):
            interrupted = self.open()
            interrupted.close()
        self.assertFalse(any(row["status"] == "current" for row in interrupted.get(document["id"])["plan_drafts"]))
        resumed = self.open()
        resumed.close()
        drafts = resumed.get(document["id"])["plan_drafts"]
        self.assertEqual(1, sum(row["status"] == "current" for row in drafts))
        self.assertEqual([], self.calls)

    def test_safe_failed_planner_recovery_commits_without_an_extra_restart(self):
        path, document = self.seed()
        turn = document["_active_logical_turn"]
        self.capture_both(document)
        document["_planner_dispatches"][turn].update(state="SAFE_NOT_DISPATCHED", result=None)
        document["plan_drafts"][0]["status"] = "failed"
        document["plan_drafts"][0]["freshness"]["state"] = "failed"
        path.write_text(json.dumps(document))
        store = self.open()
        store.close()
        self.assertEqual(1, sum(row["status"] == "current" for row in store.get(document["id"])["plan_drafts"]))
        self.assertEqual(["planner"], [role for role, *_ in self.calls])

    def test_restoring_archived_interrupted_conversation_reconciles_before_retry(self):
        path, document = self.seed()
        document["archived_at"] = "2026-09-30T00:00:00+00:00"
        path.write_text(json.dumps(document))
        store = self.open()
        self.assertEqual([], store.list())
        restored = store.archive(document["id"], "restore")
        self.assertEqual("error", restored["status"])
        store.retry(document["id"])
        store.close()
        self.assertEqual("ready", store.get(document["id"])["status"])
        self.assertEqual(1, sum(role == "gatherer" for role, *_ in self.calls))
        self.assertEqual(1, sum(role == "planner" for role, *_ in self.calls))

    def test_superseded_prepared_planner_does_not_launch_on_new_transcript(self):
        path, document = self.seed()
        old_turn = document["_active_logical_turn"]
        document["requirements"]["revisions"].append({"revision": 2, "source": {"logical_turn_id": "newer-turn"}})
        document["_dispatches"][old_turn]["state"] = "UNCERTAIN"
        path.write_text(json.dumps(document))
        store = self.open()
        store.close()
        self.assertEqual([], self.calls)
        self.assertFalse(any(row["status"] == "current" for row in store.get(document["id"])["plan_drafts"]))

    def test_callback_exception_cannot_be_replayed_as_pre_dispatch_failure(self):
        def crash(messages, model, workdir):
            self.calls.append(("external-effect", messages[-1]["id"], model))
            raise RuntimeError("connection lost after remote acceptance")

        store = ContinuousConversationStore(root=self.root, provider=crash, planner=self.planner)
        result = store.create("A request with an ambiguous callback result", request_id="one")
        store.close()
        self.assertEqual("uncertain", store.get(result["id"])["status"])
        fresh = self.open()
        with self.assertRaisesRegex(ValueError, "uncertain"):
            fresh.retry(result["id"])
        fresh.close()
        self.assertEqual(1, sum(role == "external-effect" for role, *_ in self.calls))

    def test_saved_dispatch_route_is_used_even_if_future_configuration_changes(self):
        path, document = self.seed()
        turn = document["_active_logical_turn"]
        saved_model = document["_dispatches"][turn]["route"]["model"]
        document["configured_routes"]["requirements_gatherer"]["model"] = "future/model"
        path.write_text(json.dumps(document))
        # Route policy is bypassed only at this test boundary to reproduce a
        # future configuration edit; the saved launch itself must remain bound.
        with patch.object(
            ContinuousConversationStore, "_authorized_dispatch_routes", lambda self, doc: doc["configured_routes"]
        ):
            store = self.open()
            store.retry(document["id"])
            store.close()
        self.assertEqual([saved_model], [model for role, _, model in self.calls if role == "gatherer"])
        reply = next(row for row in store.get(document["id"])["messages"] if row["role"] == "assistant")
        self.assertEqual(saved_model, reply["execution"]["model"])

    def test_second_process_cannot_repeat_owned_planner_or_gatherer(self):
        helper = self.root / "owned_callback.py"
        helper.write_text(
            """import json,sys
from pathlib import Path
sys.path[:0]=PATHS
from dashboard_continuous import ContinuousConversationStore
from test_continuous_recovery import structured
root=Path(sys.argv[1])
def planner(messages,route,workdir):
 with (root/'calls').open('a') as f: f.write('planner\\n')
 print('owned',flush=True)
 sys.stdin.readline()
 return structured(messages,route,workdir)
def gatherer(messages,model,workdir):
 with (root/'calls').open('a') as f: f.write('gatherer\\n')
 return 'One reply'
store=ContinuousConversationStore(root=root,provider=gatherer,planner=planner)
result=store.create('One logical turn',request_id='stable')
store.close()
print(result['id'],flush=True)
""".replace("PATHS", repr([str(TOOLS / "dashboard"), str(TOOLS), str(Path(__file__).parent)]))
        )
        child = subprocess.Popen(
            [sys.executable, str(helper), str(self.root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(lambda: child.poll() is None and child.kill())
        ready, _, _ = select.select([child.stdout], [], [], 10)
        self.assertTrue(ready, "Injected worker did not acquire its delivery lease")
        self.assertEqual("owned", child.stdout.readline().strip())
        fresh = self.open()
        documents = fresh.list()
        self.assertEqual(1, len(documents))
        self.assertEqual("thinking", fresh.retry(documents[0]["id"])["status"])
        output, errors = child.communicate("release\n", timeout=10)
        self.assertEqual(0, child.returncode, errors)
        self.assertEqual(["planner", "gatherer"], (self.root / "calls").read_text().splitlines())
        public = fresh.get(output.strip())
        self.assertEqual(1, sum(row["role"] == "assistant" for row in public["messages"]))
        fresh.close()
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()
