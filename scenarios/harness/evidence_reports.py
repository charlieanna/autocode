"""Black-box completion postcondition for the public canonical report pair.

Only public status metadata and report files are read; no runtime imports or
child state inspection. Runtime status authenticates canonical rendering.
"""
import hashlib
import json
from pathlib import Path

from .oracle import Check


def declared(flags):
    value = 'unknown'
    for index, flag in enumerate(flags):
        if flag == '--evidence-provenance' and index + 1 < len(flags):
            value = flags[index + 1]
        elif flag.startswith('--evidence-provenance='):
            value = flag.split('=', 1)[1]
    return value


def complete(view):
    return (view or {}).get('status') in ('TASK_COMPLETE', 'COMPLETE')


def nodes(run):
    expected = run.get('provider_evidence')
    if 'program' in run:
        summary = run['program']
        if complete(summary):
            yield 'program', Path(summary['state_file']).parent, summary.get('evidence_report'), 'program', expected
        plan = run.get('plan') or {}
        view = plan.get('view') or {}
        if complete(view):
            yield 'program plan', Path(plan['run_dir']), view.get('evidence_report'), 'task', expected
        for identity, children in (run.get('children') or {}).items():
            for child in children:
                if complete(child):
                    yield identity, Path(child['run_dir']), child.get('evidence_report'), 'task', expected
    elif 'components' in run:
        summary = run['components']
        children = summary.get('components') or {}
        if summary.get('exit_code') == 0 and children:
            root = Path(next(iter(children.values()))['workspace']).parent
            yield 'components', root, summary.get('evidence_report'), 'components', expected
        for identity, child in children.items():
            view = child.get('view') or {}
            if complete(view):
                yield identity, Path(child['run_dir']), view.get('evidence_report'), 'task', expected
        view = run.get('architecture_view') or {}
        if complete(view):
            yield 'architecture', Path(run['architecture_run_dir']), view.get('evidence_report'), 'task', expected
    elif complete(run.get('view')):
        yield 'task', Path(run['run_dir']), run['view'].get('evidence_report'), 'task', expected


def check_pair(root, report, kind, expected):
    root = root.resolve()
    if not isinstance(report, dict) or report.get('availability') not in ('current', 'recorded'):
        raise ValueError('No authenticated completed-run report is exposed')
    content = {}
    for name, stem in (('json', 'json'), ('markdown', 'md')):
        path = root / ('evidence.' + stem)
        if path.is_symlink() or not path.is_file() or report.get(name + '_path') != str(path):
            raise ValueError('Canonical pair is missing or redirected')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != report.get(name + '_sha256'):
            raise ValueError('Canonical pair digest changed')
        content[name] = raw
    value = json.loads(content['json'])
    if value.get('version') != 1 or value.get('kind') != kind or value.get('binding') != report.get('binding'):
        raise ValueError('Report version, kind or public binding differs')
    if expected and value.get('provenance', {}).get('kind') != expected:
        raise ValueError('Provider evidence disclosure differs from the harness route')
    if report.get('document') is not None and report['document'] != value:
        raise ValueError('Public document differs from persisted JSON')
    if report.get('markdown') is not None and report['markdown'].encode() != content['markdown']:
        raise ValueError('Public Markdown differs from persisted Markdown')


def checks(run):
    if not run or not run.get('report_expected'):
        return []  # check-only or a synthetic oracle unit input, not an executed CLI
    results = []
    try:
        for identity, root, report, kind, expected in nodes(run):
            try:
                check_pair(root, report, kind, expected)
                results.append(Check('canonical evidence: ' + identity, True, 'Authenticated public report pair'))
            except (OSError, ValueError, TypeError, KeyError) as error:
                results.append(Check('canonical evidence: ' + identity, False, str(error)))
    except (OSError, ValueError, TypeError, KeyError) as error:
        results.append(Check('canonical evidence metadata', False, str(error)))
    return results
