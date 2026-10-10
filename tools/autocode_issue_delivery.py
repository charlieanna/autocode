"""Authenticate a delivery commit made from previously checked source bytes.

The issue adapter supplies public TaskRun facts and owns the receipt. Nothing
here opens a run's state, changes completion or calls a provider.
"""
import hashlib
import html
import os
import subprocess
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_source_snapshot as source
    from . import autocode_util as util
except ImportError:
    import autocode_source_snapshot as source
    import autocode_util as util


def git(root, *args, binary=False):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=not binary)
    if result.returncode:
        raise ValueError('Cannot authenticate issue delivery Git state')
    return result.stdout if binary else result.stdout.strip()


def source_name(name):
    return (name and name not in ('.autocode', '.autocode-ui') and
            not name.startswith(('.autocode/', '.autocode-ui/')) and
            '/__pycache__/' not in '/' + name and not name.endswith('.pyc'))


def require_safe_public_names(workspace, base_commit):
    """Refuse source names that would change in a public Git/HTML wrapper."""
    changed = git(workspace, "diff", "--no-renames", "--name-only", "-z", base_commit, binary=True)
    untracked = git(workspace, "ls-files", "--others", "--exclude-standard", "-z", binary=True)
    names = sorted({os.fsdecode(name) for name in (changed + untracked).split(b"\0")
                    if name and source_name(os.fsdecode(name))})
    for name in names:
        if util.redact(name) != name or util.redact(html.escape(name)) != html.escape(name):
            raise ValueError("A source filename is unsafe for the public delivery wrapper")
    if not names:
        return
    literal = [":(literal)" + name for name in names]
    for quote in ("true", "false"):
        for arguments in (("diff", "--no-renames", "--name-only", base_commit, "--", *literal),
                          ("ls-files", "--others", "--exclude-standard", "--", *literal)):
            formatted = git(workspace, "-c", "core.quotePath=" + quote, *arguments)
            # Git quotes control characters, so each physical line is one name.
            for name in formatted.split("\n"):
                if util.redact(name) != name or util.redact(html.escape(name)) != html.escape(name):
                    raise ValueError("A Git-quoted source filename is unsafe for the public delivery wrapper")


def files(snapshot):
    return {name: identity for name, identity in snapshot['files'].items() if identity != 'deleted'}


def report_binding(view, report):
    approved = view.get('approved_contract') or {}
    plan = (report.get('document') or {}).get('plan') or {}
    if not approved.get('token') or approved['token'] != plan.get('contract_token'):
        raise ValueError('The report is not bound to the public approved plan')
    return {'binding': report.get('binding'), 'json_sha256': report.get('json_sha256'),
            'markdown_sha256': report.get('markdown_sha256'), 'plan_token': approved['token'],
            'criteria_revision': plan.get('criteria_revision')}


def capture(workspace, view, report):
    if report.get('availability') != 'current':
        raise ValueError('First publication requires current canonical evidence')
    approved = view.get('approved_contract') or {}
    body = approved.get('body') or {}
    selected = [path for row in [*body.get('milestones', []), body.get('initial_task') or {}]
                for path in row.get('affected_paths', [])]
    current = source.snapshot(workspace, paths=source.literal_paths(selected))
    if current['revision'] != report['document']['revision']['source_revision']:
        raise ValueError('Source changed after report authentication; revalidate before publication')
    return {'version': 1, 'report': report_binding(view, report), 'tested_source': deepcopy(current)}


def unchanged(workspace, receipt):
    tested = receipt['tested_source']
    current = source.snapshot(workspace, paths=tested.get('source_paths', []))
    if files(current) != files(tested):
        raise ValueError('Source bytes, deletions or modes changed after authentication')
    return current


def clean(workspace):
    for args in (('-c', 'core.fileMode=true', 'ls-files', '--modified', '--deleted', '--others', '--exclude-standard', '-z'),
                 ('diff', '--cached', '--name-only', '-z')):
        names = git(workspace, *args, binary=True).decode().split('\0')
        if any(source_name(name) for name in names):
            raise ValueError('Delivery source must be clean, with no staged, modified or untracked source')


def tree_matches(workspace, expected):
    """The commit must contain the checked bytes, including modes and removals."""
    root = Path(workspace)
    tree = {}
    for entry in git(workspace, 'ls-tree', '-r', '-z', 'HEAD', binary=True).split(b'\0'):
        if not entry:
            continue
        info, raw_name = entry.split(b'\t', 1)
        mode, kind, oid = info.decode().split()
        name = os.fsdecode(raw_name)
        if source_name(name):
            tree[name] = (mode, kind, oid)
    if set(tree) != set(expected):
        raise ValueError('Delivery tree does not contain exactly the checked source files and deletions')
    algorithm = git(workspace, 'rev-parse', '--show-object-format')
    for name, (mode, kind, oid) in tree.items():
        path = root/name
        if kind == 'commit':
            if git(path, 'rev-parse', 'HEAD') != oid:
                raise ValueError('Delivery submodule commit differs')
            clean(path)
            continue
        if path.is_symlink():
            content = os.fsencode(os.readlink(path))
            actual_mode = '120000'
        else:
            content = path.read_bytes()
            actual_mode = '100755' if path.stat().st_mode & 0o111 else '100644'
        digest = hashlib.new(algorithm, ('blob ' + str(len(content)) + '\0').encode() + content).hexdigest()
        if mode != actual_mode or oid != digest:
            raise ValueError('Delivery tree bytes or modes differ from checked source')


def seal(workspace, receipt):
    current = unchanged(workspace, receipt)
    clean(workspace)
    tree_matches(workspace, files(receipt['tested_source']))
    return {**receipt, 'delivery_commit': current['head'], 'delivery_tree': git(workspace, 'rev-parse', 'HEAD^{tree}')}


def verify(workspace, view, report, receipt):
    if (not isinstance(receipt, dict) or type(receipt.get('version')) is not int or receipt.get('version') != 1 or
            not receipt.get('delivery_commit') or not receipt.get('delivery_tree') or
            not isinstance(receipt.get('tested_source'), dict) or
            report.get('availability') not in ('current', 'recorded') or
            receipt.get('report') != report_binding(view, report)):
        raise ValueError('Missing, stale or incomplete authenticated issue delivery receipt')
    tested = receipt['tested_source']
    if (tested.get('revision') != report['document']['revision']['source_revision'] or
            tested.get('revision') != util.digest({'head': tested.get('head'), 'files': tested.get('files')})):
        raise ValueError('The delivery manifest is not the source snapshot bound to canonical evidence')
    current = unchanged(workspace, receipt)
    if (current['head'] != receipt['delivery_commit'] or
            git(workspace, 'rev-parse', 'HEAD^{tree}') != receipt['delivery_tree']):
        raise ValueError('Git HEAD/tree differs from the recorded delivery commit')
    clean(workspace)
    tree_matches(workspace, files(receipt['tested_source']))
