"""Controlled original-byte tests only; no native image/protection qualification."""

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import autocode_util as util
import autocode_visual_evidence as evidence
import autocode_visual_history as history

from tests.visual_capture_fixtures import make_capture


class OriginalCaptureBytesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="visual-history-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / ".autocode" / "runs" / "unit"
        self.run.mkdir(parents=True)
        (self.root / "page.js").write_text("original served bytes\n")
        source = {"head": "controlled-original", "files": {"page.js": util.file_hash(self.root / "page.js")}}
        self.original = {**source, "revision": util.digest(source)}
        self.case = {
            "id": "only",
            "route": "/only",
            "state": "only",
            "viewport": {"width": 2, "height": 1, "device_scale_factor": 1},
        }
        self.reference_hash = "a" * 64
        self.state = {"workspace": str(self.root), "settings": {"figma_file": "controlled"}}
        with (
            patch.object(evidence, "reference_hash", return_value=self.reference_hash),
            patch.object(util, "snapshot", return_value=self.original),
        ):
            self.selected = make_capture(self.root, self.reference_hash, self.case, asset="page.js")
            body, refs = evidence.verify(
                self.state,
                self.selected["capture_ref"],
                self.selected["capture_sha256"],
                case=self.case,
                current=self.original,
            )
        self.original_bytes = {str(Path(name)): Path(name).read_bytes() for name in refs}
        self.engine = deepcopy(body["engine"])

    def verify_original(self, *, case=None, snapshot=None):
        with patch.object(evidence, "reference_hash", return_value=self.reference_hash):
            return evidence.verify_original_bytes(
                self.state,
                self.selected["capture_ref"],
                self.selected["capture_sha256"],
                case=case or self.case,
                current=snapshot or self.original,
                read_bytes=lambda name: self.original_bytes[name],
                engine_hashes=self.engine,
            )

    def test_original_bytes_survive_later_source_write_without_becoming_current(self):
        (self.root / "page.js").write_text("authorized later source bytes\n")
        body, refs = self.verify_original()
        self.assertEqual(self.original["revision"], body["source_revision"])
        current = {"head": self.original["head"], "files": {"page.js": util.file_hash(self.root / "page.js")}}
        current["revision"] = util.digest(current)
        with patch.object(evidence, "reference_hash", return_value=self.reference_hash), self.assertRaises(ValueError):
            evidence.verify(
                self.state,
                self.selected["capture_ref"],
                self.selected["capture_sha256"],
                case=self.case,
                current=current,
            )
        self.assertNotEqual(current["revision"], body["source_revision"])

    def test_wrong_original_source_is_rejected(self):
        changed = {**self.original, "revision": "b" * 64}
        with self.assertRaisesRegex(ValueError, "Stale implementation capture"):
            self.verify_original(snapshot=changed)

    def test_retained_input_change_is_rejected(self):
        body = json.loads(self.original_bytes[self.selected["capture_ref"]])
        path = str(self.root / next(iter(body["inputs"])))
        self.original_bytes[path] += b"changed"
        with self.assertRaisesRegex(ValueError, "Capture input changed"):
            self.verify_original()

    def test_missing_original_input_and_changed_engine_do_not_get_substituted_from_live_files(self):
        body = json.loads(self.original_bytes[self.selected["capture_ref"]])
        path = str(self.root / next(iter(body["inputs"])))
        del self.original_bytes[path]
        with self.assertRaises(KeyError):
            self.verify_original()
        self.original_bytes[path] = Path(path).read_bytes()
        self.engine["autocode_visual_browser.cjs"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "Capture engine changed"):
            self.verify_original()

    def test_case_route_viewport_drift_and_reader_escape_are_refused(self):
        for key, value in (
            ("route", "/wrong"),
            ("state", "wrong"),
            ("viewport", {"width": 3, "height": 1, "device_scale_factor": 1}),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify_original(case={**self.case, key: value})
        with self.assertRaises(ValueError):
            evidence.verify_original_bytes(
                self.state,
                "/outside/manifest.json",
                current=self.original,
                read_bytes=lambda name: b"{}",
                engine_hashes=self.engine,
            )

    def test_missing_real_namespace_protection_cannot_construct_history(self):
        for dependency in (None, True, {}, {"verified": True}):
            with self.subTest(dependency=dependency), self.assertRaises(util.Paused):
                history.NativeReviewHistory(self.root, self.run, protection=dependency)

    def test_a_forged_checkpoint_handle_cannot_commit_or_create_history(self):
        class NoAuthority:
            def verify_namespace(self, **kwargs):
                raise AssertionError("A forged handle must fail before protection authentication")

        owner = history.NativeReviewHistory(self.root, self.run, protection=NoAuthority())
        handle = history.PreparedCheckpoint(owner, "not-prepared", b"{}", b"{}", "a" * 64, "b" * 64, "c" * 64)
        before = sorted(self.run.rglob("*"))
        with self.assertRaises(util.Paused):
            owner.commit_checkpoint(handle, {}, snapshot=self.original)
        self.assertEqual(before, sorted(self.run.rglob("*")))

    def test_initial_assignment_requires_actual_approval_not_an_existing_task_or_saved_hint(self):
        class NoAuthority:
            def verify_namespace(self, **kwargs):
                raise AssertionError("No native review authority before initial approval")

        owner = history.NativeReviewHistory(self.root, self.run, protection=NoAuthority())
        with patch.object(util, "snapshot", return_value=self.original):
            for state in ({}, {"current_task": {"id": "already-assigned"}}, {"approval_status": "approved"}):
                with self.subTest(state=state), self.assertRaises(util.Paused):
                    owner.authorize_initial_assignment(state, {}, snapshot=self.original)


class HistoryPartitionRefusalTests(unittest.TestCase):
    """Selection/refusal tests only; controlled metadata is not native proof.

    These tests never create accepted image authority. Native replay seams are
    stubbed only to isolate foreign-contract exclusion or explicit refusal.
    All outputs granting owners/current images remain empty in fresh_facts.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="visual-history-partition-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / ".autocode" / "runs" / "unit"
        self.run.mkdir(parents=True)

        class NoAuthority:
            def verify_namespace(self, **kwargs):
                raise AssertionError("Selection tests cannot grant native namespace authority")

        self.owner = history.NativeReviewHistory(self.root, self.run, protection=NoAuthority())
        self.source = {"head": "controlled", "files": {}}
        self.source["revision"] = util.digest(self.source)
        mapping = {"shared-id": ["C1"], "future": ["C2"]}
        self.manifest = {
            "version": 1,
            "cases": [
                {"id": "shared-id", "implementation_paths": ["ui/first.py"]},
                {"id": "future", "implementation_paths": ["ui/second.py"]},
            ],
        }
        body = {
            "constraints": [
                history.scope.MARKER + history.scope.POLICY,
                history.scope.CASE_MARKER + json.dumps(mapping),
            ],
            "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}],
            "milestones": [
                {"id": "M1", "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": ["ui/first.py"]},
                {"id": "M2", "acceptance_criteria": ["C2"], "depends_on": ["M1"], "affected_paths": ["ui/second.py"]},
            ],
            "initial_task": {"milestone_id": "M1"},
        }
        self.contract = {"task_id": "product", "revision": 2, "body": body}
        self.old_contract = {**deepcopy(self.contract), "revision": 1}
        self.state = {"goal_contract": self.contract, "settings": {"design_manifest": {"body": self.manifest}}}

    def selector(self, name):
        return {"kind": "reviews", "path": str(self.run / name), "sha256": util.digest(name)}

    def original(self, contract, owner="M1"):
        # This is controlled partition metadata, not a native verdict/receipt.
        return {
            "metadata": {"binding": {"contract": deepcopy(contract), "task": {"milestone_id": owner}}},
            "case_footprints": {"foreign-case-not-in-new-roster": {"input_hashes": {}}},
            "protection_proof": {"evidence_hashes": {}},
        }

    def test_state_file_path_is_refused_before_physical_or_retained_byte_read(self):
        path = self.run / "state.json"  # Never created or read.
        functions = (
            lambda: self.owner._safe(path),
            lambda: self.owner._live_bytes(path),
            lambda: self.owner._reader({"member_hashes": {str(path): "a" * 64}}, {str(path): b"{}"})(path),
        )
        with patch.object(Path, "read_bytes", side_effect=AssertionError("No state file read")):
            for function in functions:
                with self.subTest(function=function), self.assertRaisesRegex(util.Paused, "state-file evidence"):
                    function()

    def test_foreign_contract_current_cases_are_empty_without_write_journal_or_runtime_access(self):
        original = self.original(self.old_contract)
        with (
            patch.object(self.owner, "_writes_since_review", side_effect=AssertionError("Foreign current proof")),
            patch.object(history, "runtime_files", side_effect=AssertionError("Foreign runtime freshness")),
        ):
            self.assertEqual([], self.owner._current_cases(self.state, self.source, original, evidence_pins={}))

    def test_foreign_original_remains_verified_but_cannot_contribute_case_ids_or_input_eligibility(self):
        selector = self.selector("old")
        original = self.original(self.old_contract)
        with (
            patch.object(util, "snapshot", return_value=self.source),
            patch.object(history.design, "verify", return_value=None),
            patch.object(self.owner, "_selectors", side_effect=lambda kind: [selector] if kind == "reviews" else []),
            patch.object(self.owner, "_verify_review", return_value=original) as verify,
            patch.object(self.owner, "_retained_pins", return_value={}),
            patch.object(self.owner, "_current_cases", side_effect=AssertionError("Foreign case lookup")),
            patch.object(history, "runtime_files", return_value={}),
            patch.object(history, "_task_binding", return_value={"controlled": "no-native-authority"}),
        ):
            facts = self.owner.fresh_facts(state=self.state, snapshot=self.source)
        verify.assert_called_once()
        self.assertEqual(selector, verify.call_args.args[0])
        self.assertEqual([], facts["historical_milestone_ids"])
        self.assertEqual([], facts["eligible_milestone_ids"])
        self.assertEqual([], facts["current_case_ids"])
        self.assertEqual({}, facts["case_input_paths"])

    def test_corrupt_foreign_original_still_holds_instead_of_being_silently_skipped(self):
        selector = self.selector("old")
        with (
            patch.object(util, "snapshot", return_value=self.source),
            patch.object(history.design, "verify", return_value=None),
            patch.object(self.owner, "_selectors", side_effect=lambda kind: [selector] if kind == "reviews" else []),
            patch.object(
                self.owner,
                "_verify_review",
                side_effect=util.Paused("PAUSED_VISUAL_EVIDENCE", "Original protected bytes changed"),
            ),
        ):
            with self.assertRaisesRegex(util.Paused, "Original protected bytes changed"):
                self.owner.fresh_facts(state=self.state, snapshot=self.source)

    def test_new_scope_basis_excludes_foreign_and_future_reviews_after_verifying_every_original(self):
        selectors = [self.selector(name) for name in ("foreign", "own", "future")]
        rows = [self.original(self.old_contract), self.original(self.contract), self.original(self.contract, "M2")]
        admission = history.scope.admit(self.contract["body"], self.manifest)
        with (
            patch.object(self.owner, "_selectors", return_value=selectors),
            patch.object(self.owner, "_verify_review", side_effect=rows) as verify,
        ):
            selected = self.owner._scope_input_review_selectors(self.contract, "M1", admission)
        self.assertEqual([selectors[1]], selected)
        self.assertEqual(selectors, [call.args[0] for call in verify.call_args_list])


class CommittedRosterTests(unittest.TestCase):
    """Controlled roster semantics only, not native launch/image proof."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name).resolve()
        self.run = self.workspace / ".autocode" / "runs" / "roster"
        self.run.mkdir(parents=True)
        self.rows = []
        outer = self

        class ControlledRoster:
            def verify_namespace(self, **_query):
                raise AssertionError("Roster tests grant no native replay authority")

            def committed_selectors(self, kind):
                return outer.rows

        self.owner = history.NativeReviewHistory(self.workspace, self.run, protection=ControlledRoster())

    def copied(self, label, kind="reviews"):
        raw = label.encode()
        pin = hashlib.sha256(raw).hexdigest()
        path = self.owner.root / kind / pin / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return {"kind": kind, "path": str(path), "sha256": pin}

    def test_uncommitted_physical_copy_cannot_become_scheduling_input(self):
        pending = self.copied("pending")
        self.assertEqual([], self.owner._selectors("reviews"))
        self.assertTrue(Path(pending["path"]).is_file())

    def test_committed_roster_is_returned_detached_in_original_order(self):
        self.rows = [self.copied("second"), self.copied("first")]
        selected = self.owner._selectors("reviews")
        self.assertEqual(self.rows, selected)
        selected[0]["path"] = "changed"
        self.assertNotEqual(selected, self.rows)

    def test_missing_original_committed_copy_blocks_instead_of_being_omitted(self):
        self.rows = [self.copied("missing")]
        Path(self.rows[0]["path"]).unlink()
        with self.assertRaises(util.Paused):
            self.owner._selectors("reviews")

    def test_changed_original_committed_bytes_block(self):
        self.rows = [self.copied("before")]
        Path(self.rows[0]["path"]).write_bytes(b"after")
        with self.assertRaises(util.Paused):
            self.owner._selectors("reviews")

    def test_repeated_committed_selector_blocks(self):
        row = self.copied("one")
        self.rows = [row, dict(row)]
        with self.assertRaises(util.Paused):
            self.owner._selectors("reviews")

    def test_foreign_namespace_and_wrong_kind_block(self):
        row = self.copied("foreign", kind="checkpoints")
        for selector in (row, {**row, "kind": "reviews"}):
            self.rows = [selector]
            with self.subTest(selector=selector), self.assertRaises(util.Paused):
                self.owner._selectors("reviews")

    def test_missing_guarded_roster_and_malformed_roster_block(self):
        del self.owner.protection.__class__.committed_selectors
        with self.assertRaises(util.Paused):
            self.owner._selectors("reviews")
        for rows in (None, {}, [None], [{"kind": "reviews", "path": "", "sha256": "no"}]):
            self.owner.protection.committed_selectors = lambda _kind, rows=rows: rows
            with self.subTest(rows=rows), self.assertRaises(util.Paused):
                self.owner._selectors("reviews")

    def test_symlinked_committed_copy_blocks(self):
        self.rows = [self.copied("original")]
        path = Path(self.rows[0]["path"])
        target = path.with_name("target.json")
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(util.Paused):
            self.owner._selectors("reviews")


class RetainedOperatorTaskCriteriaTests(unittest.TestCase):
    """Pure original-task compatibility, never native replay qualification."""

    def test_original_canonical_task_and_equal_aliases_remain_detached(self):
        for task in (
            {"acceptance_criteria": ["C1", "C2"]},
            {"acceptance_criteria": ["C1", "C2"], "criterion_ids": ["C1", "C2"]},
            {"criterion_ids": ["C1", "C2"]},
        ):
            original = deepcopy(task)
            selected = history.task_criteria.ids(task)
            self.assertEqual(["C1", "C2"], selected)
            selected.append("C3")
            self.assertEqual(original, task)

    def test_original_conflict_or_malformed_field_never_borrows_another_alias(self):
        for task in (
            {},
            {"acceptance_criteria": None, "criterion_ids": ["C1", "C2"]},
            {"acceptance_criteria": [], "criterion_ids": ["C1", "C2"]},
            {"acceptance_criteria": ["C1", "C2"], "criterion_ids": ["C2", "C1"]},
            {"acceptance_criteria": ["C1", "C2"], "criterion_ids": ["C1", "C2", "C2"]},
            {"acceptance_criteria": ["C1", "C2"], "criterion_ids": None},
            {"acceptance_criteria": ["C1", "C1"]},
            {"criterion_ids": ["C1", "C1"]},
            {"acceptance_criteria": ["C1", 2]},
            {"criterion_ids": "C1"},
            {"acceptance_criteria": ["C1", " "]},
        ):
            original = deepcopy(task)
            with self.subTest(task=task), self.assertRaises(ValueError):
                history.task_criteria.ids(task)
            self.assertEqual(original, task)


class FreshRuntimeFilesTests(unittest.TestCase):
    """Real filesystem selection and freshness; no native authority or timing claim."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="runtime-files-unit-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "runtime"
        self.root.mkdir()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_exact_code_selection_and_excluded_subtrees_are_not_visited(self):
        contents = {
            ".hidden.cjs": b"hidden",
            "a/nested.mjs": b"nested",
            "a.py": b"python",
            "node_modules.py": b"ordinary filename",
        }
        for name, content in contents.items():
            self.write(name, content)
        for name in (
            "notes.txt",
            "upper.PY",
            "__pycache__/ignored.py",
            "pkg/node_modules/deep/ignored.cjs",
            "pkg/__pycache__/ignored.mjs",
        ):
            self.write(name, b"excluded")
        (self.root / "directory.py").mkdir()
        original_scandir = history.os.scandir
        visited = []

        def observe(path):
            visited.append(Path(path))
            return original_scandir(path)

        with patch.object(history.os, "scandir", side_effect=observe):
            actual = history.runtime_files(self.root)
        self.assertEqual({name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}, actual)
        self.assertEqual(list(contents), list(actual), "Keep original sorted-Path insertion order")
        self.assertTrue(visited)
        self.assertFalse(any({"__pycache__", "node_modules"}.intersection(path.parts) for path in visited))
        for excluded in ("__pycache__", "node_modules"):
            nested = self.base / excluded / "runtime"
            nested.mkdir(parents=True)
            (nested / "real.py").write_bytes(b"under excluded ancestor")
            self.assertEqual({}, history.runtime_files(nested))

    def test_file_links_are_hashed_but_directory_links_are_not_followed(self):
        self.write("real.py", b"inside")
        outside = self.base / "outside"
        outside.mkdir()
        target = outside / "target.txt"
        target.write_bytes(b"outside file")
        (outside / "unvisited.py").write_bytes(b"outside directory")
        (self.root / "linked.py").symlink_to(target)
        (self.root / "directory-link").symlink_to(outside, target_is_directory=True)
        (self.root / "directory-link.py").symlink_to(outside, target_is_directory=True)
        (self.root / "broken.py").symlink_to(outside / "missing")
        expected = {
            "linked.py": hashlib.sha256(b"outside file").hexdigest(),
            "real.py": hashlib.sha256(b"inside").hexdigest(),
        }
        self.assertEqual(expected, history.runtime_files(self.root))
        alias = self.base / "runtime-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(expected, history.runtime_files(alias), "Root resolution remains unchanged")
        target.write_bytes(b"changed file")
        expected["linked.py"] = hashlib.sha256(b"changed file").hexdigest()
        self.assertEqual(expected, history.runtime_files(self.root), "File-link targets are freshly read")

    def test_each_call_rehashes_included_files_and_detects_all_content_membership_changes(self):
        code = self.write("entry.py", b"first")
        self.write("nested/entry.cjs", b"child")
        with patch.object(util, "file_hash", wraps=util.file_hash) as hashed:
            first = history.runtime_files(self.root)
            second = history.runtime_files(self.root)
        self.assertEqual(first, second)
        self.assertEqual(
            [self.root / "entry.py", self.root / "nested/entry.cjs"] * 2,
            [call.args[0] for call in hashed.call_args_list],
        )
        first.clear()
        code.write_bytes(b"other")  # Same length; a previous map/metadata is not authority.
        third = history.runtime_files(self.root)
        self.assertEqual(hashlib.sha256(b"other").hexdigest(), third["entry.py"])
        self.assertNotEqual(second["entry.py"], third["entry.py"])
        added = self.write("new.mjs", b"new")
        self.assertIn("new.mjs", history.runtime_files(self.root))
        added.rename(self.root / "renamed.mjs")
        current = history.runtime_files(self.root)
        self.assertNotIn("new.mjs", current)
        self.assertIn("renamed.mjs", current)
        code.unlink()
        self.assertNotIn("entry.py", history.runtime_files(self.root))


if __name__ == "__main__":
    unittest.main()
