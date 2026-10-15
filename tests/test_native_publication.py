"""Original sink ordering controls; controlled factory rows grant no native proof."""

import json
import tempfile
import unittest
from contextlib import nullcontext
from copy import deepcopy  # noqa: F401 - preserve existing import behavior
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import autocode_native_parent as parent
import autocode_parent_boundary as boundary
import autocode_util as util
import autocode_visual_factory as factories
import autocode_visual_protection as protection


class EvidencePublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-evidence-parent-unit-")
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name).resolve()
        self.order = []
        self.handle = object()  # TEST ONLY: concrete production factory checks original typed handles.
        self.factory = object.__new__(factories.NativeVisualFactory)
        self.factory.invalidate_parent_ack = Mock(side_effect=lambda: self.order.append("invalidate"))
        self.factory.evidence_publications = Mock(side_effect=lambda: (self.handle,))
        self.factory.prepare_parent_commit = Mock(side_effect=lambda *a, **kw: self.order.append("prepare") or "intent")
        self.factory.capture_parent_commit = Mock(
            side_effect=lambda *a, **kw: self.order.append("durable ACK") or "ACK"
        )
        self.factory.commit_evidence_publication = Mock(side_effect=lambda *a, **kw: self.order.append("publish"))

        def save(path, value, **hooks):
            value["normalized"] = "actual ordinary sink"
            self.order.append("normalize")
            util.atomic_json(path, value, **hooks)

        self.sink = parent.ParentSink(
            self.run, factory=self.factory, ordinary_write=save, snapshot=lambda value: {"revision": value["source"]}
        )
        self.native = SimpleNamespace(sink=self.sink)
        self.state = {"source": "same original revision"}
        self.candidate = {**self.state, "accepted_visual": "controlled original result"}
        self.path = self.run / "state.json"

    def commit(self, consume):
        return boundary.commit_boundary_candidate(
            self.state,
            self.candidate,
            self.run,
            self.run,
            save=self.sink.write,
            serialized=lambda _: nullcontext(),
            pending=lambda _: [],
            consume=consume,
            commit_pending=lambda value, _, commit: commit(value),
            native=self.native,
        )

    def test_actual_boundary_commits_exact_evidence_only_after_all_side_effects(self):
        def consume(state, *_args, persist, **_kwargs):
            persist(self.path, state)
            self.assertEqual(json.loads(self.path.read_text()), state)
            self.factory.commit_evidence_publication.assert_not_called()
            self.order.append("whole boundary returned")
            return True

        self.commit(consume)
        self.factory.prepare_parent_commit.assert_called_once()
        self.assertEqual((self.handle,), self.factory.prepare_parent_commit.call_args.kwargs["publications"])
        self.factory.commit_evidence_publication.assert_called_once()
        self.assertIs(self.handle, self.factory.commit_evidence_publication.call_args.args[0])
        self.assertEqual(
            ["invalidate", "normalize", "prepare", "durable ACK", "whole boundary returned", "publish"], self.order
        )
        self.assertEqual(json.loads(self.path.read_text()), self.state)

    def test_pre_save_failure_does_not_borrow_prior_candidate_or_publish(self):
        self.sink.write(self.path, self.state)
        previous = self.path.read_bytes()

        def consume(*_args, **_kwargs):
            raise ValueError("controlled pre-save failure")

        with self.assertRaisesRegex(ValueError, "pre-save failure"):
            self.commit(consume)
        self.assertEqual(previous, self.path.read_bytes())
        self.factory.commit_evidence_publication.assert_not_called()
        self.assertIsNone(self.sink.durable_candidate)

    def test_post_save_failure_preserves_new_candidate_and_holds_publication(self):
        def consume(state, *_args, persist, **_kwargs):
            persist(self.path, state)
            raise OSError("controlled post-save side effect")

        with self.assertRaises(util.JSONCommitUncertain) as caught:
            self.commit(consume)
        self.assertTrue(caught.exception.replaced)
        self.assertTrue(caught.exception.durable)
        self.assertEqual(json.loads(self.path.read_text()), caught.exception.candidate)
        self.assertEqual(caught.exception.candidate, self.state)
        self.factory.commit_evidence_publication.assert_not_called()
        self.assertTrue(self.sink.blocked)

    def test_ack_failure_keeps_exact_durable_candidate_without_publication(self):
        self.factory.capture_parent_commit.side_effect = OSError("controlled acknowledgement failure")
        with self.assertRaises(util.JSONCommitUncertain) as caught:
            self.commit(lambda *_args, **_kwargs: False)
        self.assertEqual(json.loads(self.path.read_text()), caught.exception.candidate)
        self.factory.commit_evidence_publication.assert_not_called()

    def test_changed_publication_tuple_after_save_refuses_before_any_commit(self):
        with self.sink.boundary(publications=(self.handle,)):
            self.sink.write(self.path, self.candidate)
        with self.assertRaises(util.JSONCommitUncertain):
            self.sink.publish(self.candidate, publications=(object(),))
        self.factory.commit_evidence_publication.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text()), self.candidate)

    def test_publication_error_preserves_actual_candidate_and_blocks_restart(self):
        self.factory.commit_evidence_publication.side_effect = OSError("controlled evidence commit failure")
        with self.assertRaises(util.JSONCommitUncertain) as caught:
            self.commit(lambda *_args, **_kwargs: False)
        self.assertEqual(json.loads(self.path.read_text()), caught.exception.candidate)
        self.assertTrue(self.sink.blocked)
        reopened = parent.ParentSink(
            self.run, factory=self.factory, ordinary_write=util.atomic_json, snapshot=lambda value: {}
        )
        with self.assertRaises(util.Paused):
            reopened.require_ready()


