"""Preparation and verification of overlay-compatibility validation copies.

Issue #223 follow-up. During a live pilot a compatibility validation copy was
assembled from a detached HEAD alone: the candidate's new regression tests
never reached the copy, the old suite passed, and the result looked green
while the candidate tests had never run. This module is the maintained
preparation contract that binds a compatibility proof to the exact candidate
inputs and the complete overlay it claims:

* Candidate-first composition. The validation copy is a detached worktree of
  the base revision with the candidate's own changes (dirty and untracked,
  from ``autocode_verify.changed_files``) copied in FIRST; the overlay is
  applied afterwards, unedited, at this module's own seam. ``make_tree`` keeps
  its patch-before-changes contract for reviewed follow-up base trees; this
  path never passes that argument.
* A pinned overlay. The expected sha256 of the complete patch file is an
  explicit input, verified before application. A file truncated or edited
  relative to the pin — including a truncation whose remaining hunks would
  apply cleanly — is a partial overlay: the run is rejected as incomplete
  proof, and no result of a mismatched run attests its file as the pinned
  overlay.
* Hash-bound receipts. Pre-overlay candidate hashes (with dirty/untracked
  status) are recorded separately from post-overlay copy hashes. A
  patch-untouched input must still equal its candidate hash; a patch-touched
  input must retain its content or mode effect. Git's reverse check also
  verifies the applied overlay before intentional copy-only adapters, so a
  green suite cannot certify a copy that erased it.
* Nodeid accounting. Every required original guard and added candidate test
  must run and be reported passed or failed; skipped, deselected or missing
  nodeids are incomplete proof.
* Recorded transformations. Intentional validation-copy edits (for example a
  local-operator adapter that qualifies a synthetic caller) happen after the
  overlay and are recorded with path, before/after sha256 and a rationale, in
  a section distinct from the candidate inputs and the overlay.

A failed assembly is an explicit non-PASS result naming the failing step; it
leaves no destination worktree behind and never changes the candidate
workspace's revision. Model-free: this module imports only ``autocode_verify``,
``autocode_util`` and the standard library, and adds no CLI surface.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import time
import uuid
from pathlib import Path

try:
    from . import autocode_util as util
    from . import autocode_verify as verify
except ImportError:
    import autocode_util as util
    import autocode_verify as verify

PASS, FAIL, INCOMPLETE = "PASS", "FAIL", "INCOMPLETE"


def _sha256(path) -> str | None:
    """The sha256 of a file's bytes, or None when there is no such file."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _mode(path) -> int | None:
    try:
        return stat.S_IMODE(Path(path).stat().st_mode)
    except OSError:
        return None


def candidate_hashes(workspace, changes) -> dict:
    """Pre-overlay sha256 and change status of every candidate input.

    A missing working-tree file hashes to None: the input disappeared after
    the change set was computed, which the copy step must reject rather than
    silently skip (``make_tree`` alone would copy nothing for it).
    """
    root = Path(workspace)
    return {
        path: {
            "sha256": None if status == "deleted" else _sha256(root / path),
            "mode": None if status == "deleted" else _mode(root / path),
            "status": status,
        }
        for path, status in sorted(changes.items())
    }


def copy_hashes(tree, changes) -> dict:
    """sha256 of every candidate input inside an assembled copy (None when absent)."""
    root = Path(tree)
    return {path: _sha256(root / path) for path in sorted(changes)}


