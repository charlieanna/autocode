"""The change-set record links runs across repositories without starting any (#2066, step 4)."""

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
