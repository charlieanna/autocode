"""Acquire a source-bound implementation image with the task's browser fixture."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
import uuid
from pathlib import Path

try:
    from . import autocode_process as processes
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_util as util
    from . import autocode_visual_evidence as evidence
except ImportError:
    import autocode_process as processes
    import autocode_source_snapshot as source_snapshot
    import autocode_util as util
    import autocode_visual_evidence as evidence


def config_inputs(root, path, config):
    for key in ('inputs', 'assets', 'ready', 'build_command'):
        if not isinstance(config[key], list):
            raise ValueError(f'Capture {key} must be an array')
    case = config['case']
    if not all(isinstance(case[key], str) and case[key].strip() for key in ('id', 'state', 'route')):
        raise ValueError('Capture needs an exact case ID, route and rendered state')
    if not case['route'].startswith('/') or '?' in case['route'] or '#' in case['route']:
        raise ValueError('Capture route must be a URL pathname')
    viewport = case['viewport']
    if set(viewport) != {'width', 'height', 'device_scale_factor'} or any(
            type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in viewport.values()):
        raise ValueError('Capture viewport must have positive finite dimensions and device scale')
    if type(viewport['width']) is not int or type(viewport['height']) is not int:
        raise ValueError('Browser CSS width and height must be integers')
    reference = config['reference_hash']
    if not isinstance(reference, str) or len(reference) != 64 or any(c not in '0123456789abcdef' for c in reference):
        raise ValueError('Copy reference_hash from the stage capture context')
    if not config['ready'] or any(not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k]
            for k in ('selector', 'attribute', 'equals')) for row in config['ready']):
        raise ValueError('Declare deterministic selector/attribute/equals readiness conditions')
    assets = config['assets']
    if not assets or any(not isinstance(a, dict) or not isinstance(a.get('url'), str) for a in assets):
        raise ValueError('Declare URL-to-file bindings for every browser response')
    if len({a['url'] for a in assets}) != len(assets):
        raise ValueError('Declare unique URL-to-file bindings for every browser response')
    for asset in assets:
        if not isinstance(asset['url'], str) or not asset['url'].startswith(('/', 'http://', 'https://')):
            raise ValueError('Asset URLs must be origin-relative or explicit HTTP(S) URLs')
    command = config['build_command']
    if not isinstance(command, list) or any(not isinstance(arg, str) or not arg for arg in command):
        raise ValueError('build_command must be an argv array; use [] for directly served source')
    names = [str(path.relative_to(root)), config['fixture'], *config['inputs']]
    for name in [*names, *(item['path'] for item in assets)]:
        if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts or str(Path(name)) != name:
            raise ValueError('Capture file paths must be normalized paths relative to the workspace')
    # Build outputs may not exist until after the build.
    return {str(evidence.local_file(root, name).relative_to(root)): util.file_hash(evidence.local_file(root, name))
            for name in names}


def execute(argv, root, output, timeout):
    with output.open('xb') as log, processes.interruption_handler():
        child = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code, expired = processes.wait_for_stage(child, timeout, lambda _: None)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    if expired or code:
        raise ValueError(f'Capture command failed (exit {code}, timeout={expired}); retained log: {output}')
    return {'command': argv, 'exit_code': code, 'timed_out': expired}


def capture(root, config_path, timeout=120):
    root = Path(root).resolve()
    config_path = evidence.local_file(root, config_path)
    config = util.read_object(config_path)
    inputs = config_inputs(root, config_path, config)
    before = source_snapshot.snapshot(root, paths=config.get('source_paths', ()))
    deadline = time.monotonic() + timeout
    folder = root / '.autocode' / 'captures' / (util.now().replace(':', '-') + '-' + uuid.uuid4().hex)
    if any(path.is_symlink() for path in (root / '.autocode', folder.parent)):
        raise ValueError('Capture storage must not be a symlink')
    folder.mkdir(parents=True, exist_ok=False)
    try:
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise ValueError('Capture exceeded its bounded execution time')
            return value

        build = None
        if config['build_command']:
            build = execute(config['build_command'], root, folder / 'build.log', remaining())
            util.atomic_json(folder / 'build.json', {**build, 'source_revision': before['revision']})
        for item in config['assets']:
            path = evidence.local_file(root, item['path'])
            inputs[str(path.relative_to(root))] = util.file_hash(path)
        if any(item['path'] not in before['files'] for item in config['assets']) and not config['build_command']:
            raise ValueError('Generated/ignored served assets require a fresh build_command')
        engines = {name: util.file_hash(Path(__file__).with_name(name)) for name in
                   ('autocode_visual_capture.py', 'autocode_visual_browser.cjs')}
        request = folder / 'request.json'
        util.atomic_json(request, {**config, 'output': str(folder), 'timeout_ms': max(1, int(remaining() * 1000))})
        execute(['node', str(Path(__file__).with_name('autocode_visual_browser.cjs')), str(request)],
                root, folder / 'browser.log', remaining())
        if source_snapshot.snapshot(root, paths=config.get('source_paths', ()))['revision'] != before['revision']:
            raise ValueError('Source changed during capture; retain this attempt and recapture')
        for name, expected in inputs.items():
            if util.file_hash(evidence.local_file(root, name)) != expected:
                raise ValueError(f'Capture input changed during execution: {name}')
        artifacts = {key: {'path': str((folder / name).relative_to(root)), 'sha256': util.file_hash(folder / name)}
                     for key, name in (('candidate', 'candidate.png'), ('browser', 'browser.json'))}
        if build:
            artifacts['build'] = {'path': str((folder / 'build.json').relative_to(root)), 'sha256': util.file_hash(folder / 'build.json')}
        body = {'version': 1, 'kind': 'implementation_capture', 'reference_hash': config['reference_hash'],
                'case': config['case'], 'source_revision': before['revision'],
                'config_ref': str(config_path.relative_to(root)), 'inputs': inputs,
                'engine': engines, 'build_executed': bool(config['build_command']), 'artifacts': artifacts}
        path = folder / 'manifest.json'
        # Verify before publishing a discoverable bundle. No PASS verdict is produced.
        util.atomic_json(path, body)
        state = {'workspace': str(root), 'settings': {'design_manifest': {'manifest_hash': config['reference_hash']}}}
        try:
            evidence.verify(state, str(path), current=before)
        except Exception:
            path.rename(folder / 'rejected-manifest.json')
            raise
        return {'capture_ref': str(path), 'capture_sha256': util.file_hash(path),
                'candidate_ref': str(root / artifacts['candidate']['path']), 'visual_acceptance': None}
    except BaseException as error:
        util.atomic_json(folder / 'failure.json', {'error': str(error), 'source_revision': before['revision']})
        raise


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('timeout must be finite and positive')
    try:
        print(json.dumps(capture(Path.cwd(), args.config, args.timeout)))
        return 0
    except (ValueError, OSError, KeyError, TypeError, processes.ProcessError) as error:
        print(json.dumps({'status': 'CAPTURE_UNAVAILABLE', 'reason': str(error)}))
        return 2
