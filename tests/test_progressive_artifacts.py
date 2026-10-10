"""Offline integrity and immutable publication tests for progressive artifacts."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import autocode_progressive_artifacts as artifacts


class ProgressiveArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)

    def prepare(self, kind="proposal", **changes):
        inputs = dict(
            contract_token="goal-token",
            predecessor_identity=None if kind == "proposal" else "previous-plan",
            candidate_identity="candidate-hash",
            plan_identity="plan-hash",
            source_snapshot_identity="source-revision",
            report={"checks": [{"id": "A", "result": "PASS"}]},
        )
        inputs.update(changes)
        return artifacts.prepare(kind, **inputs)

    def test_all_kinds_round_trip_and_content_address(self):
        for kind in sorted(artifacts.KINDS):
            with self.subTest(kind=kind):
                envelope, identity = self.prepare(kind)
                self.assertEqual(artifacts.persist(self.run, envelope), identity)
                self.assertEqual(artifacts.verify(self.run, identity), envelope)
                self.assertEqual(
                    hashlib.sha256((self.run / identity["path"]).read_bytes()).hexdigest(), identity["sha256"]
                )
                self.assertEqual(identity["version"], 1)

    def test_prepare_is_detached_and_order_independent(self):
        report = {"z": [], "a": {"nested": [1]}}
        envelope, identity = self.prepare(report=report)
        report["a"]["nested"].append(2)
        self.assertEqual(envelope["report"]["a"]["nested"], [1])
        self.assertEqual(self.prepare(report={"a": {"nested": [1]}, "z": []})[1], identity)

    def test_identical_recovery_does_not_replace_inode(self):
        envelope, identity = self.prepare()
        artifacts.persist(self.run, envelope)
        path = self.run / identity["path"]
        inode = path.stat().st_ino
        self.assertEqual(artifacts.persist(self.run, envelope), identity)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_missing_never_scans_or_creates(self):
        envelope, identity = self.prepare()
        with self.assertRaises(FileNotFoundError):
            artifacts.verify(self.run, identity)
        self.assertFalse((self.run / "progressive").exists())
        artifacts.persist(self.run, envelope)
        other = self.prepare(source_snapshot_identity="other-source")[1]
        with self.assertRaises(FileNotFoundError):
            artifacts.verify(self.run, other)

    def test_tamper_and_collision_refuse_overwrite(self):
        envelope, identity = self.prepare()
        artifacts.persist(self.run, envelope)
        path = self.run / identity["path"]
        original = path.read_bytes()
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            artifacts.verify(self.run, identity)
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            artifacts.persist(self.run, envelope)
        self.assertEqual(path.read_bytes(), b"tampered")
        path.write_bytes(json.dumps(envelope).encode())
        with self.assertRaises(ValueError):
            artifacts.persist(self.run, envelope)
        path.write_bytes(original)
        different = self.prepare(report={"different": True})[0]
        with patch.object(artifacts.util, "digest", return_value=identity["sha256"]):
            with self.assertRaisesRegex(ValueError, "collision"):
                artifacts.persist(self.run, different)

    def test_failed_publication_leaves_no_pointer_target_or_temporary(self):
        envelope, identity = self.prepare()
        with patch.object(artifacts.os, "link", side_effect=OSError("injected publish failure")):
            with self.assertRaisesRegex(OSError, "publish failure"):
                artifacts.persist(self.run, envelope)
        self.assertFalse((self.run / identity["path"]).exists())
        self.assertEqual(list((self.run / "progressive").iterdir()), [])
        self.assertEqual(artifacts.persist(self.run, envelope), identity)

    def test_recovery_after_published_but_unacknowledged_write(self):
        envelope, identity = self.prepare("checkpoint")
        real_link = artifacts.os.link

        def interrupted_link(*args, **kwargs):
            real_link(*args, **kwargs)
            raise OSError("injected crash after publication")

        with patch.object(artifacts.os, "link", side_effect=interrupted_link):
            with self.assertRaisesRegex(OSError, "after publication"):
                artifacts.persist(self.run, envelope)
        self.assertEqual(artifacts.persist(self.run, envelope), identity)
        self.assertEqual(artifacts.verify(self.run, identity), envelope)
        self.assertEqual(list((self.run / "progressive").iterdir()), [self.run / identity["path"]])

    def test_every_binding_and_report_changes_identity(self):
        _, original = self.prepare("review")
        for field in (
            "contract_token",
            "predecessor_identity",
            "candidate_identity",
            "plan_identity",
            "source_snapshot_identity",
            "report",
        ):
            with self.subTest(field=field):
                changed = {field: {"verdict": "REJECT"} if field == "report" else "changed"}
                self.assertNotEqual(self.prepare("review", **changed)[1], original)
        self.assertNotEqual(self.prepare("revision")[1], original)

    def test_traversal_absolute_wrong_kind_hash_and_versions(self):
        _, identity = self.prepare()
        for path in (
            "../outside.json",
            "/tmp/outside.json",
            "progressive/../outside.json",
            "progressive/nested/file.json",
            "progressive\\outside.json",
            identity["path"].replace("proposal", "latest"),
            "./" + identity["path"],
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                artifacts.verify(self.run, {**identity, "path": path})
        for change in ({"sha256": "0" * 64}, {"version": 2}, {"version": True}, {"extra": 1}):
            with self.assertRaises(ValueError):
                artifacts.verify(self.run, {**identity, **change})

    def test_symlink_directory_file_and_run_rejected(self):
        envelope, identity = self.prepare()
        outside = self.run / "outside"
        outside.mkdir()
        progressive = self.run / "progressive"
        progressive.symlink_to(outside, target_is_directory=True)
        for operation in (lambda: artifacts.persist(self.run, envelope), lambda: artifacts.verify(self.run, identity)):
            with self.assertRaises(OSError):
                operation()
        self.assertEqual(list(outside.iterdir()), [])
        progressive.unlink()
        progressive.mkdir()
        target = outside / "target"
        target.write_text("untouched")
        (self.run / identity["path"]).symlink_to(target)
        with self.assertRaises(OSError):
            artifacts.verify(self.run, identity)
        with self.assertRaises(OSError):
            artifacts.persist(self.run, envelope)
        self.assertEqual(target.read_text(), "untouched")
        alias = self.run / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            artifacts.persist(alias, envelope)

    def test_invalid_envelopes_and_no_run_dir_bypass(self):
        for kind in ("unknown", "revision", "review", "checkpoint"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.prepare(kind, predecessor_identity=None)
        for changes in (
            {"report": []},
            {"report": {1: "bad"}},
            {"report": {"bad": float("nan")}},
            {"report": {"bad": (1, 2)}},
            {"source_snapshot_identity": ""},
            {"contract_token": None},
        ):
            with self.assertRaises(ValueError):
                self.prepare(**changes)
        envelope, identity = self.prepare()
        for root in (None, "", self.run / "absent"):
            with self.assertRaises((ValueError, OSError)):
                artifacts.persist(root, envelope)
            with self.assertRaises((ValueError, OSError)):
                artifacts.verify(root, identity)
        for changes in ({"version": 2}, {"version": True}, {"extra": "bad"}):
            with self.assertRaises(ValueError):
                artifacts.persist(self.run, {**envelope, **changes})

    def test_forged_hash_cannot_hide_noncanonical_or_wrong_kind(self):
        envelope, identity = self.prepare()
        artifacts.persist(self.run, envelope)
        data = json.dumps(envelope, indent=2).encode()
        digest = hashlib.sha256(data).hexdigest()
        forged = {**identity, "path": f"progressive/proposal-{digest}.json", "sha256": digest}
        (self.run / forged["path"]).write_bytes(data)
        with self.assertRaisesRegex(ValueError, "envelope identity"):
            artifacts.verify(self.run, forged)
        wrong = {**identity, "path": identity["path"].replace("proposal", "review")}
        (self.run / wrong["path"]).write_bytes((self.run / identity["path"]).read_bytes())
        with self.assertRaisesRegex(ValueError, "envelope identity"):
            artifacts.verify(self.run, wrong)


if __name__ == "__main__":
    unittest.main()
