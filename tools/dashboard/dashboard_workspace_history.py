"""Fresh, reversible presentation eligibility for missing temporary workspaces.

The registry and run rows remain intact. A workspace returning on disk becomes
visible again on the next snapshot; this classifier never removes files.
"""
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

TEMPORARY = re.compile(r'^/(?:private/)?(?:tmp/|var/folders/)')


def worker_commands():
    try:
        result = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def annotate_workspace_history(rows, *, process_reader=worker_commands):
    """Annotate every row; never infer missing files from cached error strings."""
    checked = datetime.now(UTC).isoformat()
    processes, inspected = None, False
    for row in rows:
        evidence = {'eligible': False, 'reason': 'not_temporary', 'checked_at': checked}
        row['workspace_cleanup'] = evidence
        raw = row.get('workspace')
        if not isinstance(raw, str) or not TEMPORARY.match(raw):
            continue
        path = Path(raw)
        try:
            valid_path = '..' not in path.parts and str(path.resolve()) == raw
        except (OSError, RuntimeError):
            valid_path = False
        if not valid_path:
            evidence['reason'] = 'uncertain_path'
            continue
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            evidence['reason'] = 'existence_unavailable'
            continue
        else:
            evidence['reason'] = 'workspace_exists'
            continue
        live = (row.get('monitor') or {}).get('live') or {}
        if live.get('state') in ('alive', 'unknown', 'uncertain') or row.get('active_stage'):
            evidence['reason'] = 'active_or_uncertain_work'
            continue
        if not inspected:
            processes, inspected = process_reader(), True
        if processes is None:
            evidence['reason'] = 'worker_inspection_unavailable'
            continue
        # Match conservatively, including provider prompt/event paths below
        # the selected workspace. A possible live reference keeps it visible.
        identities = [raw, row.get('run')]
        if any(identity and identity in command for command in processes for identity in identities):
            evidence['reason'] = 'live_process_reference'
            continue
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            evidence['reason'] = 'existence_unavailable'
            continue
        else:
            evidence['reason'] = 'workspace_exists'
            continue
        evidence.update(eligible=True, reason='confirmed_missing', worker_check='no_live_references')
    return rows
