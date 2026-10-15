"""Small child locators for inventories retained by the original CODE owner.

These pure functions never read a manifest or grant authority. A caller must
retain the full owned descriptor and verify its manifest, files and native
boundary normally. The child needs only the exact manifest/hash reference;
duplicating thousands of SDK file identities in its environment exceeds exec's
argument limit. Full parent receipts and legacy exact envelopes stay intact.
"""

from copy import deepcopy
import json
import re


def descriptor_value(descriptor):
    if (
        not isinstance(descriptor, dict)
        or not isinstance(descriptor.get("manifest"), str)
        or not descriptor["manifest"].startswith("/")
        or "\x00" in descriptor["manifest"]
        or not isinstance(descriptor.get("manifest_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", descriptor["manifest_sha256"]) is None
    ):
        raise ValueError("Native locator lacks its original manifest identity")
    return {key: descriptor[key] for key in ("manifest", "manifest_sha256")}


def _value(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Native locator repeats a field")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique) if isinstance(raw, (str, bytes)) else raw


def descriptor_matches(raw, expected):
    locator = descriptor_value(expected)
    value = _value(raw)
    return value == expected or value == locator


def boundary_value(boundary):
    value = deepcopy(boundary)
    if not isinstance(value, dict):
        raise ValueError("Native locator lacks its original boundary")
    if "native_role_isolation" in value:
        value["native_role_isolation"] = descriptor_value(value["native_role_isolation"])
    return value


def boundary_matches(raw, expected):
    locator = boundary_value(expected)
    value = _value(raw)
    return value == expected or value == locator


def expand_boundary(raw, descriptor):
    """Restore only the inventory supplied by the original owner, never a file."""
    value = deepcopy(_value(raw))
    if not isinstance(value, dict) or not descriptor_matches(value.get("native_role_isolation"), descriptor):
        raise ValueError("Native boundary locator differs from its original owner")
    value["native_role_isolation"] = deepcopy(descriptor)
    return value
