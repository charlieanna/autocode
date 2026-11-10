"""Retained copy inspection observes pinned bytes without enabling replay."""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_replay_cli as cli
import autocode_replay_recording as recording
import autocode_replay_view as replay_view
import autocode_run_view as run_view
import autocode_util as util

from .test_replay_recording import make_export_projection, sha


class CopyFixture:
    def __init__(self, workspace, run, *, contents=None, snapshot=None):
        self.workspace, self.run = workspace, run
        self.tree = run / ("tool-containment-" + "a" * 32) / "scratch/verification"
        self.tree.mkdir(parents=True)
        self.contents = contents or {"test.py": (b"assert True\n", 0o644), "bin/check": (b"check bytes\n", 0o755)}
        for name, (data, mode) in self.contents.items():
            path = self.tree / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(data)
            path.chmod(mode)
        self.snapshot = snapshot or {
            "head": "a" * 40,
            "files": {name: recording.file_identity(data, mode) for name, (data, mode) in self.contents.items()},
            "source_paths": ["bin"],
        }
        self.revision = util.digest({key: self.snapshot[key] for key in ("head", "files")})
        self.manifest = self.tree.parent.parent / "verification-copy.json"
        self.body = {
            "version": 1,
            "workspace": str(workspace),
            "run_dir": str(run),
            "tree": str(self.tree),
            "source_revision": self.revision,
            "source_paths": self.snapshot["source_paths"],
            "files": {**self.snapshot["files"], ".git/config": "b" * 64},
            "directories": ["bin", ".git"],
            "joint_inputs": {},
        }
        self.core = {
            "manifest": str(self.manifest),
            "source_revision": self.revision,
            "workspace": str(workspace),
            "tree": str(self.tree),
            "private": "not projected",
        }
        self.save()

    def save(self, data=None):
        self.manifest.write_bytes(json.dumps(self.body).encode() if data is None else data)
        self.core["sha256"] = sha(self.manifest.read_bytes())

    def selector(self):
        return {"version": 1, **{key: self.core[key] for key in ("manifest", "sha256", "source_revision")}}


