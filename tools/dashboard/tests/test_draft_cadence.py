"""Public conversation operations with deterministic offline provider delivery."""

import json
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]
import autocode_conversation as protocol
import dashboard_continuous as continuous


class Queue:
    def __init__(self, *args, **kwargs):
        self.jobs = []
        self.reject = False

    def submit(self, callback, *args):
        if self.reject:
            raise RuntimeError("Offline worker unavailable")
        self.jobs.append((callback, args))
        return Future()

    def drain(self):
        while self.jobs:
            callback, args = self.jobs.pop(0)
            callback(*args)

    def shutdown(self, **kwargs):
        pass


def structured(messages, route):
    user = next(row for row in reversed(messages) if row["role"] == "user")
    return json.dumps(
        {
            "contract_version": 1,
            "kind": "autocode.planner-structured-draft",
            "goal": "Keep all answers",
            "requirements": ["Retain saved messages"],
            "milestones": ["Implement and verify"],
            "parallelism": [],
            "unresolved_questions": [],
            "source_revision": {
                "requirements_revision": sum(row["role"] == "user" for row in messages),
                "logical_turn_id": user["logical_turn_id"],
            },
            "attribution": {"role": "planner", "model": route["model"], "reasoning_effort": route["reasoning_effort"]},
            "freshness": {"state": "fresh", "updated_at": "2026-10-03T00:00:00+00:00"},
        }
    )


class DraftCadenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.calls = []
        worker = patch.object(continuous, "ThreadPoolExecutor", Queue)
        worker.start()
        self.addCleanup(worker.stop)
        self.store = self.open()

    def open(self):
        store = continuous.ContinuousConversationStore(root=self.root, provider=self.gatherer, planner=self.planner)
        self.addCleanup(store.close)
        return store

    def gatherer(self, messages, model, workdir):
        self.calls.append(("gatherer", messages[-1]["text"], model))
        return "Your answer is saved. What should happen next?"

    def planner(self, messages, route, workdir):
        self.calls.append(("planner", messages[-1]["text"], route.copy()))
        return structured(messages, route)

    def create(self):
        doc = self.store.create("Build a workspace", request_id="create-cadence")
        self.store.pool.drain()
        return self.store.get(doc["id"])

    def send(self, doc, text):
        doc = self.store.send(doc["id"], text)
        self.store.pool.drain()
        return self.store.get(doc["id"])

    def refresh(self, doc, request="refresh-cadence"):
        update = doc["draft_update"]
        return self.store.refresh_draft(
            doc["id"],
            requirements_revision=update["requirements_revision"],
            logical_turn_id=update["logical_turn_id"],
            request_id=request,
        )

    def test_ten_small_turns_make_four_draft_calls_and_preserve_every_reply(self):
        doc = self.create()
        for index in range(2, 11):
            doc = self.send(doc, f"Answer {index}")
        self.assertEqual(10, sum(kind == "gatherer" for kind, *_ in self.calls))
        self.assertEqual(4, sum(kind == "planner" for kind, *_ in self.calls))
        self.assertEqual(10, sum(row["role"] == "assistant" for row in doc["messages"]))
        generated = [row for row in doc["plan_drafts"] if row.get("attribution")]
        self.assertEqual([1, 4, 7, 10], [row["requirements_revision"] for row in generated])
        self.assertEqual(
            [[1], [2, 3, 4], [5, 6, 7], [8, 9, 10]],
            [[source["requirements_revision"] for source in row["freshness"]["source_messages"]] for row in generated],
        )
        transported = protocol.validate_handoff(protocol.handoff_from_document(doc))
        self.assertEqual(doc["plan_drafts"], transported["plan_drafts"])
        self.assertTrue(all(row["freshness"]["source_messages"] for row in doc["plan_drafts"]))

    def test_explicit_refresh_is_revision_bound_idempotent_and_does_not_add_a_user_answer(self):
        doc = self.send(self.create(), "Use SQLite")
        self.assertTrue(doc["draft_update"]["can_refresh"])
        messages = doc["messages"]
        self.refresh(doc)
        self.refresh(doc)
        self.store.pool.drain()
        result = self.store.get(doc["id"])
        self.assertEqual(2, sum(kind == "planner" for kind, *_ in self.calls))
        self.assertEqual(messages, result["messages"])
        self.assertEqual("fresh", result["plan_drafts"][-1]["freshness"]["state"])
        self.assertEqual("Use SQLite", result["plan_drafts"][-1]["freshness"]["source_messages"][0]["excerpt"])
        again = self.send(result, "Keep a dark theme")
        with self.assertRaisesRegex(ValueError, "different draft revision"):
            self.refresh(again)
        self.assertEqual(2, sum(kind == "planner" for kind, *_ in self.calls))

    def test_stale_target_archived_and_attached_conversations_refuse_refresh(self):
        doc = self.send(self.create(), "Use SQLite")
        newer = self.send(doc, "Keep a dark theme")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.refresh(doc)
        self.store.archive(doc["id"], "archive")
        with self.assertRaises(ValueError):
            self.refresh(newer)
        self.store.archive(doc["id"], "restore")
        self.store.update(doc["id"], attachment={"status": "linked"})
        with self.assertRaises(ValueError):
            self.refresh(newer)
        self.assertEqual(1, sum(kind == "planner" for kind, *_ in self.calls))

    def test_held_update_survives_restart_and_retry_cannot_bypass_batching(self):
        doc = self.send(self.create(), "Use SQLite")
        self.store.close()
        self.store = self.open()
        self.store.pool.drain()
        after = self.store.get(doc["id"])
        self.assertEqual(doc["draft_update"], after["draft_update"])
        self.assertEqual(doc["plan_drafts"], after["plan_drafts"])
        with self.assertRaisesRegex(ValueError, "batching answers"):
            self.store.retry(doc["id"])
        self.assertEqual(1, sum(kind == "planner" for kind, *_ in self.calls))

    def test_saved_explicit_release_recovers_once_after_restart_before_dispatch(self):
        doc = self.send(self.create(), "Use SQLite")
        self.refresh(doc)
        self.store.close()  # queued offline worker never ran
        self.store = self.open()
        self.store.pool.drain()
        self.assertEqual("fresh", self.store.get(doc["id"])["plan_drafts"][-1]["freshness"]["state"])
        self.refresh(doc)  # repeat the original id after recovery
        self.store.pool.drain()
        self.assertEqual(2, sum(kind == "planner" for kind, *_ in self.calls))

    def test_worker_unavailable_after_explicit_release_is_safe_and_retryable(self):
        doc = self.send(self.create(), "Use SQLite")
        self.store.pool.reject = True
        saved = self.refresh(doc)
        self.assertEqual("SAFE_NOT_DISPATCHED", saved["planner_delivery"]["state"])
        self.store.pool.reject = False
        self.store.retry(doc["id"])
        self.store.pool.drain()
        self.assertEqual("fresh", self.store.get(doc["id"])["plan_drafts"][-1]["freshness"]["state"])
        self.assertEqual(2, sum(kind == "planner" for kind, *_ in self.calls))

    def test_queued_old_update_does_not_overwrite_newer_answers(self):
        doc = self.send(self.create(), "Use SQLite")
        self.refresh(doc)
        doc = self.store.send(doc["id"], "Use Postgres instead")
        # The queued update counts as in flight (#21): the newer answer joins
        # the one update released when it ends instead of waiting for the cadence.
        self.assertTrue(self.store.get(doc["id"])["draft_update"]["coalesced"])
        self.store.pool.drain()
        latest = self.store.get(doc["id"])
        self.assertFalse(latest["draft_update"]["held"])
        self.assertEqual(
            2, sum(kind == "planner" for kind, *_ in self.calls), "Superseded queued revision cannot spend or commit"
        )
        self.assertEqual(
            ("current", 3), (latest["plan_drafts"][-1]["status"], latest["plan_drafts"][-1]["requirements_revision"])
        )
        self.assertEqual(
            ["Use SQLite", "Use Postgres instead"],
            [row["excerpt"] for row in latest["plan_drafts"][-1]["freshness"]["source_messages"]],
        )
        self.assertEqual("Use Postgres instead", latest["messages"][-2]["text"])
