"""An operator-supplied instrumentation patch for the original code in a bug-fix proof (#299).

Some bugs can only be observed through something the fix itself adds: a hook, a package variable,
an injectable function. A regression test that uses it cannot even build on the unfixed code, so
the proof can never show it failing there (autocode_proof_seam). The operator may then supply
``--base-patch PATH``: a patch that adds only that seam to the original code, so the same test
runs and fails there because of the bug.

The runner cannot tell whether a patch only adds instrumentation, so every use is bounded:

- it is pinned by hash when set, and a later change to the file makes the proof UNVERIFIED;
- it may not change test files, so the proof still runs the candidate's tests unchanged;
- it must apply to the base; its additions and deletions must appear in candidate edits at the
  corresponding original source locations, rather than borrowing text elsewhere in the file;
- every proof that uses it carries a review reason naming it, so the Validator and the Completion
  Owner check that it changes no behavior.

The pin lives in ``settings.regression.base_patch``; only ``pin``/``configure_resume`` write it
and autocode_regression reads it. Setting it on a saved run is a ``base_patch_set`` user event.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

try:
    from . import autocode_patch_containment as containment
    from . import autocode_util as util
    from . import autocode_verify as verify
except ImportError:
    import autocode_patch_containment as containment
    import autocode_util as util
    import autocode_verify as verify


def pin(path, workspace, base):
    """The saved pin for ``path``, or ValueError when it cannot serve as a base patch."""
    patch = Path(path).expanduser().resolve()
    if not patch.is_file():
        raise ValueError(f"--base-patch {path}: no such file")
    contents = patch.read_bytes()
    try:
        changes = containment.applied_files(workspace, base or "HEAD", contents)
    except ValueError as exc:
        raise ValueError(f"--base-patch {path} does not apply to the original code: {exc}") from exc
    files = sorted(change.name for change in changes)
    tests = [name for name in files if verify.is_test_path(name)]
    if tests:
        raise ValueError("--base-patch may not change test files, so the proof runs the candidate's tests "
                         "unchanged: " + ", ".join(tests))
    return {"path": str(patch), "sha256": hashlib.sha256(contents).hexdigest(), "files": files}


def pinned(state):
    return ((state.get("settings") or {}).get("regression") or {}).get("base_patch")


def check(saved, workspace, base):
    """(patch path, "") when the pinned patch may be applied to the original code, else (None, why not)."""
    patch = Path(saved["path"])
    if not patch.is_file():
        return None, f"the operator base patch {patch} is missing"
    contents = patch.read_bytes()
    if hashlib.sha256(contents).hexdigest() != saved["sha256"]:
        return None, f"the operator base patch {patch.name} changed after it was set"
    try:
        changes = containment.applied_files(workspace, base, contents)
        for change in changes:
            if verify.is_test_path(change.name):
                return None, f"the operator base patch {patch.name} changes a test file: {change.name}"
            problem = containment.candidate_problem(change, workspace)
            if problem:
                return None, (f"the change does not contain the operator base patch {patch.name}: "
                              f"{change.name}: {problem}")
    except (ValueError, OSError) as exc:
        return None, f"cannot verify the operator base patch {patch.name} against the base revision ({exc})"
    return patch, ""


def review_reason(saved):
    return (f"the original code ran with the operator's base patch {Path(saved['path']).name} (sha256 "
            f"{saved['sha256'][:12]}, changing {', '.join(saved['files'][:5])}): check that it only adds "
            "instrumentation the tests use and changes no behavior")


def configure_resume(state, settings, args):
    """Set the base patch on a saved run, at the same kind of stop where verification commands change."""
    path = getattr(args, "base_patch", None)
    if path is None:
        return settings
    if not getattr(args, "resume_paused", False) or not str(state.get("status", "")).startswith("PAUSED_"):
        raise ValueError("Setting --base-patch on a saved run requires a paused run and --resume-paused")
    if state.get("next_stage") not in ("sol", "astra_checkpoint", "astra_review"):
        raise ValueError("Setting --base-patch requires a stop before the Validator or a completion check, "
                         "so the new proof runs before any acceptance")
    if any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts",
                                      "active_runner_check", "runner_check")):
        raise ValueError("Reconcile the active or uncertain attempt before setting --base-patch")
    saved = pin(path, state["workspace"], state.get("base_commit"))
    previous = (settings.get("regression") or {}).get("base_patch")
    settings.setdefault("regression", {})["base_patch"] = saved
    state.setdefault("user_events", []).append({
        "kind": "base_patch_set", "actor": "user_cli", "at": util.now(), "previous": previous, "current": saved})
    return settings