class RetainedCopyTests(unittest.TestCase):
    def setUp(self):
        import autocode_replay_dependency_evidence as evidence

        self.evidence = evidence
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.run = self.workspace / ".autocode/runs/one"
        self.fixture = CopyFixture(self.workspace, self.run)

    def inspect(self, *, value=None, read=None, snapshot=None, budget=None):
        return self.evidence.inspect_copy(
            self.fixture.selector() if value is None else value,
            self.fixture.snapshot if snapshot is None else snapshot,
            workspace=self.workspace,
            run_dir=self.run,
            read=read or recording._read,
            budget=budget,
        )

    def reason(self, expected, **kwargs):
        result = self.inspect(**kwargs)
        self.assertNotEqual("matched", result["status"], result)
        self.assertEqual(expected, result["reason"], result)
        return result

    def test_exact_regular_source_matches_without_current_workspace_or_git_reads(self):
        before = self.fixture.manifest.read_bytes()
        reads = []

        def read(root, name, **kwargs):
            reads.append(name)
            return recording._read(root, name, **kwargs)

        with (
            patch("subprocess.Popen", side_effect=AssertionError("no processes")),
            patch("autocode_verification_copy.execution", side_effect=AssertionError("no executor")),
        ):
            result = self.inspect(read=read)
        self.assertEqual(
            {
                "version": 1,
                "scope": "manifest_and_regular_source",
                "status": "matched",
                "reason": "recorded_manifest_and_regular_source_match",
                "source_files_checked": 2,
                "source_bytes_checked": sum(len(data) for data, _mode in self.fixture.contents.values()),
            },
            result,
        )
        self.assertEqual(3, len(reads))
        self.assertTrue(all(".git" not in name for name in reads))
        self.assertFalse((self.workspace / "test.py").exists())
        self.assertEqual(before, self.fixture.manifest.read_bytes())

    def test_changed_manifest_source_pin_bytes_modes_and_selection_are_distinguished(self):
        self.reason("manifest_pin_mismatch", value={**self.fixture.selector(), "sha256": "0" * 64})
        self.reason("source_revision_mismatch", value={**self.fixture.selector(), "source_revision": "0" * 64})
        path = self.fixture.tree / "test.py"
        data = path.read_bytes()
        path.write_bytes(b"broken bytes\n")
        self.reason("source_bytes_or_mode_mismatch")
        path.write_bytes(data)
        path.chmod(0o755)
        self.reason("source_bytes_or_mode_mismatch")
        path.chmod(0o644)
        for field, value, expected in (
            ("source_paths", [], "source_selection_mismatch"),
            ("source_revision", "0" * 64, "source_revision_mismatch"),
            ("files", {"test.py": "0" * 64}, "source_manifest_mismatch"),
        ):
            old = self.fixture.body[field]
            self.fixture.body[field] = value
            self.fixture.save()
            with self.subTest(field=field):
                self.reason(expected)
            self.fixture.body[field] = old
        self.fixture.save()

    def test_foreign_run_layout_and_escaping_selectors_are_refused_before_read(self):
        base = self.fixture.selector()
        variants = (
            (str(self.run.parent / "other" / self.fixture.manifest.relative_to(self.run)), "foreign_run_selector"),
            (str(self.run / "../one" / self.fixture.manifest.relative_to(self.run)), "unsafe_path"),
            (str(self.run / "unexpected/verification-copy.json"), "unsupported_copy_layout"),
            ("relative/verification-copy.json", "unsafe_path"),
            (str(self.fixture.manifest) + "\x00", "malformed_selector"),
        )
        for manifest, reason in variants:
            with self.subTest(manifest=manifest):
                self.reason(
                    reason, value={**base, "manifest": manifest}, read=lambda *_a, **_k: self.fail("must not read")
                )
        for field in ("workspace", "run_dir", "tree"):
            old = self.fixture.body[field]
            self.fixture.body[field] = str(self.root / "foreign")
            self.fixture.save()
            with self.subTest(field=field):
                self.reason("foreign_copy_layout")
            self.fixture.body[field] = old

    def test_missing_manifest_and_source_are_unavailable(self):
        source = self.fixture.tree / "test.py"
        source.unlink()
        self.assertEqual("unavailable", self.reason("evidence_missing")["status"])
        self.fixture.manifest.unlink()
        self.reason("evidence_missing")

    def test_io_refusals_and_disappearing_input_keep_explicit_diagnostics(self):
        for error, reason in (
            (PermissionError("private detail"), "evidence_unreadable"),
            (NotADirectoryError("private detail"), "unsafe_or_changed_path"),
            (ValueError("private detail"), "malformed_or_changed_evidence"),
        ):
            with self.subTest(error=error), patch.object(recording, "_read", side_effect=error):
                result = self.reason(reason)
                self.assertNotIn("private detail", json.dumps(result))

        def read(root, name, **kwargs):
            result = recording._read(root, name, **kwargs)
            if name.endswith("/test.py"):
                (self.fixture.tree / "test.py").unlink()
            return result

        self.reason("changed_during_inspection", read=read)

    def test_symlinked_files_directories_and_hardlinks_are_not_followed(self):
        for path in (self.fixture.manifest, self.fixture.tree / "test.py", self.fixture.tree / "bin"):
            moved = path.with_name(path.name + "-retained")
            path.rename(moved)
            path.symlink_to(moved)
            with self.subTest(path=path):
                self.reason("aliased_path")
            path.unlink()
            moved.rename(path)
        path = self.fixture.tree / "test.py"
        os.link(path, self.root / "hardlink")
        self.reason("aliased_file")

    def test_special_files_are_refused_without_blocking(self):
        path = self.fixture.tree / "test.py"
        path.unlink()
        os.mkfifo(path)
        self.reason("special_or_wrong_file_type")
        path.unlink()
        path.mkdir()
        self.reason("special_or_wrong_file_type")

    def test_distinct_spellings_of_the_same_inode_are_refused(self):
        first = (self.fixture.tree / "test.py").lstat()
        lstat = Path.lstat

        def aliased(path, *args, **kwargs):
            info = lstat(path, *args, **kwargs)
            if path == self.fixture.tree / "bin/check":
                fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
                return SimpleNamespace(**{key: getattr(first, key) for key in fields})
            return info

        with patch.object(Path, "lstat", aliased):
            self.reason("aliased_path")

    def test_leaf_and_parent_replacement_during_read_are_detected(self):
        for replaced in (self.fixture.tree / "test.py", self.fixture.tree):
            moved = replaced.with_name(replaced.name + "-old")
            fired = False

            def read(root, name, **kwargs):
                nonlocal fired
                result = recording._read(root, name, **kwargs)
                if name.endswith("/test.py"):
                    replaced.rename(moved)
                    if replaced == self.fixture.tree:
                        replaced.mkdir()
                    else:
                        replaced.write_bytes(result[0])
                    fired = True
                return result

            with self.subTest(replaced=replaced):
                self.reason("changed_during_inspection", read=read)
                self.assertTrue(fired)
            replaced.rmdir() if replaced.is_dir() else replaced.unlink()
            moved.rename(replaced)

    def test_change_to_an_earlier_input_is_detected_at_final_observation(self):
        def read(root, name, **kwargs):
            result = recording._read(root, name, **kwargs)
            if name.endswith("/bin/check"):
                (self.fixture.tree / "test.py").write_bytes(b"changed after its read")
            return result

        self.reason("changed_during_inspection", read=read)

    def test_malformed_manifests_duplicates_and_nonfinite_numbers_never_match(self):
        for data, reason in (
            (b"{", "malformed_or_changed_evidence"),
            (b"[]", "malformed_manifest"),
            (b'{"version":1,"version":1}', "duplicate_manifest_key"),
            (b'{"version":NaN}', "malformed_manifest"),
        ):
            self.fixture.save(data)
            with self.subTest(data=data):
                self.reason(reason)
        self.fixture.save()
        for field, value, reason in (
            ("version", True, "unsupported_manifest_version"),
            ("files", [], "malformed_manifest"),
            ("directories", ["bin", "bin"], "duplicate_directory"),
            ("directories", ["test.py"], "ambiguous_manifest_path"),
            ("directories", ["../escape"], "unsafe_path"),
        ):
            old = self.fixture.body[field]
            self.fixture.body[field] = value
            self.fixture.save()
            with self.subTest(field=field):
                self.reason(reason)
            self.fixture.body[field] = old

    def test_unsupported_copy_and_snapshot_inputs_are_explicit(self):
        body = copy.deepcopy(self.fixture.body)
        for field, value, reason in (
            ("joint_inputs", {"payload": {}}, "joint_inputs_unsupported"),
            ("files", {**body["files"], "venv": "symlink:/foreign"}, "nonregular_copy_input"),
        ):
            self.fixture.body = {**body, field: value}
            self.fixture.save()
            self.assertEqual("unsupported", self.reason(reason)["status"])
        self.fixture.body = body
        self.fixture.save()
        for name, identity, reason in (
            ("link", "symlink:target", "nonregular_source_input"),
            ("sub", "submodule:" + "a" * 64, "nonregular_source_input"),
            ("old", "deleted", "nonregular_source_input"),
            ("../escape", "a" * 64, "unsafe_path"),
            (".git/config", "a" * 64, "unsafe_path"),
        ):
            snapshot = {**self.fixture.snapshot, "files": {name: identity}}
            revision = util.digest({key: snapshot[key] for key in ("head", "files")})
            self.fixture.body["source_revision"] = revision
            self.fixture.save()
            with self.subTest(identity=identity):
                self.reason(reason, snapshot=snapshot, value={**self.fixture.selector(), "source_revision": revision})

    def test_size_count_and_shared_budget_bounds_are_explicit(self):
        with patch.object(self.evidence, "MAX_FILE_BYTES", 1):
            self.assertEqual("limited", self.reason("inspection_byte_limit")["status"])
        with patch.object(self.evidence, "MAX_FILES", 1):
            self.reason("snapshot_entry_limit")
        with patch.object(self.evidence, "MAX_FILES", 2):
            self.reason("manifest_entry_limit")
        self.reason(
            "inspection_file_limit", budget={"files": self.evidence.MAX_FILES, "bytes": self.evidence.MAX_BYTES}
        )
        budget = {"files": 0, "bytes": len(self.fixture.manifest.read_bytes())}
        self.reason("inspection_byte_limit", budget=budget)
        self.assertEqual(0, budget["bytes"])

    def test_path_size_and_depth_are_bounded_before_parsing_or_inspecting(self):
        for name in ("a" * (self.evidence.MAX_PATH + 1), "a/" * self.evidence.MAX_DEPTH + "leaf"):
            snapshot = {**self.fixture.snapshot, "source_paths": [name]}
            with (
                self.subTest(name=name[:20]),
                patch.object(self.evidence, "PurePosixPath", wraps=self.evidence.PurePosixPath) as parse,
            ):
                result = self.reason(
                    "inspection_path_limit", snapshot=snapshot, read=lambda *_a, **_k: self.fail("must not read")
                )
                self.assertEqual("limited", result["status"])
                self.assertNotIn(name, [call.args[0] for call in parse.call_args_list])

            original = self.fixture.body["directories"]
            self.fixture.body["directories"] = [name]
            self.fixture.save()
            reads = []

            def read(root, relative, **kwargs):
                reads.append(relative)
                return recording._read(root, relative, **kwargs)

            self.reason("inspection_path_limit", read=read)
            self.assertEqual([self.fixture.manifest.relative_to(self.run).as_posix()], reads)
            self.fixture.body["directories"] = original
            self.fixture.save()

    def test_legacy_and_malformed_selectors_remain_readable_without_reads(self):
        reader = lambda *_a, **_k: self.fail("must not read")
        missing = self.evidence.inspect_copy(
            None, self.fixture.snapshot, workspace=self.workspace, run_dir=self.run, read=reader
        )
        self.assertEqual(("unavailable", "selector_missing"), (missing["status"], missing["reason"]))
        for value, reason in (
            ({}, "malformed_selector"),
            ([], "malformed_selector"),
            ({**self.fixture.selector(), "sha256": None}, "malformed_selector"),
            ({**self.fixture.selector(), "version": 2}, "unsupported_selector_version"),
        ):
            with self.subTest(value=value):
                self.reason(reason, value=value, read=reader)
        self.reason("malformed_snapshot", snapshot={"files": None}, read=reader)
        for head in (None, "not-a-revision", "a" * 41):
            self.reason("malformed_snapshot", snapshot={**self.fixture.snapshot, "head": head}, read=reader)
        for paths in (["bin", "bin"], ["z", "a"], ["*"], ["a\nb"]):
            self.reason(
                "malformed_source_selection", snapshot={**self.fixture.snapshot, "source_paths": paths}, read=reader
            )


class RetainedCopyPublicTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_status_projects_only_the_versioned_core_selector_and_validates_schema(self):
        workspace, run = self.root / "workspace", self.root / "workspace/.autocode/runs/one"
        fixture = CopyFixture(workspace, run)
        state = {
            "status": "RUNNING",
            "workspace": str(workspace),
            "run_dir": str(run),
            "stages": [
                {"role": "tester", "events": "events.jsonl", "verification_copy": fixture.core},
                {"role": "tester", "events": "legacy.jsonl"},
            ],
        }
        original = copy.deepcopy(state)
        with patch.object(Path, "open", side_effect=AssertionError("projection must not read")):
            projected = replay_view.project(state)
        shown = run_view.view(state)
        self.assertEqual(projected, shown["replay_source"])
        records = shown["replay_source"]["records"]
        self.assertEqual(fixture.selector(), records[0]["verification_copy"])
        self.assertNotIn("verification_copy", records[1])
        self.assertEqual(original, state)
        schema = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/status-view.schema.json").read_text())
        projection_schema = schema["properties"]["view"]["properties"]["replay_source"]
        util.validate_schema(shown["replay_source"], projection_schema)
        malformed = copy.deepcopy(shown)
        malformed["replay_source"]["records"][0]["verification_copy"].pop("sha256")
        with self.assertRaises(ValueError):
            util.validate_schema(malformed["replay_source"], projection_schema)
        state["stages"][0]["verification_copy"] = {"manifest": "x" * 4097, "sha256": [], "source_revision": ""}
        bounded = replay_view.project(state)["records"][0]["verification_copy"]
        self.assertEqual({"version": 1, "manifest": None, "sha256": None, "source_revision": None}, bounded)
        state["stages"][0]["verification_copy"] = []
        self.assertEqual(bounded, replay_view.project(state)["records"][0]["verification_copy"])

    def test_public_refusal_reports_retained_copy_integrity_without_launch_or_writes(self):
        projection = make_export_projection(self.root)
        workspace, run = Path(projection["workspace"]), Path(projection["run_dir"])
        record = projection["records"][0]
        snapshot = {
            key: json.loads(Path(record["before_ref"]).read_text()).get(key, [])
            for key in ("head", "files", "source_paths")
        }
        fixture = CopyFixture(
            workspace,
            run,
            snapshot=snapshot,
            contents={".gitignore": (b".autocode/\n", 0o644), "greet.py": (b"print('before')\n", 0o644)},
        )
        core = {"stages": [{**record, "verification_copy": fixture.core}]}
        record.update(replay_view.project(core)["records"][0])
        receipt = {
            "command": ["touch", str(self.root / "NEVER")],
            "exit_code": 0,
            "full_output": str(self.root / "model-claimed.log"),
            "verification_copy": {"manifest_sha256": fixture.core["sha256"], "source_revision": fixture.revision},
        }
        event = {
            "type": "item.completed",
            "item": {"type": "command_execution", "aggregated_output": json.dumps(receipt), "exit_code": 0},
        }
        Path(record["events"]).write_text(json.dumps(event) + "\n")
        Path(record["output"]).write_text(json.dumps({"checks": [{"full_output": receipt["full_output"]}]}))
        paths = [Path(record[key]) for key in ("events", "output", "before_ref", "after_ref")] + [
            fixture.manifest,
            *(fixture.tree / name for name in fixture.contents),
        ]
        original = {path: path.read_bytes() for path in paths}
        reads = []
        read = recording._read

        def observed(root, name, **kwargs):
            reads.append(Path(root) / name)
            return read(root, name, **kwargs)

        for option, target in (
            ("--export", self.root / "bundle"),
            ("--into", self.root / "fresh/.autocode/runs/replay"),
        ):
            error = io.StringIO()
            with (
                self.subTest(option=option),
                patch.object(cli.TaskRun, "status", return_value={"replay_source": projection}),
                patch.object(cli.TaskRun, "start", side_effect=AssertionError("no run")),
                patch("subprocess.Popen", side_effect=AssertionError("no command")),
                patch("autocode_verification_copy.execution", side_effect=AssertionError("no executor")),
                patch.object(recording, "_read", side_effect=observed),
                redirect_stderr(error),
            ):
                self.assertEqual(3, cli.cli([str(run), option, str(target), "--json"]))
            refusal = json.loads(error.getvalue())
            self.assertEqual("REPLAY_REFUSED", refusal["code"])
            self.assertIn("unsupported in v1", refusal["message"])
            inventory = refusal["native_capture_dependencies"]
            self.assertEqual((False, "unverified"), (inventory["execution_authority"], inventory["closure_status"]))
            [entry] = inventory["dependencies"]
            self.assertIn("copy_evidence", entry)
            self.assertEqual("matched", entry["copy_evidence"]["status"])
            self.assertEqual(2, entry["copy_evidence"]["source_files_checked"])
            self.assertNotIn("copy_manifest_not_supplied", entry["issues"])
            self.assertNotIn("copy_manifest_claim_mismatch", entry["issues"])
            self.assertFalse(target.exists())
        self.assertNotIn(Path(receipt["full_output"]), reads)
        self.assertFalse((self.root / "NEVER").exists())
        self.assertEqual(original, {path: path.read_bytes() for path in paths})

    def test_inventory_reuses_dispatch_inspection_without_repairing_forged_claims(self):
        import autocode_replay_dependency_evidence as evidence

        workspace, run = self.root / "workspace", self.root / "workspace/.autocode/runs/one"
        fixture = CopyFixture(workspace, run)
        inventory = {
            "execution_authority": False,
            "closure_status": "unverified",
            "dependencies": [
                {
                    "dispatch": "dispatch-0",
                    "copy_manifest_sha256_claim": "0" * 64,
                    "issues": ["copy_manifest_not_supplied"],
                }
            ]
            * 2,
        }
        document = {
            "dispatches": [{"id": "dispatch-0", "ordinal": 0, "before": "before"}],
            "snapshots": {"before": fixture.snapshot},
        }
        original = copy.deepcopy(inventory)
        with patch.object(evidence, "inspect_copy", wraps=evidence.inspect_copy) as inspect:
            result = evidence.inspect_inventory(
                inventory,
                document=document,
                records=[{"verification_copy": fixture.selector()}],
                workspace=workspace,
                run_dir=run,
                read=recording._read,
            )
        self.assertEqual(1, inspect.call_count)
        self.assertEqual(original, inventory)
        for entry in result["dependencies"]:
            self.assertIn("copy_manifest_claim_mismatch", entry["issues"])
            self.assertEqual("0" * 64, entry["copy_manifest_sha256_claim"])
        fixture.manifest.unlink()
        result = evidence.inspect_inventory(
            inventory,
            document=document,
            records=[{"verification_copy": fixture.selector()}],
            workspace=workspace,
            run_dir=run,
            read=recording._read,
        )
        self.assertIn("copy_evidence_unavailable", result["dependencies"][0]["issues"])
