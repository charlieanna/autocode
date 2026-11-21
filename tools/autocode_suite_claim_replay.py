"""Independently replay a sealed collection claim without promoting its prose.

Only the runner calls this at a verification boundary. A matching execution is a
separately recorded observation, not approval or proof of the model's wording.
"""

from __future__ import annotations

import shlex
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_requirements_observations as observations
    from . import autocode_source_snapshot as source
    from . import autocode_test_environment as environment
    from . import autocode_test_results as test_results
    from . import autocode_util as util
    from . import autocode_verification_commands as commands
    from . import autocode_verification_schedule as schedule
    from . import autocode_verify as verify
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_requirements_observations as observations
    import autocode_source_snapshot as source
    import autocode_test_environment as environment
    import autocode_test_results as test_results
    import autocode_util as util
    import autocode_verification_commands as commands
    import autocode_verification_schedule as schedule
    import autocode_verify as verify


def replay(state: RunState, workspace, run_dir, claim, *, current_source, timeout=verify.DEFAULT_TIMEOUT):
    """Run the complete suite on pinned source; an unverified claim never authorizes completion."""
    bound = observations.structured_collection_claim(state, run_dir, current_source, claim)
    if not bound:
        return None
    if type(timeout) not in (int, float) or not 0 < timeout <= verify.DEFAULT_TIMEOUT:
        return None
    python = environment.recorded_python(state.get("settings"), workspace)
    executable = Path(python) if isinstance(python, str) else None
    if not executable or not executable.is_absolute() or not executable.is_file():
        return None
    workspace, run_dir = Path(workspace), Path(run_dir)
    try:
        executable_hash = util.file_hash(executable)
    except OSError:
        return None
    paths, revision = bound["source_paths"], bound["source_revision"]
    before = source.snapshot(workspace, paths=paths)
    if before["revision"] != revision:
        return None
    directory = run_dir / "claim-evidence" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    command = shlex.join([str(executable), "-m", "pytest", "-q"])
    receipt = verify.scratch_run(
        workspace,
        directory,
        command=command,
        timeout=timeout,
        python=str(executable),
        source_paths=paths,
        expected_source_revision=revision,
    )
    # No legacy, unsupervised or tampered result can grant even a recorded observation.
    if not all(key in receipt for key in command_receipt.OWNERSHIP_FIELDS) or not schedule.intact(
        receipt, root=directory
    ):
        return None
    if receipt.get("exit_code") != 0 or receipt.get("timed_out") or not schedule.complete_results(receipt):
        return None
    results = receipt["results"]
    if results["failed"] or results["skipped"] or set(results["passed"]) != set(bound["junit_node_ids"]):
        return None
    try:
        words = shlex.split(receipt["command"])
        outputs = [word.split("=", 1)[1] for word in words if word.startswith("--junitxml=")]
        if len(outputs) != 1:
            return None
        xml = Path(outputs[0])
        if command_receipt.owned_relative(xml, directory) is None or not xml.is_file():
            return None
        xml_hash = util.file_hash(xml)
        parsed = test_results.per_test_results(commands.command_framework(command), receipt, xml, tree=workspace)
        if parsed != results or util.file_hash(xml) != xml_hash:
            return None
        if (
            source.snapshot(workspace, paths=paths)["revision"] != revision
            or util.file_hash(executable) != executable_hash
        ):
            return None
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return {
        **bound,
        "reason": (
            "An independent replay reported these named tests passed; the test outcomes and claim wording "
            "have not been independently verified."
        ),
        "replay": {
            "command": command,
            "python": str(executable),
            "python_sha256": executable_hash,
            "receipt": receipt,
            "junit": str(xml),
            "junit_sha256": xml_hash,
        },
    }
