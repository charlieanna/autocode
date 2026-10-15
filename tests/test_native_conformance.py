"""Ordered native permission responses; no provider or model execution."""

import unittest
from copy import deepcopy

import autocode_native_conformance as native


class OrderedNativePermissionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"permission": "*", "pattern": "*", "action": "allow"},
            {"permission": "doom_loop", "pattern": "*", "action": "ask"},
            {"permission": "read", "pattern": "*.env", "action": "ask"},
            {"permission": "*", "pattern": "*", "action": "deny"},
            {"permission": "bash", "pattern": "*", "action": "allow"},
            {"permission": "external_directory", "pattern": "*", "action": "deny"},
        ]

    def test_universal_deny_overrides_inherited_defaults_without_changing_receipt(self):
        original = deepcopy(self.rows)
        self.assertTrue(native.non_shell_denied(self.rows))
        self.assertEqual(original, self.rows)

    def test_every_later_non_shell_grant_or_ask_is_refused(self):
        for category in ("read", "external_directory", "future_tool", "*", "ba*h"):
            for action in ("allow", "ask"):
                with self.subTest(category=category, action=action):
                    row = {"permission": category, "pattern": "*", "action": action}
                    self.assertFalse(native.non_shell_denied(self.rows + [row]))

    def test_later_universal_deny_revokes_a_previous_grant(self):
        rows = self.rows + [
            {"permission": "read", "pattern": "*", "action": "allow"},
            {"permission": "*", "pattern": "*", "action": "deny"},
        ]
        self.assertTrue(native.non_shell_denied(rows))

    def test_partial_or_category_denial_is_not_a_deny_default(self):
        for row in (
            {"permission": "*", "pattern": "/partial/*", "action": "deny"},
            {"permission": "read", "pattern": "*", "action": "deny"},
        ):
            with self.subTest(row=row):
                self.assertFalse(native.non_shell_denied([row]))

    def test_malformed_or_unknown_actions_refuse_even_before_universal_deny(self):
        for row in (
            None,
            {},
            {"permission": "read", "pattern": "*", "action": "permit"},
            {"permission": "", "pattern": "*", "action": "deny"},
            {"permission": "read", "pattern": "*", "action": "deny", "extra": True},
        ):
            with self.subTest(row=row):
                self.assertFalse(native.non_shell_denied([row, *self.rows]))
        self.assertFalse(native.non_shell_denied([]))
        self.assertFalse(native.non_shell_denied({}))
