"""The GitHub channel adapter: whose replies count, and only the ones newer than the gate they answer."""

import tempfile
import unittest
from pathlib import Path

import autocode_bridge_github as bridge_github
import autocode_bridge_store as store
import autocode_github as github


class FakeClient:
    """The three calls the adapter makes, against in-memory state."""

    def __init__(self, issues):
        self.issues = issues  # number -> {"issue": {...}, "comments": [...]}
        self.posted = []
        self.next_id = 1000

    def open_issues(self, owner, repo, label):
        return [
            row["issue"]
            for row in self.issues.values()
            if row["issue"].get("state") == "open" and label in [item.get("name") for item in row["issue"].get("labels") or []]
        ]

    def issue(self, ref):
        row = self.issues[ref.number]
        return {**row["issue"], "comments_list": list(row["comments"])}

    def issue_comment(self, ref, body):
        self.next_id += 1
        self.posted.append((ref.number, body))
        return {"id": self.next_id}


def comment(client: FakeClient, number: int, login: str, body: str) -> int:
    client.next_id += 1
    client.issues[number]["comments"].append({"id": client.next_id, "user": {"login": login}, "body": body})
    return client.next_id


def channel(client, actors=("dev", "lead")):
    return bridge_github.GitHubChannel(client, "owner/repo", "autocode", actors)


class AdapterTests(unittest.TestCase):
    def test_the_repo_must_name_an_owner(self):
        with self.assertRaises(ValueError):
            bridge_github.GitHubChannel(FakeClient({}), "justarepo", "autocode", ("dev",))

    def test_only_allowlisted_comments_above_the_floor_count_as_replies(self):
        client = FakeClient({7: {"issue": {"number": 7, "state": "open"}, "comments": []}})
        selected = channel(client)
        comment(client, 7, "dev", "old news")  # below the floor
        comment(client, 7, "stranger", "approve")  # not allowlisted
        at_floor = comment(client, 7, "dev", "current")  # at the gate floor: not a reply to it
        fresh = comment(client, 7, "lead", "approve")
        record = {"initial_comment_id": 0, "comments_seen": [], "gate": {"floor": at_floor}}
        found = selected.new_comments(record, selected.issue(7))
        self.assertEqual([(fresh, "lead", "approve")], [(row.id, row.actor, row.text) for row in found])

    def test_the_floor_covers_history_so_words_written_before_a_gate_cannot_answer_it(self):
        client = FakeClient({7: {"issue": {"number": 7, "state": "open"}, "comments": []}})
        selected = channel(client)
        early = comment(client, 7, "dev", "approve")
        record = {"initial_comment_id": early}
        self.assertEqual(early, selected.floor(record, selected.issue(7)))

    def test_posting_returns_the_comment_id_so_the_bridge_counts_its_own_words(self):
        client = FakeClient({7: {"issue": {"number": 7, "state": "open"}, "comments": []}})
        selected = channel(client)
        identity = selected.post(7, "taken")
        self.assertEqual([(7, "taken")], client.posted)
        self.assertIsInstance(identity, int)
        self.assertEqual([], selected.new_comments({"initial_comment_id": 0, "comments_seen": [identity]}, selected.issue(7)))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        import os

        self.saved = os.environ.get("AUTOCODE_BRIDGE_HOME")
        os.environ["AUTOCODE_BRIDGE_HOME"] = self.home.name

    def tearDown(self):
        import os

        if self.saved is None:
            os.environ.pop("AUTOCODE_BRIDGE_HOME", None)
        else:
            os.environ["AUTOCODE_BRIDGE_HOME"] = self.saved

    def test_records_round_trip_outside_every_workspace(self):
        record = {
            "owner": "Owner", "repo": "Repo", "number": 7, "title": "Greeting",
            "project": "/work/project", "worktree": "/work/project/.autocode/issues/owner-repo-7",
            "branch": "autocode/issue-7", "run_dir": "/work/project/.autocode/runs/r1",
            "gate": {"key": "answer:x"}, "receipt": {"launch_id": "a" * 32},
        }
        store.save(record)
        self.assertEqual(record, store.load("Owner", "Repo", 7))
        self.assertEqual([record], store.records())
        self.assertEqual(
            Path(self.home.name) / "owner-repo-7.json",
            store.path("Owner", "Repo", 7),
        )

    def test_a_failed_start_is_a_record_without_a_run(self):
        record = {"owner": "o", "repo": "r", "number": 9, "project": "/work/project",
                  "gate": {"key": "failed"}, "failed": "durable execution is unavailable"}
        store.save(record)
        self.assertEqual("failed", store.load("o", "r", 9)["gate"]["key"])

    def test_the_comment_ledger_stays_bounded_and_unique(self):
        record = store.seen_comments({"comments_seen": [1, 2]}, [2, 3])
        self.assertEqual([1, 2, 3], record["comments_seen"])
        flooded = store.seen_comments({"comments_seen": list(range(400))}, [999])
        self.assertEqual(store.REMEMBERED_COMMENTS, len(flooded["comments_seen"]))
        self.assertEqual(999, flooded["comments_seen"][-1])


if __name__ == "__main__":
    unittest.main()
