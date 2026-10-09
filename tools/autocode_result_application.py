"""Apply one prepared result while retaining ownership of its local checks.

The caller supplies result interpretation and holds the run lock. Activity is
persisted from the original state; speculative candidate changes are committed
only after the application and independent command cleanup both succeed.
"""
from copy import deepcopy

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_command_supervision as commands
    from . import autocode_runner_check as runner_check
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_command_supervision as commands
    import autocode_runner_check as runner_check


def raise_if_uncertain(error):
    """Keep an ownership hold outside ordinary report rejection and archival."""
    if isinstance(error, receipts.OwnershipUncertain):
        raise error


def prepare(state, run_dir, stage, apply_candidate, *, persist):
    """Prepare a candidate under the outermost caller's ownership checkpoint."""
    if run_dir is None or commands.CHECKPOINT.get() is not None:
        candidate = deepcopy(state)
        apply_candidate(candidate)
        return candidate
    runner_check.clear(state, run_dir, persist)
    candidate = deepcopy(state)
    with runner_check.track(state, run_dir, stage, "Checking the completed report", persist, deferred=True):
        apply_candidate(candidate)
    return candidate


def commit(state, run_dir, stage, apply_candidate, commit_candidate, *, persist):
    """Keep the caller's existing commit boundary after verified application."""
    candidate = prepare(state, run_dir, stage, apply_candidate, persist=persist)
    return commit_candidate(candidate)
