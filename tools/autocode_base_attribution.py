"""A planned check that fails identically on the base is the base's failure, not the change's (#3160).

The runner's clean replay (autocode_check_replay) runs every check the approved plan names on the
current source, and a failure there used to count against the change whatever its cause. Three
self-fix runs planned repository-wide checks (a linter over the whole tree, the repository's own test
selector) and ended as their change's failure on a lint error the base revision already had, in a file
the change never touched (#3160). Before such a failure counts, the runner runs the same command once
in a clean copy of the base revision (autocode_base_command.run) and ``judge`` compares the two.
The failure is attributed to the base only when every one of these holds; anything else keeps it the
change's, as before:

- the command is one the approved plan names, never one a Validator reported (the replay decides that);
- it is not a test runner the runner recognizes (autocode_verification_commands.command_framework).
  Tests are judged by name: the regression proof needs the change's own tests to fail on the base and
  pass on the candidate, and preservation compares every named outcome with the base;
- both runs completed, without a runner error or a time limit, and exited with the same nonzero code;
- their whole output is the same once each copy's own directory is replaced by one placeholder.
  A change that moves the failure at all (another message, another line, another file) is the change's.

The decision and its reason are recorded on the check's replay row (``base_attribution``), which is
shown with the replay's evidence and in the evidence report. It is AutoCode's own decision; a person is
never asked (AGENTS.md "What a person is asked"). The caller supplies the base runner, so this module
imports nothing from the runner.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_verification_commands as verification_commands
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_verification_commands as verification_commands

BASE, CHANGE = "base", "change"
PLACEHOLDER = b"<clean-copy>"


def eligible(command) -> bool:
    """Whether a planned command's failure may be compared with the base: any plain command except a
    test runner the runner recognizes, whose tests are judged by name instead."""
    return (
        isinstance(command, str) and bool(command.strip()) and verification_commands.command_framework(command) is None
    )


def candidate_root(row):
    """The clean copy the replay ran ``row`` in: autocode_verify.scratch_run writes its log beside the
    ``scratch/tree`` it runs in. A guess that is wrong only leaves the outputs different."""
    output = row.get("output")
    return str(Path(output).parent / "scratch" / "tree") if isinstance(output, str) and output else None


def _output(path, root):
    """The run's whole output with its copy's directory replaced, or None when it cannot be read."""
    try:
        data = Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        return None
    if root:
        for spelling in dict.fromkeys((str(Path(root).resolve()), str(root))):
            data = data.replace(spelling.encode(), PLACEHOLDER)
    return data


def _failed(row):
    return bool(row.get("error") or row.get("timed_out") or row.get("exit_code") != 0)


def judge(candidate, base):
    """Attribute ``candidate`` (the failed replay row) to the base revision or to the change.

    ``base`` is the same command's receipt from a clean copy of the base revision, naming its copy as
    ``tree``. Returns the record saved as the row's ``base_attribution``: ``kind`` is BASE or CHANGE,
    with the reason and the base run's exit code, output and digest."""
    record = {
        "kind": CHANGE,
        "base_exit_code": base.get("exit_code"),
        "base_timed_out": bool(base.get("timed_out")),
        "base_error": base.get("error") or "",
        "base_output": base.get("output"),
        "base_output_sha256": base.get("output_sha256"),
        **{"base_" + name: value for name, value in command_receipt.project(base).items()},
    }
    if candidate.get("error") or candidate.get("timed_out") or not command_receipt.completed(candidate):
        return {**record, "reason": "The check did not complete on the change, so the base cannot explain it."}
    if base.get("error") or base.get("timed_out") or not command_receipt.completed(base):
        return {**record, "reason": "The check could not be completed on the base revision."}
    if not _failed(base):
        return {**record, "reason": "The check passes on the base revision, so the change broke it."}
    if base.get("exit_code") != candidate.get("exit_code"):
        return {
            **record,
            "reason": f"The check exits {base.get('exit_code')} on the base revision, not "
            f"{candidate.get('exit_code')}, so the change altered how it fails.",
        }
    before = _output(base.get("output"), base.get("tree"))
    after = _output(candidate.get("output"), candidate_root(candidate))
    if before is None or after is None:
        return {**record, "reason": "The output of one of the two runs could not be read, so they cannot be compared."}
    if before != after:
        return {
            **record,
            "reason": "The check fails on the base revision too, but its output differs, so the change "
            "altered how it fails.",
        }
    return {
        **record,
        "kind": BASE,
        "normalized_output_sha256": hashlib.sha256(after).hexdigest(),
        "reason": f"The check fails identically on the base revision (exit {base.get('exit_code')}, the same "
        "output): the failure was already there before this change, so it is attributed to the base and "
        "not counted as the change's.",
    }


def unavailable(reason):
    """The record of a comparison that could not run: the failure stays the change's."""
    return {"kind": CHANGE, "reason": reason}
