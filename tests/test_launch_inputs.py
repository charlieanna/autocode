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


class WorkspaceAnchorTests(Project):
    """System spelling must not widen source, storage or copy authority (#658)."""

    def clone(self, name):
        root = self.destination(name) / "project"
        shutil.copytree(self.root, root)
        return root

    def assert_capture_refused(self, root, run_dir):
        state = json.loads(json.dumps(self.state))
        try:
            launch_inputs.record(state, root, run_dir)
        except (OSError, ValueError):
            return
        supplied = launch_inputs.supply(state, root, run_dir)
        self.assertTrue(supplied.unverified, "A rejected anchor cannot admit a complete launch inventory")
        with self.assertRaises(ValueError):
            supplied.copy_into(Path(tempfile.mkdtemp(prefix="refused-copy-", dir=self.temporary)))

    def system_aliases(self):
        if sys.platform != "darwin":
            self.skipTest("The native macOS /var and /tmp aliases are required")
        for alias in (Path("/var"), Path("/tmp")):
            if not alias.is_symlink():
                self.skipTest(f"The native system alias {alias} is absent")
            self.assertEqual(0, alias.lstat().st_uid)
            self.assertEqual(Path("/private") / alias.name, alias.resolve())
        canonical_temp = Path(tempfile.gettempdir()).resolve()
        try:
            var_temp = Path("/var") / canonical_temp.relative_to("/private/var")
        except ValueError:
            self.skipTest("The actual user temporary parent is not underneath the native /var alias")
        return (var_temp, Path("/tmp"))

    def test_native_system_alias_capture_supply_and_copy_matches_canonical(self):
        self.hide({"vendor/marker.py": "VALUE = 1\n"})
        (self.root / "_version.py").chmod(0o751)
        (self.root / "vendor/marker.py").chmod(0o640)
        for alias in self.system_aliases():
            with self.subTest(alias=str(alias)), tempfile.TemporaryDirectory(
                    prefix="launch-alias-native-", dir=str(alias)) as temporary:
                lexical = Path(temporary) / "project"
                canonical = lexical.resolve()
                self.assertNotEqual(lexical, canonical, "Preserve the actual lexical system alias")
                shutil.copytree(self.root, canonical)
                canonical_run = canonical / ".autocode/runs/canonical"
                lexical_run = lexical / ".autocode/runs/lexical"
                canonical_run.mkdir(); lexical_run.mkdir()
                canonical_state = json.loads(json.dumps(self.state))
                lexical_state = json.loads(json.dumps(self.state))
                launch_inputs.record(canonical_state, canonical, canonical_run)
                launch_inputs.record(lexical_state, lexical, lexical_run)
                canonical_supply = launch_inputs.supply(canonical_state, canonical, canonical_run)
                lexical_supply = launch_inputs.supply(lexical_state, lexical, lexical_run)
                self.assertEqual([], canonical_supply.unverified)
                self.assertEqual([], lexical_supply.unverified)
                self.assertTrue(canonical_supply.recorded and lexical_supply.recorded)
                self.assertEqual(canonical_supply.generated, lexical_supply.generated)
                self.assertEqual(canonical_supply.vendored, lexical_supply.vendored)
                self.assertEqual(canonical_supply.identity, lexical_supply.identity)
                self.assertEqual(canonical_state["launch_sources"], lexical_state["launch_sources"])
                self.assertEqual((canonical_run / "launch-sources/manifest.json").read_bytes(),
                                 (lexical_run / "launch-sources/manifest.json").read_bytes())
                copies = Path(temporary) / "copies"
                copies.mkdir()
                canonical_copy = copies.resolve() / "canonical"
                lexical_copy = copies / "lexical"
                canonical_copy.mkdir(); lexical_copy.mkdir()
                canonical_supply.copy_into(canonical_copy)
                lexical_supply.copy_into(lexical_copy)
                for name, data, mode in (("_version.py", VERSION.encode(), 0o751),
                                         ("vendor/marker.py", b"VALUE = 1\n", 0o640)):
                    for tree in (canonical_copy, lexical_copy):
                        self.assertEqual(data, (tree / name).read_bytes())
                        self.assertEqual(mode, (tree / name).stat().st_mode & 0o777)

    def test_arbitrary_workspace_run_and_store_symlinks_are_refused(self):
        for role in ("workspace-leaf", "workspace-ancestor", "autocode", "run-parent", "run-leaf", "store-leaf"):
            with self.subTest(role=role):
                root = self.clone("anchor-" + role)
                outside = self.clone("outside-" + role)
                sentinel = outside / "sentinel"
                sentinel.write_text("external authority")
                external_run = outside / ".autocode/runs/policy"
                run_dir = root / ".autocode/runs/policy"
                if role == "workspace-leaf":
                    link = root.parent / "workspace-link"
                    link.symlink_to(outside, target_is_directory=True)
                    root = link
                    run_dir = root / ".autocode/runs/policy"
                elif role == "workspace-ancestor":
                    link = root.parent / "parent-link"
                    link.symlink_to(outside.parent, target_is_directory=True)
                    root = link / "project"
                    run_dir = root / ".autocode/runs/policy"
                elif role in ("autocode", "run-parent", "run-leaf"):
                    relative = {"autocode": ".autocode", "run-parent": ".autocode/runs",
                                "run-leaf": ".autocode/runs/policy"}[role]
                    shutil.rmtree(root / relative)
                    (root / relative).symlink_to(outside / relative, target_is_directory=True)
                else:
                    external_store = external_run / "launch-sources"
                    external_store.mkdir()
                    (run_dir / "launch-sources").symlink_to(external_store, target_is_directory=True)
                self.assert_capture_refused(root, run_dir)
                self.assertEqual("external authority", sentinel.read_text())
                self.assertFalse((external_run / "launch-sources/manifest.json").exists())
                self.assertFalse((external_run / "launch-sources" /
                                  hashlib.sha256(VERSION.encode()).hexdigest()).exists())

    def test_system_alias_does_not_trust_nested_source_or_destination_links(self):
        self.hide({"vendor/pkg/marker.py": "VALUE = 1\n"})
        for alias in self.system_aliases():
            for role in ("source-leaf", "source-parent", "destination-leaf", "destination-parent"):
                with self.subTest(alias=str(alias), role=role), tempfile.TemporaryDirectory(
                        prefix="launch-alias-links-", dir=str(alias)) as temporary:
                    root = Path(temporary) / "project"
                    shutil.copytree(self.root, root)
                    run_dir = root / ".autocode/runs/policy"
                    state = json.loads(json.dumps(self.state))
                    launch_inputs.record(state, root, run_dir)
                    supplied = launch_inputs.supply(state, root, run_dir)
                    self.assertEqual([], supplied.unverified)
                    outside = Path(temporary).resolve() / "outside"
                    outside.mkdir()
                    (outside / "_version.py").write_text(VERSION)
                    (outside / "marker.py").write_text("VALUE = 1\n")
                    (outside / "sentinel").write_text("external matching bytes")
                    tree = Path(temporary) / "copy"
                    tree.mkdir()
                    if role == "source-leaf":
                        (root / "_version.py").unlink()
                        (root / "_version.py").symlink_to(outside / "_version.py")
                    elif role == "source-parent":
                        shutil.rmtree(root / "vendor/pkg")
                        (root / "vendor/pkg").symlink_to(outside, target_is_directory=True)
                    elif role == "destination-leaf":
                        (tree / "_version.py").symlink_to(outside / "_version.py")
                    else:
                        (tree / "vendor").mkdir()
                        (tree / "vendor/pkg").symlink_to(outside, target_is_directory=True)
                    if role.startswith("source"):
                        supplied = launch_inputs.supply(state, root, run_dir)
                        self.assertTrue(supplied.unverified)
                    with self.assertRaises((OSError, ValueError)):
                        supplied.copy_into(tree)
                    self.assertEqual("external matching bytes", (outside / "sentinel").read_text())
                    self.assertEqual(VERSION, (outside / "_version.py").read_text())
                    self.assertEqual("VALUE = 1\n", (outside / "marker.py").read_text())
                    self.assertEqual({"_version.py", "marker.py", "sentinel"},
                                     {path.name for path in outside.iterdir()})

    def test_destination_root_replaced_after_validation_cannot_receive_captured_bytes(self):
        self.capture()
        supplied = self.supply()
        self.assertEqual([], supplied.unverified)
        for replacement in ("directory", "symlink"):
            with self.subTest(replacement=replacement):
                tree = self.destination("destination-race-" + replacement)
                saved = tree.with_name(tree.name + "-original")
                outside = self.destination("destination-external-" + replacement)
                sentinel = outside / "sentinel"
                sentinel.write_text("external destination")
                external_files = {str(path.relative_to(outside)) for path in outside.rglob("*") if path.is_file()}
                original_path = launch_inputs._path
                swapped = False

                def validate_then_replace(root, name):
                    nonlocal swapped
                    result = original_path(root, name)
                    if Path(root) == tree and name == "_version.py" and not swapped:
                        tree.rename(saved)
                        if replacement == "directory":
                            outside.rename(tree)
                        else:
                            tree.symlink_to(outside, target_is_directory=True)
                        swapped = True
                    return result

                with mock.patch.object(launch_inputs, "_path", side_effect=validate_then_replace):
                    with self.assertRaises((OSError, ValueError)):
                        supplied.copy_into(tree)
                self.assertTrue(swapped, "Inject replacement after successful path validation")
                destination = tree if replacement == "directory" else outside
                self.assertEqual("external destination", (destination / "sentinel").read_text())
                self.assertFalse((destination / "_version.py").exists())
                self.assertEqual(external_files,
                                 {str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()})

    def test_capture_root_replacement_cannot_admit_matching_external_source(self):
        for replacement in ("directory", "symlink"):
            with self.subTest(replacement=replacement):
                root = self.clone("capture-race-" + replacement)
                external = self.clone("capture-external-" + replacement)
                (external / "sentinel").write_text("external source")
                external_files = {str(path.relative_to(external)) for path in external.rglob("*") if path.is_file()}
                original = root.with_name("original-project")
                run_dir = root / ".autocode/runs/policy"
                original_path = launch_inputs._path
                swapped = False

                def validate_then_replace(anchor, name):
                    nonlocal swapped
                    result = original_path(anchor, name)
                    if Path(anchor) == root and name == "_version.py" and not swapped:
                        root.rename(original)
                        if replacement == "directory":
                            external.rename(root)
                        else:
                            root.symlink_to(external, target_is_directory=True)
                        swapped = True
                    return result

                with mock.patch.object(launch_inputs, "_path", side_effect=validate_then_replace):
                    self.assert_capture_refused(root, run_dir)
                self.assertTrue(swapped, "Matching bytes must not conceal a different root identity")
                target = root if replacement == "directory" else external
                self.assertEqual("external source", (target / "sentinel").read_text())
                self.assertEqual(VERSION, (target / "_version.py").read_text())
                self.assertFalse((target / ".autocode/runs/policy/launch-sources/manifest.json").exists())
                self.assertEqual(external_files,
                                 {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()})

    def test_run_root_replaced_after_validation_cannot_capture_into_external_storage(self):
        for replacement in ("directory", "symlink"):
            with self.subTest(replacement=replacement):
                root = self.clone("run-race-" + replacement)
                run_dir = root / ".autocode/runs/policy"
                original = run_dir.with_name("original-policy")
                external = self.destination("run-external-" + replacement)
                (external / "sentinel").write_text("external storage")
                external_files = {str(path.relative_to(external)) for path in external.rglob("*") if path.is_file()}
                digest = hashlib.sha256(VERSION.encode()).hexdigest()
                original_path = launch_inputs._path
                swapped = False

                def validate_then_replace(anchor, name):
                    nonlocal swapped
                    result = original_path(anchor, name)
                    if Path(anchor) == run_dir and name == "launch-sources/" + digest and not swapped:
                        run_dir.rename(original)
                        if replacement == "directory":
                            external.rename(run_dir)
                        else:
                            run_dir.symlink_to(external, target_is_directory=True)
                        swapped = True
                    return result

                with mock.patch.object(launch_inputs, "_path", side_effect=validate_then_replace):
                    self.assert_capture_refused(root, run_dir)
                self.assertTrue(swapped, "Inject storage replacement after successful path validation")
                target = run_dir if replacement == "directory" else external
                self.assertEqual("external storage", (target / "sentinel").read_text())
                self.assertFalse((target / "launch-sources" / digest).exists())
                self.assertFalse((target / "launch-sources/manifest.json").exists())
                self.assertEqual(external_files,
                                 {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()})

    def test_copy_keeps_original_checkout_store_and_destination_across_files(self):
        self.hide({"_build.py": "BUILD = 1\n"})
        for role in ("checkout", "store", "destination"):
            with self.subTest(role=role):
                root = self.clone("multi-copy-" + role)
                run_dir = root / ".autocode/runs/policy"
                state = json.loads(json.dumps(self.state))
                launch_inputs.record(state, root, run_dir)
                supplied = launch_inputs.supply(state, root, run_dir)
                self.assertEqual([], supplied.unverified)
                generated = list(supplied.generated)
                self.assertEqual(2, len(generated), "Exercise two actual generated file admissions")
                first, second = generated
                tree = self.destination("multi-destination-" + role)
                store = run_dir / "launch-sources"
                replace = {"checkout": root, "store": store, "destination": tree}[role]
                external = self.temporary / ("multi-external-" + role)
                shutil.copytree(replace, external)
                (external / "sentinel").write_text("external multi-file authority")
                if role == "destination":
                    shutil.copy2(root / first, external / first)
                external_files = {str(path.relative_to(external)) for path in external.rglob("*") if path.is_file()}
                saved = replace.with_name(replace.name + "-original")
                original_read = launch_inputs._read
                swapped = False

                def read_then_replace(anchor, name, *args, **kwargs):
                    nonlocal swapped
                    result = original_read(anchor, name, *args, **kwargs)
                    trigger = ((role == "checkout" and Path(anchor) == root and name == first)
                               or (role == "store" and Path(anchor) == store
                                   and name == supplied.generated[first][0])
                               or (role == "destination" and Path(anchor) == root and name == second))
                    if trigger and not swapped:
                        replace.rename(saved)
                        external.rename(replace)
                        swapped = True
                    return result

                with mock.patch.object(launch_inputs, "_read", side_effect=read_then_replace):
                    with self.assertRaises((OSError, ValueError)):
                        supplied.copy_into(tree)
                self.assertTrue(swapped, "Replace between actual file admissions, not during initial root pinning")
                self.assertEqual("external multi-file authority", (replace / "sentinel").read_text())
                self.assertEqual(external_files,
                                 {str(path.relative_to(replace)) for path in replace.rglob("*") if path.is_file()})
                if role == "destination":
                    self.assertFalse((replace / second).exists())

    def test_cleanup_close_failure_preserves_primary_root_refusal_and_closes_fds(self):
        self.capture()
        supplied = self.supply()
        self.assertEqual([], supplied.unverified)
        tree = self.destination("cleanup-race-copy")
        saved = tree.with_name(tree.name + "-original")
        outside = self.destination("cleanup-external")
        (outside / "sentinel").write_text("external cleanup authority")
        original_path = launch_inputs._path
        original_os = launch_inputs.os
        swapped = False

        class LocalOS:
            def __init__(self):
                self.opened = []
                self.injected = False

            def __getattr__(self, name):
                return getattr(original_os, name)

            def open(self, *args, **kwargs):
                fd = original_os.open(*args, **kwargs)
                self.opened.append(fd)
                return fd

            def dup(self, fd):
                duplicated = original_os.dup(fd)
                self.opened.append(duplicated)
                return duplicated

            def close(self, fd):
                original_os.close(fd)
                if sys.exc_info()[0] is ValueError and not self.injected:
                    self.injected = True
                    raise OSError("controlled failure after actual close")

        def validate_then_replace(root, name):
            nonlocal swapped
            result = original_path(root, name)
            if Path(root) == tree and name == "_version.py" and not swapped:
                tree.rename(saved)
                outside.rename(tree)
                swapped = True
            return result

        local_os = LocalOS()
        # Replace only this module's facade, not the global OS used by threads/subprocesses.
        with mock.patch.object(launch_inputs, "os", local_os), \
                mock.patch.object(launch_inputs, "_path", side_effect=validate_then_replace):
            with self.assertRaisesRegex(ValueError, "root changed during admission"):
                supplied.copy_into(tree)
        self.assertTrue(swapped)
        self.assertTrue(local_os.injected, "The real close must occur before its controlled failure")
        self.assertTrue(local_os.opened)
        for fd in set(local_os.opened):
            with self.assertRaises(OSError):
                original_os.fstat(fd)
        self.assertEqual({"sentinel"}, {path.name for path in tree.iterdir()})
        self.assertEqual("external cleanup authority", (tree / "sentinel").read_text())


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
