"""Go loading metadata may accompany tests; product/dependency changes may not."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import autocode_go_test_prerequisites as prerequisites

BASE = "module example.test/app\n\ngo 1.23.0\n\nrequire example.test/direct v1.0.0\n"
ADDED = BASE + "\nrequire example.test/indirect v1.2.0 // indirect\n"
SUM = "example.test/indirect v1.2.0 h1:" + "A" * 43 + "=\n"
SELECTED = {"example.test/direct": "v1.0.0", "example.test/indirect": "v1.2.0"}


class MetadataPolicyTests(unittest.TestCase):
    def test_only_additive_indirect_requirements_at_selected_versions_and_sums(self):
        self.assertEqual(
            ["go.mod", "go.sum"],
            prerequisites.metadata_changes({"go.mod": BASE, "go.sum": ""}, {"go.mod": ADDED, "go.sum": SUM}, SELECTED),
        )
        self.assertEqual(
            [],
            prerequisites.metadata_changes({"go.mod": BASE, "go.sum": SUM}, {"go.mod": BASE, "go.sum": SUM}, SELECTED),
        )

    def test_blocks_and_formatting_do_not_change_existing_requirements(self):
        after = BASE.replace(
            "require example.test/direct v1.0.0",
            "require (\n example.test/direct v1.0.0\n example.test/indirect v1.2.0 // indirect\n)",
        )
        self.assertEqual(["go.mod"], prerequisites.metadata_changes({"go.mod": BASE}, {"go.mod": after}, SELECTED))

    def test_refuses_product_dependency_or_toolchain_changes(self):
        variants = [
            ADDED.replace("v1.2.0", "v1.3.0"),
            ADDED.replace(" // indirect", ""),
            ADDED.replace("example.test/indirect", "example.test/foreign"),
            ADDED.replace("direct v1.0.0", "direct v2.0.0"),
            ADDED.replace("go 1.23.0", "go 1.24.0"),
            ADDED + "replace example.test/direct => ../fake\n",
            ADDED + "exclude example.test/direct v1.0.0\n",
            ADDED + "toolchain go1.24.0\n",
            ADDED + "require example.test/indirect v1.2.0 // indirect\n",
            ADDED.replace("module example.test/app", "module another.test/app"),
        ]
        for after in variants:
            with self.subTest(after=after), self.assertRaises(ValueError):
                prerequisites.metadata_changes({"go.mod": BASE}, {"go.mod": after}, SELECTED)

    def test_refuses_removed_or_rewritten_sums_and_unselected_checksums(self):
        for after in ["", SUM.replace("A", "B"), SUM + SUM.replace("v1.2.0", "v1.3.0"), SUM + "malformed\n"]:
            with self.subTest(after=after), self.assertRaises(ValueError):
                prerequisites.metadata_changes(
                    {"go.mod": BASE, "go.sum": SUM}, {"go.mod": BASE, "go.sum": after}, SELECTED
                )

    def test_refuses_missing_manifest_and_unsupported_syntax(self):
        for before, after in [
            ({}, {"go.mod": BASE}),
            ({"go.mod": BASE}, {}),
            ({"go.mod": BASE}, {"go.mod": ADDED + "require (\n"}),
        ]:
            with self.subTest(after=after), self.assertRaises(ValueError):
                prerequisites.metadata_changes(before, after, SELECTED)


class SelectionTests(unittest.TestCase):
    def test_native_selection_requires_successful_graph_and_unique_versioned_rows(self):
        seen = []

        def run(argv):
            seen.append(argv)
            if argv == ["go", "mod", "graph"]:
                return {"exit_code": 0, "stdout": "example.test/app example.test/direct@v1.0.0\n"}
            return {
                "exit_code": 0,
                "stdout": "\n".join(
                    json.dumps(row)
                    for row in [
                        {"Path": "example.test/app", "Main": True},
                        {"Path": "example.test/direct", "Version": "v1.0.0"},
                        {"Path": "example.test/indirect", "Version": "v1.2.0"},
                    ]
                ),
            }

        self.assertEqual(SELECTED, prerequisites.selected_versions(run))
        self.assertEqual(2, len(seen))

    def test_incomplete_or_ambiguous_selection_is_refused(self):
        bad = [
            {"exit_code": 1, "stdout": ""},
            {"exit_code": 0, "stdout": "{}"},
            {"exit_code": 0, "stdout": json.dumps({"Path": "x", "Error": {"Err": "missing"}})},
            {"exit_code": 0, "stdout": json.dumps({"Path": "x", "Version": "v1.0.0"}) * 2},
        ]
        for result in bad:
            with self.subTest(result=result), self.assertRaises(ValueError):
                prerequisites.selected_versions(lambda argv: result)


class TreePrerequisitesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.before, self.after = self.root / "base", self.root / "candidate"
        self.before.mkdir()
        self.after.mkdir()
        (self.before / "go.mod").write_text(BASE)
        (self.after / "go.mod").write_text(ADDED)
        (self.after / "go.sum").write_text(SUM)
        self.calls = []

    def command(self, command, cwd, log_path, **kwargs):
        self.calls.append((command, str(cwd), kwargs))
        self.assertEqual(os.environ["PATH"], kwargs["env"]["PATH"])
        self.assertEqual("off", kwargs["env"]["GOPROXY"])
        self.assertEqual("off", kwargs["env"]["GOWORK"])
        self.assertEqual("local", kwargs["env"]["GOTOOLCHAIN"])
        text = (
            "example.test/app example.test/direct@v1.0.0\n"
            if command == "go mod graph"
            else "\n".join(
                json.dumps(r)
                for r in [
                    {"Path": "example.test/app", "Main": True},
                    *[{"Path": p, "Version": v} for p, v in SELECTED.items()],
                ]
            )
        )
        if command.startswith("go mod download "):
            text = json.dumps({"Path": "example.test/indirect", "Version": "v1.2.0", "Sum": "h1:" + "A" * 43 + "="})
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(text)
        return {
            "exit_code": 0,
            "output": str(log_path),
            "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "timed_out": False,
        }

    def test_collects_exact_metadata_without_mutating_either_tree(self):
        result = prerequisites.from_trees(
            self.before, self.after, ["go.mod", "go.sum"], self.root / "evidence", self.command
        )
        self.assertEqual({"go.mod": ADDED, "go.sum": SUM}, result["files"])
        self.assertEqual(BASE, (self.before / "go.mod").read_text())
        self.assertEqual(5, len(self.calls))
        self.assertTrue((self.root / "evidence" / "receipt.json").is_file())

    def test_prepares_original_test_tree_with_admitted_metadata_only(self):
        from unittest.mock import Mock

        factory = Mock(return_value=self.before)
        notes = []
        tree, receipt = prerequisites.prepare_base(
            factory,
            self.after,
            "base-sha",
            self.root / "proof",
            self.after,
            {"regression_test.go": "added"},
            {"go.mod": "modified", "go.sum": "added"},
            notes,
            self.command,
            patch=b"test patch",
            dependencies_from=self.after,
        )
        factory.assert_called_once_with(
            self.after,
            "base-sha",
            self.root / "proof/scratch/base-with-tests",
            self.after,
            {"regression_test.go": "added"},
            patch=b"test patch",
            dependencies_from=self.after,
        )
        self.assertEqual(self.before, tree)
        self.assertEqual(ADDED, (tree / "go.mod").read_text())
        self.assertEqual(SUM, (tree / "go.sum").read_text())
        self.assertEqual({"go.mod": ADDED, "go.sum": SUM}, receipt["files"])
        self.assertEqual([], notes)

    def test_non_prerequisite_dependency_change_keeps_original_comparison(self):
        (self.after / "go.mod").write_text(BASE.replace("v1.0.0", "v2.0.0"))
        notes = []
        tree, receipt = prerequisites.prepare_base(
            lambda *a, **k: self.before,
            self.after,
            "base-sha",
            self.root / "proof",
            self.after,
            {},
            {"go.mod": "modified"},
            notes,
            self.command,
        )
        self.assertEqual(BASE, (tree / "go.mod").read_text())
        self.assertIsNone(receipt)
        self.assertIn("not admitted", notes[0])

    def test_no_metadata_change_skips_inspection(self):
        tree, receipt = prerequisites.prepare_base(
            lambda *a, **k: self.before,
            self.after,
            "base-sha",
            self.root / "proof",
            self.after,
            {},
            {"app.go": "modified"},
            [],
            self.command,
        )
        self.assertEqual(self.before, tree)
        self.assertIsNone(receipt)
        self.assertEqual([], self.calls)

    def test_receipt_preserves_manifest_bytes_including_line_endings(self):
        raw = ADDED.replace("\n", "\r\n").encode()
        (self.after / "go.mod").write_bytes(raw)
        result = prerequisites.from_trees(
            self.before, self.after, ["go.mod", "go.sum"], self.root / "evidence", self.command
        )
        self.assertEqual(raw, result["files"]["go.mod"].encode())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result["modules"][0]["candidate_sha256"]["go.mod"])

    def test_new_checksums_must_match_offline_go_download_evidence(self):
        (self.after / "go.sum").write_text(SUM.replace("A", "B"))
        with self.assertRaises(ValueError):
            prerequisites.from_trees(
                self.before, self.after, ["go.mod", "go.sum"], self.root / "evidence", self.command
            )

    def test_reads_the_owned_output_path_from_the_command_receipt(self):
        def command(command, cwd, log_path, **kwargs):
            return self.command(command, cwd, Path(str(log_path) + ".owned"), **kwargs)

        result = prerequisites.from_trees(
            self.before, self.after, ["go.mod", "go.sum"], self.root / "evidence", command
        )
        self.assertEqual({"go.mod": ADDED, "go.sum": SUM}, result["files"])

    def test_tampered_or_unowned_command_evidence_cannot_admit_metadata(self):
        for extra in ({"output_sha256": "0" * 64}, {"supervision": None}):

            def command(command, cwd, log_path, **kwargs):
                return {**self.command(command, cwd, log_path, **kwargs), **extra}

            with self.subTest(extra=extra), self.assertRaises(ValueError):
                prerequisites.from_trees(self.before, self.after, ["go.mod", "go.sum"], self.root / "evidence", command)

    def test_metadata_changed_during_inspection_is_refused(self):
        def command(command, cwd, log_path, **kwargs):
            receipt = self.command(command, cwd, log_path, **kwargs)
            (self.after / "go.sum").write_text("changed during observation\n")
            return receipt

        with self.assertRaises(ValueError):
            prerequisites.from_trees(self.before, self.after, ["go.mod", "go.sum"], self.root / "evidence", command)

    def test_refuses_links_and_parent_escape_before_command_execution(self):
        for name in ["../go.mod", "/go.mod", ".autocode/go.mod", "x/../go.mod", "other.py"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                prerequisites.from_trees(self.before, self.after, [name], self.root / "evidence", self.command)
        (self.after / "go.mod").unlink()
        (self.after / "go.mod").symlink_to(self.before / "go.mod")
        with self.assertRaises(ValueError):
            prerequisites.from_trees(self.before, self.after, ["go.mod"], self.root / "evidence", self.command)
        self.assertEqual([], self.calls)

    def test_refuses_unreported_companion_edit(self):
        with self.assertRaises(ValueError):
            prerequisites.from_trees(self.before, self.after, ["go.mod"], self.root / "evidence", self.command)

    def test_replacement_is_refused_before_candidate_module_inspection(self):
        (self.after / "go.mod").write_text(ADDED + "replace example.test/direct => /outside\n")
        with self.assertRaises(ValueError):
            prerequisites.from_trees(
                self.before, self.after, ["go.mod", "go.sum"], self.root / "evidence", self.command
            )
        self.assertTrue(all(cwd == str(self.before) for _, cwd, _ in self.calls))

    def test_acceptance_before_snapshot_must_match_committed_metadata(self):
        import subprocess

        import autocode_util as util

        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.before), *args], text=True).strip()

        git("init", "-q")
        git("add", "go.mod")
        git("-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-qm", "base")
        snapshot = util.snapshot(self.before)
        before_ref = self.root / "before.json"
        before_ref.write_text(json.dumps(snapshot))
        (self.before / "go.mod").write_text(ADDED)
        (self.before / "go.sum").write_text(SUM)
        record = {"before_ref": str(before_ref), "changed_files": ["go.mod", "go.sum"]}
        result = prerequisites.from_attempt(
            self.before, record, ["go.mod", "go.sum"], self.root / "attempt", self.command
        )
        self.assertEqual({"go.mod": ADDED, "go.sum": SUM}, result["files"])
        snapshot["files"]["go.mod"] = "not-the-committed-bytes"
        before_ref.write_text(json.dumps(snapshot))
        with self.assertRaises(ValueError):
            prerequisites.from_attempt(
                self.before, record, ["go.mod", "go.sum"], self.root / "bad-attempt", self.command
            )
        snapshot["head"] = "missing-original"
        before_ref.write_text(json.dumps(snapshot))
        with self.assertRaises(ValueError):
            prerequisites.from_attempt(
                self.before, record, ["go.mod", "go.sum"], self.root / "missing-attempt", self.command
            )


class AcceptancePrerequisitesTests(unittest.TestCase):
    def test_validated_metadata_is_archived_with_tests_and_remains_in_blind_workspace(self):
        from unittest import mock

        import autocode_acceptance_tests as acceptance
        import autocode_acceptance_tests_job as job

        from tests.test_acceptance_tests import approved

        for blind in (False, True):
            with self.subTest(blind=blind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "go.mod").write_text(ADDED)
                (root / "go.sum").write_text(SUM)
                (root / "app_test.go").write_text("package app\nfunc TestAC1(t *testing.T) {}\n")
                state = approved({"AC1": "test: TestAC1"})
                state["settings"]["blind_tests"] = blind
                acceptance.route(state)
                report = {
                    "tests": [{"case_id": "AC1", "test_name": "TestAC1", "file": "app_test.go"}],
                    "cannot_write": [],
                    "already_passing": [],
                }
                record = {"changed_files": ["app_test.go", "go.mod", "go.sum"]}
                validated = {"files": {"go.mod": ADDED, "go.sum": SUM}, "modules": []}
                with mock.patch.object(prerequisites, "from_attempt", return_value=validated):
                    job.apply(
                        state,
                        report,
                        record,
                        root,
                        evidence_dir=root / "evidence",
                        run_tests=lambda files: {"results": {"failed": ["app::TestAC1"], "passed": []}},
                    )
                self.assertEqual("terra", state["next_stage"])
                receipt = next(iter(acceptance.receipts(state).values()))
                self.assertEqual({"go.mod", "go.sum", "app_test.go"}, set(receipt["files"]))
                self.assertEqual(ADDED, (root / "go.mod").read_text())
                self.assertEqual(SUM, (root / "go.sum").read_text())
                self.assertEqual(not blind, (root / "app_test.go").exists())

    def test_invalid_metadata_remains_a_stray_write_and_test_runner_is_not_called(self):
        from unittest import mock

        import autocode_acceptance_tests_job as job
        import autocode_stray_writes as stray

        from tests.test_acceptance_tests import approved

        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(prerequisites, "from_attempt", side_effect=ValueError("dependency changed")):
                with self.assertRaises(stray.StrayWrites):
                    job.apply(
                        approved(),
                        {},
                        {"changed_files": ["go.mod"]},
                        folder,
                        run_tests=lambda files: self.fail("invalid prerequisites cannot run"),
                    )

    def test_blind_metadata_stays_protected_from_builder_edits(self):
        import autocode_acceptance_tests as acceptance

        from tests.test_acceptance_tests import approved

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = approved({"AC1": "test: TestAC1"})
            state["settings"]["blind_tests"] = True
            (root / "go.mod").write_text(ADDED)
            (root / "archived.mod").write_text(ADDED)
            entry = {"sha256": acceptance.sha256(root / "go.mod"), "archive": str(root / "archived.mod")}
            acceptance.record_receipt(state, acceptance.due(state), {"contract_hash": "h1", "files": {"go.mod": entry}})
            self.assertIn("go.mod", acceptance.protected(state))
            self.assertIn("Go prerequisite files are fixed byte-for-byte", acceptance.builder_note(state))
            (root / "go.mod").write_text(ADDED + "require foreign v1.0.0\n")
            self.assertTrue(acceptance.check_untouched(state, root))

    def test_parallel_patch_cannot_append_to_frozen_metadata(self):
        import autocode_acceptance_tests as acceptance

        from tests.test_acceptance_tests import approved

        state = approved({"AC1": "test: TestAC1"})
        acceptance.record_receipt(state, acceptance.due(state), {"contract_hash": "h1", "files": {"go.mod": {}}})
        patch = b"diff --git a/go.mod b/go.mod\n--- a/go.mod\n+++ b/go.mod\n@@ -1 +1,2 @@\n module app\n+require foreign v1.0.0\n"
        self.assertEqual(["go.mod"], acceptance.edited(state, patch))
