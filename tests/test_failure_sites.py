"""Structural source sites never replace authentication of source or runner output."""

import unittest

import autocode_failure_sites as sites


class FailureSiteIdentityTests(unittest.TestCase):
    def test_unrelated_line_insertions_preserve_the_same_statement(self):
        before = "class T:\n    def check(self):\n        raise ValueError('missing')\n"
        after = "# unrelated\n\n" + before
        self.assertIsNotNone(sites.identity(before, 3))
        self.assertEqual(sites.identity(before, 3), sites.identity(after, 5))

    def test_same_error_text_at_different_sites_or_scopes_does_not_match(self):
        source = (
            "def check(flag):\n"
            "    if flag:\n        raise ValueError('same')\n"
            "    raise ValueError('same')\n"
            "def other():\n    raise ValueError('same')\n"
        )
        self.assertNotEqual(sites.identity(source, 3), sites.identity(source, 4))
        self.assertNotEqual(sites.identity(source, 4), sites.identity(source, 6))

    def test_repeated_statement_does_not_survive_an_enclosing_scope_change(self):
        before = "def check(flag):\n    if flag:\n        raise ValueError('same')\n    raise ValueError('same')\n"
        after = before.replace("if flag:", "if not flag:")
        self.assertNotEqual(sites.identity(before, 3), sites.identity(after, 3))

    def test_changed_assertion_or_parameter_produces_a_different_identity(self):
        before = "def check(value):\n    assert value == 1, 'same'\n"
        self.assertNotEqual(sites.identity(before, 2), sites.identity(before.replace("== 1", "== 2"), 2))
        self.assertNotEqual(sites.identity(before, 2), sites.identity(before.replace("'same'", "'other'"), 2))

    def test_same_outer_error_with_a_different_explicit_cause_does_not_match(self):
        before = "def check():\n    raise RuntimeError('same') from ValueError('database down')\n"
        after = before.replace("database down", "authorization denied")
        self.assertNotEqual(sites.identity(before, 2), sites.identity(after, 2))

    def test_ambiguous_scopes_or_unparseable_source_are_not_evidence(self):
        duplicate = "def check():\n    return 1\ndef check():\n    return 1\n"
        self.assertIsNone(sites.identity(duplicate, 2))
        self.assertIsNone(sites.identity("def broken(:\n", 1))
        self.assertIsNone(sites.identity("pass\n", 0))
        self.assertIsNone(sites.identity("if True: raise ValueError('same')\n", 1))
