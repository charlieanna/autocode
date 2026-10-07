"""Fresh original-brief observations through the existing contained scratch runner.

The caller supplies scratch_run, the current source revision and its existing
replay timeout. This helper neither launches a process nor changes run state.
Results belong at validation.check_replay.brief_acceptance. Completion rereads
full pinned runner output and the summary, never a provider report or log tail.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import shlex
import uuid

try:
    from . import autocode_brief_obligations as obligations, autocode_util as util
    from . import autocode_brief_acceptance as acceptance, autocode_command_receipt as command_receipt
except ImportError:
    import autocode_brief_acceptance as acceptance
    import autocode_brief_obligations as obligations
    import autocode_util as util
    import autocode_command_receipt as command_receipt

PASS, FAIL = 'PASS', 'FAIL'
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_RESULT_KEYS = frozenset({'verdict', 'source_revision', 'contract_hash', 'manifest_hash',
                          'timeout_seconds', 'checks', 'summary', 'summary_sha256'})
_CHECK_KEYS = frozenset({'observation_hash', 'command', 'command_sha256', 'exit_code',
                         'timed_out', 'error', 'output', 'output_sha256', 'observation'})


def _identity(state):
    contract = state.get('goal_contract') or {}
    wrapper = (contract.get('body') or {}).get('brief_acceptance') or {}
    return contract.get('hash'), (wrapper.get('manifest') or {}).get('hash')


def _pinned_bytes(path, expected):
    if not isinstance(path, str) or not isinstance(expected, str) or not _HASH.fullmatch(expected):
        raise ValueError('Original-brief execution output has no intact content pin')
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise ValueError('Original-brief execution output is missing or replaced by a link')
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Original-brief execution output changed after the runner recorded it')
    return data


def _decode(encoded):
    if not isinstance(encoded, str):
        raise ValueError('Original-brief CLI output needs actual base64 bytes')
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('Original-brief CLI output has invalid base64 bytes') from error


def _observation(data, expected):
    try:
        observed = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError('Original-brief runner output is not one complete JSON observation') from error
    if (not isinstance(observed, dict)
            or set(observed) != {'verdict', 'observation_hash', 'steps', 'reason'}
            or observed['verdict'] != PASS or observed['reason'] != ''
            or observed['observation_hash'] != expected['hash']
            or not isinstance(observed['steps'], list)
            or len(observed['steps']) != len(expected['proposal']['steps'])):
        raise ValueError('Original-brief runner did not return a complete passing observation for this exact case')
    outputs = []
    for row, invocation in zip(observed['steps'], expected['proposal']['steps']):
        if (not isinstance(row, dict)
                or set(row) != {'argv', 'exit_code', 'stdout_base64', 'stderr_base64'}
                or row['argv'] != invocation['argv'] or type(row['exit_code']) is not int
                or row['exit_code'] != 0):
            raise ValueError('Original-brief runner steps differ from the mandatory source-derived invocation')
        outputs.append(_decode(row['stdout_base64']))
        _decode(row['stderr_base64'])
    output = outputs[expected['proposal']['observe_step']]
    reason = acceptance.output_reason(output, expected['pattern'], acceptance.line_pattern(expected))
    if reason:
        raise ValueError('Actual ' + reason)
    return observed


def _runner_reason(row, case):
    """The contained runner's own refusal and what the step printed, for a readable rejection.

    Diagnostic only: a failed runner exit is refused whatever this returns.
    """
    try:
        observed = json.loads(_pinned_bytes(row['output'], row['output_sha256']).decode('utf-8'))
        reason, steps, planned = observed['reason'], observed.get('steps'), case['proposal']['steps']
        if not isinstance(reason, str) or not reason:
            return ''
        if not isinstance(steps, list):
            return reason  # refused before any step ran (the entrypoint check)
        # The runner stops at a step that exits nonzero or times out (a timed-out step
        # records nothing), and checks the listing only once every step has run.
        if steps and steps[-1]['exit_code'] != 0:
            step = steps[-1]
            stderr = _decode(step['stderr_base64'])
            return f"{reason}; `{shlex.join(step['argv'])}` exited {step['exit_code']} and wrote {stderr[-400:]!r} to stderr"
        if len(steps) < len(planned):
            return f"{reason}; `{shlex.join(planned[len(steps)]['argv'])}` did not finish"
        step = steps[case['proposal']['observe_step']]
        return f"{reason}; `{shlex.join(step['argv'])}` printed {_decode(step['stdout_base64'])[:400]!r}"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return ''


def evidence_pins(result):
    """Output and summary pins for the existing completion evidence map."""
    pins = {row['output']: row['output_sha256'] for row in (result or {}).get('checks', [])
            if row.get('output') and row.get('output_sha256')}
    for row in (result or {}).get('checks', []):
        pins.update(command_receipt.pins(row))
    if (result or {}).get('summary') and result.get('summary_sha256'):
        pins[result['summary']] = result['summary_sha256']
    return pins


def replay(state, workspace, out, scratch_run, *, timeout, source_revision, progressive_context=None, all_observations=False):
    """Run every selected mandatory case fresh, retaining failure evidence before refusal.

    Source_revision is the calling runner's measured candidate revision; its
    existing before/after source guard still owns that measurement. Selection
    and commands share the same trusted contract/current-task context.
    """
    cases = obligations.observations(state, progressive_context=progressive_context, all_observations=all_observations)
    if not cases:
        return None
    if type(timeout) not in (int, float) or not 0 < timeout <= 900:
        raise ValueError('Original-brief replay needs the existing bounded replay timeout')
    if not isinstance(source_revision, str) or not source_revision:
        raise ValueError('Original-brief replay needs the current measured source revision')
    commands = obligations.commands(state, python='python3', timeout=min(15, timeout),
                                     progressive_context=progressive_context, all_observations=all_observations)
    if len(commands) != len(cases) or len({case['hash'] for case in cases}) != len(cases):
        raise ValueError('Original-brief replay omitted or repeated a mandatory observation')
    contract_hash, manifest_hash = _identity(state)
    if not contract_hash or not manifest_hash:
        raise ValueError('Original-brief replay requires the retained contract and manifest identities')
    directory = Path(out) / 'brief-acceptance' / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    checks = []
    for index, (case, command) in enumerate(zip(cases, commands), 1):
        receipt = scratch_run(workspace, directory / f'case-{index:02d}', command=command, timeout=timeout)
        row = {'observation_hash': case['hash'], 'command': command,
               'command_sha256': hashlib.sha256(command.encode()).hexdigest(),
               'exit_code': receipt.get('exit_code'), 'timed_out': bool(receipt.get('timed_out')),
               'error': receipt.get('error') or '', 'output': receipt.get('output'),
               'output_sha256': receipt.get('output_sha256'), 'observation': None,
               **command_receipt.project(receipt)}
        try:
            if (row['error'] or row['timed_out'] or type(row['exit_code']) is not int or row['exit_code'] != 0
                    or not command_receipt.completed(row)):
                reason = row['error'] or ('' if row['timed_out'] else _runner_reason(row, case))
                raise ValueError(reason or 'mandatory original-brief invocation failed or timed out')
            row['observation'] = _observation(_pinned_bytes(row['output'], row['output_sha256']), case)
        except (OSError, ValueError, TypeError, KeyError) as error:
            row['error'] = str(error)
        checks.append(row)
    result = {'verdict': FAIL if any(row['error'] for row in checks) else PASS,
              'source_revision': source_revision, 'contract_hash': contract_hash,
              'manifest_hash': manifest_hash, 'timeout_seconds': timeout, 'checks': checks}
    summary = directory / 'summary.json'
    util.atomic_json(summary, result)
    result = {**result, 'summary': str(summary), 'summary_sha256': util.file_hash(summary)}
    if result['verdict'] != PASS:
        failed = next(row for row in checks if row['error'])
        raise ValueError('The mandatory original-brief output observation did not pass on this candidate: '
                         + failed['error'] + f'. Receipt: {summary}')
    return result


def ready(state, current_revision):
    """Require every current original-brief observation, irrespective of task slice."""
    try:
        cases = obligations.observations(state, all_observations=True)
        if not cases:
            return not obligations.inventory(state)
        result = ((state.get('validation') or {}).get('check_replay') or {}).get('brief_acceptance')
        if not isinstance(result, dict) or set(result) != _RESULT_KEYS or result.get('verdict') != PASS:
            return False
        contract_hash, manifest_hash = _identity(state)
        if (not current_revision or result['source_revision'] != current_revision
                or not contract_hash or result['contract_hash'] != contract_hash
                or not manifest_hash or result['manifest_hash'] != manifest_hash):
            return False
        timeout = result['timeout_seconds']
        if type(timeout) not in (int, float) or not 0 < timeout <= 900:
            return False
        commands = obligations.commands(state, python='python3', timeout=min(15, timeout), all_observations=True)
        checks = result['checks']
        if not isinstance(checks, list) or len(checks) != len(cases) or len(commands) != len(cases):
            return False
        if json.loads(_pinned_bytes(result['summary'], result['summary_sha256']).decode('utf-8')) != {
                key: value for key, value in result.items() if key not in ('summary', 'summary_sha256')}:
            return False
        outputs = set()
        for row, case, command in zip(checks, cases, commands):
            if (not isinstance(row, dict)
                    or set(row) not in (_CHECK_KEYS, _CHECK_KEYS | set(command_receipt.OWNERSHIP_FIELDS))
                    or not command_receipt.completed(row)
                    or row['observation_hash'] != case['hash'] or row['command'] != command
                    or row['command_sha256'] != hashlib.sha256(command.encode()).hexdigest()
                    or type(row['exit_code']) is not int or row['exit_code'] != 0
                    or row['timed_out'] is not False or row['error'] != ''
                    or row['output'] in outputs):
                return False
            outputs.add(row['output'])
            actual = _observation(_pinned_bytes(row['output'], row['output_sha256']), case)
            if row['observation'] != actual:
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False
