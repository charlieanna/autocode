"""Runner-owned per-test evidence from direct Vitest invocations.

A package script is supported only when it resolves to a single `vitest run`
command. JSON is read from a fresh sidecar, never from npm's echoed command or
ordinary stdout. This does not certify the semantics of arbitrary Node wrappers.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path


def _words(command):
    if not isinstance(command, str) or re.search(r"[;&|<>`$()\n\r]", command):
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _vitest(words):
    if not words:
        return False
    if Path(words[0]).name in ('vitest', 'vitest.cmd'):
        options = words[1:]
    elif Path(words[0]).name in ('node', 'node.exe') and len(words) > 1 \
            and Path(words[1]).name == 'vitest.mjs':
        options = words[2:]
    elif Path(words[0]).name in ('npx', 'npx.cmd') and words[1:3] == ['--no-install', 'vitest']:
        options = words[3:]
    else:
        return False
    return bool(options) and (options[0] == 'run' or '--run' in options) and '--' not in options and not any(
        word.lower().replace('-', '').startswith(('reporter', 'outputfile', 'watch', 'ui', 'mergereports'))
        or word == '-w' or word.startswith('--run=') for word in options if word.startswith('-'))


def command_words(command, tree=None):
    """Recognize a one-shot command whose JSON reporter the runner can own."""
    words = _words(command)
    if _vitest(words):
        return words
    if not words or Path(words[0]).name not in ('npm', 'npm.cmd') or tree is None:
        return None
    at = words.index('--') if '--' in words else len(words)
    args, extra = words[1:at], words[at + 1:]
    prefix, selected = '.', []
    while args:
        arg, *args = args
        if arg in ('--silent', '-s'):
            continue
        if arg == '--prefix':
            if not args:
                return None
            prefix, *args = args
        elif arg.startswith('--prefix='):
            prefix = arg.partition('=')[2]
        else:
            selected.append(arg)
    if selected not in (['test'], ['t'], ['run', 'test'], ['run-script', 'test']):
        return None
    try:
        root = Path(tree).resolve()
        package_root = (root / prefix).resolve()
        if not package_root.is_relative_to(root):
            return None
        package = json.loads((package_root / 'package.json').read_text())
        script = _words(package.get('scripts', {}).get('test'))
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        return None
    if script is None or extra is None or not _vitest([*script, *extra]):
        return None
    return words


def instrument(command, result_path, tree=None):
    words = command_words(command, tree)
    if not words:
        return command
    if Path(words[0]).name in ('npm', 'npm.cmd') and '--' not in words:
        words = [*words, '--']
    reporter = Path(__file__).with_name('autocode_vitest_reporter.cjs')
    return shlex.join([*words, '--reporter=default', '--reporter=' + str(reporter.resolve()),
                       '--exclude=**/.autocode/**', '--outputFile=' + str(Path(result_path).resolve())])


def results(path, tree):
    """Read only an owned, complete protocol; absent/ambiguous cases never pass."""
    try:
        value = json.loads(Path(path).read_text())
        if not isinstance(value, dict) or value.get('protocol') != 'autocode-vitest-tests' \
                or type(value.get('version')) is not int or value['version'] != 1 \
                or value.get('complete') is not True \
                or not re.fullmatch(r'4\.\d+\.\d+(?:[-+].*)?', str(value.get('vitest_version', ''))) \
                or value.get('reason') not in ('passed', 'failed'):
            return None
        files, count, errors = value['files'], value['total'], value['unhandled_errors']
        if not isinstance(files, list) or type(count) is not int or type(errors) is not int or errors < 0:
            return None
        root = Path(tree).resolve()
        groups = {key: set() for key in ('passed', 'failed', 'skipped')}
        identities, file_names, collection = set(), set(), set()
        uncollected = set()
        for file in files:
            if not isinstance(file, dict) or not isinstance(file.get('file'), str) or not file['file']:
                return None
            name = Path(file['file'])
            source = name.resolve() if name.is_absolute() else (root / name).resolve()
            if not source.is_relative_to(root):
                return None
            relative = source.relative_to(root).as_posix()
            if relative in file_names or type(file.get('collection_error')) is not bool:
                return None
            file_names.add(relative)
            if file['collection_error']:
                collection.add(relative + '::[collection]')
                uncollected.add(relative + '::[collection]')
            tests = file['tests']
            if not isinstance(tests, list):
                return None
            for test in tests:
                if not isinstance(test, dict) or not isinstance(test.get('name'), str) or not test['name'] \
                        or type(test.get('collection_error')) is not bool:
                    return None
                identity = relative + '::' + test['name']
                state = test['state']
                if identity in identities or state not in groups:
                    return None
                identities.add(identity)
                if test['collection_error'] or file['collection_error']:
                    collection.add(identity)
                else:
                    groups[state].add(identity)
        if count != len(identities) or (value['reason'] == 'passed' and (groups['failed'] or collection or errors)):
            return None
        if errors or (value['reason'] == 'failed' and not groups['failed'] and not collection):
            collection.add('[vitest runtime error]')
        return {'passed': sorted(groups['passed']), 'failed': sorted(groups['failed'] | collection),
                'skipped': sorted(groups['skipped']), 'collection_errors': sorted(collection),
                'uncollected': sorted(uncollected),
                'total': len(identities | collection), 'complete': True}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None
