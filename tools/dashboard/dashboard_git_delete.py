"""Delete one confirmed Git branch with Git's checkout guard and a tip guard."""
import os
import shlex
import sys
import tempfile
from pathlib import Path

_GUARD = r'''
import os
import subprocess
import sys

stage = sys.argv[1]
data = sys.stdin.buffer.read()
# Preserve the repository's hook and its refusal. The temporary hook is only a
# per-command option; the repository configuration and hook files are untouched.
if os.path.isfile(original_hook) and os.access(original_hook, os.X_OK):
    result = subprocess.run([original_hook, stage], input=data)
    if result.returncode:
        raise SystemExit(result.returncode)
if stage == 'prepared':
    # branch -D may emit zero as the unspecified old OID. Read the real
    # ref while its prepared transaction holds the lock, before commit.
    # A packed-ref delete can fire more than one prepared transaction (and
    # extra rows for the packed-refs update). The tip guard is on the target
    # ref's own row; a later prepared may already see that ref gone (#668).
    zero = '0' * len(head)
    rows = [line.split() for line in data.decode().splitlines()]
    target = [row for row in rows if len(row) >= 3 and row[2] == reference]
    actual = subprocess.run(['git', 'rev-parse', '--verify', reference],
                            capture_output=True, text=True)
    current = actual.stdout.strip() if actual.returncode == 0 else None
    if (len(target) != 1 or target[0] not in ([head, zero, reference], [zero, zero, reference])
            or (current is not None and current != head)):
        sys.stderr.write('Branch changed since confirmation; branch retained\n')
        raise SystemExit(1)
    # Git branch also checks worktree ownership. Check again with the reference
    # transaction prepared, after any existing hook, before allowing deletion.
    result = subprocess.run(['git', 'worktree', 'list', '--porcelain'],
                            capture_output=True, text=True)
    if result.returncode or ('branch ' + reference) in result.stdout.splitlines():
        sys.stderr.write('Branch ownership changed or is uncertain; branch retained\n')
        raise SystemExit(1)
'''


def delete_branch(git, root, branch, head):
    """Keep branch -D's worktree guard and bind deletion to the confirmed OID.

    Git's normal branch command does not accept an expected tip argument. Its
    prepared reference-transaction hook runs while the ref is locked, so it can
    refuse a tip change without disabling Git's own checked-out-branch check.
    External Git clients do not share the dashboard's lock; as with Git branch,
    this does not serialize arbitrary concurrent worktree checkout operations.
    """
    original = git(root, 'rev-parse', '--path-format=absolute', '--git-path',
                   'hooks/reference-transaction')
    with tempfile.TemporaryDirectory(prefix='autocode-branch-delete-') as directory:
        hooks = Path(directory)
        script = hooks / 'guard.py'
        script.write_text('reference = ' + repr('refs/heads/' + branch) + '\n'
                          + 'head = ' + repr(head) + '\n'
                          + 'original_hook = ' + repr(original) + '\n' + _GUARD)
        hook = hooks / 'reference-transaction'
        hook.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' '
                        + shlex.quote(str(script)) + ' "$@"\n')
        os.chmod(hook, 0o700)
        return git(root, '-c', 'core.hooksPath=' + str(hooks), 'branch', '-D', '--', branch)
