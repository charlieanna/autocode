"""Fail-closed coordination decisions and publication recovery, without models."""

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_coordinate as coordinate
import autocode_coordination_access as access
import autocode_coordination_budget as budget
import autocode_coordination_context as member_context
import autocode_coordination_joint as joint
import autocode_coordination_publish as publication
import autocode_coordination_repositories as repos
import autocode_coordination_store as store
import autocode_github as github
import autocode_goals as goals
import autocode_related_profile as profiles
import autocode_repo_catalog as source_catalog
import autocode_run_view as run_view
import autocode_scope_assignment as assignment


class RepositoryGitEnvironmentTests(unittest.TestCase):
    def test_https_transport_keeps_installed_helpers_without_repository_overrides(self):
        environment = {
            "PATH": "/installed/bin",
            "HOME": "/operator/home",
            "GIT_EXEC_PATH": "/installed/libexec/git-core",
            "GIT_DIR": "/another/repository/.git",
            "GIT_WORK_TREE": "/another/repository",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "url.file:///other/.insteadOf",
            "GIT_CONFIG_VALUE_0": "https://github.com/",
            "GIT_TERMINAL_PROMPT": "1",
        }
        expected = {
            "PATH": "/installed/bin",
            "HOME": "/operator/home",
            "GIT_EXEC_PATH": "/installed/libexec/git-core",
            "GIT_TERMINAL_PROMPT": "0",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(repos.subprocess, "run") as run:
            run.return_value = Mock(returncode=0, stdout="baseline\trefs/heads/main\n")
            result = repos.git("/authorized/repository", "ls-remote", "https://github.com/example/project.git")
        self.assertEqual("baseline\trefs/heads/main", result)
        run.assert_called_once_with(
            ["git", "-C", "/authorized/repository", "ls-remote", "https://github.com/example/project.git"],
            env=expected,
            capture_output=True,
            text=True,
            input=None,
            timeout=120,
        )

    def test_unset_helper_directory_stays_unset(self):
        with (
            patch.dict(os.environ, {"PATH": "/installed/bin"}, clear=True),
            patch.object(repos.subprocess, "run") as run,
        ):
            run.return_value = Mock(returncode=0, stdout="")
            self.assertEqual("", repos.git("/authorized/repository", "status", "--porcelain"))
        self.assertEqual({"PATH": "/installed/bin", "GIT_TERMINAL_PROMPT": "0"}, run.call_args.kwargs["env"])


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

    def plan_view(self, token):
        body = {
            "acceptance_criteria": [
                {
                    "id": "fix",
                    "criterion": "Fix the real behavior",
                    "verification_method": "unit tests",
                    "human_review": False,
                }
            ],
            "constraints": [],
            "permission_boundaries": [],
            "scope_exclusions": [],
        }
        return {
            "needs": {"kind": "approve_plan", "token": token},
            "displayed_plan": {"token": token, **body},
        }

    def test_another_viewer_cannot_advance_the_same_run_while_locked(self):
        anchor = self.document["anchor"]
        with store.locked(anchor):
            with self.assertRaisesRegex(store.CoordinationError, "already advancing"):
                with store.locked(anchor):
                    self.fail("A second viewer acquired the held run")
            with store.locked(str(self.root / "other-run")) as independent:
                self.assertIsNone(independent)
        with store.locked(anchor) as released:
            self.assertIsNone(released)

    def test_the_joint_brief_distinguishes_candidate_inputs_from_citable_source(self):
        # #2487: the brief sends the model to .autocode/candidate-inputs.json, so it must
        # also say those copies are task inputs it may read but never cite, name the
        # lasting repos/ source for citations, and warn that a baseline repos/ file still
        # holds the broken code until its candidate is installed.
        text = joint.brief(self.document)
        self.assertIn(".autocode/candidate-inputs.json", text)
        self.assertIn("task inputs, not source", text)
        self.assertIn("cite only the lasting repos/ source in source_refs and code_refs", text)
        self.assertIn("still holds the original broken code until its candidate is installed", text)

    def test_exception_releases_the_coordination_lock_and_keeps_saved_record(self):
        anchor = self.document["anchor"]
        with self.assertRaisesRegex(RuntimeError, "operation interrupted"):
            with store.locked(anchor):
                store.save(self.document)
                raise RuntimeError("operation interrupted")
        with store.locked(anchor) as recovered:
            self.assertEqual(self.document, recovered)

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

    def test_discovered_normalized_remote_keeps_identity_but_an_actual_remote_change_is_refused(self):
        import autocode_repo_catalog as catalog

        self.repository()
        catalog.add_root(self.root)
        entry = next(row for row in repos.catalog() if Path(row["origin"]).name == "api")
        coordinate.add_member(self.document, "api", entry, "Fix the contract")
        member = self.document["members"][0]
        repos.prepare(member, member["workspace"])
        repos.require_identity(member)
        repos.git(member["workspace"], "remote", "set-url", "origin", "https://github.com/fixture/other.git")
        with self.assertRaisesRegex(store.CoordinationError, "changed after discovery"):
            repos.require_identity(member)

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

    def test_repository_resolution_carries_the_validated_scope_grounding(self):
        source = self.repository("api")
        entry = self.repository("history")
        grounding = {
            "source_quote": "keep its history in history",
            "evidence": "client.py:4",
            "evidence_repository": source["origin"],
            "evidence_revision": source["base"],
        }
        self.document["members"] = [{"target": "api", **source}]
        self.document["initial_scope"] = [{"target": "history", "work": "Store the new result", **grounding}]
        with patch.object(repos, "catalog", return_value=[entry]):
            found = coordinate.resolve_request(self.document, {})
        self.assertEqual(grounding, found["supporting_evidence"])

    def test_catalog_discovers_sources_outside_the_anchor_and_in_standalone_registrations(self):
        nested = self.repository("sources/api")
        standalone = self.repository("separate/history")
        source_catalog.add_root(self.root / "sources")
        source_catalog.add_repository(standalone["origin"])
        discovered = repos.catalog()
        self.assertEqual({nested["origin"], standalone["origin"]}, {row["origin"] for row in discovered})
        self.assertEqual({nested["base"], standalone["base"]}, {row["base"] for row in discovered})

    def test_initialize_persists_catalog_identity_and_source_snapshot_with_the_task(self):
        view = {
            "execution": {"mode": "owned"},
            "workflow": "bugfix",
            "scope": {"external_parts": [{"target": "api", "work": "Fix response mapping"}], "identity": "scope-1"},
            "related_profile": {"settings": {"limits": {}}},
            "workspace": str(self.root / "launch"),
            "task": "Fix the API response mapping",
            "turn": 4,
            "request_sources": [{"id": "answer:Q1", "kind": "user_answer", "text": "Retain result 2304."}],
        }
        snapshot = {
            "catalog_identity": "catalog-1",
            "source_snapshot": "sources-1",
            "catalog": "/home/.autocode/source-catalog.json",
        }
        with (
            patch.object(coordinate, "anchor_view", return_value=view),
            patch.object(repos, "source_snapshot", return_value=snapshot),
            patch.object(repos, "identity", return_value={"origin": view["workspace"]}),
            patch.object(coordinate, "add_member") as add_member,
        ):
            saved = coordinate.initialize(self.root / "run")
        self.assertEqual("Fix the API response mapping", saved["task"])
        self.assertEqual(snapshot, saved["source_catalog"])
        self.assertEqual(view["request_sources"], saved["request_sources"])
        self.assertEqual("scope-1", saved["anchor_scope"])
        self.assertEqual([], saved["members"], "The launch checkout must not be selected as a repository member")
        add_member.assert_not_called()

    def test_changed_request_sources_refuse_execution_even_when_task_and_scope_match(self):
        self.document.update(anchor_turn=1, anchor_scope="same", request_sources=[])
        view = {
            "turn": 1,
            "task": self.document["task"],
            "scope": {"identity": "same"},
            "request_sources": [{"id": "answer:Q1", "kind": "user_answer", "text": "New acceptance"}],
        }
        with patch.object(coordinate, "anchor_view", return_value=view):
            with self.assertRaisesRegex(store.CoordinationError, "request.*changed"):
                coordinate.observe(self.document)

    def test_resolved_members_and_joint_brief_include_original_clarifications(self):
        self.document["request_sources"] = [{"id": "answer:Q1", "kind": "user_answer", "text": "Preserve 2304."}]
        step = {"kind": "resolve", "target": "api", "entry": {}, "work": "Investigate history"}
        with (
            patch.object(store, "read", return_value=self.document),
            patch.object(coordinate, "observe", return_value={}),
            patch.object(coordinate, "next_step", return_value=step),
            patch.object(coordinate, "observe_dependencies"),
            patch.object(coordinate, "sync_set"),
            patch.object(coordinate, "status", return_value={}),
            patch.object(coordinate, "add_member") as add,
        ):
            coordinate.advance(self.document["anchor"])
        self.assertIn("Preserve 2304.", add.call_args.args[3])

    def test_joint_brief_retains_request_clarifications(self):
        self.document["request_sources"] = [{"id": "answer:Q1", "kind": "user_answer", "text": "Preserve 2304."}]
        self.assertIn("Preserve 2304.", joint.brief(self.document))

    def test_old_coordinator_offers_source_recovery_before_any_child_advance(self):
        self.document["anchor_turn"] = 1
        sources = [{"id": "task:0", "kind": "task", "text": self.document["task"]}]
        with patch.object(coordinate, "anchor_view", return_value={"request_sources": sources}):
            self.assertEqual({"kind": "sync_request", "sources": sources}, coordinate.next_step(self.document, {}))

    def test_request_source_recovery_and_feedback_use_the_public_task_action_once(self):
        sources = [{"id": "answer:Q1", "kind": "user_answer", "text": "Keep 2304."}]
        member = {"target": "api", "started": False, "brief": self.document["task"]}
        self.document["members"] = [member]
        run = Mock()
        with (
            patch.object(store, "read", return_value=self.document),
            patch.object(coordinate, "observe", return_value={}),
            patch.object(coordinate, "next_step") as next_step,
            patch.object(coordinate, "observe_dependencies"),
            patch.object(coordinate, "status", return_value={}),
            patch.object(coordinate, "handle", return_value=run),
        ):
            next_step.return_value = {"kind": "sync_request", "sources": sources}
            coordinate.advance(self.document["anchor"])
            self.assertEqual(sources, self.document["request_sources"])
            self.assertIn("Keep 2304.", member["brief"])
            run.feedback.assert_not_called()
            member.update(started=True, brief=self.document["task"])
            message = self.document["task"] + "\n\nKeep 2304."
            next_step.return_value = {"kind": "request_feedback", "target": "api", "message": message}
            coordinate.advance(self.document["anchor"])
            run.feedback.assert_called_once_with(message)
            self.assertEqual(message, member["brief"])
            run.reset_mock()
            self.document["pending"] = {"operation": "request_feedback", "target": "api", "message": message}
            next_step.return_value = {"kind": "reconcile", **self.document["pending"]}
            run.status.return_value = {"chat": {"messages": [{"role": "user", "text": message}]}}
            coordinate.advance(self.document["anchor"])
            self.assertIsNone(self.document["pending"])
            run.feedback.assert_not_called()
            self.document["pending"] = {"operation": "request_feedback", "target": "api", "message": message}
            run.status.return_value = {"chat": {"messages": []}}
            with self.assertRaisesRegex(store.CoordinationError, "uncertain"):
                coordinate.advance(self.document["anchor"])
            run.feedback.assert_not_called()

    def test_changed_catalog_snapshot_blocks_saved_coordinated_work(self):
        self.document["source_catalog"] = {
            "catalog_identity": "catalog-1",
            "source_snapshot": "sources-1",
            "catalog": "/home/.autocode/source-catalog.json",
        }
        with patch.object(
            repos,
            "source_snapshot",
            return_value={
                "catalog_identity": "catalog-2",
                "source_snapshot": "sources-2",
                "catalog": "/home/.autocode/source-catalog.json",
            },
        ):
            step = coordinate.next_step(self.document, {})
        self.assertEqual("blocked", step["kind"])
        self.assertIn("source catalog changed", step["reason"].lower())

    def test_discovered_work_is_a_hint_not_a_member_user_requirement(self):
        hint = "Apply tax here unless the user answers Q1 that checkout owns the calculation."
        entry = self.repository("tax")
        store.save(self.document)
        with (
            patch.object(coordinate, "observe", return_value={}),
            patch.object(
                coordinate,
                "next_step",
                return_value={
                    "kind": "resolve",
                    "target": "tax",
                    "entry": entry,
                    "work": hint,
                    "supporting_evidence": {"source_quote": "evidence from the ticket", "evidence": "client.py:2"},
                },
            ),
            patch.object(coordinate, "status", return_value={}),
        ):
            coordinate.advance(self.document["anchor"])
        member = store.read(self.document["anchor"])["members"][0]
        self.assertEqual(self.document["task"], member["brief"])
        self.assertEqual(hint, member["reported_work"])
        self.assertEqual(
            {"source_quote": "evidence from the ticket", "evidence": "client.py:2"},
            member["supporting_evidence"],
        )
        self.assertEqual("source", member["role"])
        with self.assertRaisesRegex(ValueError, "source_quote is not in"):
            goals.check_requirement_handoff(
                {"task": member["brief"]},
                {"requirements": [{"id": "R1", "text": hint, "source_quote": hint}]},
            )

    def test_status_exposes_canonical_member_locations_revisions_roles_and_grounding(self):
        self.document.update(
            anchor=str(self.root / "workspace/.autocode/runs/anchor"),
            revision=4,
            source_catalog={
                "catalog_identity": "catalog",
                "source_snapshot": "sources",
                "catalog": "/home/.autocode/source-catalog.json",
            },
            members=[
                {
                    "target": "history",
                    "role": "member",
                    "origin": str(self.root / "history"),
                    "base": "a" * 40,
                    "remote": "https://github.com/example/history.git",
                    "workspace": str(self.root / "worktrees/history"),
                    "run_dir": str(self.root / "worktrees/history/.autocode/runs/fix"),
                    "started": False,
                    "supporting_evidence": {
                        "source_quote": "keep its history in history",
                        "evidence": "client.py:4",
                        "evidence_repository": str(self.root / "api"),
                        "evidence_revision": "b" * 40,
                    },
                }
            ],
        )
        with (
            patch.object(store, "read", return_value=self.document),
            patch.object(coordinate, "observe", return_value={}),
            patch.object(coordinate, "next_step", return_value={"kind": "blocked", "reason": "test"}),
        ):
            shown = coordinate.status(self.document["anchor"])
        member = shown["members"][0]
        self.assertEqual(str(self.root / "history"), member["location"])
        self.assertEqual("a" * 40, member["base"])
        self.assertEqual("member", member["role"])
        self.assertEqual(self.document["members"][0]["supporting_evidence"], member["supporting_evidence"])
        self.assertEqual(self.document["source_catalog"], shown["source_catalog"])

    def test_approval_token_binds_the_original_request_identity(self):
        row = {
            "target": "api",
            "workspace": str(self.root / "api"),
            "origin": str(self.root / "api"),
            "run_dir": str(self.root / "api/run"),
            "base": "a" * 40,
            "role": "source",
            "remote": "https://github.com/fixture/api.git",
            "branch": "autocode/fix-api",
            "repository": ["fixture", "api"],
            "supporting_evidence": {},
        }
        document = {
            **self.document,
            "revision": 3,
            "task": "Fix the API result",
            "anchor_turn": 1,
            "anchor_scope": "scope-1",
            "source_catalog": {"catalog_identity": "catalog", "source_snapshot": "snapshot"},
            "members": [row],
        }
        offer = {"kind": "approval", "summary": {"runs": [], "approval": {"set_token": "set"}}}
        with (
            patch.object(store, "read", return_value=document),
            patch.object(coordinate, "observe", return_value={}),
            patch.object(coordinate, "next_step", return_value=offer),
        ):
            first = coordinate.status(document["anchor"])["approval"]["token"]
            document["task"] = "Fix a different API result"
            second = coordinate.status(document["anchor"])["approval"]["token"]
            document["request_sources"] = [{"id": "answer:Q1", "text": "Preserve redaction."}]
            third = coordinate.status(document["anchor"])["approval"]["token"]
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)

    def test_coordinate_cli_sets_shared_catalog_before_its_project_local_registry(self):
        run_dir = self.root / ".autocode" / "runs" / "chat"
        run_dir.mkdir(parents=True)
        output = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(coordinate, "status", return_value={"version": 1}),
            redirect_stdout(output),
        ):
            self.assertEqual(0, coordinate.cli(["status", "--run-dir", str(run_dir), "--json"]))
            self.assertEqual(str(Path.home() / ".autocode"), os.environ["AUTOCODE_SOURCE_CATALOG_HOME"])
            self.assertEqual(str(self.root / ".autocode/registry"), os.environ["AUTOCODE_HOME"])

    def joint_scope_fixture(self, target="checkout"):
        self.document["members"] = [
            {
                "target": name,
                "started": True,
                "run_dir": str(self.root / name),
                "origin": str(self.root / name),
                "base": name + "-base",
                "git_dir": str(self.root / name / ".git"),
            }
            for name in ("history", "tax")
        ]
        active = {
            "workspace": str(self.root / "joint"),
            "run_dir": str(self.root / "joint/run"),
            "base": "joint-base",
            "started": True,
        }
        self.document.update(joint=active, joint_attempts=[active])
        views = {name: self.plan_view(name + "-plan") for name in ("history", "tax")}
        joint_view = {
            "needs": {"kind": "answer"},
            "scope": {
                "identity": "joint-scope",
                "decision": None,
                "external_parts": [
                    {
                        "target": target,
                        "work": "Fix the real renderer",
                        "source_quote": "Preserve the original checkout behavior",
                        "evidence": "repos/history/code.py",
                    }
                ],
            },
        }
        return views, joint_view

    def test_joint_scope_discovers_catalog_repository_with_joint_source_binding(self):
        entry = self.repository("checkout")
        views, joint_view = self.joint_scope_fixture()
        with (
            patch.object(repos, "catalog", return_value=[entry]),
            patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))),
        ):
            step = coordinate.next_step(self.document, views)
        self.assertEqual("resolve", step["kind"])
        self.assertEqual("checkout", step["target"])
        self.assertEqual(entry, step["entry"])
        self.assertEqual(
            {
                "source_quote": "Preserve the original checkout behavior",
                "evidence": "repos/history/code.py",
                "evidence_repository": self.document["joint"]["workspace"],
                "evidence_revision": "joint-base",
            },
            step["supporting_evidence"],
        )

    def test_joint_source_evidence_is_not_bound_to_the_first_member(self):
        views, joint_view = self.joint_scope_fixture()
        evidence = coordinate.request_evidence(self.document, {**views, "Joint regression": joint_view}, "checkout")
        self.assertEqual(self.document["joint"]["workspace"], evidence["evidence_repository"])
        self.assertEqual("joint-base", evidence["evidence_revision"])

    def test_joint_scope_never_authorizes_an_uncatalogued_repository(self):
        views, joint_view = self.joint_scope_fixture()
        with (
            patch.object(repos, "catalog", return_value=[]),
            patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))),
        ):
            step = coordinate.next_step(self.document, views)
        self.assertEqual("repository", step["kind"])
        self.assertEqual([], step["candidates"])
        self.assertIn("authorized local catalog", step["reason"])

    def test_joint_scope_waits_for_running_work_before_expanding_the_cohort(self):
        entry = self.repository("checkout")
        views, joint_view = self.joint_scope_fixture()
        with (
            patch.object(repos, "catalog", return_value=[entry]),
            patch.object(coordinate.actions, "working", return_value=True),
            patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))),
        ):
            step = coordinate.next_step(self.document, views)
        self.assertEqual({"kind": "watch", "target": "Joint regression"}, step)

    def test_joint_scope_reuses_linked_members_instead_of_asking_to_narrow_scope(self):
        views, joint_view = self.joint_scope_fixture("history")
        with patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))):
            step = coordinate.next_step(self.document, views)
        self.assertEqual(
            {
                "kind": "assign",
                "target": "Joint regression",
                "identity": "joint-scope",
                "members": {"history": str(self.root / "history")},
            },
            step,
        )

    def test_linked_assignment_retries_until_its_exact_scope_question_is_consumed(self):
        # Assignment receipt publication precedes the child action. A still-active
        # child can refuse that action, leaving a linked view and its old question.
        for owner in ("Joint regression", "tax"):
            with self.subTest(owner=owner):
                views, pending = self.joint_scope_fixture("history")
                pending["scope"]["decision"] = "linked_repositories"
                pending["needs"] = {
                    "kind": "answer",
                    "questions": [{"id": "workspace-scope-joint-scope"}],
                }
                if owner == "tax":
                    self.document.pop("joint")
                    self.document["joint_attempts"] = []
                    views["tax"] = pending
                with patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=pending))):
                    step = coordinate.next_step(self.document, views)
                self.assertEqual(
                    {
                        "kind": "assign",
                        "target": owner,
                        "identity": "joint-scope",
                        "members": {"history": str(self.root / "history")},
                    },
                    step,
                )

    def test_linked_assignment_does_not_consume_other_or_stale_questions(self):
        for question in ("business-ambiguity", "workspace-scope-old-identity", None):
            with self.subTest(question=question):
                views, pending = self.joint_scope_fixture("history")
                pending["scope"]["decision"] = "linked_repositories"
                pending["needs"] = {"kind": "answer", "questions": [{"id": question}] if question else []}
                with patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=pending))):
                    step = coordinate.next_step(self.document, views)
                self.assertNotEqual("assign", step["kind"])

    def test_discovery_retires_joint_plan_but_preserves_its_failed_evidence(self):
        views, _ = self.joint_scope_fixture()
        active = self.document["joint"]
        active["verification"] = {"verdict": "FAIL", "reasons": ["missing checkout"]}
        self.document["changeset"] = "set-1"
        store.save(self.document)
        with (
            patch.object(
                coordinate, "observe", return_value={key: {"status": "AWAITING_GOAL_APPROVAL"} for key in views}
            ),
            patch.object(
                coordinate,
                "next_step",
                return_value={
                    "kind": "resolve",
                    "target": "checkout",
                    "entry": {},
                    "work": "Fix checkout",
                },
            ),
            patch.object(coordinate, "add_member"),
            patch.object(coordinate, "sync_set"),
            patch.object(coordinate.sets, "remove_joint") as remove,
            patch.object(coordinate, "status", return_value={}),
        ):
            coordinate.advance(self.document["anchor"])
        saved = store.read(self.document["anchor"])
        self.assertNotIn("joint", saved)
        self.assertEqual([active], saved["joint_attempts"])
        remove.assert_called_once_with(str(self.root / "history"), active["run_dir"])

    def test_joint_scope_assignment_uses_the_joint_run_in_the_changeset(self):
        views, joint_view = self.joint_scope_fixture("history")
        store.save(self.document)
        run = Mock()
        with (
            patch.object(
                coordinate, "observe", return_value={key: {"status": "AWAITING_GOAL_APPROVAL"} for key in views}
            ),
            patch.object(
                coordinate,
                "next_step",
                return_value={
                    "kind": "assign",
                    "target": "Joint regression",
                    "identity": "joint-scope",
                    "members": {"history": str(self.root / "history")},
                },
            ),
            patch.object(coordinate.sets, "find", return_value={"id": "set-1"}) as find,
            patch.object(coordinate.sets, "set_fields") as fields,
            patch.object(coordinate, "handle", return_value=run) as handle,
            patch.object(coordinate, "status", return_value={}),
        ):
            coordinate.advance(self.document["anchor"])
        find.assert_called_once_with(self.document["joint"]["run_dir"])
        self.assertEqual(self.document["joint"], handle.call_args.args[0])
        fields.assert_called_once_with(
            "set-1",
            {
                "scope_assignments": {
                    self.document["joint"]["run_dir"]: {
                        "identity": "joint-scope",
                        "members": {"history": str(self.root / "history")},
                    },
                }
            },
        )
        run.assign_linked_scope.assert_called_once_with("joint-scope")

    def test_joint_plan_is_prepared_before_approval_or_any_builder(self):
        self.document["members"] = [
            {"target": target, "started": True, "run_dir": str(self.root / target)} for target in ("api", "history")
        ]
        views = {target: self.plan_view(target + "-plan") for target in ("api", "history")}
        with patch.object(coordinate.changesets, "status", return_value={"runs": []}):
            self.assertEqual("joint_plan_start", coordinate.next_step(self.document, views)["kind"])

    def test_unchanged_completed_member_plan_can_join_a_revised_joint_plan(self):
        self.document["members"] = [
            {"target": "api", "started": True, "run_dir": str(self.root / "api")},
            {"target": "history", "started": True, "run_dir": str(self.root / "history")},
        ]
        body = {
            "acceptance_criteria": [
                {"id": "fix", "criterion": "Fix it", "verification_method": "unit tests", "human_review": False}
            ],
            "constraints": [],
            "permission_boundaries": [],
            "scope_exclusions": [],
        }
        approved = {"token": "r1:abc", "hash": "abc", "revision": 1, "body": body}
        views = {
            "api": {
                "done": True,
                "approved_contract": approved,
                "displayed_plan": {"token": "r1:abc", "hash": "abc", "revision": 1, **body},
            },
            "history": self.plan_view("history-plan"),
        }
        self.assertEqual("joint_plan_start", coordinate.next_step(self.document, views)["kind"])

    def test_sync_set_registers_the_joint_plan_as_an_approval_participant(self):
        rows = []
        for target in ("api", "history"):
            entry = self.repository(target)
            run_dir = self.root / target / ".autocode/runs/coordinated-fix"
            run_dir.mkdir(parents=True)
            rows.append(
                {
                    **entry,
                    "target": target,
                    "workspace": str(self.root / target),
                    "run_dir": str(run_dir),
                    "brief": "Fix the actual behavior",
                    "started": True,
                }
            )
        joint_workspace = self.root / "joint"
        joint_run_dir = joint_workspace / ".autocode/runs/joint-regression"
        joint_run_dir.mkdir(parents=True)
        self.document["members"] = rows
        self.document["joint"] = {
            "workspace": str(joint_workspace),
            "run_dir": str(joint_run_dir),
            "started": True,
            "set_target": "Joint regression",
        }
        coordinate.sync_set(self.document)
        record = coordinate.sets.find(rows[0]["run_dir"])
        participant = next(row for row in coordinate.sets.members(record) if row["target"] == "Joint regression")
        self.assertEqual("joint", participant["role"])
        self.assertEqual(str(joint_run_dir), participant["run_dir"])

    def test_member_and_joint_plans_share_one_approval_offer(self):
        self.document["members"] = [
            {"target": target, "started": True, "run_dir": str(self.root / target)} for target in ("api", "history")
        ]
        self.document["joint"] = {
            "workspace": str(self.root / "joint"),
            "run_dir": str(self.root / "joint/.autocode/runs/joint"),
        }
        views = {target: self.plan_view(target + "-plan") for target in ("api", "history")}
        plan = {
            "token": "joint-plan-token",
            "acceptance_criteria": [
                {
                    "id": "joint",
                    "criterion": "Run the combined regression",
                    "verification_method": "pytest",
                    "human_review": False,
                }
            ],
            "constraints": [],
            "permission_boundaries": [],
            "scope_exclusions": [],
        }
        joint_view = {"needs": {"kind": "approve_plan", "token": plan["token"]}, "displayed_plan": plan}
        summary = {"runs": [], "approval": {"set_token": "complete-set"}}
        with (
            patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))),
            patch.object(coordinate.actions, "plan_for_approval", return_value=plan),
            patch.object(coordinate.changesets, "status", return_value=summary),
        ):
            step = coordinate.next_step(self.document, views)
        self.assertEqual("approval", step["kind"])
        self.assertEqual(plan, step["joint_plan"])

    def test_the_persons_approval_records_each_plans_contract(self):
        self.document.update(revision=4, members=[{"target": "api", "run_dir": str(self.root / "api/run")}])
        store.save(self.document)
        plan = self.plan_view("api-plan")["displayed_plan"]
        joint_plan = {**plan, "token": "joint-plan", "milestones": [{"id": "M1", "objective": "run it"}]}
        shown = {
            "approval": {"token": "shown"},
            "needs": {
                "kind": "approval",
                "summary": {"runs": [{"target": "api", "plan": plan}], "approval": {"set_token": "set-1"}},
                "joint_plan": joint_plan,
            },
        }
        with (
            patch.object(coordinate, "status", return_value=shown),
            patch.object(coordinate.changesets, "approve") as approve,
        ):
            coordinate.approve(self.document["anchor"], "shown")
        approve.assert_called_once_with(str(self.root / "api/run"), "set-1", command=coordinate.tasks.AUTOCODE)
        saved = store.read(self.document["anchor"])["approved_contracts"]
        self.assertEqual("shown", saved["token"])
        self.assertEqual(coordinate.contract(plan), saved["members"]["api"])
        self.assertEqual(coordinate.contract(joint_plan), saved["joint"])
        self.assertNotIn("milestones", saved["joint"])  # the order of work is the Planner's, not a term
        self.assertEqual([], saved["carried"])

    def test_revised_plans_that_keep_the_approved_contract_carry_the_approval(self):
        self.document["members"] = [
            {"target": target, "started": True, "run_dir": str(self.root / target)} for target in ("api", "history")
        ]
        self.document["joint"] = {"workspace": str(self.root / "joint"), "run_dir": str(self.root / "joint/run")}
        views = {target: self.plan_view(target + "-plan-2") for target in ("api", "history")}
        joint_plan = {
            **self.plan_view("joint-plan-2")["displayed_plan"],
            "milestones": [{"id": "M2", "objective": "Run the combined regression again"}],
        }
        self.document["approved_contracts"] = {
            "revision": 2,
            "token": "first",
            "members": {target: coordinate.contract(views[target]["displayed_plan"]) for target in views},
            "joint": coordinate.contract(joint_plan),
            "carried": [],
        }
        joint_view = {"needs": {"kind": "approve_plan", "token": "joint-plan-2"}, "displayed_plan": joint_plan}
        summary = {
            "runs": [{"target": target, "plan": views[target]["displayed_plan"]} for target in views],
            "approval": {"set_token": "set-2"},
        }

        def step():
            with (
                patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))),
                patch.object(coordinate.actions, "plan_for_approval", return_value=joint_plan),
                patch.object(coordinate.changesets, "status", return_value=summary),
            ):
                return coordinate.next_step(self.document, views)

        carried = step()
        self.assertEqual("carry_approval", carried["kind"])
        self.assertEqual({"revision": 2, "token": "first", "unchanged": ["api", "history"]}, carried["carried"])
        self.assertEqual(joint_plan, carried["joint_plan"])
        self.assertEqual("set-2", carried["summary"]["approval"]["set_token"])
        # A changed term in a member or the joint plan, or a repository the person never saw, asks again.
        summary["runs"][1]["plan"] = {**views["history"]["displayed_plan"], "constraints": ["Keep the old endpoint"]}
        self.assertEqual("approval", step()["kind"])
        summary["runs"][1]["plan"] = views["history"]["displayed_plan"]
        joint_plan["scope_exclusions"] = ["Not covered: billing"]
        self.assertEqual("approval", step()["kind"])
        joint_plan["scope_exclusions"] = []
        self.assertEqual("carry_approval", step()["kind"])
        del self.document["approved_contracts"]["members"]["history"]
        self.assertEqual("approval", step()["kind"])
        del self.document["approved_contracts"]
        self.assertEqual("approval", step()["kind"])

    def test_a_revised_plan_with_new_criterion_citations_keeps_the_carried_approval(self):
        # #2565: a mismatch repair's follow-up instruction becomes a run source, so the
        # replanned criteria gain citations. Citations are provenance (#595), not a term;
        # a revision that only cites more sources carries the approval instead of asking again.
        self.document["members"] = [
            {"target": target, "started": True, "run_dir": str(self.root / target)} for target in ("api", "history")
        ]
        self.document["joint"] = {"workspace": str(self.root / "joint"), "run_dir": str(self.root / "joint/run")}
        approved_plan = self.plan_view("api-plan")["displayed_plan"]
        approved_plan["acceptance_criteria"][0]["sources"] = [{"source_id": "task:0", "quote": "Fix the real behavior"}]
        joint_plan = {**approved_plan, "token": "joint-plan"}
        joint_plan["acceptance_criteria"] = [
            {**joint_plan["acceptance_criteria"][0], "id": "joint", "criterion": "Run the combined regression"}
        ]

        def revised(token):
            plan = copy.deepcopy(approved_plan)
            plan["token"] = token
            # The coordinator's own rework instruction is now a user-feedback source.
            plan["acceptance_criteria"][0]["sources"] = [
                {"source_id": "task:0", "quote": "Fix the real behavior"},
                {
                    "source_id": "feedback:abc123",
                    "quote": "A joint regression found a mismatch in another repository.",
                },
            ]
            return plan

        self.document["approved_contracts"] = {
            "revision": 2,
            "token": "first",
            "members": {target: coordinate.contract(approved_plan) for target in ("api", "history")},
            "joint": coordinate.contract(joint_plan),
            "carried": [],
        }
        revised_joint = {**revised("joint-plan-2"), "token": "joint-plan-2"}
        revised_joint["acceptance_criteria"] = [
            {**revised_joint["acceptance_criteria"][0], "id": "joint", "criterion": "Run the combined regression"}
        ]
        joint_view = {"needs": {"kind": "approve_plan", "token": "joint-plan-2"}, "displayed_plan": revised_joint}
        summary = {
            "runs": [{"target": target, "plan": revised(target + "-2")} for target in ("api", "history")],
            "approval": {"set_token": "set-2"},
        }
        views = {target: self.plan_view(target + "-plan-2") for target in ("api", "history")}

        def step():
            with (
                patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))),
                patch.object(coordinate.actions, "plan_for_approval", return_value=revised_joint),
                patch.object(coordinate.changesets, "status", return_value=summary),
            ):
                return coordinate.next_step(self.document, views)

        carried = step()
        self.assertEqual("carry_approval", carried["kind"])
        self.assertEqual({"revision": 2, "token": "first", "unchanged": ["api", "history"]}, carried["carried"])
        # A changed criterion sentence is still a changed term and asks again.
        summary["runs"][1]["plan"] = {**revised("history-3"), "constraints": ["Keep the old endpoint"]}
        self.assertEqual("approval", step()["kind"])

    def test_carrying_the_approval_takes_the_set_approval_and_records_it(self):
        self.document.update(
            revision=5,
            members=[{"target": "api", "started": True, "run_dir": str(self.root / "api/run")}],
            approved_contracts={"revision": 2, "token": "first", "members": {}, "joint": {}, "carried": []},
        )
        store.save(self.document)
        step = {
            "kind": "carry_approval",
            "summary": {"runs": [], "approval": {"set_token": "set-2"}},
            "joint_plan": {},
            "carried": {"revision": 2, "token": "first", "unchanged": ["api"]},
        }
        with (
            patch.object(coordinate, "observe", return_value={"api": {"status": "AWAITING_GOAL_APPROVAL"}}),
            patch.object(coordinate, "next_step", return_value=step),
            patch.object(coordinate.changesets, "approve") as approve,
            patch.object(coordinate, "status", return_value={}),
        ):
            coordinate.advance(self.document["anchor"])
        approve.assert_called_once_with(str(self.root / "api/run"), "set-2", command=coordinate.tasks.AUTOCODE)
        [carried] = store.read(self.document["anchor"])["approved_contracts"]["carried"]
        self.assertEqual("set-2", carried["set_token"])
        self.assertEqual(["api"], carried["unchanged"])

    def test_candidate_binding_replaces_baseline_inputs_including_deletions(self):
        entry = self.repository("api")
        member_workspace = self.root / "member-worktree"
        repos.prepare(entry, member_workspace)
        (member_workspace / "code.py").unlink()
        (member_workspace / "new-contract.py").write_text("result = 2\n")
        member = {
            **entry,
            "target": "api",
            "slug": "api-member",
            "workspace": str(member_workspace),
            "base": entry["base"],
        }
        document = {"task": "Fix the result", "members": [member]}
        directory = self.root / "joint-workspace"
        joint.prepare(document, directory)
        self.assertFalse((directory / ".autocode/candidate-inputs.json").exists())
        bound = joint.bind_candidates(document, directory)
        candidate = directory / ".autocode/candidate-inputs/api-member"
        self.assertFalse((candidate / "code.py").exists())
        self.assertEqual("result = 2\n", (candidate / "new-contract.py").read_text())
        self.assertEqual(repos.fingerprint(member_workspace), bound["api"])
        manifest = json.loads((directory / ".autocode/candidate-inputs.json").read_text())
        self.assertEqual(bound["api"], manifest["api"]["fingerprint"])

    def test_candidate_binding_action_refuses_until_every_member_is_complete(self):
        self.document["members"] = [
            {
                "target": "api",
                "started": True,
                "workspace": str(self.root / "api"),
                "run_dir": str(self.root / "api/run"),
            }
        ]
        self.document["joint"] = {
            "workspace": str(self.root / "joint"),
            "run_dir": str(self.root / "joint/run"),
            "started": True,
        }
        store.save(self.document)
        with (
            patch.object(coordinate, "observe", return_value={"api": {"done": False}}),
            patch.object(joint, "bind_candidates") as bind,
        ):
            with self.assertRaisesRegex(store.CoordinationError, "all repository runs are complete"):
                coordinate.bind_candidates(self.document["anchor"])
        bind.assert_not_called()

    def test_candidate_binding_action_requires_the_joint_plan_approval_receipt(self):
        member = {
            "target": "api",
            "started": True,
            "workspace": str(self.root / "api"),
            "run_dir": str(self.root / "api/run"),
            "base": "a" * 40,
        }
        self.document["members"] = [member]
        self.document["joint"] = {
            "workspace": str(self.root / "joint"),
            "run_dir": str(self.root / "joint/run"),
            "started": True,
        }
        store.save(self.document)
        views = {"api": {"done": True}}
        joint_view = {"done": False, "needs": {"kind": "continue"}}
        with (
            patch.object(coordinate, "observe", return_value=views),
            patch.object(coordinate, "next_step", return_value={"kind": "bind_candidates"}),
            patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))),
            patch.object(joint, "bind_candidates", return_value={"api": "fingerprint"}) as bind,
            patch.object(coordinate, "status", return_value={}),
        ):
            with self.assertRaisesRegex(store.CoordinationError, "joint plan must be approved"):
                coordinate.bind_candidates(self.document["anchor"])
        bind.assert_not_called()

    def test_binding_candidates_starts_the_joint_regression_phase(self):
        self.document["members"] = [
            {
                "target": "api",
                "started": True,
                "workspace": str(self.root / "api"),
                "run_dir": str(self.root / "api/run"),
                "base": "a" * 40,
            }
        ]
        self.document["joint"] = {
            "workspace": str(self.root / "joint"),
            "run_dir": str(self.root / "joint/run"),
            "started": True,
        }
        self.document["phase"] = "fixing"  # the members' last advance, after the joint plan started
        store.save(self.document)
        joint_view = {"done": False, "needs": {"kind": "continue"}, "approved_contract": {"token": "joint-plan"}}
        with (
            patch.object(coordinate, "observe", return_value={"api": {"done": True}}),
            patch.object(coordinate, "next_step", return_value={"kind": "bind_candidates"}),
            patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))),
            patch.object(joint, "bind_candidates", return_value={"api": "fingerprint"}),
            patch.object(coordinate, "status", return_value={}),
        ):
            coordinate.bind_candidates(self.document["anchor"])
        saved = store.read(self.document["anchor"])
        self.assertEqual("joint_regression", saved["phase"])
        self.assertEqual("fingerprint", saved["joint"]["candidate_binding"]["members"]["api"]["fingerprint"])

    def test_approved_joint_plan_binds_candidates_before_joint_execution(self):
        body = {
            "acceptance_criteria": [
                {
                    "id": "joint",
                    "criterion": "Run both components together",
                    "verification_method": "integration test",
                    "human_review": False,
                }
            ],
            "constraints": [],
            "permission_boundaries": [],
            "scope_exclusions": [],
        }
        approved = {"token": "joint-plan", "hash": "joint-hash", "revision": 1, "body": body}
        displayed = {"token": "joint-plan", "hash": "joint-hash", "revision": 1, **body}
        self.document["members"] = [
            {
                "target": "api",
                "started": True,
                "workspace": str(self.root / "api"),
                "run_dir": str(self.root / "api/run"),
            }
        ]
        self.document["joint"] = {
            "workspace": str(self.root / "joint"),
            "run_dir": str(self.root / "joint/run"),
            "started": True,
        }
        views = {"api": {"done": True, "approved_contract": approved, "displayed_plan": displayed}}
        joint_view = {"done": False, "needs": {"kind": "continue"}, "approved_contract": approved}
        with patch.object(coordinate, "handle", return_value=Mock(status=Mock(return_value=joint_view))):
            step = coordinate.next_step(self.document, views)
        self.assertEqual("bind_candidates", step["kind"])

    def test_dirty_original_is_not_silently_replaced_by_head(self):
        entry = self.repository()
        (Path(entry["origin"]) / "code.py").write_text("uncommitted bug reproduction\n")
        with self.assertRaisesRegex(store.CoordinationError, "local product edits"):
            coordinate.add_member(self.document, "api", entry, "fix it")
        self.assertEqual([], self.document["members"])

    def test_member_orchestration_context_is_bound_before_its_first_model_call(self):
        for target in ("checkout", "history"):
            coordinate.add_member(self.document, target, self.repository(target), "Fix the real bug")

        def started(workspace, brief, **options):
            shown = member_context.context({"workspace": workspace, "run_dir": options["run_dir"]})
            self.assertIsNotNone(shown, "The first Requirements call must know who owns joint proof")
            self.assertEqual(["checkout", "history"], shown["members"])
            self.assertIsNotNone(self.document["pending"], "Membership does not certify a successful launch")

        with (
            patch.object(coordinate.store, "save"),
            patch.object(coordinate.repos, "prepare"),
            patch.object(coordinate, "options", return_value=()),
            patch.object(coordinate.tasks.TaskRun, "start", side_effect=started),
        ):
            for member in self.document["members"]:
                coordinate.start_member(self.document, member)

    def test_descriptive_target_uses_exact_catalog_names_before_a_suffix_fallback(self):
        checkout = self.repository("checkout")
        literal = self.repository("literal/checkout (formatter)")
        self.assertEqual([literal], repos.candidates("checkout (formatter)", [checkout, literal]))
        self.assertEqual([checkout], repos.candidates("checkout (formatter)", [checkout]))
        another = self.repository("another/checkout", "other")
        self.assertEqual([checkout, another], repos.candidates("checkout (formatter)", [checkout, another]))
        self.assertEqual([], repos.candidates("../checkout (formatter)", [checkout]))

    def test_descriptive_alias_reuses_the_existing_member_for_scope_assignment(self):
        checkout = self.repository("checkout")
        history = self.repository("history")
        for alias in ("checkout repository", "checkout (formatter)", "Coordinated checkout repository"):
            with self.subTest(alias=alias):
                document = copy.deepcopy(self.document)
                coordinate.add_member(document, "checkout", checkout, "Format the receipt")
                coordinate.add_member(document, "history", history, "Serialize the receipt")
                document["initial_scope"] = [{"target": alias, "work": "Format the receipt"}]
                with patch.object(repos, "catalog", return_value=[checkout]):
                    found = coordinate.resolve_request(document, {})
                self.assertEqual("alias", found["kind"])
                self.assertEqual("checkout", found["canonical"])
                document["choices"][alias] = found["origin"]
                self.assertEqual({}, coordinate.requests(document, {}))
                for member in document["members"]:
                    member["started"] = True
                views = {
                    "checkout": {"needs": {"kind": "approve_plan"}},
                    "history": {
                        "scope": {"identity": "current", "decision": None, "external_parts": [{"target": alias}]},
                        "needs": {"kind": "answer"},
                    },
                }
                step = coordinate.next_step(document, views)
                self.assertEqual("assign", step["kind"])
                self.assertEqual({alias: "checkout"}, step["aliases"])
                self.assertEqual({alias: document["members"][0]["run_dir"]}, step["members"])
                self.assertEqual(2, len(document["members"]), "An alias does not create another task")

    def test_repository_noun_fallback_preserves_exact_names_and_ambiguity(self):
        checkout = self.repository("checkout")
        another = self.repository("other/checkout", "other")
        for suffix in ("repository", "repo"):
            target = "checkout " + suffix
            literal = self.repository("literal/" + target)
            self.assertEqual([literal], repos.candidates(target, [checkout, literal]))
            self.assertEqual([checkout], repos.candidates(target.upper(), [checkout]))
            self.assertEqual([checkout, another], repos.candidates(target, [checkout, another]))
        for target in (
            "../checkout repository",
            "/checkout repository",
            "https://host/checkout repository",
            "checkout service",
            "unknown repository",
        ):
            self.assertEqual([], repos.candidates(target, [checkout]))

    def test_coordinated_repository_description_preserves_catalog_priority_and_refusals(self):
        checkout = self.repository("checkout")
        another = self.repository("other/checkout", "other")
        named = self.repository("named/Coordinated checkout")
        for suffix in ("repository", "repo"):
            target = "Coordinated checkout " + suffix
            literal = self.repository("literal/" + target)
            self.assertEqual([literal], repos.candidates(target, [checkout, named, literal]))
            self.assertEqual([named], repos.candidates(target, [checkout, named]))
            self.assertEqual([checkout], repos.candidates(target.upper(), [checkout]))
            self.assertEqual([checkout, another], repos.candidates(target, [checkout, another]))
        for target in (
            "Coordinated ../checkout repository",
            "Coordinated /checkout repository",
            "Coordinated ~checkout repository",
            "Coordinated https://host/checkout repository",
            "Coordinated checkout service",
            "Uncoordinated checkout repository",
            "Coordinated coordinated checkout repository",
            "Coordinated unknown repository",
        ):
            self.assertEqual([], repos.candidates(target, [checkout]), target)

    def member_document(self, *targets):
        self.entries = {target: self.repository(target) for target in targets}
        for target, entry in self.entries.items():
            coordinate.add_member(self.document, target, entry, "Fix the real bug")
        return self.document

    def test_a_member_role_label_reuses_the_linked_member(self):
        self.member_document("checkout", "history", "tax")
        checkout, history, tax = (self.entries[name] for name in ("checkout", "history", "tax"))
        # The live attempt-5 label, and its bare form (#2420).
        for alias in ("Checkout display member", "checkout member"):
            with self.subTest(alias=alias):
                document = copy.deepcopy(self.document)
                document["initial_scope"] = [{"target": alias, "work": "Produce the final '$110.00' display"}]
                with patch.object(repos, "catalog", return_value=[checkout, history, tax]):
                    found = coordinate.resolve_request(document, {})
                self.assertEqual("alias", found["kind"])
                self.assertEqual("checkout", found["canonical"])
                document["choices"][alias] = found["origin"]
                self.assertEqual({}, coordinate.requests(document, {}))
                for member in document["members"]:
                    member["started"] = True
                views = {
                    "tax": {
                        "scope": {"identity": "current", "decision": None, "external_parts": [{"target": alias}]},
                        "needs": {"kind": "answer"},
                    },
                }
                views.update({name: {"needs": {"kind": "approve_plan"}} for name in ("checkout", "history")})
                step = coordinate.next_step(document, views)
                self.assertEqual("assign", step["kind"])
                self.assertEqual({alias: "checkout"}, step["aliases"])
                self.assertEqual(3, len(document["members"]), "A role label creates no duplicate task")

    def test_a_member_role_label_naming_a_second_repository_refuses(self):
        self.member_document("checkout", "history")
        checkout, history = self.entries["checkout"], self.entries["history"]
        self.document["initial_scope"] = [{"target": "checkout history member", "work": "Fix both displays"}]
        with patch.object(repos, "catalog", return_value=[checkout, history]):
            need = coordinate.resolve_request(self.document, {})
        self.assertEqual(
            ("repository", [], "Repository is not available in the authorized local catalog"),
            (need["kind"], need["candidates"], need["reason"]),
        )

    def test_catalog_ambiguity_for_the_full_target_is_never_overridden_by_the_fallback(self):
        self.member_document("checkout")
        checkout = self.entries["checkout"]
        first = self.repository("one/Checkout display member")
        second = self.repository("two/Checkout display member", "another")
        self.document["initial_scope"] = [{"target": "Checkout display member", "work": "Produce the display"}]
        with patch.object(repos, "catalog", return_value=[checkout, first, second]):
            need = coordinate.resolve_request(self.document, {})
        self.assertEqual("repository", need["kind"])
        self.assertEqual("Repository match is ambiguous", need["reason"])
        self.assertEqual([first["origin"], second["origin"]], [row["workspace"] for row in need["candidates"]])

    def test_a_member_role_label_enumerates_every_authorized_prefix_identity(self):
        self.member_document("checkout")
        checkout = self.entries["checkout"]
        display = self.repository("literal/checkout display")
        self.document["initial_scope"] = [{"target": "checkout display member", "work": "Format the receipt"}]
        with patch.object(repos, "catalog", return_value=[checkout, display]):
            need = coordinate.resolve_request(self.document, {})
        self.assertEqual("repository", need["kind"])
        self.assertEqual("Repository match is ambiguous", need["reason"])
        # Catalog order, never set-iteration order, so the public offer is stable.
        self.assertEqual([checkout["origin"], display["origin"]], [row["workspace"] for row in need["candidates"]])
        # The person's choice of the unlinked identity starts its discovery instead.
        self.document["choices"]["checkout display member"] = display["origin"]
        with patch.object(repos, "catalog", return_value=[checkout, display]):
            found = coordinate.resolve_request(self.document, {})
        self.assertEqual("resolve", found["kind"])
        self.assertEqual(display["git_dir"], found["entry"]["git_dir"])

    def test_a_multiword_alias_takes_the_longest_bounded_match(self):
        self.member_document("checkout")
        checkout = self.entries["checkout"]
        display = self.repository("literal/checkout-display")
        # The catalog alias is the package name, a remote-safe repository identity.
        display_root = self.root / "literal" / "checkout-display"
        (display_root / "package.json").write_text('{"name": "checkout display"}')
        repos.git(display_root, "add", "--all")
        repos.git(display_root, "commit", "-qm", "package identity")
        coordinate.add_member(self.document, "checkout display", display, "Fix the real bug")
        catalog = [checkout, display]
        # Two job words fit the "checkout display" alias; the same tail is three
        # words past "checkout", so only the longer authorized identity matches.
        with patch.object(repos, "catalog", return_value=catalog):
            found = coordinate.resolve_request(
                {**self.document, "initial_scope": [{"target": "checkout display final view member", "work": "w"}]},
                {},
            )
        self.assertEqual("alias", found["kind"])
        self.assertEqual("checkout display", found["canonical"])

    def test_a_job_phrase_naming_another_repository_refuses(self):
        self.member_document("checkout")
        checkout = self.entries["checkout"]
        billing = self.repository("literal/billing api")
        for target in ("checkout billing api member", "checkout . member", "checkout .. member"):
            with self.subTest(target=target):
                with patch.object(repos, "catalog", return_value=[checkout, billing]):
                    need = coordinate.resolve_request(
                        {**self.document, "initial_scope": [{"target": target, "work": "w"}]}, {}
                    )
                self.assertEqual(("repository", []), (need["kind"], need["candidates"]), target)

    def test_a_member_role_label_requires_a_current_catalog_identity(self):
        self.member_document("checkout", "history")
        history = self.entries["history"]
        self.document["initial_scope"] = [{"target": "Checkout display member", "work": "Produce the display"}]
        with patch.object(repos, "catalog", return_value=[history]):  # checkout left the authorized catalog
            need = coordinate.resolve_request(self.document, {})
        self.assertEqual(("repository", []), (need["kind"], need["candidates"]))

    def test_a_repository_literally_named_like_a_role_label_wins_the_exact_match(self):
        self.member_document("checkout")
        checkout = self.entries["checkout"]
        literal = self.repository("literal/Checkout display member")
        self.document["initial_scope"] = [{"target": "Checkout display member", "work": "Produce the display"}]
        with patch.object(repos, "catalog", return_value=[checkout, literal]):
            found = coordinate.resolve_request(self.document, {})
        self.assertEqual("resolve", found["kind"])
        self.assertEqual(literal["git_dir"], found["entry"]["git_dir"])

    def test_an_unnamed_role_label_stays_an_unavailable_catalog_gate(self):
        self.member_document("checkout", "history")
        checkout, history = self.entries["checkout"], self.entries["history"]
        self.document["initial_scope"] = [{"target": "Receipt-handling member", "work": "Preserve receipts"}]
        with patch.object(repos, "catalog", return_value=[checkout, history]):
            need = coordinate.resolve_request(self.document, {})
        self.assertEqual(
            ("repository", [], "Repository is not available in the authorized local catalog"),
            (need["kind"], need["candidates"], need["reason"]),
        )

    def test_alias_receipt_must_bind_the_existing_canonical_member_run(self):
        alias = "checkout (formatter)"
        state = {
            "run_dir": str(self.root / "run"),
            "workspace_scope": {
                "turn": 0,
                "spans_repositories": True,
                "external_parts": [{"target": alias, "work": "Format the receipt"}],
            },
        }
        receipt = {
            "identity": assignment.scope.identity(state),
            "members": {alias: "/runs/checkout"},
            "aliases": {alias: "checkout"},
        }
        record = {"automatic_anchor": "anchor", "scope_assignments": {state["run_dir"]: receipt}}
        with patch.object(assignment.store, "find", return_value=record):
            with patch.object(
                assignment.store, "members", return_value=[{"target": "checkout", "run_dir": "/runs/checkout"}]
            ):
                self.assertEqual(receipt, assignment.current(state))
                receipt["aliases"][alias] = "unregistered"
                self.assertIsNone(assignment.current(state))
                receipt["aliases"][alias] = "checkout"
                receipt["members"][alias] = "/runs/another"
                self.assertIsNone(assignment.current(state))

    def test_reserved_worktree_cannot_redirect_to_the_original_checkout(self):
        entry = self.repository()
        destination = self.root / "reserved"
        destination.symlink_to(entry["origin"], target_is_directory=True)
        with self.assertRaisesRegex(store.CoordinationError, "redirect"):
            repos.prepare(entry, destination)
        self.assertEqual("result = 1\n", (Path(entry["origin"]) / "code.py").read_text())

    def test_redirected_worktree_parent_cannot_create_an_external_checkout(self):
        entry = self.repository()
        external = self.root / "external"
        external.mkdir()
        parent = self.root / "redirected"
        parent.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(store.CoordinationError, "redirect"):
            repos.prepare(entry, parent / "member")
        self.assertFalse((external / "member").exists())

    def test_original_checkout_or_primary_git_directory_is_not_a_prepared_worktree(self):
        entry = self.repository()
        alias = self.root / "primary-alias"
        alias.mkdir()
        (alias / ".git").write_text("gitdir: " + entry["git_dir"] + "\n")
        for destination in (Path(entry["origin"]), alias):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(store.CoordinationError, "isolated"):
                    repos.prepare(entry, destination)

    def test_prepared_worktree_reuse_preserves_pending_edits_and_original_source(self):
        entry = self.repository()
        destination = self.root / "worktrees" / "member"
        repos.prepare(entry, destination)
        (destination / "code.py").write_text("result = 2\n")
        repos.prepare(entry, destination)
        self.assertEqual(entry["base"], repos.git(destination, "rev-parse", "HEAD"))
        self.assertEqual("", repos.git(destination, "symbolic-ref", "--quiet", "HEAD", accepted=(0, 1)))
        self.assertEqual("result = 2\n", (destination / "code.py").read_text())
        self.assertEqual("result = 1\n", (Path(entry["origin"]) / "code.py").read_text())

    def test_redirected_member_is_refused_before_status_or_publication(self):
        entry = self.repository()
        coordinate.add_member(self.document, "api", entry, "Fix its contract")
        member = self.document["members"][0]
        destination = Path(member["workspace"])
        repos.prepare(member, destination)
        destination.rename(self.root / "moved-worktree")
        destination.symlink_to(entry["origin"], target_is_directory=True)
        client = Mock()
        with patch.object(coordinate.tasks, "TaskRun") as task:
            with self.assertRaisesRegex(store.CoordinationError, "redirect"):
                coordinate.handle(member)
            with self.assertRaisesRegex(store.CoordinationError, "redirect"):
                publication.publish_one(self.document, member, client=client)
            task.assert_not_called()
        self.assertFalse(client.mock_calls)

    def test_only_owned_chats_offer_automatic_coordination(self):
        state = {
            "status": "PAUSED_QUESTION",
            "workflow": {"kind": "bugfix", "source": "user", "reason": ""},
            "turns": [],
            "workspace_scope": {
                "turn": 0,
                "spans_repositories": True,
                "external_parts": [{"target": "history", "work": "Fix its contract"}],
            },
        }
        run_dir = self.root / "runs" / "anchor"
        self.assertIsNotNone(run_view.view(state, run_dir=run_dir)["coordination"])
        state["execution"] = {
            "version": 1,
            "mode": "durable_local",
            "launch_id": "0123456789abcdef0123456789abcdef",
            "controller_epoch": "0123456789abcdef0123456789abcdef",
        }
        durable = run_view.view(state, run_dir=run_dir)
        self.assertEqual("durable_local", durable["execution"]["mode"])
        self.assertIsNone(durable["coordination"])
        self.assertTrue(durable["scope"]["spans_repositories"])
        with patch.object(coordinate, "anchor_view", return_value=durable):
            with self.assertRaisesRegex(store.CoordinationError, "owned terminal chat"):
                coordinate.initialize(run_dir)
        # Even an existing record may not offer an unsupported executable route.
        with patch.object(store, "read", return_value={"phase": "fixing", "members": []}):
            self.assertIsNone(run_view.view(state, run_dir=run_dir)["coordination"])

    def test_changed_anchor_scope_cannot_publish_the_old_request(self):
        self.document.update(anchor_turn=1, anchor_scope="shown")
        view = {"turn": 1, "task": self.document["task"], "scope": {"identity": "shown", "decision": None}}
        with patch.object(coordinate, "anchor_view", return_value=view):
            self.assertEqual({}, coordinate.observe(self.document))
            view["scope"]["decision"] = "this_repository"
            with self.assertRaisesRegex(store.CoordinationError, "scope decision changed"):
                coordinate.observe(self.document)

    def test_answered_member_scope_is_not_reassigned_on_every_refresh(self):
        self.document["members"] = [
            {"target": name, "started": True, "run_dir": str(self.root / name)} for name in ("api", "history")
        ]
        views = {
            "api": {
                "scope": {
                    "identity": "current-scope",
                    "external_parts": [{"target": "history"}],
                    "decision": "this_repository",
                },
                "needs": {"kind": "continue"},
            },
            "history": {"needs": {"kind": "approve_plan"}},
        }
        with patch.object(coordinate.actions, "working", return_value=False):
            self.assertEqual("advance", coordinate.next_step(self.document, views)["kind"])

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

    def test_finishing_joint_rework_retires_the_old_approval_participant(self):
        self.document.update(
            changeset="set-1",
            members=[{"target": "api", "run_dir": "/api/run"}],
            joint={"run_dir": "/joint/run", "mismatch_targets": ["api"]},
            joint_attempts=[{"run_dir": "/joint/run"}],
            rework={"targets": ["api"], "remaining": [{"target": "api"}], "phase": "correcting_mismatch"},
        )
        with patch.object(coordinate.sets, "remove_joint", return_value=True) as remove:
            coordinate.finish_rework_step(self.document, "api")
        remove.assert_called_once_with("/api/run", "/joint/run")
        self.assertNotIn("joint", self.document)

    def test_stale_approval_cannot_approve_a_member(self):
        store.save(self.document)
        with (
            patch.object(coordinate, "status", return_value={"approval": {"token": "new"}}),
            patch.object(coordinate.changesets, "approve") as approve,
        ):
            with self.assertRaisesRegex(store.CoordinationError, "changed after display"):
                coordinate.approve(self.document["anchor"], "old")
        approve.assert_not_called()

    def test_joint_plan_reserves_candidate_copy_scope_before_inputs_exist(self):
        text = joint.brief(self.document)
        self.assertIn("affected_paths", text)
        self.assertIn("repos/ subtrees", text)
        self.assertIn("before candidate inputs exist", text)

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

    def test_follow_up_receipt_ignores_appended_historical_resolver_display_rows(self):
        view = {
            "turn": 2,
            "chat": {
                "messages": [
                    {"id": "feedback-current", "role": "user", "text": "Correct history"},
                    {"id": "resolver-old-response", "role": "user", "text": "The prior runtime was repaired"},
                ]
            },
        }
        self.assertTrue(coordinate.follow_up_received(view, 1, "Correct history"))
        self.assertFalse(coordinate.follow_up_received(view, 0, "Correct history"))
        self.assertFalse(coordinate.follow_up_received(view, 1, "The prior runtime was repaired"))
        view["chat"]["messages"].insert(1, {"id": "feedback-newer", "role": "user", "text": "Another request"})
        self.assertFalse(coordinate.follow_up_received(view, 1, "Correct history"))
        self.assertTrue(coordinate.follow_up_received(view, 1, "Another request"))

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
