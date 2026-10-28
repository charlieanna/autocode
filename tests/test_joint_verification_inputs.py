"""Independent checks retain only fingerprint-bound local joint candidates."""

import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import unittest

import autocode_joint_inputs as inputs
import autocode_verification_copy as copies
import autocode_verify as verify

from . import test_verification_copy as fixture

COMPARE = """import hashlib, json
from pathlib import Path
m=json.loads(Path('.autocode/candidate-inputs.json').read_text())
def files(root):
 return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
for row in m.values():
 assert files(Path(row['source'])) == files(Path(row['destination'])), 'candidate differs, including deletions'
assert not Path('.autocode/private.txt').exists()
print('exact candidate comparison passed')
"""


class JointVerificationInputsTests(unittest.TestCase):
    setUp = fixture.VerificationSourceTests.setUp
    git = fixture.VerificationSourceTests.git
    init_git = fixture.VerificationSourceTests.init_git

    def fixture(self):
        (self.root / ".gitignore").write_text(".autocode/\n")
        self.original = self.root / ".autocode/candidate-inputs/service-abc"
        self.original.mkdir(parents=True)
        (self.original / "service.py").write_text("def total(): return 110\n")
        self.destination = self.root / "repos/service-abc"
        shutil.copytree(self.original, self.destination)
        (self.root / "repositories.json").write_text(json.dumps({"service": {"path": "repos/service-abc"}}))
        self.mapping = {
            "service": {"source": ".autocode/candidate-inputs/service-abc", "destination": "repos/service-abc"}
        }
        self.reseal()
        (self.root / ".autocode/private.txt").write_text("must not be copied")
        self.command = shlex.join([sys.executable, "-B", "-c", COMPARE])

    def reseal(self):
        entries = {
            str(p.relative_to(self.original)): {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "executable": bool(p.stat().st_mode & 0o111),
            }
            for p in self.original.rglob("*")
            if p.is_file()
        }
        self.mapping["service"]["fingerprint"] = hashlib.sha256(
            json.dumps(entries, sort_keys=True).encode()
        ).hexdigest()
        (self.root / ".autocode/candidate-inputs.json").write_text(json.dumps(self.mapping))

    def test_capture_and_clean_replay_execute_exact_candidate_comparison(self):
        self.fixture()
        self.assertEqual(
            {".autocode/candidate-inputs.json", ".autocode/candidate-inputs/service-abc/service.py"},
            set(inputs.inventory(self.root)),
        )
        prepared = copies.allocate(self.root, self.run_dir)
        tree, _ = copies.execution(prepared["manifest"], prepared["sha256"], self.root)
        checked = subprocess.run([sys.executable, "-B", "-c", COMPARE], cwd=tree, text=True, capture_output=True)
        self.assertEqual(0, checked.returncode, checked.stderr)
        replay = verify.scratch_run(self.root, self.run_dir / "replay", command=self.command, timeout=20)
        self.assertEqual(0, replay["exit_code"], replay)
        (self.destination / "unexpected.py").write_text("extra = True\n")
        failed = verify.scratch_run(self.root, self.run_dir / "mismatch", command=self.command, timeout=20)
        self.assertNotEqual(0, failed["exit_code"])
        self.assertIn("candidate differs", failed["tail"])

    def test_changed_input_and_resealed_input_invalidate_existing_copy(self):
        self.fixture()
        prepared = copies.allocate(self.root, self.run_dir)
        before = verify.execution_identity(self.root, command=self.command)
        (self.original / "service.py").write_text("def total(): return 999\n")
        with self.assertRaises(ValueError):
            copies.execution(prepared["manifest"], prepared["sha256"], self.root)
        self.reseal()
        with self.assertRaises(ValueError):
            copies.execution(prepared["manifest"], prepared["sha256"], self.root)
        after = verify.execution_identity(self.root, command=self.command)
        self.assertNotEqual(before, after)

    def test_copied_payload_cannot_gain_unbound_files(self):
        self.fixture()
        prepared = copies.allocate(self.root, self.run_dir)
        tree, _ = copies.execution(prepared["manifest"], prepared["sha256"], self.root)
        (tree / ".autocode/candidate-inputs/service-abc/injected.py").write_text("extra = True\n")
        with self.assertRaises(ValueError):
            copies.execution(prepared["manifest"], prepared["sha256"], self.root)

    def test_unsafe_or_corrupt_inputs_are_not_copied(self):
        self.fixture()
        for source in ("../outside", "/tmp/outside", ".autocode/runs/fixture", ".autocode/candidate-inputs/../private"):
            self.mapping["service"]["source"] = source
            self.reseal()
            with self.assertRaises(ValueError):
                copies.allocate(self.root, self.run_dir)
        self.mapping["service"]["source"] = ".autocode/candidate-inputs/service-abc"
        self.reseal()
        (self.original / "service.py").write_text("tampered\n")
        with self.assertRaises(ValueError):
            copies.allocate(self.root, self.run_dir)
        (self.original / "service.py").unlink()
        (self.original / "service.py").symlink_to(self.root / ".autocode/private.txt")
        self.reseal()
        with self.assertRaises(ValueError):
            copies.allocate(self.root, self.run_dir)
