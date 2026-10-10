"""Attest existing supported external inputs, without granting new controls."""

from __future__ import annotations

import copy
import json
from pathlib import Path

try:
    from . import autocode_dependency as dependency
    from . import autocode_recovery_novelty as novelty
    from . import autocode_util as util
except ImportError:
    import autocode_dependency as dependency
    import autocode_recovery_novelty as novelty
    import autocode_util as util


TARGET = "input:verified_dependency_delivery"


def capture(state, run_dir):
    """Revalidate only an already-authorized delivery copied into this run."""
    wait = state.get("dependency_wait") or {}
    if wait.get("status") != "delivered":
        return {}, {}
    if wait.get("authorization") != "user_cli_dependency_binding" or not any(
        row.get("kind") == "dependency_binding"
        and row.get("actor") == "user_cli"
        and all(
            row.get(key) == wait.get(key)
            for key in ("request_id", "consumer_contract", "consumer_source", "destination", "files", "producer_run")
        )
        for row in state.get("user_events", [])
    ):
        raise ValueError("Changed dependency input has no matching user binding")
    run = Path(run_dir).resolve()
    root = dependency.contained(run, wait["destination"])
    manifest = root / "manifest.json"
    if str(manifest) != wait.get("manifest") or util.file_hash(manifest) != wait.get("manifest_sha256"):
        raise ValueError("Changed dependency input lost its delivered manifest pin")
    candidate = copy.deepcopy(state)
    candidate["status"] = "WAITING_FOR_DEPENDENCY"
    candidate["dependency_wait"]["status"] = "waiting"
    dependency.ready(candidate, manifest)
    receipt = util.read(manifest)
    paths = {str(manifest): util.file_hash(manifest)}
    paths.update(
        {str(dependency.contained(root / "source", path)): digest for path, digest in receipt["files"].items()}
    )
    paths.update({str(dependency.contained(root, path)): digest for path, digest in receipt["pins"].items()})
    paths.update(
        {str(dependency.contained(root, proof["path"])): proof["sha256"] for proof in receipt["proofs"].values()}
    )
    value = {key: receipt[key] for key in ("files", "producer_contract", "source_revision", "consumer_contract")}
    return {TARGET: json.dumps(value, sort_keys=True, separators=(",", ":"))}, paths


def change_identity(change, current, previous, *, operations, wrappers=()):
    """An already-observed causal input transition, not a proposed env mutation."""
    if not isinstance(change, dict) or change.get("target") != TARGET or TARGET not in current:
        return None
    for key in ("hypothesis", "before", "after", "expected_check", "expected_result"):
        if not isinstance(change.get(key), str) or not change[key].strip():
            return None
    before = previous.get(TARGET, "null")
    after = current[TARGET]
    command = novelty.normalize(change["expected_check"], wrappers)
    if before == after or change["before"] != before or change["after"] != after or command not in operations:
        return None
    return util.digest({"target": TARGET, "before": before, "after": after, "check": command})
