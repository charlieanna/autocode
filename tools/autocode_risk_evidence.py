"""Current-source lifecycle proof through the existing injected scratch runner.

The protected contract selects protocols; this helper has no controller, model
route or process launcher. Results belong to validation.check_replay.risk_acceptance.
A single bounded replay deadline covers all due cases. Completion re-reads full
pinned transcripts and recomputes their protocol verdicts.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_risk_obligations as obligations
    from . import autocode_risk_protocols as protocols
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_risk_obligations as obligations
    import autocode_risk_protocols as protocols
    import autocode_util as util

PASS, FAIL = 'PASS', 'FAIL'
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_RESULT_KEYS = frozenset({'verdict', 'source_revision', 'contract_hash', 'manifest_hash',
                         'target_inventory_hash', 'timeout_seconds', 'checks', 'summary', 'summary_sha256'})
_CHECK_KEYS = frozenset({'observation_hash', 'command', 'command_sha256', 'exit_code',
                        'timed_out', 'error', 'output', 'output_sha256', 'observation'})
MAX_TRANSCRIPT_BYTES = 1024 * 1024


def _identity(state):
    contract = state.get('goal_contract') or {}
    wrapper = (contract.get('body') or {}).get(obligations.KEY) or {}
    return contract.get('hash'), (wrapper.get('manifest') or {}).get('hash'), (wrapper.get('targets') or {}).get('sha256')


def _pinned_bytes(path, expected):
    if not isinstance(path, str) or not isinstance(expected, str) or not _HASH.fullmatch(expected):
        raise ValueError('Risk observation has no intact execution content pin')
    file = Path(path)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError('Risk observation output is missing, linked or exceeds its bound')
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Risk observation output changed after execution')
    return data


def evidence_pins(result):
    pins = {row['output']: row['output_sha256'] for row in (result or {}).get('checks', [])
            if row.get('output') and row.get('output_sha256')}
    for row in (result or {}).get('checks', []):
        pins.update(command_receipt.pins(row))
    if (result or {}).get('summary') and result.get('summary_sha256'):
        pins[result['summary']] = result['summary_sha256']
    return pins


def replay(state, workspace, out, scratch_run, *, timeout, source_revision,
           progressive_context=None, all_observations=False):
    cases = obligations.observations(state, progressive_context=progressive_context,
                                    all_observations=all_observations)
    if not cases:
        return None
    if type(timeout) not in (int, float) or not 0 < timeout <= 900 or not source_revision:
        raise ValueError('Risk replay needs the existing bounded timeout and measured source revision')
    # This is a cap within the supplied replay allowance, never a new grant.
    budget = min(30, timeout)
    commands = obligations.commands(state, python='python3', timeout=budget,
                                    progressive_context=progressive_context, all_observations=all_observations)
    if len(commands) != len(cases) or len({case['hash'] for case in cases}) != len(cases):
        raise ValueError('Risk replay omitted or repeated a mandatory lifecycle observation')
    contract_hash, manifest_hash, target_hash = _identity(state)
    if not contract_hash or not manifest_hash or not target_hash:
        raise ValueError('Risk replay needs the protected source, contract and initial API identities')
    directory = Path(out) / 'risk-acceptance' / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    checks = []
    deadline = time.monotonic() + budget
    for index, (case, command) in enumerate(zip(cases, commands, strict=False), 1):
        remaining = deadline - time.monotonic()
        row = {'observation_hash': case['hash'], 'command': command,
               'command_sha256': hashlib.sha256(command.encode()).hexdigest(), 'exit_code': None,
               'timed_out': False, 'error': '', 'output': None, 'output_sha256': None, 'observation': None}
        try:
            if remaining <= 0:
                row['timed_out'] = True
                raise ValueError('The shared lifecycle observation deadline is exhausted')
            receipt = scratch_run(workspace, directory / f'case-{index:02d}', command=command, timeout=remaining)
            row.update(exit_code=receipt.get('exit_code'), timed_out=bool(receipt.get('timed_out')),
                       error=receipt.get('error') or '', output=receipt.get('output'),
                       output_sha256=receipt.get('output_sha256'), **command_receipt.project(receipt))
            if (row['error'] or row['timed_out'] or type(row['exit_code']) is not int or row['exit_code'] != 0
                    or not command_receipt.completed(row)):
                reason = row['error'] or 'Mandatory lifecycle observation failed or timed out'
                # Full pinned supervisor output can explain a rejection. It can
                # never turn a nonzero/timed-out execution into accepted proof.
                if row['output'] and row['output_sha256']:
                    try:
                        failure = json.loads(_pinned_bytes(row['output'], row['output_sha256']))
                        if (isinstance(failure, dict) and failure.get('verdict') == FAIL
                                and failure.get('protocol') == case['protocol']
                                and failure.get('observation_hash') == case['hash']
                                and isinstance(failure.get('error'), str) and failure['error']):
                            reason += ': ' + failure['error'][:500]
                    except (OSError, ValueError, TypeError):
                        pass
                raise ValueError(reason)
            row['observation'] = protocols.validate_transcript(_pinned_bytes(row['output'], row['output_sha256']), case)
        except (OSError, ValueError, TypeError, KeyError) as error:
            row['error'] = str(error)
        checks.append(row)
    result = {'verdict': FAIL if any(row['error'] for row in checks) else PASS,
              'source_revision': source_revision, 'contract_hash': contract_hash, 'manifest_hash': manifest_hash,
              'target_inventory_hash': target_hash, 'timeout_seconds': budget, 'checks': checks}
    summary = directory / 'summary.json'
    util.atomic_json(summary, result)
    result = {**result, 'summary': str(summary), 'summary_sha256': util.file_hash(summary)}
    if result['verdict'] != PASS:
        failed = next(row for row in checks if row['error'])
        raise ValueError('The mandatory risk lifecycle observation did not pass on this candidate: '
                         + failed['error'] + f'. Receipt: {summary}')
    return result


def ready(state, current_revision):
    try:
        cases = obligations.observations(state, all_observations=True)
        if not cases:
            return not obligations.inventory(state)
        result = ((state.get('validation') or {}).get('check_replay') or {}).get('risk_acceptance')
        if not isinstance(result, dict) or set(result) != _RESULT_KEYS or result['verdict'] != PASS:
            return False
        contract_hash, manifest_hash, target_hash = _identity(state)
        if (not current_revision or result['source_revision'] != current_revision
                or not contract_hash or result['contract_hash'] != contract_hash
                or not manifest_hash or result['manifest_hash'] != manifest_hash
                or not target_hash or result['target_inventory_hash'] != target_hash):
            return False
        timeout = result['timeout_seconds']
        if type(timeout) not in (int, float) or not 0 < timeout <= 30:
            return False
        commands = obligations.commands(state, python='python3', timeout=timeout, all_observations=True)
        checks = result['checks']
        if not isinstance(checks, list) or len(checks) != len(cases) or len(commands) != len(cases):
            return False
        if json.loads(_pinned_bytes(result['summary'], result['summary_sha256'])) != {
                key: value for key, value in result.items() if key not in ('summary', 'summary_sha256')}:
            return False
        outputs = set()
        for row, case, command in zip(checks, cases, commands, strict=False):
            if (not isinstance(row, dict)
                    or set(row) not in (_CHECK_KEYS, _CHECK_KEYS | set(command_receipt.OWNERSHIP_FIELDS))
                    or not command_receipt.completed(row)
                    or row['observation_hash'] != case['hash'] or row['command'] != command
                    or row['command_sha256'] != hashlib.sha256(command.encode()).hexdigest()
                    or type(row['exit_code']) is not int or row['exit_code'] != 0
                    or row['timed_out'] is not False or row['error'] != '' or row['output'] in outputs):
                return False
            outputs.add(row['output'])
            actual = protocols.validate_transcript(_pinned_bytes(row['output'], row['output_sha256']), case)
            if row['observation'] != actual:
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False
