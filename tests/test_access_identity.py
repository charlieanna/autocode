"""Relocation aliases require retained complete transport preimages (#2477)."""

import copy
import unittest

import autocode_access_identity as identity
from autocode_util import digest


class AccessIdentityTests(unittest.TestCase):
    def setUp(self):
        self.old = {
            "engine": "kilocode",
            "identity_version": 1,
            "executable": "/bin/kilo",
            "version": "7.8.3",
            "config_path": "/old/kilocode.toml",
            "config_sha256": "a" * 64,
        }
        self.current = {**self.old, "config_path": "/new/kilocode.toml"}
        self.roles = {"glm": {"model": "plan/glm", "reasoning_effort": "low"}}
        self.state = {
            "settings": self.settings(self.current),
            "configuration_changes": [{"previous": self.settings(self.old), "selected": self.settings(self.current)}],
        }
        self.receipt = {"provider": "kilocode", "transport_sha256": digest(self.current), "reasoning_effort": "low"}
        self.original = {**self.receipt, "transport_sha256": digest(self.old)}

    def settings(self, transport):
        return {
            "transport_identity": copy.deepcopy(transport),
            "transport_identities": {"opencode": copy.deepcopy(transport)},
            "roles": copy.deepcopy(self.roles),
        }

    def test_complete_relocation_preserves_effort_and_original_inputs(self):
        before = copy.deepcopy((self.state, self.receipt))
        self.assertEqual([self.receipt, self.original], identity.equivalents(self.state, self.receipt))
        self.assertEqual(before, (self.state, self.receipt))

    def test_chain_requires_continuous_locations_but_skips_metadata_only_notes(self):
        middle = {**self.old, "config_path": "/middle/kilocode.toml"}
        self.state["configuration_changes"] = [
            {"previous": self.settings(self.old), "selected": self.settings(middle)},
            {"reason": "Recorded provider identity metadata", "at": "2026-10-29"},
            {"previous": self.settings(middle), "selected": self.settings(self.current)},
        ]
        mid = {**self.receipt, "transport_sha256": digest(middle)}
        self.assertEqual([self.receipt, mid, self.original], identity.equivalents(self.state, self.receipt))
        self.state["configuration_changes"][0]["selected"] = self.settings(self.old)
        self.assertEqual([self.receipt, mid], identity.equivalents(self.state, self.receipt))

    def test_non_path_identity_changes_do_not_alias(self):
        for field, value in (
            ("engine", "other"),
            ("identity_version", 2),
            ("executable", "/other/kilo"),
            ("version", "8.0"),
            ("config_sha256", "b" * 64),
        ):
            with self.subTest(field=field):
                state = copy.deepcopy(self.state)
                changed = {**self.old, field: value}
                state["configuration_changes"][0]["previous"] = self.settings(changed)
                self.assertEqual([self.receipt], identity.equivalents(state, self.receipt))

    def test_missing_malformed_or_extra_identity_data_stays_path_bound(self):
        for field, value in (
            ("config_sha256", None),
            ("config_sha256", "unknown"),
            ("identity_version", True),
            ("version", 7),
            ("executable", "kilo"),
            ("config_path", "relative.toml"),
            ("new_field", "unrecognized binding"),
        ):
            for side in ("previous", "selected"):
                with self.subTest(field=field, side=side):
                    state = copy.deepcopy(self.state)
                    transport = dict(self.old if side == "previous" else self.current)
                    if value is None:
                        transport.pop(field)
                    else:
                        transport[field] = value
                    state["configuration_changes"][0][side] = self.settings(transport)
                    self.assertEqual([self.receipt], identity.equivalents(state, self.receipt))

    def test_unknown_version_at_either_location_cannot_certify_relocation(self):
        for version in (None, "", " "):
            for side in ("previous", "selected"):
                with self.subTest(version=version, side=side):
                    state = copy.deepcopy(self.state)
                    transport = {**(self.old if side == "previous" else self.current), "version": version}
                    state["configuration_changes"][0][side] = self.settings(transport)
                    if side == "selected":
                        state["settings"] = self.settings(transport)
                    bound = {**self.receipt, "transport_sha256": digest(state["settings"]["transport_identity"])}
                    self.assertEqual([bound], identity.equivalents(state, bound))

    def test_missing_history_and_conflicting_primary_binding_do_not_invent_a_preimage(self):
        variants = [[], None, [None], [{"selected": self.settings(self.current)}]]
        for history in variants:
            with self.subTest(history=history):
                state = {**self.state, "configuration_changes": history}
                self.assertEqual([self.receipt], identity.equivalents(state, self.receipt))
        state = copy.deepcopy(self.state)
        state["settings"]["transport_identity"] = self.old
        self.assertEqual([self.receipt], identity.equivalents(state, self.receipt))
        self.assertEqual([], identity.equivalents(self.state, None))

    def test_other_tool_or_changed_role_does_not_borrow_receipt(self):
        for field, value in (("provider", "other"), ("transport_sha256", "b" * 64)):
            bound = {**self.receipt, field: value}
            self.assertEqual([bound], identity.equivalents(self.state, bound))
        for field, value in (("model", "plan/other"), ("reasoning_effort", "high")):
            with self.subTest(field=field):
                state = copy.deepcopy(self.state)
                state["configuration_changes"][0]["previous"]["roles"]["glm"][field] = value
                self.assertEqual([self.receipt], identity.equivalents(state, self.receipt))

    def test_tool_changed_and_changed_back_does_not_resurrect_older_locations(self):
        other = {**self.old, "config_sha256": "b" * 64}
        self.state["configuration_changes"] = [
            {"previous": self.settings(self.old), "selected": self.settings(other)},
            {"previous": self.settings(other), "selected": self.settings(self.current)},
        ]
        self.assertEqual([self.receipt], identity.equivalents(self.state, self.receipt))
