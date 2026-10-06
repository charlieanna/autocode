"""Pure hold policy for a supervised response recovered from a saved attempt."""

NORMAL_CAUSES = frozenset({'controller_finished', 'provider_stopped', 'stage_deadline'})


def hold(record, receipt, *, attempt):
    """Missing ownership/result facts never authorize report adoption or replay.

    The caller authenticates the independent receipt and supplies the attempt
    label. A known finished timeout can use the existing bounded timeout route;
    an unsaved exit remains uncertain even if its events contain a full turn.
    """
    if not record.get('supervision'):
        return None
    if (record.get('interrupted') or not record.get('finished_at')
            or type(record.get('exit_code')) is not int
            or not isinstance(receipt, dict) or receipt.get('phase') != 'stopped'
            or receipt.get('cleanup_error') or receipt.get('cause') not in NORMAL_CAUSES):
        return {'status': 'PAUSED_UNCERTAIN_STAGE', 'timed_out': False,
                'reason': f'Supervised attempt {attempt} lacks a verified uninterrupted result; '
                f'inspect its retained artifacts and cleanup receipt, then use --abandon-stage {attempt}. '
                'No provider call or recovered report is automatically accepted.'}
    if record.get('timed_out') or receipt.get('cause') == 'stage_deadline':
        reason = record.get('timeout_reason')
        if not isinstance(reason, str) or not reason:
            reason = f'Supervised attempt {attempt} crossed its runtime limit.'
        return {'status': 'PAUSED_PROVIDER_TIMEOUT', 'timed_out': True,
                'reason': reason + ' Its retained terminal response is not accepted as a successful result.'}
    return None
