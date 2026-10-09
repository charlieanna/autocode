"""Public evidence pinning for the runner's mutable metadata."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import autocode_support as support


class EvidenceSnapshotTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="evidence-snapshot-")
        self.addCleanup(scratch.cleanup)
        self.workspace = Path(scratch.name).resolve()
        self.run = self.workspace / ".autocode" / "runs" / "owned-run"
        self.run.mkdir(parents=True)
        self.activity = self.run / "activity.jsonl"
        self.prefix = b'{"event":"validator-completed"}\n'
        self.activity.write_bytes(self.prefix)

    def pins(self, *refs):
        return support.evidence_hashes(refs or [str(self.activity)], self.workspace, self.run)

    def test_own_activity_is_content_addressed_and_complete(self):
        pins = self.pins()
        frozen = Path(next(iter(pins)))
        digest = hashlib.sha256(self.prefix).hexdigest()
        self.assertEqual(self.run / "evidence" / f"run-activity-{digest}.jsonl", frozen)
        self.assertEqual(self.prefix, frozen.read_bytes())
        self.assertEqual(digest, pins[str(frozen)])
        self.assertEqual([frozen], list(frozen.parent.iterdir()))

    def test_live_append_leaves_accepted_prefix_and_pin_unchanged(self):
        pins = self.pins()
        frozen = Path(next(iter(pins)))
        with self.activity.open("ab") as handle:
            handle.write(b'{"event":"completion-rework"}\n')
        self.assertEqual(self.prefix, frozen.read_bytes())
        self.assertEqual(pins[str(frozen)], support.file_hash(frozen))
        self.assertNotEqual(pins[str(frozen)], support.file_hash(self.activity))

    def test_identical_prefix_reuses_snapshot_without_rewriting_it(self):
        pins = self.pins()
        frozen = Path(next(iter(pins)))
        before = frozen.stat()
        self.assertEqual(pins, self.pins())
        after = frozen.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual([frozen], list(frozen.parent.iterdir()))

    def test_new_prefix_gets_a_new_snapshot_and_keeps_the_old_pin(self):
        first = self.pins()
        self.activity.write_bytes(self.prefix + b'{"event":"builder-repair"}\n')
        second = self.pins()
        self.assertNotEqual(first, second)
        self.assertEqual(2, len(list((self.run / "evidence").glob("run-activity-*.jsonl"))))
        for path, digest in {**first, **second}.items():
            self.assertEqual(digest, support.file_hash(path))
        self.assertEqual(self.prefix, Path(next(iter(first))).read_bytes())
        self.assertEqual(self.activity.read_bytes(), Path(next(iter(second))).read_bytes())

    def test_relative_fragment_and_symlink_aliases_pin_the_same_own_log(self):
        alias = self.workspace / "activity-alias.jsonl"
        alias.symlink_to(self.activity)
        relative = self.activity.relative_to(self.workspace)
        refs = [
            str(self.activity),
            f"{relative}#validator",
            str(alias),
            str(self.run / ".." / self.run.name / "activity.jsonl"),
        ]
        self.assertEqual(self.pins(), self.pins(*refs))

    def test_ordinary_project_activity_is_pinned_directly_and_mutation_changes_hash(self):
        activity = self.workspace / "activity.jsonl"
        activity.write_bytes(self.prefix)
        first = self.pins(str(activity))
        self.assertEqual({str(activity): support.file_hash(activity)}, first)
        self.assertFalse((self.run / "evidence").exists())
        activity.write_bytes(b'{"event":"changed-project-output"}\n')
        self.assertNotEqual(first, self.pins(str(activity)))

    def test_mutated_frozen_copy_breaks_its_pin_and_is_rejected_on_repin(self):
        pins = self.pins()
        frozen = Path(next(iter(pins)))
        frozen.write_bytes(b'{"event":"forged"}\n')
        self.assertNotEqual(pins[str(frozen)], support.file_hash(frozen))
        with self.assertRaisesRegex(ValueError, "Frozen run-activity evidence differs from its content hash"):
            self.pins()
        self.assertEqual(b'{"event":"forged"}\n', frozen.read_bytes())

    def test_run_state_keeps_existing_filename_and_corruption_rejection(self):
        state = self.run / "state.json"
        original = b'{"stage":"validation"}'
        state.write_bytes(original)
        pins = self.pins(str(state))
        frozen = Path(next(iter(pins)))
        self.assertEqual(f"run-state-{hashlib.sha256(original).hexdigest()}.json", frozen.name)
        state.write_bytes(b'{"stage":"review"}')
        self.assertEqual(original, frozen.read_bytes())
        self.assertEqual(pins[str(frozen)], support.file_hash(frozen))
        state.write_bytes(original)
        frozen.write_bytes(b'{"stage":"forged"}')
        with self.assertRaisesRegex(ValueError, "Frozen run-state evidence differs from its content hash"):
            self.pins(str(state))
