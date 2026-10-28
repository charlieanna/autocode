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
import autocode_coordination_context as member_context
import autocode_coordination_joint as joint
import autocode_coordination_publish as publication
import autocode_coordination_repositories as repos
import autocode_coordination_store as store
import autocode_github as github
import autocode_goals as goals
import autocode_related_profile as profiles
import autocode_run_view as run_view
import autocode_scope_assignment as assignment


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
                },
            ),
            patch.object(coordinate, "status", return_value={}),
        ):
            coordinate.advance(self.document["anchor"])
        member = store.read(self.document["anchor"])["members"][0]
        self.assertEqual(self.document["task"], member["brief"])
        self.assertEqual(hint, member["reported_work"])
        with self.assertRaisesRegex(ValueError, "source_quote is not in"):
            goals.check_requirement_handoff(
                {"task": member["brief"]},
                {"requirements": [{"id": "R1", "text": hint, "source_quote": hint}]},
            )

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