class OriginalEvidenceHandleTests(unittest.TestCase):
    def setUp(self):
        self.factory = object.__new__(factories.NativeVisualFactory)
        self.protection = object.__new__(protection.NativeProtection)
        self.factory.protection = self.protection
        self.factory._checkpoint_publications = {}
        self.protection._assignments = {}
        self.protection._publications = {}

        def committed(handle, **kwargs):
            del self.protection._publications[handle.nonce]
            return "actual controlled journal ID"

        self.protection.commit_parent_publication = Mock(side_effect=committed)

    def handle(self, kind, nonce):
        handle = protection.PreparedParentPublication(
            self.protection, nonce, "controlled original row", json.dumps({"selector": {"kind": kind}}).encode()
        )
        self.protection._publications[nonce] = handle
        return handle

    def test_only_exact_original_review_and_checkpoint_handles_are_selected(self):
        review = self.handle("reviews", "original review")
        checkpoint = self.handle("checkpoints", "original checkpoint")
        with self.assertRaisesRegex(util.Paused, "unsupported or foreign"):
            self.factory.evidence_publications()
        self.factory._checkpoint_publications[checkpoint.nonce] = checkpoint
        self.assertEqual((review, checkpoint), self.factory.evidence_publications())
        foreign = protection.PreparedParentPublication(object(), review.nonce, review.row_id, review.body)
        self.protection._publications[review.nonce] = foreign
        with self.assertRaisesRegex(util.Paused, "foreign native evidence"):
            self.factory.evidence_publications()

    def test_review_commit_consumes_exact_handle_and_refuses_foreign_or_replay(self):
        original = self.handle("reviews", "original review")
        foreign = protection.PreparedParentPublication(self.protection, original.nonce, original.row_id, original.body)
        arguments = {
            "parent_commit": object(),
            "committed_candidate": {"controlled": "durable"},
            "snapshot": {"revision": "original"},
        }
        with self.assertRaisesRegex(util.Paused, "missing exact local"):
            self.factory.commit_evidence_publication(foreign, **arguments)
        self.protection.commit_parent_publication.assert_not_called()
        self.assertEqual(
            "actual controlled journal ID", self.factory.commit_evidence_publication(original, **arguments)
        )
        self.protection.commit_parent_publication.assert_called_once_with(original, **arguments)
        with self.assertRaisesRegex(util.Paused, "missing exact local"):
            self.factory.commit_evidence_publication(original, **arguments)

    def test_checkpoint_commit_requires_factory_preparation_and_retires_both_handles(self):
        original = self.handle("checkpoints", "original checkpoint")
        arguments = {
            "parent_commit": object(),
            "committed_candidate": {"controlled": "durable"},
            "snapshot": {"revision": "original"},
        }
        with self.assertRaisesRegex(util.Paused, "unsupported or foreign"):
            self.factory.commit_evidence_publication(original, **arguments)
        self.factory._checkpoint_publications[original.nonce] = original
        self.factory.commit_evidence_publication(original, **arguments)
        self.assertNotIn(original.nonce, self.protection._publications)
        self.assertNotIn(original.nonce, self.factory._checkpoint_publications)
        with self.assertRaisesRegex(util.Paused, "missing exact local"):
            self.factory.commit_evidence_publication(original, **arguments)
