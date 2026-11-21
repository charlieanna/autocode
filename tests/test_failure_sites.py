"""Structural source sites never replace authentication of source or runner output."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

import autocode_failure_sites as sites


class FailureSiteIdentityTests(unittest.TestCase):
    def identify(self, source, line, path="module.py"):
        return sites.identity(source, line, path=path)

    def test_unrelated_line_insertions_preserve_the_same_statement(self):
        before = "class T:\n    def check(self):\n        raise ValueError('missing')\n"
        after = "# unrelated\n\n" + before
        self.assertIsNotNone(self.identify(before, 3))
        self.assertEqual(self.identify(before, 3), self.identify(after, 5))

    def test_same_error_text_at_different_sites_or_scopes_does_not_match(self):
        source = (
            "def check(flag):\n"
            "    if flag:\n        raise ValueError('same')\n"
            "    raise ValueError('same')\n"
            "def other():\n    raise ValueError('same')\n"
        )
        self.assertNotEqual(self.identify(source, 3), self.identify(source, 4))
        self.assertNotEqual(self.identify(source, 4), self.identify(source, 6))
        self.assertNotEqual(self.identify(source, 4, path="one.py"), self.identify(source, 4, path="other.py"))

    def test_repeated_statement_does_not_survive_an_enclosing_scope_change(self):
        before = "def check(flag):\n    if flag:\n        raise ValueError('same')\n    raise ValueError('same')\n"
        after = before.replace("if flag:", "if not flag:")
        self.assertNotEqual(self.identify(before, 3), self.identify(after, 3))

    def test_changed_assertion_or_parameter_produces_a_different_identity(self):
        before = "def check(value):\n    assert value == 1, 'same'\n"
        self.assertNotEqual(self.identify(before, 2), self.identify(before.replace("== 1", "== 2"), 2))
        self.assertNotEqual(self.identify(before, 2), self.identify(before.replace("'same'", "'other'"), 2))

    def test_same_outer_error_with_a_different_explicit_cause_does_not_match(self):
        before = "def check():\n    raise RuntimeError('same') from ValueError('database down')\n"
        after = before.replace("database down", "authorization denied")
        self.assertNotEqual(self.identify(before, 2), self.identify(after, 2))

    def test_ambiguous_scopes_or_unparseable_source_are_not_evidence(self):
        duplicate = "def check():\n    return 1\ndef check():\n    return 1\n"
        self.assertIsNone(self.identify(duplicate, 2))
        self.assertIsNone(self.identify("def broken(:\n", 1))
        self.assertIsNone(self.identify("\ud800", 1))
        self.assertIsNone(self.identify("pass\n", 0))
        self.assertIsNone(self.identify("pass\n", 1, path=".GIT/config.py"))
        self.assertIsNone(self.identify("if True: raise ValueError('same')\n", 1))


@unittest.skipUnless(
    hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd,
    "descriptor-relative no-follow source reads are unavailable",
)
class PinnedFailureSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.package = self.root / "pkg"
        self.package.mkdir()
        self.file = self.package / "module.py"
        self.file.write_text("def check():\n    raise ValueError('same')\n")
        self.expected = hashlib.sha256(self.file.read_bytes()).hexdigest()

    def capture(self):
        return sites.source_stamp(self.root, "pkg/module.py", self.expected)

    def test_unchanged_pinned_source_has_the_same_site(self):
        before = self.capture()
        self.assertIsNotNone(before)
        self.assertEqual(
            sites.identity(self.file.read_text(), 2, path="pkg/module.py"),
            sites.pinned_identity(self.root, "pkg/module.py", 2, self.expected, before),
        )

    def test_mutated_and_restored_source_is_not_accepted(self):
        before = self.capture()
        original = self.file.read_bytes()
        self.file.write_text("def check():\n    raise ValueError('other')\n")
        self.file.write_bytes(original)
        self.assertIsNone(sites.pinned_identity(self.root, "pkg/module.py", 2, self.expected, before))

    def test_swapped_and_restored_parent_is_not_accepted(self):
        before = self.capture()
        saved = self.root / "saved"
        self.package.rename(saved)
        self.package.mkdir()
        (self.package / "module.py").write_text("def check():\n    raise ValueError('other')\n")
        (self.package / "module.py").unlink()
        self.package.rmdir()
        saved.rename(self.package)
        self.assertIsNone(sites.pinned_identity(self.root, "pkg/module.py", 2, self.expected, before))

    def test_ignored_output_in_a_watched_directory_remains_a_conservative_hold(self):
        before = self.capture()
        (self.root / ".pytest_cache").mkdir()
        self.assertIsNone(sites.pinned_identity(self.root, "pkg/module.py", 2, self.expected, before))

    def test_recreated_coverage_output_in_the_root_remains_a_conservative_hold(self):
        coverage = self.root / ".coverage"
        coverage.write_text("earlier output")
        before = self.capture()
        replacement = self.root / ".coverage.tmp"
        replacement.write_text("new output")
        replacement.replace(coverage)
        self.assertIsNone(sites.pinned_identity(self.root, "pkg/module.py", 2, self.expected, before))

    def test_links_hardlinks_and_noncanonical_paths_are_refused(self):
        self.assertIsNone(sites.source_stamp(self.root, "../pkg/module.py", self.expected))
        self.assertIsNone(sites.source_stamp(self.root, ".GIT/config.py", self.expected))
        self.assertIsNone(sites.source_stamp(self.root, "pkg//module.py", self.expected))
        outside = self.root / "hardlink.py"
        os.link(self.file, outside)
        self.assertIsNone(self.capture())
        outside.unlink()
        self.file.unlink()
        self.file.symlink_to("../outside.py")
        self.assertIsNone(self.capture())