def _patch_paths(contents, *, reverse=False):
    result = subprocess.run(
        ["git", "apply", "--numstat", "-z", *(["--reverse"] if reverse else [])], input=contents, capture_output=True
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return {row.split(b"\t", 2)[2].decode(errors="surrogateescape") for row in result.stdout.split(b"\0") if row}


def overlay_paths(patch_path, *, patch_bytes=None) -> list[str]:
    """Repository paths a patch touches, decoded by Git without path quoting."""
    contents = Path(patch_path).read_bytes() if patch_bytes is None else patch_bytes
    # Reverse statistics supply rename sources as well as their destinations.
    return sorted(_patch_paths(contents) | _patch_paths(contents, reverse=True))


def _overlay_modes(contents):
    modes = {}
    for part in re.split(rb"(?m)(?=^diff --git )", contents):
        mode = re.search(rb"(?m)^new (?:file )?mode ([0-7]{6})$", part)
        if mode:
            for path in _patch_paths(part):
                modes[path] = int(mode.group(1), 8)
    return modes


def _git_mode(path):
    try:
        mode = Path(path).lstat().st_mode
    except OSError:
        return None
    if stat.S_ISREG(mode):
        return 0o100755 if mode & stat.S_IXUSR else 0o100644
    if stat.S_ISLNK(mode):
        return 0o120000
    return None


def composition_problems(copy, changes, pre_overlay, patch_path, *, patch_bytes=None) -> list[str]:
    """Why an assembled copy is not valid proof, judged against the pinned overlay.

    A patch-touched input whose content and mode both equal the candidate
    shows none of the patch's effect. A patch-untouched input with a different
    hash or mode is not the candidate. The reverse check retains the complete
    applied patch before any intentional transformations.
    """
    post = copy_hashes(copy, changes)
    touched = set(overlay_paths(patch_path, patch_bytes=patch_bytes))
    problems = []
    for path, record in sorted(pre_overlay.items()):
        present = post.get(path)
        if record["status"] == "deleted":
            if present is not None:
                problems.append(f"copy failure: deleted candidate input {path} is present in the copy")
        elif path in touched:
            if present == record["sha256"] and _mode(Path(copy) / path) == record["mode"]:
                problems.append(
                    f"erased overlay: the pinned patch touches {path} but the copy's {path} "
                    "shows none of the patch's effect"
                )
        elif present != record["sha256"] or _mode(Path(copy) / path) != record["mode"]:
            problems.append(
                f"unbound copy: patch-untouched candidate input {path} does not equal the candidate working-tree hash"
            )
    contents = Path(patch_path).read_bytes() if patch_bytes is None else patch_bytes
    for path, expected in _overlay_modes(contents).items():
        if _git_mode(Path(copy) / path) != expected:
            problems.append(f"erased overlay: {path} does not retain pinned Git mode {expected:o}")
    reverse = subprocess.run(
        ["git", "-C", str(copy), "apply", "--reverse", "--check", "-"], input=contents, capture_output=True
    )
    if reverse.returncode:
        problems.append(
            "erased overlay: the complete pinned patch is not retained in the copy: "
            + reverse.stderr.decode(errors="replace").strip()[-300:]
        )
    return problems


def _bind_candidate_copy(tree, pre_overlay) -> list[str]:
    """The moment before the overlay: the copy must BE the candidate inputs."""
    failures = []
    for path, record in sorted(pre_overlay.items()):
        present = _sha256(Path(tree) / path)
        if record["status"] == "deleted":
            if present is not None:
                failures.append(f"copy failure: deleted candidate input {path} is still present in the copy")
        elif record["sha256"] is None:
            failures.append(
                f"copy failure: candidate input {path} ({record['status']}) disappeared from the "
                "candidate workspace before the copy step; the copy cannot bind it"
            )
        elif present is None:
            failures.append(f"copy failure: candidate input {path} ({record['status']}) is missing from the copy")
        elif present != record["sha256"]:
            failures.append(
                f"copy failure: candidate input {path} ({record['status']}) in the copy does not "
                "equal the candidate working-tree file"
            )
        elif _mode(Path(tree) / path) != record["mode"]:
            failures.append(f"copy failure: candidate input {path} has a different mode in the copy")
    return failures


def _apply_transformations(tree, transformations, records) -> list[str]:
    """Apply intentional post-overlay edits, recording identity and rationale."""
    problems = []
    for spec in transformations or ():
        path = str(spec.get("path"))
        relative = Path(path)
        target = Path(tree) / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not target.resolve().is_relative_to(Path(tree).resolve())
        ):
            problems.append(f"invalid transformation path: {path} must stay within the validation copy")
            continue
        rationale = str(spec.get("rationale") or "").strip()
        if not rationale:
            problems.append(f"unrecorded transformation: {path} has no rationale")
            continue
        before = _sha256(target)
        if before is None:
            problems.append(f"copy failure: transformation target {path} does not exist in the copy")
            continue
        try:
            rewritten = spec["transform"](target.read_text())
        except (KeyError, TypeError, ValueError, OSError) as error:
            problems.append(f"transformation failed for {path}: {error}")
            continue
        target.write_text(rewritten)
        records.append({"path": path, "before_sha256": before, "after_sha256": _sha256(target), "rationale": rationale})
    return problems


