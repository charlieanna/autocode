"""Explicit Codex artifact access for registered command providers.

This opt-in adapter owns only argv construction and run-owned artifact paths.
It does not change global configuration, authentication, source-write policy,
report verification, or the provider's selected models.
"""
import json
import os
import re
import stat
from pathlib import Path

ADAPTER = 'codex_artifacts'
TOKEN = '{sandbox_args}'


def validate(config):
    adapter = config.get('sandbox_adapter')
    slots = config.get('command', []).count(TOKEN)
    if adapter is None:
        if slots:
            raise ValueError('{sandbox_args} requires sandbox_adapter = "codex_artifacts"')
        return
    if adapter != ADAPTER:
        raise ValueError('sandbox_adapter must be "codex_artifacts"')
    if slots != 1 or config.get('output', 'report_file') != 'report_file':
        raise ValueError('codex_artifacts requires report_file output and one standalone {sandbox_args}')
    if not config.get('version_command'):
        raise ValueError('codex_artifacts requires version_command for the underlying Codex CLI')
    command = config['command']
    if not any(command[i] in ('-o', '--output-last-message') and command[i + 1] == '{report}'
               for i in range(len(command) - 1)):
        raise ValueError('codex_artifacts requires Codex final-message persistence: -o {report}')
    conflicts = {'--sandbox', '-s', '--add-dir', '--profile', '-p', '--full-auto', '--approve-for-me',
                 '--dangerously-bypass-approvals-and-sandbox'}
    for part in command:
        override = part.removeprefix('--config=').removeprefix('-c').strip()
        key = override.split('=', 1)[0].strip().split('.', 1)[0].strip('"\'')
        if (part == '{sandbox}' or part.split('=', 1)[0] in conflicts
                or any(part.startswith(flag) and part != flag for flag in ('-s', '-p'))
                or key in {'sandbox_mode', 'sandbox_workspace_write', 'default_permissions', 'permissions', 'projects'}):
            raise ValueError('codex_artifacts cannot combine its policy with sandbox, profile, workspace or project-trust overrides')


def check_version(version):
    match = re.fullmatch(r'codex-cli (\d+)\.(\d+)\.(\d+)(?:[-+][\w.-]+)?', version or '')
    if not match or tuple(map(int, match.groups())) < (0, 160, 0):
        raise RuntimeError('codex_artifacts requires Codex CLI 0.160.0 or newer; '
                           'no provider request was launched')


def _descendant(root, path, *, directory=False):
    """Reject aliases before authorizing a write; never canonicalize an escape."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError('Operational path must stay under its runner-owned root') from None
    if not relative.parts or '..' in relative.parts:
        raise ValueError('Operational path must be a strict descendant without traversal')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Operational write path contains a symlink: ' + str(current))
    if path.exists():
        info = path.stat()
        if directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('Operational directory is not a directory: ' + str(path))
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Assigned report must be a regular file without hard links')
    return path


def _artifact_tree(root):
    # A pre-existing hard link inside a writable directory can bypass a
    # path-based sandbox's source boundary. Reject aliases; never remove them.
    if not root.exists():
        return
    pending = [root]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Operational directory contains an alias or special file: ' + entry.path)


def arguments(sandbox, workspace, run_dir, report):
    # The provider author selects this adapter explicitly. Ignoring user config
    # prevents legacy sandbox_mode from silently overriding the named profile;
    # saved login state and administrator-enforced requirements still apply.
    isolated = ['--ignore-user-config']
    if sandbox == 'workspace-write':
        return [*isolated, '--sandbox', sandbox]
    if sandbox != 'read-only':
        raise ValueError('codex_artifacts supports read-only or workspace-write source policy')
    workspace, run_dir = Path(workspace).resolve(), Path(run_dir).absolute()
    _descendant(Path(run_dir.anchor), run_dir, directory=True)
    if report is None:
        raise ValueError('codex_artifacts requires the assigned report path')
    report = Path(report).absolute()
    # Only stage reports qualify. Never authorize state.json or the run root.
    try:
        relative = report.relative_to(run_dir)
    except ValueError:
        raise ValueError('Assigned report must be inside the run directory') from None
    if len(relative.parts) < 3 or relative.parts[0] != 'iterations' or report.suffix != '.json':
        raise ValueError('Assigned report must be a stage JSON file under run/iterations')
    if run_dir.is_relative_to(workspace):
        _descendant(workspace, report)
    report = _descendant(run_dir, report)
    directories = [_descendant(workspace, workspace / '.autocode' / name, directory=True)
                   for name in ('evidence', 'output')]
    for path in directories:
        _artifact_tree(path)
    for path in directories:
        path.mkdir(parents=True, exist_ok=True)
    rules = ','.join(json.dumps(str(path)) + '="write"' for path in [*directories, report])
    profile = '{extends=":read-only",filesystem={' + rules + '}}'
    return [*isolated, '-c', 'default_permissions="autocode_artifacts"',
            '-c', 'permissions.autocode_artifacts=' + profile]
