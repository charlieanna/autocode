"""Fail-closed coordination decisions and publication recovery, without models."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_coordinate as coordinate
import autocode_coordination_access as access
import autocode_coordination_budget as budget
import autocode_coordination_joint as joint
import autocode_coordination_publish as publication
import autocode_coordination_repositories as repos
import autocode_coordination_store as store
import autocode_github as github
import autocode_related_profile as profiles


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "catalog")})
        environment.start()
        self.addCleanup(environment.stop)
        checking = patch.object(coordinate.access, "check")
        checking.start()
        self.addCleanup(checking.stop)
        self.document = {
            "version": 1,
            "id": "fixture",
            "anchor": str(self.root / "run"),
            "phase": "discovering",
            "members": [],
            "initial_scope": [],
            "choices": {},
            "task": "Fix the real bug",
            "joint_attempts": [],
        }

    def repository(self, name="api", owner="fixture"):
        root = self.root / name
        root.mkdir(parents=True)
        (root / "code.py").write_text("result = 1\n")
        for args in [
            ("init", "-q", "-b", "main"),
            ("config", "user.name", "Fixture"),
            ("config", "user.email", "fixture@example.test"),
            ("config", "commit.gpgsign", "false"),
            ("add", "--all"),
            ("commit", "-qm", "base"),
            ("remote", "add", "origin", "https://github.com/" + owner + "/" + root.name + ".git"),
        ]:
            repos.git(root, *args)
        return repos.identity(root)

    def test_only_authorized_exact_matches_resolve_and_ambiguity_is_a_question(self):
        first = self.repository("one/api")
        second = self.repository("two/api", "another")
        self.document["initial_scope"] = [{"target": "api", "work": "Fix its contract"}]
        with patch.object(repos, "catalog", return_value=[first]):
            self.assertEqual("resolve", coordinate.resolve_request(self.document, {})["kind"])
        with patch.object(repos, "catalog", return_value=[first, second]):
            need = coordinate.resolve_request(self.document, {})
            self.assertEqual("repository", need["kind"])
            self.assertEqual(2, len(need["candidates"]))
        with patch.object(repos, "catalog", return_value=[]):
            self.assertEqual([], coordinate.resolve_request(self.document, {})["candidates"])
        for target in (str(self.root / "one/api"), "https://github.com/fixture/api", "../api"):
            self.assertEqual([], repos.candidates(target, [first]))

    def test_dirty_original_is_not_silently_replaced_by_head(self):
        entry = self.repository()
        (Path(entry["origin"]) / "code.py").write_text("uncommitted bug reproduction\n")
        with self.assertRaisesRegex(store.CoordinationError, "local product edits"):
            coordinate.add_member(self.document, "api", entry, "fix it")
        self.assertEqual([], self.document["members"])

    def test_changed_anchor_scope_cannot_publish_the_old_request(self):
        self.document.update(anchor_turn=1, anchor_scope="shown")
        view = {"turn": 1, "task": self.document["task"], "scope": {"identity": "shown", "decision": None}}
        with patch.object(coordinate, "anchor_view", return_value=view):
            self.assertEqual({}, coordinate.observe(self.document))
            view["scope"]["decision"] = "this_repository"
            with self.assertRaisesRegex(store.CoordinationError, "scope decision changed"):
                coordinate.observe(self.document)

    def test_delivery_stages_literal_source_without_ignored_run_files(self):
        entry = self.repository()
        workspace = Path(entry["origin"])
        (workspace / ".gitignore").write_text(".autocode/\n")
        (workspace / ".autocode").mkdir()
        (workspace / ".autocode/private.json").write_text('{"private":true}')
        (workspace / "result[1].py").write_text("result = 2\n")
        (workspace / "code.py").unlink()
        publication.delivery.stage_source(workspace)
        staged = repos.git(workspace, "diff", "--cached", "--name-only", "-z").split("\0")
        self.assertEqual({".gitignore", "code.py", "result[1].py"}, set(staged) - {""})

    def test_changed_profile_is_rejected_and_export_does_not_copy_approvals_or_sessions(self):
        settings = {
            "roles": {"terra": {"model": "selected", "effort": "high"}},
            "engine": "codex",
            "limits": {"max_seconds": 90, "max_cost_usd": 2},
        }
        exported = profiles.project(
            {"settings": settings, "sessions": {"secret": "private"}, "goal_contract": {"approved": True}}
        )
        self.assertEqual({"version": 1, "settings": settings}, exported)
        path = self.root / "profile.json"
        path.write_text(json.dumps(exported))
        digest = coordinate.util.file_hash(path)
        self.assertEqual(settings["roles"], profiles.load(path, digest)["roles"])
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "changed"):
            profiles.load(path, digest)

    def test_cancellation_precedes_any_start_or_publication(self):
        store.save(self.document)
        coordinate.cancel(self.document["anchor"])
        with patch.object(coordinate.tasks.TaskRun, "start") as start:
            result = coordinate.advance(self.document["anchor"])
        self.assertEqual("cancelled", result["needs"]["kind"])
        start.assert_not_called()

    def test_cancellation_before_initial_advance_never_initializes_a_run(self):
        coordinate.cancel(self.document["anchor"])
        with (
            patch.object(coordinate, "anchor_view", return_value={"coordination": {"version": 1}}),
            patch.object(coordinate, "initialize") as initialize,
        ):
            result = coordinate.advance(self.document["anchor"])
        self.assertEqual("cancelled", result["needs"]["kind"])
        self.assertIsNone(result["next_command"])
        initialize.assert_not_called()

    def test_late_member_reopens_completed_peers_for_the_expanded_cohort(self):
        self.document["members"] = [{"target": name, "started": True} for name in ("api", "history")]
        views = {"api": {"done": True}, "history": {"needs": {"kind": "approve_plan"}}}
        step = coordinate.next_step(self.document, views)
        self.assertEqual({"kind": "refresh", "targets": ["api"]}, step)
        self.document["rework"] = {"targets": ["api"], "remaining": [{"target": "api"}], "phase": "refreshing_scope"}
        coordinate.finish_rework_step(self.document, "api")
        self.assertNotIn("rework", self.document)
        self.assertEqual("refreshing_scope", self.document["phase"])

    def test_stale_approval_cannot_approve_a_member(self):
        store.save(self.document)
        with (
            patch.object(coordinate, "status", return_value={"approval": {"token": "new"}}),
            patch.object(coordinate.changesets, "approve") as approve,
        ):
            with self.assertRaisesRegex(store.CoordinationError, "changed after display"):
                coordinate.approve(self.document["anchor"], "old")
        approve.assert_not_called()

    def test_joint_check_cannot_be_omitted_or_replaced_by_human_review(self):
        for body in ({}, {"acceptance_criteria": [{"human_review": True}]}):
            with self.assertRaisesRegex(store.CoordinationError, "executable regression"):
                joint.criteria(self.document, {"approved_contract": {"body": body}})

    def test_failed_independent_proof_replans_joint_work_but_never_publishes(self):
        self.document.update(
            members=[{"target": "api", "started": True, "run_dir": "/api/run"}],
            joint={"workspace": "/joint", "verification": {"verdict": "FAIL", "reasons": ["candidate tests failed"]}},
            joint_attempts=[{}],
        )
        with (
            patch.object(coordinate, "handle", return_value=Mock(status=lambda: {"done": True})),
            patch.object(joint, "mismatches", return_value=[]),
        ):
            self.assertEqual("joint_retry", coordinate.next_step(self.document, {"api": {"done": True}})["kind"])
            self.document["joint_attempts"] = [{}, {}, {}]
            self.assertEqual("blocked", coordinate.next_step(self.document, {"api": {"done": True}})["kind"])
        self.assertIn("candidate tests failed", joint.failure_feedback(self.document["joint"]["verification"]))

    def test_money_cap_is_shared_without_recharging_the_current_runs_own_spend(self):
        limits = {"cap_usd": 10, "initial_spent_usd": 1}
        views = [
            {"run_dir": "a", "cost": {"spent_usd": 2, "complete": True}},
            {"run_dir": "b", "cost": {"spent_usd": 3, "complete": True}},
        ]
        self.assertEqual(["--max-cost-usd", "4"], budget.envelope(limits, views)["options"])
        self.assertEqual(["--max-cost-usd", "6"], budget.envelope(limits, views, "a")["options"])
        with self.assertRaisesRegex(store.CoordinationError, "cap is exhausted"):
            budget.envelope({"cap_usd": 6, "initial_spent_usd": 1}, views)
        views.append({"run_dir": "unpriced", "cost": {"spent_usd": None, "complete": False}})
        self.assertFalse(budget.envelope(limits, views)["complete"])

    def test_failed_verifier_spend_is_charged_before_the_next_joint_attempt(self):
        verification = {
            "record": "/verification-1",
            "evidence_report": {
                "document": {
                    "attempts": [
                        {"reported_cost_usd": 2, "historical_estimated_cost_usd": 9},
                        {"reported_cost_usd": None, "historical_estimated_cost_usd": 1},
                    ]
                }
            },
        }
        view = budget.verification_view(verification)
        self.assertEqual(3, view["cost"]["spent_usd"])
        self.assertEqual(
            ["--max-cost-usd", "6"], budget.envelope({"cap_usd": 10, "initial_spent_usd": 1}, [view])["options"]
        )

    def test_follow_up_receipt_uses_submitted_chat_text_not_augmented_task(self):
        view = {
            "turn": 2,
            "task": "Correct history\n\nThis follows up an earlier request...",
            "chat": {"messages": [{"role": "user", "text": "Correct history"}]},
        }
        self.assertTrue(coordinate.follow_up_received(view, 1, "Correct history"))
        self.assertTrue(coordinate.follow_up_received(view, 1, "Correct history\n"))
        self.assertFalse(coordinate.follow_up_received(view, 0, "Correct history"))
        self.assertFalse(coordinate.follow_up_received(view, 1, "Different correction"))

    def test_repository_authority_and_dependency_order_are_not_guessed(self):
        with self.assertRaisesRegex(store.CoordinationError, "configured GitHub host"):
            repos.publication_repository("https://gitlab.com/fixture/api.git")
        self.document["members"] = [{"target": name} for name in ("api", "history", "tax")]
        self.document["dependencies"] = {"api": ["history"], "history": ["tax"], "tax": []}
        self.assertEqual("tax, then history, then api.", publication.merge_order(self.document))
        self.document["dependencies"]["tax"] = ["api"]
        self.assertIn("cycle", publication.merge_order(self.document))

    def test_dependency_evidence_survives_member_follow_up_turns(self):
        self.document["members"] = [{"target": name} for name in ("checkout", "history", "tax")]
        coordinate.observe_dependencies(
            self.document,
            {
                "checkout": {"scope": {"external_parts": [{"target": "history"}]}},
                "history": {"scope": {"external_parts": [{"target": "tax"}]}},
                "tax": {},
            },
        )
        coordinate.observe_dependencies(self.document, {"checkout": {}, "history": {}, "tax": {}})
        self.assertEqual("tax, then history, then checkout.", publication.merge_order(self.document))

    def publication_fixture(self):
        entry = self.repository()
        coordinate.add_member(self.document, "api", entry, "Fix the contract")
        row = self.document["members"][0]
        repos.prepare(row, row["workspace"])
        row["tested_fingerprint"] = repos.fingerprint(row["workspace"])
        row["publication"] = {"phase": "pushed", "commit": row["base"]}
        self.document["joint"] = {"verification": {"record": "/proof/verification.json"}}
        store.save(self.document)
        client = Mock(api=github.API)
        pull = {
            "number": 42,
            "html_url": "https://github.com/fixture/api/pull/42",
            "head": {"sha": row["base"], "ref": row["branch"]},
            "base": {"sha": row["base"], "ref": row["base_branch"]},
            "state": "open",
            "body": publication.marker(self.document),
        }
        return row, client, pull

    def test_lost_publication_response_is_reconciled_without_a_second_post(self):
        row, client, pull = self.publication_fixture()
        client.pulls_for_branch.side_effect = [[], [pull], [pull]]
        client.open_pull_request.side_effect = github.GitHubError("connection lost after creation")
        with self.assertRaises(github.GitHubError):
            publication.publish_one(self.document, row, client=client)
        self.assertEqual("creating", store.read(self.document["anchor"])["members"][0]["publication"]["phase"])
        publication.publish_one(self.document, row, client=client)
        publication.publish_one(self.document, row, client=client)
        self.assertEqual(1, client.open_pull_request.call_count)
        self.assertEqual(pull["html_url"], row["publication"]["url"])

    def test_github_canonical_repository_casing_is_the_same_authority(self):
        row, client, pull = self.publication_fixture()
        pull["html_url"] = "https://github.com/FiXtUrE/Api/pull/42"
        client.pulls_for_branch.return_value = [pull]
        publication.publish_one(self.document, row, client=client)
        self.assertEqual(pull["html_url"], row["publication"]["url"])
        client.open_pull_request.assert_not_called()

    def test_changed_candidate_or_remote_stops_before_any_github_request(self):
        row, client, _ = self.publication_fixture()
        source = Path(row["workspace"]) / "code.py"
        original = source.read_text()
        source.write_text("different candidate\n")
        with self.assertRaisesRegex(store.CoordinationError, "changed after joint verification"):
            publication.publish_one(self.document, row, client=client)
        source.write_text(original)
        repos.git(row["workspace"], "remote", "set-url", "origin", "https://github.com/unexpected/api.git")
        with self.assertRaisesRegex(store.CoordinationError, "remote changed"):
            publication.publish_one(self.document, row, client=client)
        client.pulls_for_branch.assert_not_called()

    def test_uncertain_or_conflicting_publication_never_creates_replacement(self):
        row, client, pull = self.publication_fixture()
        row["publication"]["phase"] = "creating"
        client.pulls_for_branch.return_value = []
        with self.assertRaisesRegex(store.CoordinationError, "uncertain"):
            publication.publish_one(self.document, row, client=client)
        changed = copy.deepcopy(pull)
        changed["head"]["sha"] = "different"
        client.pulls_for_branch.return_value = [changed]
        with self.assertRaisesRegex(store.CoordinationError, "exact tested commit"):
            publication.publish_one(self.document, row, client=client)
        client.open_pull_request.assert_not_called()


class RepositoryAccessTests(unittest.TestCase):
    def test_missing_credentials_stop_before_any_repository_operation(self):
        selected = Mock(token=None)
        with patch.object(access, "client", return_value=selected):
            with self.assertRaisesRegex(store.CoordinationError, "authentication is missing"):
                access.check({"repository": ["fixture", "api"]})
        selected.repository.assert_not_called()

    def test_denied_or_wrong_repository_access_cannot_launch_a_checkout(self):
        for response in (
            {"full_name": "fixture/api", "permissions": {"push": False}},
            {"full_name": "different/api", "permissions": {"push": True}},
        ):
            with self.subTest(response=response):
                selected = Mock(token="fixture")
                selected.repository.return_value = response
                with patch.object(access, "client", return_value=selected), patch.object(repos, "git") as git:
                    with self.assertRaisesRegex(store.CoordinationError, "write access is unavailable"):
                        access.check({"repository": ["fixture", "api"]})
                git.assert_not_called()

    def test_remote_baseline_drift_refuses_delivery(self):
        selected = Mock(token="fixture")
        selected.repository.return_value = {"full_name": "fixture/api", "permissions": {"push": True}}
        member = {
            "repository": ["fixture", "api"],
            "origin": "/api",
            "push_remote": "remote",
            "base_branch": "main",
            "base": "pinned",
        }
        with (
            patch.object(access, "client", return_value=selected),
            patch.object(repos, "git", return_value="changed\trefs/heads/main"),
        ):
            with self.assertRaisesRegex(store.CoordinationError, "local baseline does not match"):
                access.check(member)
