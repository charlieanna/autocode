"""Authenticate one interrupted clean-command admission without run state.

This establishes permission to record a failed obligation and run it fresh. It
never supplies a collected command exit, a successful check, or cache evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat

try:
    from . import autocode_command_receipt as receipts, autocode_process as processes
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_process as processes
    import autocode_util as util

# The request envelope leaves 4 MiB framing headroom for an 8 MiB Supply.
# Receipts, admissions and worker answers keep the 4 MiB default reader bound.
MAX_PREPARATION_REQUEST_BYTES = 12 * 1024 * 1024


def hold(reason):
    raise receipts.OwnershipUncertain('Interrupted verification cannot be recovered: ' + reason)


def owned_path(path, root, *, directory=False):
    """Require a canonical owned path and no symlink anywhere in its hierarchy."""
    path, root = Path(path), Path(root)
    if (not path.is_absolute() or not path.is_relative_to(root)
            or path != path.resolve() or root.is_symlink()
            or any(parent.is_symlink() for parent in (path, *path.parents))):
        hold('an admission path is unbound or symlinked')
    mode = path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        hold('an admission path is not a regular ' + ('directory' if directory else 'file'))
    return path


def read_regular(path, root, *, max_bytes=receipts.MAX_RECEIPT_BYTES):
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_PREPARATION_REQUEST_BYTES:
        hold('an admission reader limit is invalid')
    path = owned_path(path, root)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            hold('an admission file changed type')
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        hold('an admission file exceeds the reader limit')
    value = json.loads(data)
    if not isinstance(value, dict):
        hold('an admission is not an object')
    return value, hashlib.sha256(data).hexdigest()


def native_identity(row):
    return (isinstance(row, dict) and type(row.get('pid')) is int and row['pid'] > 0
            and type(row.get('birth_identity')) in (int, float) and row['birth_identity'] > 0
            and math.isfinite(row['birth_identity']))


def admission_identity(active):
    """Validate path components before deriving any referenced owned path."""
    if (not isinstance(active, dict) or not isinstance(active.get('identity'), dict)
            or not isinstance(active.get('started_at'), str) or not active['started_at']
            or not isinstance(active.get('attempt_id'), str)
            or re.fullmatch('[0-9a-f]{32}', active['attempt_id']) is None):
        hold('scheduler admission is malformed')
    return util.digest(active['identity'])


def bind_command(out, check, *, preparation_cwd=None):
    """Authenticate static command custody without requiring a stopped process."""
    if not isinstance(check, dict):
        hold('runner check is missing')
    output = owned_path(check['output'], out)
    if output.parent != out or not isinstance(check.get('command'), str) or not check['command']:
        hold('runner output or command does not bind this scheduled attempt')
    metadata = check['supervision']
    path = owned_path(metadata['receipt'], out)
    relative = path.relative_to(out)
    if (len(relative.parts) != 3 or relative.parts[0] != 'command-supervision'
            or re.fullmatch('[0-9a-f]{32}', relative.parts[1]) is None
            or relative.name != 'supervision.json'):
        hold('supervision does not belong to this scheduled attempt')
    admission_path = path.parent / 'admission.json'
    admission, admission_sha = read_regular(admission_path, out)
    cwd = Path(admission['cwd'])
    if (type(admission.get('schema')) is not int or admission['schema'] != 1
            or admission.get('command') != check['command']
            or admission.get('output') != str(output) or admission.get('supervision') != metadata
            or not cwd.is_absolute()
            or (cwd != preparation_cwd if preparation_cwd else not cwd.is_relative_to(out / 'scratch'))
            or cwd != cwd.resolve() or any(parent.is_symlink() for parent in (cwd, *cwd.parents))):
        hold('command admission does not match the exact runner check')
    # read_regular rejects FIFO and other nonregular references before load.
    _, terminal_sha = read_regular(path, out)
    terminal = receipts.load(metadata, root=out, pin=terminal_sha)
    if not terminal:
        hold('native custody receipt is malformed or unbound')
    recorded = check.get('processes')
    if not isinstance(recorded, list) or not all(native_identity(row) for row in recorded):
        hold('recorded runner process identities are unknown or malformed')
    return {'out': out, 'check': check, 'metadata': metadata, 'terminal': terminal,
            'pins': {str(path): terminal_sha, str(admission_path): admission_sha},
            'output': output, 'output_sha256': util.file_hash(output)}


def bind(root, active, check, *, allow_idle=False):
    """Bind the exact current phase before public native liveness inspection.

