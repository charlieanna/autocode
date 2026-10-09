"""Read-only evidence inspection for the public status command.

The interface supplies checkpoint reading and source snapshots. The inspection
does not run a check, change a saved report or grant completion authority.
"""
import stat
from pathlib import Path
from subprocess import SubprocessError

try:
    from .autocode_util import digest, file_hash
    from .autocode_verification_view import project
except ImportError:
    from autocode_util import digest, file_hash
    from autocode_verification_view import project


BOUND_FIELDS = ('workspace', 'run_dir', 'status', 'current_task', 'goal_contract',
                'acceptance_criteria', 'criteria_revision', 'validation',
                'active_stage', 'active_runner_check', 'human_reviews', 'answers', 'user_events')


def identity(state):
    return digest({key: state.get(key) for key in BOUND_FIELDS})


def pins_match(pins, workspace):
    if not isinstance(pins, dict) or not pins:
        return False
    root = Path(workspace).resolve()
    for name, expected in pins.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            return False
        path = Path(name)
        # Runner pins are absolute project-local paths. A moved or legacy pin
        # is unverified; inspection must not follow it into another project.
        if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(root):
            return False
        if not stat.S_ISREG(path.stat().st_mode) or file_hash(path) != expected:
            return False
    return True


def inspect(state, workspace, *, snapshot, read_state, accepted_human_ids=(), initial_snapshot=None):
    if not state.get('validation'):
        return project(state)
    try:
        before = initial_snapshot if initial_snapshot is not None else snapshot(workspace)
        pins = state['validation'].get('evidence_hashes')
        intact = pins_match(pins, workspace)
        after = snapshot(workspace)
        latest = read_state()
        if before['revision'] != after['revision'] or identity(latest) != identity(state):
            return project(state, inspection_error='The source or saved task changed during inspection. Refresh to inspect it again.')
        # Proof files can be excluded from the source snapshot. Recheck their
        # pins too so a change during the source walk cannot yield green rows.
        intact = intact and pins_match(pins, workspace)
        return project(state, current_revision=after['revision'], evidence_matches=intact,
                       accepted_human_ids=accepted_human_ids)
    except (OSError, ValueError, TypeError, KeyError, SubprocessError):
        return project(state, inspection_error='Current source or saved evidence could not be inspected. Recorded results remain available.')
