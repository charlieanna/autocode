"""Bind offline host approvals to a reviewed complete public plan."""

import copy
import hashlib
import json
import re


def canonical_plan(plan: dict, interpreter: str) -> bytes:
    """Canonicalize only the reviewed plan's run-specific token and interpreter."""
    value = copy.deepcopy(plan)
    value["hash"] = "<contract-hash>"
    value["token"] = "<approval-token>"
    escaped = re.sub(r"([\\`*{}\[\]()#+.!_>~|<>-])", r"\\\1", interpreter)

    def scrub(item):
        if isinstance(item, str):
            return item.replace(escaped, "<fixture-python>").replace(interpreter, "<fixture-python>")
        if isinstance(item, list):
            return [scrub(entry) for entry in item]
        if isinstance(item, dict):
            return {key: scrub(entry) for key, entry in item.items()}
        return item

    return json.dumps(scrub(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def require_reviewed_plan(plan: dict, need: dict, interpreter: str, expected_digest: str) -> None:
    revision, plan_hash, token = plan.get("revision"), plan.get("hash"), plan.get("token")
    if (
        need.get("kind") != "approve_plan"
        or type(revision) is not int
        or not isinstance(plan_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", plan_hash) is None
        or token != f"r{revision}:{plan_hash}"
        or need.get("token") != token
    ):
        raise AssertionError("The exact displayed plan approval token is not current")
    digest = hashlib.sha256(canonical_plan(plan, interpreter)).hexdigest()
    if digest != expected_digest:
        raise AssertionError(f"The full displayed plan differs from the reviewed fixture (sha256 {digest})")
