"""Fail-closed comparison of completed Vitest default-reporter logs.

A matching report is evidence, never permission to waive a failing check.
Unknown reporter layouts deliberately require review instead of guessing.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ANSI = re.compile(r'\x1b\[[0-9;]*m')
DIVIDER = re.compile(r'^⎯{5,}.*$', re.M)
HEADER = re.compile(r'^ FAIL  (.+)$', re.M)
CAUSE = re.compile(r'^(?:[\w$]*Error:|(?:[-+]\s+)?(?:Expected|Received)(?::|\s|$))', re.M)


def clean(text):
    return ANSI.sub('', text).replace('\r\n', '\n').replace('\r', '\n')


def diagnostic(text, root=None, *, dependency_prefixes=False):
    value = '\n'.join(line.rstrip(' \t') for line in clean(text).splitlines()
                      if not DIVIDER.fullmatch(line)).strip('\n')
    if root:
        # Only an explicit checkout prefix; preserve numbers, operands and locations.
        value = re.sub(r'(?<![\w/.-])' + re.escape(str(Path(root).resolve()).rstrip('/') + '/'),
                       '<workspace>/', value)
    if dependency_prefixes:
        # Opt-in for equivalent installed dependencies in differently nested checkouts.
        # Only Vitest stack frames; preserve the package/version path and line/column.
        value = re.sub(r'^(\s*❯[^\n]*?)(?:\.\./)+(?:[^\s/:]+/)*?node_modules/',
                       r'\1node_modules/', value, flags=re.M)
    return value


def parse(text, *, root=None, dependency_prefixes=False):
    raw = text
    text = clean(text)
    errors, failures = [], {}
    headers = list(HEADER.finditer(text))
    if len(re.findall(r'^\s*FAIL\s+.+$', text, re.M)) != len(headers):
        errors.append('Unsupported failure header layout')
    for index, header in enumerate(headers):
        title = header[1].strip()
        suite = re.fullmatch(r'(.+?) \[ .+ \]', title)
        parts = [suite[1], '[suite-load]'] if suite else title.split(' > ', 1)
        if len(parts) != 2 or not all(parts):
            errors.append('Malformed failure header: ' + title)
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        boundary = DIVIDER.search(text, header.end(), end)
        if boundary is None:
            errors.append('Missing failure block terminator: ' + title)
        material = diagnostic(text[header.end():boundary.start() if boundary else end], root, dependency_prefixes=dependency_prefixes)
        identity = ' > '.join(parts)
        if identity in failures:
            errors.append('Duplicate failure identity: ' + identity)
        if not CAUSE.search(material):
            errors.append('Missing diagnostic cause: ' + identity)
        failures[identity] = {'file': parts[0], 'kind': 'suite' if suite else 'test',
                              'diagnostic': material,
                              'signature': hashlib.sha256(material.encode()).hexdigest()}
    counters = {}
    for label in ('Test Files', 'Tests'):
        rows = list(re.finditer(r'^\s*' + label + r'\s+(.+?)\s*\((\d+)\)\s*$', text, re.M))
        if len(rows) != 1:
            errors.append('Expected one ' + label + ' summary')
            continue
        row = rows[0]
        values = {}
        for part in row[1].split('|'):
            item = re.fullmatch(r'\s*(\d+) (failed|passed|skipped|todo)\s*', part)
            if not item or item[2] in values:
                errors.append('Unsupported ' + label + ' counter: ' + part)
                continue
            values[item[2]] = int(item[1])
        counters[label] = {**values, 'total': int(row[2])}
        if sum(values.values()) != int(row[2]):
            errors.append(label + ' totals do not reconcile')
        if headers and row.start() < headers[-1].start():
            errors.append('Summary precedes final failure')
    for pattern, label in ((r'^\s*RUN\s+v\S+.*$', 'RUN'),
                           (r'^\s*Start at\s+.+$', 'Start at'),
                           (r'^\s*Duration\s+.+$', 'Duration')):
        rows = list(re.finditer(pattern, text, re.M))
        if len(rows) != 1:
            errors.append('Expected one ' + label + ' marker; incomplete or combined runs')
        elif label == 'Duration' and rows[0].end() < len(text.rstrip()):
            errors.append('Unexpected output after Duration; inspect raw log')
    for label, kind in (('Failed Suites', 'suite'), ('Failed Tests', 'test')):
        rows = re.findall(label + r'\s+(\d+)', text)
        count = sum(f['kind'] == kind for f in failures.values())
        if len(rows) > 1 or (int(rows[0]) if rows else 0) != count:
            errors.append(label + ' does not match parsed blocks')
    files, tests = counters.get('Test Files', {}), counters.get('Tests', {})
    if files.get('failed', 0) != len({f['file'] for f in failures.values()}):
        errors.append('Unique failure-bearing files do not match failed file total')
    if tests.get('failed', 0) != sum(f['kind'] == 'test' for f in failures.values()):
        errors.append('Failed test total does not match parsed blocks')
    if not files.get('total') or not (tests.get('passed', 0) + tests.get('failed', 0)):
        errors.append('No executed tests; comparison cannot establish coverage')
    if re.search(r'Unhandled (?:Error|Rejection)|Errors\s+\d+ errors?', text):
        errors.append('Unhandled runner errors need separate review')
    return {'raw_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'failures': failures,
            'counters': counters, 'errors': errors, 'valid': not errors}


def compare(baseline, candidate):
    left, right = baseline['failures'], candidate['failures']
    shared = left.keys() & right.keys()
    changed = sorted(k for k in shared if left[k]['signature'] != right[k]['signature'])
    errors = [side + ': ' + error for side, report in (('baseline', baseline), ('candidate', candidate))
              for error in report['errors']]
    # A smaller/disabled suite must not masquerade as recovery.
    for label in ('Test Files', 'Tests'):
        before, after = baseline['counters'].get(label, {}), candidate['counters'].get(label, {})
        if after.get('total', 0) < before.get('total', 0):
            errors.append(label + ': candidate inventory shrank')
        if sum(after.get(k, 0) for k in ('skipped', 'todo')) > sum(before.get(k, 0) for k in ('skipped', 'todo')):
            errors.append(label + ': candidate disabled additional entries')
    current_only = sorted(right.keys() - left.keys())
    return {'version': 1, 'format': 'vitest-text', 'status': 'invalid' if errors else
            'blocked' if changed or current_only else 'matched',
            'matched': sorted(shared - set(changed)), 'changed_signatures': changed,
            'current_only': current_only, 'baseline_only': sorted(left.keys() - right.keys()),
            'errors': errors, 'baseline': baseline, 'candidate': candidate,
            'policy': 'Evidence only. Same test selection and an authorized baseline exception must be verified separately; absent failures are not proof of recovery.'}


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--baseline-root', type=Path)
    parser.add_argument('--candidate-root', type=Path)
    parser.add_argument('--normalize-dependency-prefixes', action='store_true',
                        help='For equivalent dependencies only: remove relative directory prefixes before node_modules in stack frames')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() in (args.baseline.resolve(), args.candidate.resolve()):
        parser.error('Output must not overwrite input evidence')
    try:
        from .autocode_support import atomic_json
    except ImportError:
        from autocode_support import atomic_json
    report = compare(parse(args.baseline.read_bytes().decode('utf-8'), root=args.baseline_root, dependency_prefixes=args.normalize_dependency_prefixes),
                     parse(args.candidate.read_bytes().decode('utf-8'), root=args.candidate_root, dependency_prefixes=args.normalize_dependency_prefixes))
    report['normalization'] = {'transport': 'ANSI SGR, line endings, trailing whitespace and structural dividers',
                               'dependency_prefixes': args.normalize_dependency_prefixes}
    report['inputs'] = {'baseline': str(args.baseline.resolve()), 'candidate': str(args.candidate.resolve()),
                        'baseline_root': str(args.baseline_root.resolve()) if args.baseline_root else None,
                        'candidate_root': str(args.candidate_root.resolve()) if args.candidate_root else None}
    atomic_json(args.output, report)
    print(json.dumps({k: report[k] for k in ('status', 'current_only', 'changed_signatures', 'errors')}))
    return 2 if report['status'] == 'invalid' else 1 if report['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(cli())
