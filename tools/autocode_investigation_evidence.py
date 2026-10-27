"""Same-turn investigation evidence for discovery, never verification or approval.

The runtime brackets the runner's reproduction with the existing complete execution
identity. Only accepted, pinned reports can publish a planning reuse receipt. The
consumer rechecks it; absent/changed inputs keep the diagnosis as unverified leads.
No model-provided filename or statement grants reuse or changes the human contract.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_requirement_cues as requirement_cues
    from . import autocode_source_snapshot as source
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
    from . import autocode_verify as verify
except ImportError:
    import autocode_launch_inputs as launch_inputs
    import autocode_requirement_cues as requirement_cues
    import autocode_source_snapshot as source
    import autocode_test_cases as test_cases
    import autocode_util as util
    import autocode_verify as verify


STAGE = "investigate_bug"
# These source fields are checked separately, allowing only the exact note the
# runner creates after reproduction. All runtime/dependency/environment pins stay.
_SOURCE_FIELDS = {"source_revision", "source_metadata"}


def _requirements(state: RunState):
    return util.digest(
        {
            "sources": requirement_cues.source_texts(state),
            "requirements": state.get("requirements_handoff"),
            "turns": state.get("turns"),
            "workflow": (state.get("workflow") or {}).get("kind"),
        }
    )


def _identity_command(workspace, command):
    # A probe is often Python -c with a semicolon INSIDE its quoted program.
    # Collect that interpreter's dependency identity without treating Python
    # punctuation as shell syntax. Never execute the supplied program here.
    words = shlex.split(command)
    rest = words[1:]
    if rest[:1] == ["-B"]:
        rest = rest[1:]
    if (
        not words
        or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(words[0]).name)
        or len(rest) != 2
        or rest[0] != "-c"
        or any(c in command for c in "`$")
    ):
        raise ValueError("reproduction command has no bounded Python runtime identity")
    executable = words[0]
    if "/" in executable and not Path(executable).is_absolute():
        executable = str(Path(workspace) / executable)
    return shlex.join([executable, "-c", "pass"])


def _runtime(state: RunState, workspace, run_dir, command, *, source_revision=None):
    if sys.platform not in ("linux", "darwin"):
        raise ValueError("complete reproduction runtime identity is unavailable on this platform")
    _, identify = launch_inputs.runners(state, workspace, run_dir, verify.scratch_run, verify.execution_identity)
    result = identify(workspace, command=_identity_command(workspace, command))
    if source_revision is not None and result.get("source_revision") != source_revision:
        raise ValueError("source changed during runtime identity collection")
    if result.get("cache_binding_complete") is not True or result.get("reuse_supported") is not True:
        raise ValueError("reproduction runtime/dependencies are not completely bound")
    if result.get("unbound_source_symlinks"):
        raise ValueError("source symlinks are not bound")
    return util.digest({key: value for key, value in result.items() if key not in _SOURCE_FIELDS})


def _snapshot(workspace):
    return source.snapshot(workspace)


def _cited_sources(report, files):
    paths = source.literal_paths([*report["affected_paths"], *report["test_paths"], report["counterexample"]["file"]])
    return {
        name: identity
        for name, identity in files.items()
        if any(name == path or name.startswith(path + "/") for path in paths)
    }


def begin(state: RunState, report, record, workspace, run_dir):
    """Capture before the runner probe; an unavailable binding never rejects a diagnosis."""
    try:
        if report.get("outcome") != "reproduced" or not report.get("probe", "").strip():
            raise ValueError("no runner-executable reproduction")
        if (record.get("original_stage") or record.get("stage")) != STAGE:
            raise ValueError("missing original Investigator stage")
        output, before = Path(record["output"]), Path(record["before_ref"])
        run_dir = Path(run_dir).resolve()
        if not output.resolve().is_relative_to(run_dir) or not before.resolve().is_relative_to(run_dir):
            raise ValueError("attempt artifacts belong to a different run")
        if output.is_symlink() or before.is_symlink():
            raise ValueError("attempt artifacts are symlinks")
        original = util.read_object(before)
        current = _snapshot(workspace)
        if original != current or current["revision"] != record.get("source_revision"):
            raise ValueError("original investigation source is not current")
        raw = util.read_object(output)
        if raw != report:
            raise ValueError("accepted report does not match original output")
        cited = _cited_sources(report, current["files"])
        if not cited or len(cited) > 128:
            raise ValueError("cited source inventory is absent or too broad")
        return {
            "eligibility": "pending",
            "origin": {
                "run_dir": str(run_dir),
                "stage": STAGE,
                "iteration": record["iteration"],
                "output": str(output),
                "output_hash": util.file_hash(output),
                "source_snapshot": str(before),
                "source_snapshot_hash": util.file_hash(before),
                "source_revision": current["revision"],
            },
            "requirements_hash": _requirements(state),
            "runtime_hash": _runtime(state, workspace, run_dir, report["probe"], source_revision=current["revision"]),
            "cited_sources": cited,
        }
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        return {"eligibility": "unavailable", "reason": str(error)}


def finish(state: RunState, report, record, workspace, captured):
    """Seal only after bug_job accepted both the reproduction and its counterexample."""
    found = state.get("investigation") or {}
    evidence = dict(captured)
    if evidence.get("eligibility") == "pending":
        try:
            result = found.get("probe_result") or {}
            counter = result.get("counterexample") or {}
            if type(result.get("exit_code")) is not int or result["exit_code"] != 0:
                raise ValueError("missing successful runner reproduction")
            if type(counter.get("exit_code")) is not int or counter["exit_code"] == 0:
                raise ValueError("missing discriminating counterexample")
            origin = evidence["origin"]
            original = util.read_object(Path(origin["source_snapshot"]))
            current = _snapshot(workspace)
            note = report["note_path"]
            files = dict(current["files"])
            # The note is the sole authorized post-probe write. Preserve an older
            # note's original identity when this investigation replaced it.
            files.pop(note, None)
            if note in original["files"]:
                files[note] = original["files"][note]
            if current["head"] != original["head"] or files != original["files"]:
                raise ValueError("source changed while the reproduction ran")
            if (
                _runtime(state, workspace, origin["run_dir"], report["probe"], source_revision=current["revision"])
                != evidence["runtime_hash"]
            ):
                raise ValueError("runtime changed while the reproduction ran")
            evidence.update(
                eligibility="bound",
                source_revision=current["revision"],
                reproduction_hash=util.digest(result),
                note_hash=util.file_hash(Path(workspace) / note),
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            evidence = {"eligibility": "unavailable", "reason": str(error)}
    found["discovery_evidence"] = evidence


def context(state: RunState, run_dir):
    """A checked planning-only projection. Missing pins always require fresh discovery."""
    found = test_cases.current_diagnosis(state)
    fallback = {"eligibility": "fresh_discovery_required", "reason": "no current bound investigation"}
    evidence = found.get("discovery_evidence") or {}
    try:
        if evidence.get("eligibility") != "bound":
            raise ValueError(evidence.get("reason") or "legacy or unbound investigation")
        origin = evidence["origin"]
        if Path(run_dir).resolve() != Path(origin["run_dir"]):
            raise ValueError("investigation belongs to a different run")
        if found.get("output") != origin["output"] or found.get("output_hash") != origin["output_hash"]:
            raise ValueError("diagnosis revision changed")
        records = [*(state.get("stages") or []), state.get("active_stage") or {}]
        if origin.get("stage") != STAGE or not any(
            (row.get("original_stage") or row.get("stage")) == STAGE
            and row.get("output") == origin["output"]
            and row.get("iteration") == origin["iteration"]
            and row.get("source_revision") == origin["source_revision"]
            and not row.get("rejected")
            for row in records
        ):
            raise ValueError("original task/run/stage identity is missing or changed")
        for key, hash_key in (("output", "output_hash"), ("source_snapshot", "source_snapshot_hash")):
            path = Path(origin[key])
            if path.is_symlink() or util.file_hash(path) != origin[hash_key]:
                raise ValueError("original investigation artifact changed")
        report = util.read_object(Path(origin["output"]))
        if any(found.get(key) != value for key, value in report.items()):
            raise ValueError("diagnosis content changed")
        if _requirements(state) != evidence["requirements_hash"]:
            raise ValueError("requirements or human inputs changed")
        workspace = state["workspace"]
        if _snapshot(workspace)["revision"] != evidence["source_revision"]:
            raise ValueError("source, tests or configuration changed")
        if util.file_hash(Path(workspace) / report["note_path"]) != evidence["note_hash"]:
            raise ValueError("diagnosis note changed")
        if util.digest(found.get("probe_result")) != evidence["reproduction_hash"]:
            raise ValueError("reproduction receipt changed")
        if (
            _runtime(state, workspace, run_dir, report["probe"], source_revision=evidence["source_revision"])
            != evidence["runtime_hash"]
        ):
            raise ValueError("reproduction runtime/dependencies/environment changed")
        return {
            "eligibility": "reusable_planning_context",
            "origin": origin,
            "source_revision": evidence["source_revision"],
            "cited_sources": evidence["cited_sources"],
            "requirements_hash": evidence["requirements_hash"],
            "runtime_hash": evidence["runtime_hash"],
            "reproduction_receipt": found["probe_result"],
            "coverage": "Prior local reproduction and cited source diagnosis only; not final verification or approval",
        }
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        return {**fallback, "reason": str(error)}