def _account(result) -> list[str]:
    """Why an executed run does not account for every expected nodeid by name."""
    suite = result["suite"] or {}
    results = suite.get("results")
    if results is None:
        return ["incomplete proof: the selected suite reported no per-test results"]
    accounted = set(results["passed"]) | set(results["failed"])
    skipped = set(results["skipped"])
    missing = [nodeid for nodeid in result["expected_nodeids"] if nodeid not in accounted]
    skipped_expected = [nodeid for nodeid in result["expected_nodeids"] if nodeid in skipped]
    result["accounting"] = {
        "passed": list(results["passed"]),
        "failed": list(results["failed"]),
        "skipped": list(results["skipped"]),
        "unaccounted": missing,
    }
    reasons = [
        f"incomplete proof: expected nodeid {nodeid} did not run in the selected suite (skipped, deselected or missing)"
        for nodeid in missing
    ]
    reasons += [f"incomplete proof: expected nodeid {nodeid} was skipped" for nodeid in skipped_expected]
    return reasons


def _verdict(reasons, suite) -> str:
    if reasons:
        return INCOMPLETE
    failed = ((suite or {}).get("results") or {}).get("failed") or []
    return FAIL if failed or (suite or {}).get("exit_code") != 0 else PASS


def prepare(
    workspace,
    base,
    destination,
    patch_path,
    *,
    overlay_sha256,
    expected_nodeids=(),
    suite_files=(),
    suite_command=None,
    transformations=(),
    python=None,
    timeout=verify.DEFAULT_TIMEOUT,
    dependencies_from=None,
    changes=None,
    log_dir=None,
) -> dict:
    """Assemble the validation copy candidate-first, run the selected suite in it.

    ``overlay_sha256`` is the pin: the expected sha256 of the complete patch
    file, checked at this module's own seam before application. The assembled
    copy is kept (returned as ``copy``) when the selected suite ran in it;
    rejected construction removes its validation worktree. Overlapping paths
    are refused before any copy or cleanup can change the candidate workspace.
    """
    workspace, destination, patch_path = Path(workspace), Path(destination), Path(patch_path)
    try:
        patch_bytes = patch_path.read_bytes()
    except OSError:
        patch_bytes = None
    patch_hash = hashlib.sha256(patch_bytes).hexdigest() if patch_bytes is not None else None
    revision_before = util.snapshot(workspace)["revision"]
    result = {
        "verdict": INCOMPLETE,
        "reasons": [],
        "workspace": str(workspace),
        "base": base,
        "changes": {},
        "copy": None,
        "overlay": {
            "path": str(patch_path),
            "expected_sha256": overlay_sha256,
            "patch_sha256": patch_hash,
            "touched": [],
        },
        "candidate_inputs": {"pre_overlay": {}, "post_overlay": {}, "post_overlay_modes": {}},
        "transformations": [],
        "expected_nodeids": list(expected_nodeids),
        "accounting": {},
        "suite": None,
        "workspace_revision_before": revision_before,
        "workspace_revision_after": None,
    }

    def close(tree, *, keep):
        if tree is not None and not keep:
            verify.remove_tree(workspace, tree)
        result["copy"] = str(tree) if (tree is not None and keep) else None
        result["workspace_revision_after"] = util.snapshot(workspace)["revision"]
        if result["workspace_revision_after"] != revision_before:
            result["reasons"].append("The candidate workspace changed while the copy was being prepared")
        if patch_bytes is not None and _sha256(patch_path) != patch_hash:
            result["reasons"].append("partial overlay: the pinned patch changed during preparation")
        result["verdict"] = _verdict(result["reasons"], result["suite"])
        return result

    reasons = result["reasons"]
    source_root, copy_root = workspace.resolve(), destination.resolve()
    scratch_root = source_root / ".autocode" / "scratch"
    fresh_scratch = (
        copy_root.is_relative_to(scratch_root)
        and copy_root != scratch_root
        and not destination.exists()
        and not destination.is_symlink()
    )
    if source_root.is_relative_to(copy_root) or (copy_root.is_relative_to(source_root) and not fresh_scratch):
        reasons.append("invalid destination: the validation copy must not overlap the candidate workspace")
    complete_changes = verify.changed_files(workspace, base)
    changes = dict(changes) if changes is not None else complete_changes
    result["changes"] = dict(changes)
    if changes != complete_changes:
        missing = sorted(
            path for path in set(changes) | set(complete_changes) if changes.get(path) != complete_changes.get(path)
        )
        reasons.append("copy failure: candidate inputs differ from the complete working tree: " + ", ".join(missing))
    if not result["expected_nodeids"]:
        reasons.append("incomplete proof: required original and candidate nodeids must be declared")
    sources = [path for path in sorted(changes) if not verify.is_test_path(path)]
    tests = [path for path in sorted(changes) if verify.is_test_path(path)]
    if not changes:
        reasons.append(
            "insufficient proof: the candidate change set is empty — a detached HEAD copy alone "
            "omits the candidate production and test inputs"
        )
    if changes and not sources:
        reasons.append("insufficient proof: no candidate production input is bound")
    if changes and not tests:
        reasons.append("insufficient proof: no candidate test input is bound")
    if result["overlay"]["patch_sha256"] is None:
        reasons.append(f"partial overlay: the overlay patch file {patch_path} does not exist")
    elif result["overlay"]["patch_sha256"] != overlay_sha256:
        reasons.append(
            f"partial overlay: the patch file's sha256 {result['overlay']['patch_sha256']} does "
            f"not match the pinned complete overlay sha256 {overlay_sha256}"
        )
    if reasons:
        return close(None, keep=False)

    try:
        result["overlay"]["touched"] = overlay_paths(patch_path, patch_bytes=patch_bytes)
        result["overlay"]["modes"] = _overlay_modes(patch_bytes)
    except ValueError as error:
        reasons.append(f"overlay failure: git rejected the pinned patch: {error}")
        return close(None, keep=False)
    result["candidate_inputs"]["pre_overlay"] = candidate_hashes(workspace, changes)
    tree = verify.make_tree(workspace, base, destination, workspace, changes, dependencies_from=dependencies_from)
    try:
        reasons.extend(_bind_candidate_copy(tree, result["candidate_inputs"]["pre_overlay"]))
        if reasons:
            return close(tree, keep=False)
        if _sha256(patch_path) != patch_hash:
            reasons.append("partial overlay: the pinned patch changed before application")
            return close(tree, keep=False)
        applied = subprocess.run(["git", "-C", str(tree), "apply", "-"], input=patch_bytes, capture_output=True)
        if applied.returncode:
            reasons.append(
                "overlay failure: git apply rejected the pinned patch: "
                + (applied.stderr or applied.stdout).decode(errors="replace").strip()[-300:]
            )
            return close(tree, keep=False)
        result["overlay"]["applied_sha256"] = patch_hash
        result["candidate_inputs"]["post_overlay"] = copy_hashes(tree, changes)
        result["candidate_inputs"]["post_overlay_modes"] = {path: _mode(Path(tree) / path) for path in changes}
        reasons.extend(
            composition_problems(
                tree, changes, result["candidate_inputs"]["pre_overlay"], patch_path, patch_bytes=patch_bytes
            )
        )
        if reasons:
            return close(tree, keep=False)
        reasons.extend(_apply_transformations(tree, transformations, result["transformations"]))
        if reasons:
            return close(tree, keep=False)
        interpreter = python or verify.python_for(tree)
        framework = verify.detect_framework(tree, python=interpreter)
        command = suite_command or (framework.targeted(list(suite_files)) if framework else None)
        if not command:
            reasons.append(
                "no selected suite command: pass suite_command, or suite_files for a project "
                "whose framework can be detected"
            )
            return close(tree, keep=False)
        log_root = Path(log_dir) if log_dir else destination.parent / "compat"
        logs = log_root / f"run-{uuid.uuid4().hex}"
        result["suite"] = {
            **verify.run_suite(framework, command, tree, logs, "selected-suite", timeout=timeout),
            "cwd": str(tree),
        }
        reasons.extend(_account(result))
        return close(tree, keep=True)
    except BaseException:
        verify.remove_tree(workspace, tree)
        raise


def write_receipt(directory, receipt) -> Path:
    """Write a NEW receipt file beside any existing ones; recorded results are never overwritten."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    identity = hashlib.sha256((stamp + json.dumps(receipt, sort_keys=True, default=str)).encode()).hexdigest()
    path = directory / f"compat-{stamp}-{identity[:8]}-{uuid.uuid4().hex}.json"
    with path.open("x") as stream:
        stream.write(json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n")
    return path
