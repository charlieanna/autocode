"""Launch inputs cannot let an in-place proof trust code written during the run.

Policy checks use record/supply and filesystem observations. Native proof checks
run actual Git/unittest subprocesses; the public CLI check uses an offline provider.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from copy import deepcopy

import autocode_launch_inputs as launch_inputs
import autocode_regression as regression
import autocode_taskrun as taskrun
import autocode_verify as verify

from .test_verify import isolated_python

ROOT = Path(__file__).resolve().parents[1]
SEED = {
    "calc.py": "from _version import VERSION\n\ndef add(a, b):\n    return a + b\n\n"
               "def sub(a, b):\n    return a + b\n",
    "test_calc.py": "import unittest\nimport calc\n\nclass Add(unittest.TestCase):\n"
                    "    def test_add(self):\n        self.assertEqual(5, calc.add(2, 3))\n",
}
VERSION = "VERSION = '1.0'\n"
FEATURE = "import unittest\nimport calc\n\nclass Sub(unittest.TestCase):\n" \
          "    def test_t1_sub_is_correct(self):\n        self.assertEqual(1, calc.sub(3, 2))\n"
FAILS_ONLY_ON_BASE = "import os\nimport calc\nif '/baseline' in os.getcwd():\n    calc.add = lambda a, b: -999\n"
REPAIRS_CANDIDATE = "import calc\ncalc.add = lambda a, b: a + b\n"


class SupplyTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='supply-transport-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.checkout, self.store = self.root / 'project', self.root / 'launch-sources'
        self.checkout.mkdir()
        self.store.mkdir()
        self.generated_bytes, self.vendor_bytes = b'VALUE = 41\n', b'OFFSET = 1\n'
        self.generated_hash = hashlib.sha256(self.generated_bytes).hexdigest()
        self.vendor_hash = hashlib.sha256(self.vendor_bytes).hexdigest()
        (self.checkout / '_version.py').write_bytes(self.generated_bytes)
        (self.checkout / 'vendor').mkdir()
        (self.checkout / 'vendor' / 'library.py').write_bytes(self.vendor_bytes)
        for path in (self.checkout / '_version.py', self.checkout / 'vendor' / 'library.py'):
            path.chmod(0o644)
        (self.store / self.generated_hash).write_bytes(self.generated_bytes)
        self.supplied = launch_inputs.Supply(self.checkout, self.store,
            {'_version.py': [self.generated_hash, 0o644]},
            {'vendor/library.py': [self.vendor_hash, 0o644]}, [], ['Added input omitted'], recorded=True)

    def restored(self, record=None):
        return launch_inputs.Supply.from_transport(
            self.supplied.to_transport() if record is None else record, checkout=self.checkout)

    def destination(self, name):
        path = self.root / name
        path.mkdir()
        return path

    def test_round_trip_preserves_every_field_and_actual_copy_semantics(self):
        restored = self.restored()
        self.assertEqual(self.supplied.to_transport(), restored.to_transport())
        self.assertEqual(self.supplied.identity, restored.identity)
        tree = self.destination('copy')
        restored.copy_into(tree)
        self.assertEqual(self.generated_bytes, (tree / '_version.py').read_bytes())
        self.assertEqual(self.vendor_bytes, (tree / 'vendor/library.py').read_bytes())
        self.assertEqual(0o644, (tree / '_version.py').stat().st_mode & 0o777)

    def test_transport_is_a_detached_primitive_snapshot_without_file_or_process_reads(self):
        with mock.patch.object(launch_inputs, '_read', side_effect=AssertionError('unexpected source read')), \
                mock.patch.object(launch_inputs.verify.subprocess, 'run', side_effect=AssertionError('unowned child')):
            record = self.supplied.to_transport()
            restored = self.restored(json.loads(json.dumps(record)))
        record['notes'].append('changed after capture')
        record['generated']['_version.py'][1] = 0o600
        self.assertEqual(['Added input omitted'], restored.notes)
        self.assertEqual(0o644, restored.generated['_version.py'][1])
        self.assertEqual(0o644, self.supplied.generated['_version.py'][1])

    def test_empty_recorded_and_unrecorded_supplies_remain_distinct(self):
        for recorded in (False, True):
            with self.subTest(recorded=recorded):
                supplied = launch_inputs.Supply(self.checkout, self.store, {}, {}, [], [], recorded=recorded)
                restored = launch_inputs.Supply.from_transport(supplied.to_transport(), checkout=self.checkout)
                self.assertEqual(recorded, restored.recorded)
                self.assertEqual(supplied.identity, restored.identity)
                self.assertEqual({}, restored.generated)
                self.assertEqual({}, restored.vendored)

    def test_uncertainty_and_notes_are_retained_and_block_copy(self):
        supplied = launch_inputs.Supply(self.checkout, self.store, {}, {},
            ['Captured input changed'], ['Added vendor omitted'], recorded=False)
        restored = launch_inputs.Supply.from_transport(supplied.to_transport(), checkout=self.checkout)
        self.assertEqual(supplied.to_transport(), restored.to_transport())
        with self.assertRaisesRegex(ValueError, 'Captured input changed'):
            restored.copy_into(self.destination('uncertain'))

    def test_changed_source_mode_and_missing_or_changed_capture_still_fail(self):
        for kind in ('source', 'mode', 'missing-capture', 'changed-capture'):
            with self.subTest(kind=kind):
                source, captured = self.checkout / '_version.py', self.store / self.generated_hash
                source.write_bytes(self.generated_bytes)
                source.chmod(0o644)
                captured.write_bytes(self.generated_bytes)
                restored = self.restored()
                if kind == 'source': source.write_bytes(b'VALUE = 99\n')
                elif kind == 'mode': source.chmod(0o600)
                elif kind == 'missing-capture': captured.unlink()
                else: captured.write_bytes(b'VALUE = 99\n')
                with self.assertRaises((OSError, ValueError)):
                    restored.copy_into(self.destination(kind))

    def test_symlink_and_fifo_cannot_supply_transported_source(self):
        source = self.checkout / '_version.py'
        restored = self.restored()
        source.unlink()
        source.symlink_to(self.store / self.generated_hash)
        with self.assertRaises((OSError, ValueError)):
            restored.copy_into(self.destination('symlink'))
        source.unlink()
        os.mkfifo(source)
        with self.assertRaises((OSError, ValueError)):
            restored.copy_into(self.destination('fifo'))

    def test_malformed_transport_fields_fail_before_copy(self):
        original = self.supplied.to_transport()
        variants = []
        for key, value in (('schema', True), ('kind', 'other'), ('recorded', 1),
                           ('identity', '0' * 64), ('notes', 'not a list'),
                           ('unverified', [None]), ('checkout', '/tmp/../foreign'),
                           ('store', 'relative'), ('store', '/private/invalid\0root')):
            record = deepcopy(original)
            record[key] = value
            variants.append(record)
        for name, entry in (('../foreign', [self.generated_hash, 0o644]),
                            ('invalid\0name', [self.generated_hash, 0o644]),
                            ('.git/config', [self.generated_hash, 0o644]),
                            ('_version.py', ['bad-hash', 0o644]),
                            ('_version.py', [self.generated_hash, True])):
            record = deepcopy(original)
            record['generated'] = {name: entry}
            variants.append(record)
        record = deepcopy(original)
        record['vendored'] = {'outside-vendor.py': [self.vendor_hash, 0o644]}
        variants.append(record)
        record = deepcopy(original)
        record['extra'] = 'not part of the Supply'
        variants.append(record)
        for index, record in enumerate(variants):
            with self.subTest(index=index), mock.patch.object(launch_inputs, '_read', side_effect=AssertionError('foreign read')):
                with self.assertRaises(ValueError): self.restored(record)

    def test_foreign_checkout_is_rejected_before_copy_or_inventory(self):
        with mock.patch.object(launch_inputs, '_read', side_effect=AssertionError('foreign read')):
            with self.assertRaisesRegex(ValueError, 'different checkout'):
                launch_inputs.Supply.from_transport(self.supplied.to_transport(), checkout=self.root / 'foreign')

    def test_transport_remains_bounded_by_the_existing_manifest_limit(self):
        self.supplied.notes = ['x' * (8 * 1024 * 1024)]
        with self.assertRaisesRegex(ValueError, 'manifest bound'):
            self.supplied.to_transport()


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class Project(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="launch-inputs-")
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name).resolve()
        self.root = self.temporary / "project"
        self.root.mkdir()
        self.write({**SEED, ".gitignore": ".autocode/\n_version.py\n__pycache__/\n"})
        git(self.root, "init", "-q")
        git(self.root, "config", "maintenance.auto", "false")
        git(self.root, "config", "gc.auto", "0")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.write({"_version.py": VERSION})
        self.run_dir = self.root / ".autocode" / "runs" / "policy"
        self.run_dir.mkdir(parents=True)
        self.state = {"base_commit": self.base, "goal_contract": {"body": {"task_kind": "bugfix"}},
                      "settings": {"regression": {"python": sys.executable, "test_timeout": 60}}}

    def write(self, files):
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def hide(self, files):
        self.write(files)
        with (self.root / ".git" / "info" / "exclude").open("a") as exclude:
            exclude.writelines(name + "\n" for name in files)

    def capture(self):
        launch_inputs.record(self.state, self.root, self.run_dir)

    def supply(self):
        return launch_inputs.supply(self.state, self.root, self.run_dir)

    def candidate(self, *, breaks_add=False):
        source = SEED["calc.py"].replace("def sub(a, b):\n    return a + b", "def sub(a, b):\n    return a - b")
        if breaks_add:
            source = source.replace("def add(a, b):\n    return a + b", "def add(a, b):\n    return a * b")
        self.write({"calc.py": source, "test_feature.py": FEATURE})

    def prove(self):
        return regression.prove(self.state, self.root, self.run_dir)

    def destination(self, name="copy"):
        path = self.temporary / name
        path.mkdir()
        return path

    def assert_unverified(self, proof, name="_version.py"):
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
        self.assertEqual([], proof["failures"], proof)
        self.assertIn(name, " ".join(proof["unverified"]))


class NativeProofTests(Project):
    def test_ignored_baseline_poison_cannot_hide_a_regression(self):
        self.capture()
        self.candidate(breaks_add=True)
        self.hide({"test_aaa_env.py": FAILS_ONLY_ON_BASE})
        proof = self.prove()
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("test_calc.Add.test_add", " ".join(proof["failures"]))
        self.assertIn("test_aaa_env.py", " ".join(proof["notes"]))

    def test_ignored_candidate_repair_cannot_mask_broken_delivered_code(self):
        self.capture()
        self.candidate(breaks_add=True)
        self.hide({"test_aaa_env.py": REPAIRS_CANDIDATE})
        proof = self.prove()
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("test_calc.Add.test_add", " ".join(proof["failures"]))

    def test_unchanged_required_generated_source_allows_a_correct_fix(self):
        self.capture()
        self.candidate()
        proof = self.prove()
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test_feature.Sub.test_t1_sub_is_correct"], proof["fail_to_pass"])

    def test_changed_or_removed_captured_generated_source_remains_unverified(self):
        self.capture()
        self.candidate()
        self.write({"_version.py": "VERSION = 'changed'\n"})
        self.assert_unverified(self.prove())
        (self.root / "_version.py").unlink()
        self.assert_unverified(self.prove())

    def test_cached_pass_is_not_reused_after_a_captured_input_changes(self):
        self.state["settings"]["regression"]["python"] = isolated_python(self)
        self.capture()
        self.candidate()
        self.assertEqual(verify.PASS, self.prove()["verdict"])
        with mock.patch.object(verify, "run_command", wraps=verify.run_command) as execute:
            self.assertEqual(verify.PASS, self.prove()["verdict"])
            execute.assert_not_called()  # This fixture actually exercises accepted receipt reuse.
            self.write({"_version.py": "VERSION = 'changed after PASS'\n"})
            self.assert_unverified(self.prove())
            execute.assert_not_called()

    def test_added_vendor_files_cannot_poison_base_or_repair_candidate(self):
        self.capture()
        self.candidate(breaks_add=True)
        for text in (FAILS_ONLY_ON_BASE, REPAIRS_CANDIDATE):
            with self.subTest(text=text):
                self.hide({"vendor/__init__.py": text, "test_aaa_env.py": "import vendor\n"})
                proof = self.prove()
                self.assertEqual(verify.FAIL, proof["verdict"], proof)
                self.assertIn("test_calc.Add.test_add", " ".join(proof["failures"]))


class LaunchInputPolicyTests(Project):
    def test_malformed_present_checkpoint_stays_unverified_with_no_eligible_files(self):
        (self.root / "_version.py").unlink()
        for index, checkpoint in enumerate(({}, None)):
            with self.subTest(checkpoint=checkpoint):
                self.state["launch_sources"] = checkpoint
                supplied = self.supply()
                self.assertTrue(supplied.unverified)
                tree = self.destination(f"malformed-checkpoint-{index}")
                with self.assertRaises(ValueError):
                    supplied.copy_into(tree)
                self.assertEqual([], list(tree.iterdir()))

    def test_a_fifo_replacing_a_captured_source_is_refused_without_a_blocking_open(self):
        self.capture()
        fifo = self.root / "_version.py"
        fifo.unlink()
        os.mkfifo(fifo)
        native_open = os.open

        def guarded_open(path, flags, *args, **kwargs):
            if Path(path) == fifo or (str(path) == fifo.name and "dir_fd" in kwargs):
                # Fail deterministically before a removed O_NONBLOCK can hang the test.
                self.assertTrue(flags & os.O_NONBLOCK, "FIFO admission must use a nonblocking open")
            return native_open(path, flags, *args, **kwargs)

        with mock.patch.object(launch_inputs.os, "open", side_effect=guarded_open):
            supplied = self.supply()
        self.assertTrue(supplied.unverified)
        with self.assertRaises(ValueError):
            supplied.copy_into(self.destination())

    def test_a_captured_source_mode_change_blocks_the_native_proof(self):
        self.capture()
        self.candidate()
        path = self.root / "_version.py"
        path.chmod((path.stat().st_mode & 0o777) ^ 0o100)
        self.assert_unverified(self.prove())

    def test_injected_clean_runners_refuse_a_stale_cached_identity_after_input_mutation(self):
        self.capture()
        execute = mock.Mock(return_value={"exit_code": 0})
        cached_identity = mock.Mock(return_value={"fixture": "cached identity"})
        run, identity = launch_inputs.runners(self.state, self.root, self.run_dir, execute, cached_identity)
        self.assertEqual({"fixture": "cached identity"}, identity())
        cached_identity.assert_called_once()
        self.write({"_version.py": "VERSION = 'changed after cached identity'\n"})
        with self.assertRaises(ValueError):
            identity()
        with self.assertRaises(ValueError):
            run(command="fixture command")
        cached_identity.assert_called_once()
        execute.assert_not_called()

    def test_injected_clean_runner_cannot_return_a_pass_after_its_input_changes(self):
        self.capture()

        def execute(**_kwargs):
            self.write({"_version.py": "VERSION = 'changed during clean execution'\n"})
            return {"exit_code": 0, "status": "PASS"}

        run, _ = launch_inputs.runners(self.state, self.root, self.run_dir, execute)
        with self.assertRaises(ValueError):
            run(command="fixture command")

    def test_old_checkpoint_copies_no_unrecorded_files(self):
        supplied = self.supply()
        self.assertTrue(supplied.unverified)
        tree = self.destination()
        with self.assertRaises(ValueError):
            supplied.copy_into(tree)
        self.assertEqual([], list(tree.iterdir()))
        self.candidate()
        self.assert_unverified(self.prove())

    def test_failed_capture_stays_unverified_after_offending_files_disappear(self):
        with mock.patch.object(verify, "generated_sources", side_effect=OSError("fixture cannot read launch sources")):
            self.capture()
        (self.root / "_version.py").unlink()
        supplied = self.supply()
        self.assertTrue(supplied.unverified, "A failed capture cannot become an empty successful capture")
        with self.assertRaises(ValueError):
            supplied.copy_into(self.destination())

    def test_invalid_manifest_schema_is_refused_even_with_a_matching_pin(self):
        self.capture()
        path = self.run_dir / launch_inputs.STORE / "manifest.json"
        valid = json.loads(path.read_text())
        digest = hashlib.sha256(VERSION.encode()).hexdigest()
        malformed = [
            {**valid, "version": True},
            {**valid, "workspace": str(self.temporary / "different-checkout")},
            {**valid, "generated": []},
            {**valid, "generated": {"_version.py": [digest]}},
            {**valid, "generated": {"_version.py": ["not-a-digest", 0o644]}},
            {**valid, "generated": {"../outside.py": [digest, 0o644]}},
        ]
        for index, body in enumerate(malformed):
            with self.subTest(body=body):
                raw = json.dumps(body).encode()
                path.write_bytes(raw)
                # An authentic pin for invalid fixture data exercises schema validation.
                self.state["launch_sources"]["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
                supplied = self.supply()
                self.assertTrue(supplied.unverified)
                with self.assertRaises(ValueError):
                    supplied.copy_into(self.destination(f"malformed-{index}"))
        self.assertFalse((self.temporary / "outside.py").exists())

    def test_damaged_capture_never_replays_the_source(self):
        self.capture()
        store = self.run_dir / launch_inputs.STORE
        digest = hashlib.sha256(VERSION.encode()).hexdigest()
        (store / digest).write_text("VERSION = 'damaged capture'\n")
        supplied = self.supply()
        self.assertTrue(supplied.unverified)
        with self.assertRaises(ValueError):
            supplied.copy_into(self.destination())

    def test_symlinked_destination_parent_does_not_write_outside_the_copy(self):
        self.write({"generated/anchor.py": "VALUE = 1\n"})
        git(self.root, "add", "generated/anchor.py")
        git(self.root, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "tracked parent")
        self.hide({"generated/_version.py": VERSION})
        self.capture()
        supplied = self.supply()
        self.assertEqual([], supplied.unverified)
        outside = self.destination("outside")
        sentinel = outside / "sentinel"
        sentinel.write_text("unrelated")
        tree = self.destination()
        (tree / "generated").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            supplied.copy_into(tree)
        self.assertEqual(["sentinel"], sorted(path.name for path in outside.iterdir()))
        self.assertEqual("unrelated", sentinel.read_text())

    def test_destination_parent_swapped_after_validation_cannot_receive_an_external_write(self):
        self.hide({"vendor/marker.py": "VALUE = 1\n"})
        self.capture()
        supplied = self.supply()
        self.assertEqual([], supplied.unverified)
        tree = self.destination()
        parent = tree / "vendor"
        parent.mkdir()
        outside = self.destination("outside")
        sentinel = outside / "sentinel"
        sentinel.write_text("unrelated")
        original_path = launch_inputs._path
        swapped = False

        def validate_then_swap(root, name):
            nonlocal swapped
            result = original_path(root, name)
            if Path(root) == tree and name == "vendor/marker.py" and not swapped:
                parent.rmdir()
                parent.symlink_to(outside, target_is_directory=True)
                swapped = True
            return result

        with mock.patch.object(launch_inputs, "_path", side_effect=validate_then_swap):
            with self.assertRaises((OSError, ValueError)):
                supplied.copy_into(tree)
        self.assertTrue(swapped, "The fixture must inject the race after successful validation")
        self.assertEqual(["sentinel"], sorted(path.name for path in outside.iterdir()))
        self.assertEqual("unrelated", sentinel.read_text())

    def test_source_parent_swapped_after_validation_cannot_supply_external_matching_bytes(self):
        self.hide({"vendor/pkg/marker.py": "VALUE = 1\n"})
        self.capture()
        supplied = self.supply()
        self.assertEqual([], supplied.unverified)
        outside = self.destination("outside")
        (outside / "marker.py").write_text("VALUE = 1\n")
        (outside / "sentinel").write_text("unrelated")
        parent = self.root / "vendor" / "pkg"
        original_path = launch_inputs._path
        swapped = False

        def validate_then_swap(root, name):
            nonlocal swapped
            result = original_path(root, name)
            if Path(root) == self.root and name == "vendor/pkg/marker.py" and not swapped:
                parent.rename(self.root / "vendor" / "original-pkg")
                parent.symlink_to(outside, target_is_directory=True)
                swapped = True
            return result

        with mock.patch.object(launch_inputs, "_path", side_effect=validate_then_swap):
            with self.assertRaises((OSError, ValueError)):
                supplied.copy_into(self.destination())
        self.assertTrue(swapped, "The fixture must inject the race after successful validation")
        self.assertEqual(["marker.py", "sentinel"], sorted(path.name for path in outside.iterdir()))
        self.assertEqual("VALUE = 1\n", (outside / "marker.py").read_text())
        self.assertEqual("unrelated", (outside / "sentinel").read_text())

    def test_symlinked_source_parent_cannot_supply_matching_external_bytes(self):
        self.hide({"vendor/pkg/marker.py": "VALUE = 1\n"})
        self.capture()
        outside = self.destination("outside")
        (outside / "marker.py").write_text("VALUE = 1\n")
        shutil.rmtree(self.root / "vendor" / "pkg")
        (self.root / "vendor" / "pkg").symlink_to(outside, target_is_directory=True)
        supplied = self.supply()
        self.assertTrue(supplied.unverified)
        with self.assertRaises(ValueError):
            supplied.copy_into(self.destination())
        self.assertEqual(["marker.py"], [path.name for path in outside.iterdir()])

    def test_ignored_vendor_leaf_symlink_to_tracked_file_requires_materialization(self):
        self.hide({"vendor/marker.py": "placeholder\n"})
        link = self.root / "vendor" / "marker.py"
        target = self.root / "calc.py"
        link.unlink()
        link.symlink_to("../calc.py")
        target_before, link_before = target.read_bytes(), os.readlink(link)
        self.capture()
        supplied = self.supply()
        self.assertTrue(supplied.unverified)
        self.assertIn("vendored", " ".join(supplied.unverified))
        self.assertIn("regular files", " ".join(supplied.unverified))
        tree = self.destination()
        with self.assertRaises(ValueError):
            supplied.copy_into(tree)
        self.assertEqual([], list(tree.iterdir()))
        self.assertTrue(link.is_symlink())
        self.assertEqual(link_before, os.readlink(link))
        self.assertEqual(target_before, target.read_bytes())

    def test_older_generated_only_record_is_unverified_even_after_inputs_disappear(self):
        self.state['generated_sources_at_start'] = verify.generated_source_record(self.root)
        self.assertIn('_version.py', self.state['generated_sources_at_start'])
        (self.root / '_version.py').unlink()
        supplied = self.supply()
        self.assertTrue(supplied.unverified)
        self.assertIn('_version.py', ' '.join(supplied.unverified))
        with self.assertRaises(ValueError):
            supplied.copy_into(self.destination())

    def test_vendor_copy_uses_exact_captured_inventory_with_an_existing_vendor_directory(self):
        self.hide({"vendor/marker.py": "VALUE = 1\n", "vendor/native/library.so": "native fixture"})
        self.capture()
        supplied = self.supply()
        self.hide({"vendor/extra.py": "UNTRUSTED = True\n"})
        self.assertEqual(supplied.identity, self.supply().identity)
        tree = self.destination()
        (tree / "vendor").mkdir()
        (tree / "vendor" / "tracked.txt").write_text("base input")
        supplied.copy_into(tree)
        self.assertEqual("VALUE = 1\n", (tree / "vendor" / "marker.py").read_text())
        self.assertEqual("native fixture", (tree / "vendor" / "native" / "library.so").read_text())
        self.assertEqual("base input", (tree / "vendor" / "tracked.txt").read_text())
        self.assertFalse((tree / "vendor" / "extra.py").exists())

    def test_vendor_change_between_supply_and_copy_cannot_copy_new_bytes(self):
        self.hide({"vendor/marker.py": "VALUE = 1\n"})
        self.capture()
        supplied = self.supply()
        self.write({"vendor/marker.py": "VALUE = 'changed during copy admission'\n"})
        tree = self.destination()
        with self.assertRaises(ValueError):
            supplied.copy_into(tree)
        self.assertFalse((tree / "vendor" / "marker.py").exists())

    def test_missing_captured_vendor_file_blocks_a_native_proof(self):
        self.hide({"vendor/marker.py": "VALUE = 1\n"})
        self.capture()
        self.candidate()
        (self.root / "vendor" / "marker.py").unlink()
        self.assert_unverified(self.prove(), "vendor/marker.py")


class PublicLaunchCaptureTests(Project):
    def test_cli_captures_ignored_inputs_before_any_builder_can_run(self):
        self.write({"probe.py": "raise SystemExit(7)\n"})
        git(self.root, "add", "probe.py")
        git(self.root, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "prerequisite")
        manifest = self.temporary / "preflight.json"
        manifest.write_text(json.dumps({"version": 1, "checks": [{"id": "setup", "phase": "planning",
            "argv": ["{python}", "probe.py"], "recovery": "Inspect this offline fixture", "reuse": False}]}))
        bindir = self.destination("bin")
        marker = self.temporary / "provider-calls"
        provider = (ROOT / "tools" / "live_fixture_provider.py").read_text()
        provider = provider.replace("import json", "import os\nimport json", 1)
        provider = provider.replace("    output.write_text(json.dumps(report))",
            "    with open(os.environ['LAUNCH_INPUT_PROVIDER_CALLS'], 'a') as marker:\n"
            "        marker.write(stage + '\\n')\n    output.write_text(json.dumps(report))")
        (bindir / "codex").write_text(provider)
        (bindir / "codex").chmod(0o755)
        env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
               "AUTOCODE_HOME": str(self.temporary / "registry"),
               "LAUNCH_INPUT_PROVIDER_CALLS": str(marker), "PYTHONDONTWRITEBYTECODE": "1"}
        run = taskrun.TaskRun.start(self.root, "Fix sub without breaking addition", options=("--engine", "codex"),
            start_options=("--workflow", "build", "--task-preflight", str(manifest)), env=env, timeout=60)
        self.assertEqual("PAUSED_TASK_PREFLIGHT", run.status()["status"])
        self.assertFalse(marker.exists())
        self.write({"_version.py": "VERSION = 'changed after public start'\n"})
        stopped = run.resume_paused()
        self.assertEqual("PAUSED_STALE_VALIDATION", stopped["status"], stopped)
        self.assertIn("_version.py", stopped["stop_reason"])
        self.assertFalse(marker.exists())


class PublicCompletionInputTests(Project):
    """A saved passing proof cannot authorize completion after its inputs change."""

    def start_greeting(self, *, human_review=False):
        bindir = self.destination("bin")
        self.provider_calls = self.temporary / "provider-calls"
        provider = (ROOT / "tools" / "live_fixture_provider.py").read_text()
        provider = provider.replace("def test_greet(self):", "def test_c1_greeting_contract(self):")
        provider = provider.replace('"verification_method": "Execute greeting and invalid-input regression checks",',
                                    '"verification_method": "test: test_c1_greeting_contract",')
        if human_review:
            provider = provider.replace('"human_review": False,', '"human_review": True,')
        provider = provider.replace("    output.write_text(json.dumps(report))",
            "    with open(os.environ['LAUNCH_INPUT_PROVIDER_CALLS'], 'a') as marker:\n"
            "        marker.write(stage + '\\n')\n    output.write_text(json.dumps(report))")
        (bindir / "codex").write_text(provider)
        (bindir / "codex").chmod(0o755)
        env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
               "AUTOCODE_HOME": str(self.temporary / "registry"),
               "CODEX_HOME": str(self.temporary / "codex-home"),
               "XDG_CONFIG_HOME": str(self.temporary / "config-home"),
               "LAUNCH_INPUT_PROVIDER_CALLS": str(self.provider_calls), "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("AUTOCODE_PROVIDER", None)
        brief = ("Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
                 "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
                 "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
                 "Python standard library only.")
        options = ("--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
                   "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model",
                   "gpt-6-astra", "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra")
        run = taskrun.TaskRun.start(self.root, brief, options=options, env=env, timeout=90,
                                    cwd=self.temporary)
        plan = run.status()
        self.assertEqual("approve_plan", plan["needs"]["kind"], plan)
        run.approve_plan(plan["needs"]["token"])
        view = run.advance_until_input()
        proof = view["evidence"]["regression_proof"]
        self.assertIsNotNone(proof, view)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test_greet.TestGreet.test_c1_greeting_contract"], proof["case_tests"]["C1"])
        return run, view

    def cli(self, run, *args):
        return subprocess.run([*run.command, "--workspace", str(run.workspace),
            "--run-dir", str(run.run_dir), *args], cwd=self.temporary,
            env=run.env, capture_output=True, text=True, timeout=90)

    def public_status(self, run):
        result = self.cli(run, "--status", "--inspect-evidence")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def mutate_input(self, mutation):
        if mutation == "rewrite":
            self.write({"_version.py": "VERSION = 'changed after passing proof'\n"})
        else:
            (self.root / "_version.py").unlink()

    def test_accept_completion_refuses_changed_or_deleted_inputs_after_saved_passing_proof(self):
        run, waiting = self.start_greeting(human_review=True)
        self.assertFalse(waiting["done"])
        self.assertEqual("review", waiting["needs"]["kind"], waiting)
        self.assertEqual(["C1"], waiting["needs"]["criteria"])
        approved = run.approve_review("C1", waiting["needs"]["token"])
        self.assertFalse(approved["done"])
        acceptance = ("--accept-completion",)
        provider_calls = self.provider_calls.read_bytes()
        for mutation in ("rewrite", "delete"):
            with self.subTest(mutation=mutation):
                self.mutate_input(mutation)
                rejected = self.cli(run, *acceptance)
                self.assertEqual(2, rejected.returncode, rejected.stdout + rejected.stderr)
                self.assertIn("_version.py", rejected.stdout + rejected.stderr)
                self.assertFalse(run.status()["done"])
                self.assertEqual(provider_calls, self.provider_calls.read_bytes())
                self.write({"_version.py": VERSION})
        accepted = self.cli(run, *acceptance)
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        self.assertTrue(run.status()["done"])
        self.assertTrue(self.public_status(run)["completion_current"])
        self.assertEqual(provider_calls, self.provider_calls.read_bytes())

    def test_completed_run_status_and_continuation_refuse_changed_or_deleted_inputs(self):
        run, complete = self.start_greeting()
        self.assertTrue(complete["done"], complete)
        self.assertTrue(self.public_status(run)["completion_current"])
        provider_calls = self.provider_calls.read_bytes()
        for mutation in ("rewrite", "delete"):
            with self.subTest(mutation=mutation):
                self.mutate_input(mutation)
                public = self.public_status(run)
                self.assertFalse(public["completion_current"])
                self.assertEqual("stale_or_unverified", public["view"]["verification"]["freshness"])
                self.assertEqual(0, public["view"]["efficiency"]["delivery"]["verified_deliveries"])
                self.assertEqual(provider_calls, self.provider_calls.read_bytes())
                self.write({"_version.py": VERSION})
                self.assertTrue(self.public_status(run)["completion_current"])
        self.mutate_input("rewrite")
        stopped = run.advance()
        self.assertFalse(stopped["done"], stopped)
        self.assertIn("_version.py", stopped["stop_reason"])
        self.assertEqual(0, stopped["efficiency"]["delivery"]["verified_deliveries"])
        self.assertEqual(provider_calls, self.provider_calls.read_bytes())


if __name__ == "__main__":
    unittest.main()
