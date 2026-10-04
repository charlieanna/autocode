"""Explicit recovery grants validated across this invocation's settings change.

The old settings are supplied only by locked CLI setup. Rechecking the issued
request with that one prior field preserves all source, contract, permission,
history and worker bindings; an otherwise stale request earns no allowance.
"""
try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def grant(state, run_dir, amount, *, previous_settings, current_request, count, supersede, persist):
    if type(amount) is not int or amount < 1:
        raise ValueError('recovery allowance must be a positive integer')
    issued = current_request(state)
    if issued is None and previous_settings is not None:
        issued = current_request({**state, 'settings': previous_settings})
    cause = (state.get('resolver', {}).get('human_escalations', {}).get(issued['request_id'], {})
             .get('identity', {}).get('proposal', {}).get('origin', {}).get('pause_status')) if issued else None
    if (state.get('status') != 'PAUSED_TIMEOUT_RECOVERY'
            and not (issued and issued['scope'] == 'operational_exhaustion' and cause == 'PAUSED_TIMEOUT_RECOVERY')):
        raise ValueError('--grant-recovery requires a run paused for exhausted timeout recovery')
    remaining = max(0, count - amount)
    state['automatic_recoveries_since_resume'] = remaining
    state['consecutive_timeout_recoveries'] = 0
    request_id = issued['request_id'] if issued else None
    state.setdefault('recovery_grants', []).append({
        'at': util.now(), 'actor': 'user_cli', 'amount': amount, 'request_id': request_id,
        'previous_count': count, 'remaining_count': remaining})
    state.setdefault('user_events', []).append({
        'kind': 'recovery_grant', 'at': util.now(), 'actor': 'user_cli', 'amount': amount,
        'request_id': request_id, 'previous_count': count})
    supersede(state, f'Operator granted {amount} more automatic recoveries')
    persist(run_dir / 'state.json', state)
    print(f"Recovery grant recorded: {amount} more automatic timeout recoveries authorized "
          f"({count} -> {remaining} counted); history retained.", flush=True)
    return remaining
