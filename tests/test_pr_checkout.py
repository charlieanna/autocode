"""PR review prevents incidental bytecode while refusing every unexpected cache file."""

import importlib.util
import marshal
import os
import py_compile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_pr_checkout as checkout

from tests import GIT_TEST_CONFIG


class CacheProtectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="review-checkout-")
        self.root = Path(temp.name).resolve()
        self.addCleanup(temp.cleanup)
        self.git("init", "-q")
        for key, value in {**GIT_TEST_CONFIG, "user.name": "fixture", "user.email": "fixture@example.test"}.items():
            self.git("config", key, value)
        (self.root / "module.py").write_text(
            "VALUE = 7\ndef first():\n    return (1000, 2000)\ndef second():\n    return (1000, 2000)\n"
        )
        (self.root / ".gitignore").write_text("__pycache__/\nignored.py\n")
        self.git("add", ".")
        self.git("commit", "-qm", "captured source")
        self.head = self.git("rev-parse", "HEAD")
        self.source_before = (
            (self.root / "module.py").read_bytes(),
            (self.root / "module.py").stat().st_mode,
            self.root.stat().st_mode,
        )
        self.guards = checkout._protect_caches(self.root, ["module.py"])
        self.owned = checkout.Checkout(self.root, self.root, self.head, self.head, self.head, self.guards)

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    def unprotect_for_control(self):
        (self.root / "__pycache__").chmod(0o755)

    @unittest.skipIf(os.geteuid() == 0, "POSIX cache-write prevention requires an unprivileged process")
    def test_cache_directory_does_not_change_source_or_cwd_write_access(self):
        before = self.source_before
        fixture = self.root / "ordinary-test-fixture"
        fixture.mkdir()
        (fixture / "output").write_text("tests can still write cwd fixtures")
        (fixture / "output").unlink()
        fixture.rmdir()
        process = subprocess.run(
            [sys.executable, "-E", "-c", "import module; assert module.VALUE == 7"],
            cwd=self.root,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertEqual([], list((self.root / "__pycache__").iterdir()))
        self.assertEqual(
            before,
            (
                (self.root / "module.py").read_bytes(),
                (self.root / "module.py").stat().st_mode,
                self.root.stat().st_mode,
            ),
        )
        self.owned.unchanged()
        self.assertEqual("", self.git("diff", "HEAD", "--name-only"))

    def test_permission_change_restore_replacement_and_alias_are_refused(self):
        target = self.root / "__pycache__"
        self.unprotect_for_control()
        with self.assertRaises(checkout.CheckoutError):
            self.owned.unchanged()
        target.chmod(0o555)
        with self.assertRaises(checkout.CheckoutError):
            self.owned.unchanged()
        target.rmdir()
        target.mkdir(mode=0o555)
        with self.assertRaises(checkout.CheckoutError):
            self.owned.unchanged()
        target.rmdir()
        foreign = self.root / "foreign"
        foreign.mkdir()
        target.symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(checkout.CheckoutError):
            self.owned.unchanged()
        self.assertEqual([], list(foreign.iterdir()))

    def test_no_genuine_forged_stale_malformed_symlink_or_hardlinked_cache_is_admitted(self):
        # Bytecode never gets an exemption, including a correctly named source counterpart.
        self.unprotect_for_control()
        target = Path(importlib.util.cache_from_source(str(self.root / "module.py")))
        plain = checkout.Checkout(self.root, self.root, self.head, self.head, self.head)
        py_compile.compile(str(self.root / "module.py"), doraise=True)
        genuine = target.read_bytes()
        compiled = compile((self.root / "module.py").read_bytes(), str(self.root / "module.py"), "exec")
        functions = [value for value in compiled.co_consts if isinstance(value, type(compiled))]
        first, second = functions
        shared = first.co_consts[1]
        self.assertIs(shared, second.co_consts[1])
        split = tuple(list(shared))
        self.assertIsNot(shared, split)
        altered = second.replace(co_consts=(second.co_consts[0], split))
        forged = compiled.replace(
            co_consts=tuple(altered if value is second else value for value in compiled.co_consts)
        )
        payloads = {
            "genuine": genuine,
            "forged-equal-value-different-sharing": genuine[:16] + marshal.dumps(forged),
            "stale": genuine[:8] + b"\0" * 8 + genuine[16:],
            "malformed": genuine[:16] + b"not a marshal payload",
        }
        for name, payload in payloads.items():
            with self.subTest(payload=name):
                target.write_bytes(payload)
                with self.assertRaises(checkout.CheckoutError):
                    plain.unchanged()
                with self.assertRaises(checkout.CheckoutError):
                    self.owned.unchanged()
                target.unlink()
        other = self.root / ".autocode" / "cache-control"
        other.parent.mkdir()
        other.write_bytes(genuine)
        plain.unchanged()  # The control alone is allowed; only its linked cache must refuse.
        target.symlink_to(other)
        with self.assertRaises(checkout.CheckoutError):
            plain.unchanged()
        target.unlink()
        os.link(other, target)
        with self.assertRaises(checkout.CheckoutError):
            plain.unchanged()

    def test_existing_cache_path_and_parent_alias_are_never_adopted_or_chmodded(self):
        foreign = self.root / "foreign"
        foreign.mkdir(mode=0o750)
        before = foreign.stat().st_mode
        (self.root / "alias").symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(checkout.CheckoutError):
            checkout._protect_caches(self.root, ["alias/module.py"])
        self.assertEqual([], list(foreign.iterdir()))
        self.assertEqual(before, foreign.stat().st_mode)
        with self.assertRaises(checkout.CheckoutError):
            checkout._protect_caches(self.root, ["module.py"])
        parent = self.root / "new-source"
        parent.mkdir()
        replacement = self.root / "replacement-cache"
        replacement.mkdir(mode=0o750)
        mode = replacement.stat().st_mode
        original_open = os.open
        redirected = False

        def replace_before_open(path, flags, *, dir_fd=None):
            nonlocal redirected
            if path == "__pycache__" and not redirected:
                (parent / "__pycache__").rmdir()
                replacement.rename(parent / "__pycache__")
                redirected = True
            return original_open(path, flags, dir_fd=dir_fd)

        with (
            patch.object(checkout.os, "open", side_effect=replace_before_open),
            patch.object(checkout.os, "fchmod", wraps=os.fchmod) as chmod,
            self.assertRaises(checkout.CheckoutError),
        ):
            checkout._protect_caches(self.root, ["new-source/module.py"])
        chmod.assert_not_called()
        self.assertTrue(redirected)
        self.assertEqual(mode, (parent / "__pycache__").stat().st_mode)

    def test_ignored_source_is_still_refused(self):
        (self.root / "ignored.py").write_text("hidden_source_change = True\n")
        self.assertEqual("", self.git("diff", "HEAD", "--name-only"))
        with self.assertRaises(checkout.CheckoutError):
            self.owned.unchanged()
