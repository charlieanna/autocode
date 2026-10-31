"""The change-set record links runs across repositories without starting any (#2066, step 4)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_changeset_store as store


class ChangesetStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="changeset-store-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        patcher = mock.patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "home")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = self.root / "api" / ".autocode" / "runs" / "one"
        self.member = self.root / "history" / ".autocode" / "runs" / "two"

    def reserve(self, target="history"):
        return store.reserve(
            source_workspace=self.root / "api",
            source_run_dir=self.source,
            target=target,
            workspace=self.root / "history",
            brief="In history: record it.",
        )

    def test_checkpoint_contention_longer_than_one_second_keeps_the_same_atomic_mutation(self):
        elapsed = [0.0]

        def acquire(handle):
            if elapsed[0] < 1.5:
                raise BlockingIOError()

        with (mock.patch.object(store.time, "monotonic", side_effect=lambda: elapsed[0]),
              mock.patch.object(store.time, "sleep", side_effect=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)),
              mock.patch.object(store.util, "lock_file", side_effect=acquire)):
            record = self.reserve()
        self.assertEqual(1, len(store.records()))
        self.assertEqual(record["id"], store.find(self.source)["id"])
        self.assertGreaterEqual(elapsed[0], 1.5)

    def test_persistent_checkpoint_contention_is_bounded_and_never_writes(self):
        elapsed = [0.0]
        with (mock.patch.object(store.time, "monotonic", side_effect=lambda: elapsed[0]),
              mock.patch.object(store.time, "sleep", side_effect=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)),
              mock.patch.object(store.util, "lock_file", side_effect=BlockingIOError)):
            with self.assertRaisesRegex(store.ChangesetError, "busy"):
                self.reserve()
        self.assertLessEqual(elapsed[0], 10.1)
        self.assertFalse(store.path().exists())

    def test_both_runs_of_a_linked_pair_see_each_other(self):
        record = self.reserve()
        self.assertEqual(store.changeset_id(self.source), record["id"])
        store.bind(record["id"], "history", self.member)
        [other] = store.linked_for(self.source)
        self.assertEqual(
            {"changeset": record["id"], "target": "history", "role": "member"},
            {key: other[key] for key in ("changeset", "target", "role")},
        )
        self.assertEqual(str(self.member), other["run_dir"])
        [back] = store.linked_for(self.member)
        self.assertEqual(("api", "source", str(self.source)), (back["target"], back["role"], back["run_dir"]))
        self.assertEqual([], store.linked_for(self.root / "elsewhere" / ".autocode" / "runs" / "three"))

    def test_a_target_is_linked_once(self):
        self.reserve()
        with self.assertRaisesRegex(store.ChangesetError, "already linked"):
            self.reserve()
        self.assertEqual(1, len(store.records()[0]["members"]), "the refused link left nothing behind")

    def test_member_discovery_extends_the_same_canonical_set(self):
        record = self.reserve()
        store.bind(record["id"], "history", self.member)
        third = self.root / "billing" / ".autocode" / "runs" / "three"
        linked = store.reserve(
            source_workspace=self.root / "history",
            source_run_dir=self.member,
            target="billing",
            workspace=self.root / "billing",
            brief="Bill it.",
        )
        self.assertEqual(record["id"], linked["id"])
        store.bind(linked["id"], "billing", third)
        self.assertEqual(1, len(store.records()))
        for run in (self.source, self.member, third):
            self.assertEqual(record["id"], store.find(run)["id"])
            self.assertEqual(record["id"], store.memberships()[str(run)])
            self.assertEqual(2, len(store.linked_for(run)))
        with self.assertRaisesRegex(store.ChangesetError, "already belongs"):
            store.reserve(
                source_workspace=self.root / "history",
                source_run_dir=self.member,
                target="api-alias",
                workspace=self.root / "api",
                brief="Repeat it.",
            )

    def test_stale_mutation_cannot_replace_a_newer_record(self):
        record = self.reserve()
        old = record["revision"]
        store.bind(record["id"], "history", self.member)
        with self.assertRaisesRegex(store.ChangesetError, "changed during"):
            store.set_fields(record["id"], {"check_result": {"passed": True}}, expected_revision=old)
        self.assertNotIn("check_result", store.find(self.source))

    def test_unrelated_run_is_not_blocked_by_an_overlapping_legacy_set(self):
        record = self.reserve()
        store.bind(record["id"], "history", self.member)
        document = json.loads(store.path().read_text())
        document["changesets"]["legacy-overlap"] = {
            "id": "legacy-overlap",
            "source": {"workspace": str(self.root / "history"), "run_dir": str(self.member)},
            "members": [],
        }
        store.path().write_text(json.dumps(document))
        self.assertIsNone(store.find(self.root / "unrelated" / ".autocode" / "runs" / "one"))
        with self.assertRaisesRegex(store.ChangesetError, "overlapping legacy"):
            store.find(self.member)
        with self.assertRaisesRegex(store.ChangesetError, "overlapping legacy"):
            store.records()

    def test_a_link_whose_run_never_started_can_be_made_again(self):
        record = self.reserve()
        store.release(record["id"], "history")
        self.assertEqual([], store.records())
        self.reserve()

    def test_an_unreadable_record_hides_links_but_refuses_changes(self):
        self.reserve()
        store.path().write_text("{not json")
        self.assertEqual([], store.linked_for(self.source), "a status view still renders")
        with self.assertRaises(store.ChangesetError):
            self.reserve("billing")


if __name__ == "__main__":
    unittest.main()
