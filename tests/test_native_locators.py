"""Exact owner comparisons; compact references never become new authority."""

import copy
import json
import unittest

import autocode_native_locators as locators


class NativeLocatorTests(unittest.TestCase):
    def setUp(self):
        self.descriptor = {
            "manifest": "/owned/isolation.json",
            "manifest_sha256": "a" * 64,
            "files": {"/owned/sdk/member.js": "b" * 64},
            "role": "requirements",
        }
        self.boundary = {
            "version": 1,
            "tools": ["bash"],
            "allow_write": False,
            "profile_sha256": "c" * 64,
            "native_role_isolation": self.descriptor,
        }

    def test_compact_and_exact_legacy_values_match_full_original_owner(self):
        for raw in (self.descriptor, locators.descriptor_value(self.descriptor)):
            self.assertTrue(locators.descriptor_matches(json.dumps(raw), self.descriptor))
        for raw in (self.boundary, locators.boundary_value(self.boundary)):
            self.assertTrue(locators.boundary_matches(json.dumps(raw), self.boundary))
            self.assertEqual(self.boundary, locators.expand_boundary(json.dumps(raw), self.descriptor))

    def test_wrong_manifest_hash_path_extra_fields_and_inventory_are_refused(self):
        original = locators.descriptor_value(self.descriptor)
        for changes in (
            {"manifest": "/foreign/isolation.json"},
            {"manifest_sha256": "d" * 64},
            {"files": {}},
            {"role": "requirements"},
        ):
            with self.subTest(changes=changes):
                value = {**original, **changes}
                self.assertFalse(locators.descriptor_matches(json.dumps(value), self.descriptor))
                boundary = {**self.boundary, "native_role_isolation": value}
                with self.assertRaisesRegex(ValueError, "differs"):
                    locators.expand_boundary(json.dumps(boundary), self.descriptor)
        changed = copy.deepcopy(self.descriptor)
        changed["files"]["/owned/sdk/member.js"] = "d" * 64
        self.assertFalse(locators.descriptor_matches(json.dumps(changed), self.descriptor))

    def test_compact_boundary_retains_every_other_exact_field(self):
        compact = locators.boundary_value(self.boundary)
        for key, changed in (
            ("allow_write", True),
            ("tools", ["bash", "edit"]),
            ("profile_sha256", "d" * 64),
            ("foreign", "value"),
        ):
            with self.subTest(key=key):
                self.assertFalse(locators.boundary_matches({**compact, key: changed}, self.boundary))

    def test_duplicate_json_fields_are_refused(self):
        raw = '{"manifest":"/foreign","manifest":"/owned/isolation.json","manifest_sha256":"' + "a" * 64 + '"}'
        with self.assertRaisesRegex(ValueError, "repeats"):
            locators.descriptor_matches(raw, self.descriptor)

    def test_invalid_original_identity_cannot_be_accepted_or_expanded(self):
        for value in (
            None,
            {},
            {**self.descriptor, "manifest": "relative"},
            {**self.descriptor, "manifest_sha256": "bad"},
            {**self.descriptor, "manifest": "/owned/\x00"},
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "identity"):
                locators.descriptor_matches(value, value)

    def test_expansion_does_not_mutate_or_read_supplied_manifest(self):
        before = copy.deepcopy(self.boundary)
        compact = locators.boundary_value(self.boundary)
        expanded = locators.expand_boundary(compact, self.descriptor)
        expanded["native_role_isolation"]["files"].clear()
        self.assertEqual(before, self.boundary)
        self.assertEqual({"manifest", "manifest_sha256"}, set(compact["native_role_isolation"]))

    def test_plain_non_native_boundaries_keep_exact_legacy_behavior(self):
        plain = {"tools": ["bash"], "profile_sha256": "a" * 64}
        self.assertEqual(plain, locators.boundary_value(plain))
        self.assertTrue(locators.boundary_matches(json.dumps(plain), plain))


if __name__ == "__main__":
    unittest.main()
