"""Actual Linux readonly command and sealed-policy replay, never image proof."""

import copy
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_native_linux_check as checks
import autocode_util as util
import autocode_verification_copy as copies


@unittest.skipUnless(
    sys.platform == "linux" and os.environ.get("AUTOCODE_NATIVE_LINUX_BOUNDARY_TEST") == "1",
    "explicit supported Linux kernel qualification required",
)
class NativeLinuxCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-linux-check-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.scratch = self.root / "scratch"
        self.tree = self.scratch / "verification"
        self.tree.mkdir(parents=True)
        self.writable = self.scratch / "writable"
        self.writable.mkdir()
        empty = self.scratch / "native-git-empty"
        empty.mkdir(mode=0o500)
        self.source = self.tree / "app.py"
        self.source.write_text("original-source")
        self.boundary = {
            "scratch": str(self.scratch),
            "tree": str(self.tree),
            "profile": str(self.root / "check-original.json"),
            "setup": False,
            "runtime_roots": sorted({str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve())}),
        }

    def prepare(self, command):
        value = checks.write(self.boundary, runtime=None)
        self.boundary["profile_sha256"] = util.file_hash(self.boundary["profile"])
        argv = checks.command(value, command)
        body = {"command": argv, "command_sha256": util.digest(argv)}
        checks.verify(self.boundary, body, runtime=None)
        return body

    def test_actual_approved_command_cannot_write_copy_and_can_write_structured_result(self):
        script = f"""
from pathlib import Path
p=Path({str(self.source)!r})
assert p.read_text()=='original-source'
try:p.write_text('corruption')
except PermissionError:pass
except OSError as e:
    assert e.errno==30,e
else:raise AssertionError('verification copy writable')
Path({str(self.writable / "result.json")!r}).write_text('{{"passed":true}}')
print('ACTUAL_READONLY_CHECK_PASS')
"""
        body = self.prepare(shlex.join([sys.executable, "-I", "-c", script]))
        result = subprocess.run(body["command"], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("original-source", self.source.read_text())
        self.assertEqual('{"passed":true}', (self.writable / "result.json").read_text())
        self.assertEqual("ACTUAL_READONLY_CHECK_PASS", result.stdout.strip())
        checks.verify(self.boundary, body, runtime=None)

    def test_actual_check_recreates_only_the_authenticated_node_alias(self):
        node = Path(shutil.which("node")).resolve(strict=True)
        parent = self.root / "logical-node-bin"
        parent.mkdir()
        logical = parent / "node"
        logical.symlink_to(node)
        private = parent / "private.txt"
        private.write_text("not a runtime grant")
        runtime = {
            "node": str(node),
            "files": {str(node): util.file_hash(node)},
            "logical_node": {"path": str(logical)},
        }
        value = checks.write(self.boundary, runtime=runtime)
        self.boundary["profile_sha256"] = util.file_hash(self.boundary["profile"])
        script = "const fs=require('node:fs');try{fs.readFileSync(process.argv[1]);process.exit(2)}catch(e){if(e.code!=='ENOENT')throw e}console.log('PINNED_NODE_ALIAS_PASS')"
        command = shlex.join([str(logical), "-e", script, str(private)])
        argv = checks.command(value, command)
        body = {"command": argv, "command_sha256": util.digest(argv)}
        checks.verify(self.boundary, body, runtime=runtime)
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("PINNED_NODE_ALIAS_PASS", result.stdout.strip())
        self.assertEqual("not a runtime grant", private.read_text())
        checks.verify(self.boundary, body, runtime=runtime)

    def test_actual_native_git_setup_has_only_its_original_git_directory_writable(self):
        self.boundary["setup"] = True
        (self.tree / ".git").mkdir()
        native = copies.native_git_setup(self.scratch)
        self.assertEqual("/usr/bin/git", native["executable"])
        self.boundary["runtime_roots"] = sorted({*self.boundary["runtime_roots"], native["executable"]})
        body = self.prepare(shlex.join(native["commands"][0]))
        result = subprocess.run(body["command"], env=native["environment"], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.tree / ".git/HEAD").is_file())
        self.assertEqual("original-source", self.source.read_text())
        checks.verify(self.boundary, body, runtime=None)

    def test_replay_refuses_changed_filter_and_preserves_the_original_failed_bytes(self):
        body = self.prepare("/bin/true")
        program = Path(self.boundary["profile"]).with_suffix(".bpf")
        before = program.read_bytes()
        program.chmod(0o600)
        program.write_bytes(before[:-8])
        program.chmod(0o400)
        with self.assertRaises(ValueError):
            checks.verify(self.boundary, body, runtime=None)
        self.assertEqual(before[:-8], program.read_bytes())

    def test_replay_refuses_command_bypass_or_changed_writable_authority(self):
        body = self.prepare("/bin/true")
        changed = copy.deepcopy(body)
        changed["command"] = ["/bin/bash", "-c", "/bin/true"]
        changed["command_sha256"] = util.digest(changed["command"])
        with self.assertRaises(ValueError):
            checks.verify(self.boundary, changed, runtime=None)
        changed = {**self.boundary, "setup": True}
        (self.tree / ".git").mkdir()
        with self.assertRaises(ValueError):
            checks.verify(changed, body, runtime=None)
        checks.verify(self.boundary, body, runtime=None)