The command can be rewritten for a clean checkout or augmented with reporters.
The scheduler attempt hierarchy and command admission bind the actual command;
matching the untransformed configured command alone would not be sufficient.
"""
    try:
        root = owned_path(root, root, directory=True)
        digest = admission_identity(active)
        directory = owned_path(root / digest, root, directory=True)
        out = owned_path(directory / active['attempt_id'], root, directory=True)
        preparation_pins = {}
        preparation_cwd = None
        request = prep = None
        if active.get('preparation_admission'):
            reference = active['preparation_admission']
            if (not isinstance(reference, dict) or reference.get('path') != str(out / 'preparation-admission.json')
                    or not isinstance(reference.get('sha256'), str)
                    or re.fullmatch('[0-9a-f]{64}', reference['sha256']) is None):
                hold('preparation admission reference is unbound')
            prep_path = owned_path(out / 'preparation-admission.json', out)
            prep, prep_sha = read_regular(prep_path, out)
            if (prep_path != out / 'preparation-admission.json' or prep_sha != reference['sha256']
                    or type(prep.get('schema')) is not int or prep['schema'] != 1
                    or prep.get('attempt_id') != active['attempt_id']
                    or prep.get('identity') != util.digest(active['identity']) or prep.get('out') != str(out)
                    or prep.get('started_at') != active['started_at']
                    or not isinstance(prep.get('nonce'), str)
                    or re.fullmatch('[0-9a-f]{32}', prep['nonce']) is None
                    or not native_identity(prep.get('owner'))):
                hold('preparation admission does not bind the exact scheduled attempt')
            # Current pending custody is authoritative. An older saved check may
            # describe another obligation and cannot stand in for this one.
            check = active.get('current_check')
            preparation_pins[str(prep_path)] = prep_sha
            if (allow_idle and active.get('phase') in ('admitted', 'post_execution_identity')
                    and check is None):
                return {'out': out, 'check': None, 'owner': prep['owner'], 'pins': preparation_pins}
            if (not isinstance(check, dict) or active.get('phase') not in ('preparing','executing','removing')
                    or check.get('phase') != active['phase']
                    or check.get('supervision', {}).get('owner') != prep.get('owner')):
                hold('current preparation phase has no exact native custody')
            if active['phase'] in ('preparing','removing') or check.get('request') is not None:
                request_path = owned_path(check['request'], out)
                request, request_sha = read_regular(
                    request_path, out, max_bytes=MAX_PREPARATION_REQUEST_BYTES)
                operation = {'preparing': 'prepare', 'removing': 'remove', 'executing': 'execute'}[active['phase']]
                worker_path = owned_path(prep['worker'], Path(prep['worker']).parent)
                worker_sha = util.file_hash(worker_path)
                expected_command = shlex.join([prep['python'], str(worker_path), '--request', str(request_path),
                                                '--request-sha256', request_sha])
                if (request_sha != check['request_sha256'] or type(request.get('schema')) is not int
                        or request['schema'] != 1
                        or request.get('operation') != operation or request.get('out') != str(out)
                        or request.get('preparation_admission') != reference or request.get('nonce') != prep['nonce']
                        or worker_path.name != 'autocode_verification_prepare_worker.py'
                        or worker_sha != prep['worker_sha256'] or check.get('command') != expected_command
                        or request_path.parent != out or Path(request['result']).parent != out):
                    hold('preparation request, worker or command changed')
                preparation_cwd = Path(request['workspace'])
                if (not preparation_cwd.is_absolute() or preparation_cwd != preparation_cwd.resolve()
                        or any(p.is_symlink() for p in (preparation_cwd, *preparation_cwd.parents))):
                    hold('preparation workspace is unbound')
                preparation_pins.update({str(request_path): request_sha, str(worker_path): worker_sha})
        bound = bind_command(out, check, preparation_cwd=preparation_cwd)
        return {**bound, 'pins': {**preparation_pins, **bound['pins']},
                'preparation': prep, 'request': request}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError,
            SystemError, OverflowError, processes.ProcessError) as error:
        hold(str(error))


def authenticate_bound(bound):
    """Require terminal cleanup and native absence after static admission binds."""
    try:
        out, check, terminal, metadata = (bound[name] for name in ('out','check','terminal','metadata'))
        if not terminal or terminal.get('phase') != 'stopped' or terminal.get('cleanup_error'):
            hold('authenticated terminal cleanup is missing or unfinished')
        rows = [metadata['owner'], metadata['keeper'], metadata['provider'], *terminal['processes'], *check['processes']]
        if processes.live_processes(rows):
            hold('an owned command process is still alive')
        if any(util.file_hash(path) != pin for path, pin in bound['pins'].items()):
            hold('ownership evidence changed during native inspection')
        return {'out': out, 'pins': bound['pins'], 'terminal': terminal,
                'result': {'command': check['command'], 'output': str(bound['output']),
                           'output_sha256': util.file_hash(bound['output']), 'exit_code': None,
                           'interrupted': True, 'error': 'Explicit recovery of an interrupted verification; '
                           'the post-execution identity and scheduler result were not published',
                           'supervision': metadata, 'supervision_sha256': bound['pins'][metadata['receipt']],
                           'supervision_errors': [], 'duration_seconds': None,
                           'duration_basis': 'Interrupted command duration was not collected'}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError,
            SystemError, OverflowError, processes.ProcessError) as error:
        hold(str(error))


def authenticate(root, active, check):
    return authenticate_bound(bind(root, active, check))


CONTEXT_CHANGED_ERROR = 'Source, runtime, dependencies or environment changed during verification'


def collected_command(result, out):
    """A collected native result can be authentic even when validation failed.

    Error text remains failed evidence. Removing it from this temporary custody
    check neither changes the original result nor authorizes proof/reuse.
    """
    return (isinstance(result, dict) and type(result.get('exit_code')) is int
            and isinstance(result.get('error', ''), str)
            and set(receipts.OWNERSHIP_FIELDS) <= set(result)
            and receipts.completed({**result, 'error': ''}, root=out))


def authenticate_publication(root, active):
    """Preserve a genuinely collected result at normal publication boundaries.

    A closed outer worker cannot authorize interrupted context measurement.
    Only the exact already-published scheduler result plus its inner command
    receipt can admit this path; every worker and command identity must be gone.
    """
    try:
        if (active.get('phase') != 'post_execution_identity' or active.get('current_check') is not None
                or not isinstance(active.get('completed_check'), dict)
                or active['completed_check'].get('phase') != 'executing'):
            hold('context measurement has no original completed scheduler receipt')
        check = active['completed_check']
        bound = bind(root, {**active, 'phase': 'executing', 'current_check': check}, None)
        outer = authenticate_bound(bound)
        request, prep, out = bound['request'], bound['preparation'], bound['out']
        if not request or request.get('operation') != 'execute':
            hold('completed scratch result has no exact full-worker custody')
        answer_path = owned_path(request['result'], out)
        answer, answer_sha = read_regular(answer_path, out)
        saved_path = out / 'receipt.json'
        saved, saved_sha = read_regular(saved_path, out)
        result = saved.get('result')
        returned = answer.get('receipt')
        context_failed = (isinstance(returned,dict) and isinstance(result,dict)
            and isinstance(result.get('observed_after_identity'),dict)
            and result['observed_after_identity'] != active['identity']
            and result == {**returned, 'error': CONTEXT_CHANGED_ERROR,
                           'observed_after_identity': result['observed_after_identity']})
        context_exception = (isinstance(returned,dict) and isinstance(result,dict)
            and isinstance(result.get('error'),str)
            and re.fullmatch(r'Verification attempt did not complete \([A-Za-z_][A-Za-z0-9_]{0,127}\): .{0,4096}',
                             result['error'], re.DOTALL) is not None
            and result == {**returned, 'error': result['error']})
        if (saved.get('runner_owned') is not True
                or any(saved.get(name) != active[name] for name in ('attempt_id','identity','started_at'))
                or any(not isinstance(saved.get(name), str) or not saved[name] for name in ('finished_at','reason'))
                or answer.get('request_sha256') != check['request_sha256']
                or answer.get('nonce') != prep['nonce'] or answer.get('operation') != 'execute'
                or not (result == returned or context_failed or context_exception) or not isinstance(result, dict)
                or not collected_command(result, out)):
            hold('original scheduler result does not bind the collected inner test receipt')
        inner_check = {'command': result['command'], 'output': result['output'],
                       'supervision': result['supervision'], 'processes': []}
        inner_bound = bind_command(out, inner_check)
        if inner_bound['metadata']['owner'] not in outer['terminal']['processes']:
            hold('inner command owner is not bound to the outer worker inventory')
        inner = authenticate_bound(inner_bound)
        if any(result.get(name) != inner['result'][name] for name in
               ('command','output','output_sha256','supervision','supervision_sha256')):
            hold('inner collected command evidence changed')
        pins = {**outer['pins'], **inner['pins'], str(saved_path): saved_sha, str(answer_path): answer_sha,
                str(bound['output']): bound['output_sha256'],
                str(inner_bound['output']): inner_bound['output_sha256']}
        if any(util.file_hash(path) != digest for path, digest in pins.items()):
            hold('completed worker or inner receipt changed during authentication')
        return {**inner, 'pins': pins, 'collected_failed_result': bool(result.get('error'))}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError,
            SystemError, OverflowError, processes.ProcessError) as error:
        hold(str(error))
