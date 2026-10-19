"""Capture and execute isolated review probes through the existing clean runner.

No provider or controller imports. The injected runner retains launch-input
admission and supervised preparation/execution. Each side has its own evidence.
"""

import difflib
import hashlib
import json
import uuid
from pathlib import Path

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_review_attribution as attribution
    from . import autocode_review_mutation as mutation
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_util as util
    from .autocode_protected_paths import path_in
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_review_attribution as attribution
    import autocode_review_mutation as mutation
    import autocode_source_snapshot as source_snapshot
    import autocode_util as util
    from autocode_protected_paths import path_in


def reference(path):
    return {"path": str(path), "sha256": util.file_hash(path)}


def verify_source(root, manifest):
    """Bind the actual unpatched copy to captured source before patching it."""
    saved = json.loads(Path(manifest).read_text())
    if util.file_hash(saved["patch"]["path"]) != saved["patch"]["sha256"]:
        raise ValueError("Captured review patch changed")
    for name, identity in saved["files"].items():
        captured = path_in(Path(manifest).parent / "inputs", name)
        if util.file_hash(captured) != identity.removeprefix("executable:"):
            raise ValueError("Captured review source or tests changed: " + name)
    current = source_snapshot.snapshot(root, paths=saved["source_paths"])
    if current["files"] != saved["files"]:
        raise ValueError("Review source changed after capture or while making its proof copy")


def _source_lines(data):
    """Use physical LF lines, keeping terminators and in-line control bytes."""
    parts = data.decode("utf-8").split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def source_locations(tree, manifest):
    """Bind cited lines to the actual applied source before running probes.

    Git may relocate a hunk, so patch headers are not location evidence. Unknown,
    deleted, binary or oversized source remains ineligible for an exemption.
    """
    saved = json.loads(Path(manifest).read_text())
    locations = {name: {"line_count": count, "changed": []} for name, count in saved.get("source_lines", {}).items()}
    for change in saved.get("patch_changes", []):
        name = change["path"]
        locations.pop(name, None)
        try:
            before = mutation._read(Path(manifest).parent / "inputs", name, missing=True)
            after = mutation._read(Path(tree).resolve(), name, missing=True, limit=3 * 1024 * 1024)
            if after is None or b"\0" in after or b"\0" in (before or b""):
                continue
            old, new = _source_lines(before or b""), _source_lines(after)
        except (OSError, ValueError):
            continue
        touched = []
        for tag, _, _, start, end in difflib.SequenceMatcher(a=old, b=new).get_opcodes():
            if tag != "equal":
                touched.append([start + 1, end] if start != end else [max(1, start), start + 1])
        locations[name] = {"line_count": len(new), "changed": touched}
    return locations


def collect(workspace, out, tests, patch, clean_run, *, timeout):
    workspace = Path(workspace).resolve()
    out = Path(out).resolve() / ("comparison-" + uuid.uuid4().hex)
    out.mkdir(parents=True)
    manifest, inputs, reason = None, None, "No explicit change_patch; baseline attribution is unavailable."
    captured_patch = patch
    overlay = None
    if patch:
        try:
            binding = mutation.capture_inputs(workspace, patch, out)
            captured_patch = str(binding["captured_patch"])
            snapshot = source_snapshot.snapshot(workspace, paths=tests)
            # Keep the actual pre-patch bytes as well as their source identity.
            size, source_lines = 0, {}
            for name, identity in snapshot["files"].items():
                source = path_in(workspace, name)
                with source.open("rb") as stream:
                    data = stream.read(64 * 1024 * 1024 - size + 1)
                size += len(data)
                if size > 64 * 1024 * 1024:
                    raise ValueError("review source capture exceeds 64 MiB")
                digest = hashlib.sha256(data).hexdigest()
                if digest != identity.removeprefix("executable:"):
                    raise ValueError("review source changed during capture")
                target = out / "inputs" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(source.stat().st_mode & 0o777)
                if b"\0" not in data:
                    try:
                        source_lines[name] = len(_source_lines(data))
                    except UnicodeError:
                        continue
            manifest = out / "source.json"
            util.atomic_json(
                manifest,
                {
                    **snapshot,
                    "patch": reference(binding["captured_patch"]),
                    "tests": tests,
                    "source_lines": source_lines,
                    "patch_changes": binding["changes"],
                },
            )
            inputs = reference(manifest)
            overlay = {name: str(out / "inputs" / name) for name in tests}
            verify_source(workspace, manifest)
            reason = ""
        except (OSError, ValueError) as error:
            # Unsupported capture cannot exempt anything; retain ordinary changed-side proof.
            manifest, inputs, overlay = None, None, None
            captured_patch = patch
            reason = "Baseline capture unavailable: " + str(error)

    def execute(side, reviewed_patch):
        directory = out / side
        directory.mkdir()
        if manifest:
            verify_source(workspace, manifest)
        try:
            receipt = clean_run(
                workspace,
                directory,
                patch=reviewed_patch,
                tests=tests,
                timeout=timeout,
                **({"files": overlay, "source_paths": tests, "review_source": str(manifest)} if manifest else {}),
            )
        except util.Paused:
            raise  # Includes uncertain ownership; preserve the existing hold boundary.
        except (OSError, ValueError, RuntimeError) as error:
            if side == "changed":
                raise
            receipt = {"error": str(error), "results": None}
        if manifest and util.file_hash(manifest) != inputs["sha256"]:
            raise receipts.OwnershipUncertain("Review proof source manifest changed during execution")
        if manifest:
            verify_source(workspace, manifest)
        try:
            setup_errors = attribution.preparation_errors(receipt, directory / "scratch-tests.junit.xml")
            if setup_errors and isinstance(receipt.get("results"), dict):
                receipt["results"] = {
                    **receipt["results"],
                    "setup_errors": {**receipt["results"].get("setup_errors", {}), **setup_errors},
                }
            details = attribution.failure_details(
                receipt, directory / "scratch" / "tree", directory / "scratch-tests.junit.xml"
            )
        except (OSError, ValueError, KeyError, TypeError, SyntaxError):
            details = {}
        receipt = {**receipt, "failure_details": details}
        path = directory / "proof.json"
        util.atomic_json(path, receipt)
        return {**receipt, "proof": reference(path)}

    changed = execute("changed", captured_patch)
    base = {}
    if manifest and attribution.complete(changed):
        base = execute("base", None)
    if manifest and util.file_hash(manifest) != inputs["sha256"]:
        raise receipts.OwnershipUncertain("Review proof source manifest changed during execution")
    return {
        "changed": changed,
        "base": base,
        "inputs": inputs,
        "reason": reason,
        "changed_locations": changed.get("review_locations"),
    }
