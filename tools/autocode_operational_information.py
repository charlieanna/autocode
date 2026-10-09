"""One AutoResolver re-evaluation of corrective information on an operational pause (#486).

`--resolver-response provide_information` answers an AutoResolver `operational_exhaustion`
request. The response is information, never authority: it cannot launch a provider, raise a
bound, reset a count or approve anything. Before #486 it was also never evaluated, so the run
held at the same frontier forever while its status named only `--resume-paused`.

An accepted response now schedules exactly one evaluation (``schedule``, called by
autocode_resolver_human.review_operational_response for the CLI and chat paths). The next
explicit continuation (``--resume-paused`` or ``autocode resume`` with no other recovery flag)
consumes it once (``reevaluate``), with zero provider calls:

- stale: the request/response chain, the pause status, the evidence pins or the frontier the
  response was bound to (autocode_resolver_human._binding: source revision, saved settings
  including the saved transport identity, task, contract, accounting, interventions) no longer
  match. The record is retired and AutoResolver asks again for the current frontier
  (``retired``); nothing launches.
- hold: the stop needs authority information cannot supply (an exhausted bound or automatic
  recovery allowance, a repeated failure, an unreconciled attempt, a parallel Builder member's
  model stop, whose one control is --retry-builder M: autocode_member_stop, #541). The run stays
  paused, its stop_reason names the exact command, and later invocations repeat that decision
  without evaluating again or republishing the request.
- continue: the stop's cause is on the allow-list of causes outside the run and none of the bounds
  checked here (an unreconciled attempt, the automatic-recovery allowance, spent report-only
  repairs, stalled validation-only rounds, planning reviews) holds it, so the request's own
  advice ("provide information, then autocode resume") applies. The run goes through the
  ordinary resume path and the build loop's admission guards (limits, permissions, transport,
  source, approval) before any provider launches; the continuation renews no allowance, so a
  guard not checked here (AutoResolver's own per-incident limit) stops it again as a new request.

State: ``resolver.information_reviews`` maps a request ID to its record; this module is its only
writer. autocode_run_actions acts on ``reevaluate`` and ``retired``; the status view reads
``projection``. Imports autocode_util, autocode_quota_route, autocode_member_stop and, for the
Validator gate it runs itself, autocode_findings, autocode_source_scope and autocode_validation_rounds. The runner is passed
in, as autocode_run_actions does, so these runtime dependencies are not import edges:
runner.write_json, runner.repair_limit, runner.support (Paused, snapshot), runner.interventions.admission,
runner.timeout_recovery_guard, runner.planning.review_call_limit, runner.resolver_runtime.operational_boundary and
runner.resolver_human (current, response_holds_current_frontier and its frontier helpers
_binding and _evidence_valid, which this module must match exactly).
"""
from __future__ import annotations

import copy
import shlex
import subprocess
from pathlib import Path

try:
    from . import autocode_findings as findings_ledger
    from . import autocode_member_stop as member_stop
    from . import autocode_quota_route as quota_route
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
    from . import autocode_validation_rounds as validation_rounds
except ImportError:
    import autocode_findings as findings_ledger
    import autocode_member_stop as member_stop
    import autocode_quota_route as quota_route
    import autocode_source_scope as source_scope
    import autocode_util as util
    import autocode_validation_rounds as validation_rounds

VERSION = 1
KEY = 'information_reviews'
REVIEW_STAGES = ('astra_challenge', 'astra_finalize')
BOUND_FLAGS = {'PAUSED_TIME_LIMIT': '--max-seconds', 'PAUSED_ITERATION_LIMIT': '--max-iterations',
               'PAUSED_MILESTONE_TIME_LIMIT': '--max-milestone-seconds',
               'PAUSED_MILESTONE_BUDGET': '--max-milestone-seconds', 'PAUSED_NO_PROGRESS': '--no-progress-limit'}
# Stops whose authority is an explicit operator control; information cannot stand in for it.
OPERATOR_ONLY = {
    'PAUSED_TIMEOUT_RECOVERY': 'the automatic-recovery allowance for this run is spent',
    'PAUSED_TIME_LIMIT': 'the saved active-time limit is reached',
    'PAUSED_ITERATION_LIMIT': 'the saved iteration ceiling is reached',
    'PAUSED_MILESTONE_TIME_LIMIT': "the milestone's active-time budget is reached",
    'PAUSED_MILESTONE_BUDGET': "the milestone's active-time budget is reached",
    'PAUSED_NO_PROGRESS': 'the limit on unchanged implementation batches is reached',
    'PAUSED_REPEATED_FAILURE': 'the same failure repeated on unchanged source',
    'PAUSED_BUILDER_RETRY_LIMIT': 'the Builder used its configured attempts for this task',
    'PAUSED_ORCHESTRATOR_WORKER': 'a parallel Builder member stopped and needs an explicit retry',
    'PAUSED_MILESTONE_STALLED': 'the milestone still fails after its bounded replanning',
    # A report that failed validation within its bounded repairs is the run's own bound, not an outside
    # cause; a fresh attempt is an operator decision (autocode_stuck_job.DIAGNOSE_ONLY).
    'PAUSED_REPORT_REPAIR_LIMIT': 'the bounded report-only repairs for this attempt are spent',
}

# Stops whose cause is outside the run (a provider, a process, a recovery the runner could not prove),
# where the request itself advised "provide information, then autocode resume". Only these continue,
# and only through the ordinary admission checks; any other stop is held.
INFORMATION_CAUSES = frozenset({
    'PAUSED_RESOLVER_OPERATIONAL', 'PAUSED_RESOLVER', 'PAUSED_PROVIDER_CAPACITY', 'PAUSED_RATE_LIMIT',
    'PAUSED_BUDGET', 'PAUSED_CONTENT_FILTER', 'PAUSED_PROVIDER_UNCERTAIN', 'PAUSED_UNCERTAIN_STAGE',
    'PAUSED_WORKSPACE_BUSY', 'PAUSED_PLANNING_BUDGET'})


class Outcome:
    """What this invocation does about a scheduled review: hold, continue, pending or stale."""

    def __init__(self, action, message):
        self.action, self.message = action, message


def resume_flags(state, pause):
    """The explicit operator control for a pause class, or None when a plain resume is its path (#301)."""
    if pause == 'PAUSED_TIMEOUT_RECOVERY':
        return '--resume-paused --grant-recovery N'
    if pause in BOUND_FLAGS:
        return f'--resume-paused {BOUND_FLAGS[pause]} N'
    if pause == 'PAUSED_BUILDER_RETRY_LIMIT':
        return '--resume-paused --retry-builder ' + ((state.get('current_task') or {}).get('milestone_id') or 'MILESTONE_ID')
    if pause == 'PAUSED_REPEATED_FAILURE':
        return '--resume-paused --retry-failed-stage'
    return None


def _receipt_pins(resolver, receipt_id):
    receipt = (resolver.get('operational_receipts') or {}).get(receipt_id) if receipt_id else None
    if not isinstance(receipt, dict):
        return None, {}
    output = receipt.get('output')
    try:
        pins = {output: util.file_hash(Path(output))} if output and Path(output).is_file() else {}
    except OSError:
        pins = {}
    return util.digest(receipt), pins


def schedule(state, event, entry, frontier):
    """Schedule one evaluation of an accepted `provide_information` operational response.

    Bound to the request ID and token, the response itself, the pause status, the frontier
    binding and the request's evidence pins. A request is answered once; it is never rearmed.
    """
    proposal = (entry.get('identity') or {}).get('proposal') or {}
    if event.get('action') != 'provide_information' or proposal.get('scope') != 'operational_exhaustion':
        return None
    resolver = state['resolver']
    reviews = resolver.setdefault(KEY, {})
    if event['request_id'] in reviews:
        return None
    receipt_id = (proposal.get('evidence') or {}).get('resolver_receipt_id')
    receipt, pins = _receipt_pins(resolver, receipt_id)
    reviews[event['request_id']] = record = {
        'version': VERSION, 'status': 'pending', 'scheduled_at': util.now(),
        'request_id': event['request_id'], 'request_token': event['request_token'],
        'response': util.digest(event), 'pause_status': frontier['pause_status'],
        'cause': (proposal.get('origin') or {}).get('pause_status') or frontier['pause_status'],
        'binding': copy.deepcopy(frontier['binding']),
        'receipt_id': receipt_id, 'receipt': receipt, 'pins': pins}
    return record


def _changed(saved, current):
    keys = sorted(key for key in set(saved) | set(current) if saved.get(key) != current.get(key))
    return ', '.join(keys) or 'the frontier'


def _problem(human, state, record):
    """Why this record cannot be evaluated against the current run, or None."""
    try:
        resolver = state['resolver']
        rid = record['request_id']
        entry = resolver['human_escalations'][rid]
        identity = entry['identity']
        proposal = identity['proposal']
        response = entry.get('response') or {}
        token = util.digest({'request_id': rid, 'binding': identity['binding']})
        if (record.get('version') != VERSION or util.digest(identity) != rid
                or identity.get('issuer') != 'resolver' or proposal.get('scope') != 'operational_exhaustion'
                or entry.get('status') != 'consumed' or util.digest(response) != record['response']
                or response.get('actor') != 'user_cli' or response.get('action') != 'provide_information'
                or response.get('request_id') != rid
                or not (response.get('request_token') == record['request_token'] == token)
                or (resolver.get('human_response_resolutions') or {}).get(rid, {}).get('response') != response):
            return 'its request, token or response no longer match the saved AutoResolver records'
        frontier = resolver.get('human_response_frontier') or {}
        if frontier.get('request_id') != rid or frontier.get('binding') != record['binding']:
            return 'a newer response or request replaced it'
        if human.current(state) or state.get(human.PRIVATE) or state.get('pending_questions'):
            return 'another AutoResolver request is open'
        if state.get('status') != record['pause_status']:
            return f"the run is no longer at {record['pause_status']}"
        binding = human._binding(state)
        if binding != record['binding']:
            return 'the run changed after the response (' + _changed(record['binding'], binding) + ')'
        receipt, pins = _receipt_pins(resolver, record['receipt_id'])
        if (not human._evidence_valid(proposal) or receipt != record['receipt'] or pins != record['pins']
                or (record['receipt_id'] and not pins)):
            return "the request's recorded evidence changed"
    except (KeyError, TypeError, AttributeError, ValueError):
        return 'its saved records are malformed'
    return None


def _attempt(active):
    if active.get('output') and isinstance(active.get('iteration'), int):
        return f"{active['iteration']:03d}/{Path(active['output']).stem}"
    return None


def operator_flags(state, cause):
    """The control named for an operator-only stop, only where the CLI accepts it (#288)."""
    if cause == 'PAUSED_ORCHESTRATOR_WORKER':
        batch = state.get('orchestration_batch') or {}
        members = [row['milestone_id'] for row in batch.get('workers') or [] if isinstance(row, dict)
                   and isinstance(row.get('milestone_id'), str)
                   and str(row.get('status', '')).startswith(('PAUSED_', 'FAILED', 'BLOCKED', 'INTERRUPTED'))]
        usable = state.get('next_stage') == 'orchestrator' and batch.get('status') == 'BUILDING'
        return ' '.join(['--resume-paused', *('--retry-builder ' + mid for mid in members)]) if usable and members else None
    if cause == 'PAUSED_REPEATED_FAILURE' and any(
            state.get(key) for key in ('pending_report_repair', 'active_runner_check')):
        return None  # --retry-failed-stage refuses until the pending repair is reconciled.
    if cause == 'PAUSED_BUILDER_RETRY_LIMIT' and not (
            state.get('next_stage') == 'terra' and any(
                isinstance(lane, dict) and lane.get('action') == 'pause' and lane.get('failures')
                for lane in (state.get('builder_retries') or {}).values())):
        return None  # --retry-builder needs the stopped serial Builder lane.
    return resume_flags(state, cause)


def _stalled_validation(runner, probe, workspace):
    """The validation-only rounds stop the Validator gate would make next, or None (autopilot.admit_validation)."""
    if probe.get('next_stage') != 'sol':
        return None
    blocking = findings_ledger.blocking_entries(probe)
    if not blocking:
        return None
    revision = source_scope.snapshot(Path(workspace), probe, base_snapshot=runner.support.snapshot)['revision']
    stop = validation_rounds.admit(probe, blocking, revision)
    return stop['reason'] if stop else None


def decide(runner, state, run_dir, workspace, cause):
    """AutoResolver's decision under existing authority: (action, reason, flags, adopted status)."""
    Paused = runner.support.Paused
    active = state.get('active_stage') or {}
    if active:
        attempt = _attempt(active)
        return ('hold', f'the stopped attempt {attempt or "on record"} is still unreconciled; '
                'information cannot settle what it did', f'--abandon-stage {attempt}' if attempt else None, None)
    if state.get('uncertain_artifacts'):
        return 'hold', 'a partial stage remains unreconciled: ' + str(state['uncertain_artifacts']), None, None
    member = member_stop.answered(state)
    if member:  # A batch member's model stop continues only by its own control (#541).
        return 'hold', member_stop.reason(member), member_stop.action(state), None
    if cause in OPERATOR_ONLY:
        return 'hold', OPERATOR_ONLY[cause], operator_flags(state, cause), None
    if cause not in INFORMATION_CAUSES:
        return 'hold', f'no rule lets information release a {cause} stop', None, None
    probe = copy.deepcopy(state)
    try:
        # Bounds the run set itself, whatever status the stop carries (AutoResolver publishes both as
        # PAUSED_RESOLVER). An admitted continuation renews neither, so it could only stop again.
        pending = probe.get('pending_report_repair')
        if isinstance(pending, dict) and pending.get('attempts', 0) >= runner.repair_limit(probe):
            return 'hold', OPERATOR_ONLY['PAUSED_REPORT_REPAIR_LIMIT'], None, None
        stalled = _stalled_validation(runner, probe, workspace)
        if stalled:
            return ('hold', stalled.rstrip('.') + '. Only that reviewer\'s fresh report closes a finding; close one that '
                    'no longer applies with --close-finding ID --close-reason TEXT, or revise the goal with --feedback',
                    None, None)
        if cause == 'PAUSED_PLANNING_BUDGET':
            planning = probe.get('planning') or {}
            limit = runner.planning.review_call_limit(probe)
            if (limit and planning.get('astra_calls', 0) >= limit
                    and not runner.resolver_runtime.operational_boundary(runner, probe, run_dir, workspace,
                                                                         persist=False)):
                return ('hold', 'planning used its review allowance and no reserved recovery remains',
                        '--planning-review-call-limit N', None)
        elif probe.get('next_stage') in REVIEW_STAGES:
            probe.update(status='RUNNING')
            runner.resolver_runtime.operational_boundary(runner, probe, run_dir, workspace, persist=False)
        runner.timeout_recovery_guard(probe)
    except Paused as error:
        if error.status == 'PAUSED_TIMEOUT_RECOVERY':
            return 'hold', str(error), resume_flags(state, error.status), error.status
        return 'hold', str(error), None, None
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, OSError, RuntimeError,
            subprocess.SubprocessError) as error:
        return 'hold', f'its admission checks cannot be evaluated: {error}', None, None
    return 'continue', None, None, None


def _resume_after(state, cause):
    """The resume that follows setting a stopped attempt aside.

    After a content-filter refusal it names a model change, since the same model would likely refuse
    again (quota_route.advice; #464/#465). A quota stop keeps the plain resume: the quota can reset.
    """
    active = state.get('active_stage') or {}
    role = active.get('route_role') or active.get('role')
    if cause == quota_route.REFUSAL_STATUS and role in quota_route.ROLES:
        return f'--resume-paused {quota_route.flag(role)} MODEL'
    return '--resume-paused'


def _message(record, action, reason, flags, after, run_dir, workspace):
    rid = record['request_id'][:12]
    if action == 'continue':
        return (f"AutoResolver re-evaluated the information sent for request {rid}: it found no spent bound or "
                f"operator-only control among those it checks for this {record['cause']} pause, so the run continues "
                "through the normal admission checks (limits, permissions, transport, source and approval still "
                "apply). The information renewed no allowance: a stop found there, such as AutoResolver's own "
                "per-incident limit or a parallel member that still needs a model, is a new request.")
    text = (f"AutoResolver re-evaluated the information sent for request {rid}: {reason.rstrip('.')}. "
            "Information cannot raise a bound, reset a count or authorize another attempt, so the run stays "
            "paused and no provider launched.")
    if flags:
        where = f"--workspace {shlex.quote(str(workspace))} --run-dir {shlex.quote(str(run_dir))}"
        then = '.' if flags.startswith('--resume-paused') else f", then autocode resume{after[len('--resume-paused'):]}."
        text += f" Next command: autocode {flags} {where}{then}"
    else:
        text += ' Change the cause it names; AutoResolver then asks again for the changed run.'
    return text


def _view_action(flags, after):
    if not flags:
        return None
    return flags if flags.startswith('--resume-paused') else f'{flags} then {after}'


def _current_record(state):
    resolver = state.get('resolver') if isinstance(state.get('resolver'), dict) else {}
    reviews = resolver.get(KEY) if isinstance(resolver.get(KEY), dict) else {}
    frontier = resolver.get('human_response_frontier') if isinstance(resolver.get('human_response_frontier'), dict) else {}
    record = reviews.get(frontier.get('request_id'))
    return record if isinstance(record, dict) else None


def reevaluate(runner, state, run_dir, workspace, *, resume):
    """Consume the scheduled review once at an explicit continuation; None when none applies.

    The caller holds the run lock and calls this only for an invocation without another
    decision or recovery action. Nothing here launches a provider or changes a limit or count.
    """
    record = _current_record(state)
    if record is None:
        return None
    human = runner.resolver_human
    status = record.get('status')
    if status in ('held', 'admitted'):
        try:
            same = (state.get('status') == record['evaluated_status']
                    and human._binding(state) == record['evaluated_binding'])
        except (KeyError, TypeError, ValueError):
            same = False
        if not same:
            return None
        if status == 'held':
            return Outcome('hold', record['decision']['message'])
        if state.get('stop_reason') != record['decision']['message']:
            return None  # The run moved on from this admission; resuming cleared its reason.
        if resume:  # Saved but not yet carried into the run: continue on that one decision.
            return Outcome('continue', 'AutoResolver already admitted this continuation for request '
                           f"{record['request_id'][:12]}; it is not evaluated again.")
        return Outcome('pending', 'AutoResolver admitted a continuation for request '
                       f"{record['request_id'][:12]}; run autocode resume to continue. Nothing launched.")
    if status != 'pending':
        return None
    if state.get('status') != record.get('pause_status'):
        return None  # The run left that pause another way (an explicit control); nothing to evaluate.
    if not resume:
        if not human.response_holds_current_frontier(state):
            return None
        step = member_stop.next_step(state)
        return Outcome('pending', 'AutoResolver has the information sent for request '
                       f"{str(record.get('request_id'))[:12]}" + (f'. {step} Nothing launched.' if step else
                       ' and re-evaluates it once at the next autocode resume (--resume-paused). Nothing launched.'))
    state_path = Path(run_dir) / 'state.json'
    problem = _problem(human, state, record)
    if problem:
        record.update(status='stale', retired_at=util.now(), retired_reason=problem)
        runner.write_json(state_path, state)
        return Outcome('stale', retired(state) + ' No provider launched for it.')
    try:
        with runner.interventions.admission(run_dir):
            candidate = copy.deepcopy(state)
            saved = _current_record(candidate)
            action, reason, flags, adopted = decide(runner, candidate, run_dir, workspace, record['cause'])
            after = _resume_after(candidate, record['cause'])
            message = _message(saved, action, reason, flags, after, run_dir, workspace)
            decision = {'action': action, 'reason': reason, 'flags': flags, 'view_action': _view_action(flags, after),
                        'message': message}
            evaluated = {'scope': 'operational_information_review', 'request_id': saved['request_id'],
                         'response': saved['response'], 'cause': saved['cause'], 'binding': saved['binding'],
                         'receipt_id': saved['receipt_id'], 'pins': saved['pins'], 'decision': decision}
            identity = util.digest(evaluated)
            path = Path(run_dir) / 'resolver' / f'information-{identity}.json'
            util.atomic_json(path, {'stage': 'resolver', 'role': 'resolver', 'engine': 'runner',
                                    'runner_owned': True, 'runner_calls': 0, 'finished_at': util.now(),
                                    'metrics': {'provider_tokens': {'input_tokens': 0, 'output_tokens': 0}},
                                    'decision': {'action': action, 'rationale': message},
                                    'receipt': {'id': identity, **evaluated}})
            if adopted:
                candidate['status'] = adopted
            candidate['stop_reason'] = message
            if action == 'continue':
                # A fresh frontier: a stop the guarded path finds next is a new request, never
                # the answered one again.
                candidate.setdefault('user_events', []).append({
                    'kind': 'resolver_information_review', 'actor': 'runner', 'at': util.now(),
                    'request_id': saved['request_id'], 'decision': 'continue', 'receipt': identity})
            saved.update(status='admitted' if action == 'continue' else 'held', evaluated_at=util.now(),
                         decision=decision, receipt_output=str(path),
                         evaluated_status=candidate['status'], evaluated_binding=human._binding(candidate))
            runner.write_json(state_path, candidate)
    except runner.support.Paused:
        return None  # An intervention arrived first; nothing was consumed.
    state.clear()
    state.update(candidate)
    return Outcome(action, message)


def _unscheduled(state):
    """The request ID of corrective information accepted without a scheduled review, or None.

    An AutoCode from before #486 consumed `provide_information` and saved the frontier but no review.
    """
    resolver = state.get('resolver') if isinstance(state.get('resolver'), dict) else {}
    frontier = resolver.get('human_response_frontier') if isinstance(resolver.get('human_response_frontier'), dict) else {}
    rid = frontier.get('request_id')
    reviews = resolver.get(KEY) if isinstance(resolver.get(KEY), dict) else {}
    ledger = resolver.get('human_escalations') if isinstance(resolver.get('human_escalations'), dict) else {}
    entry = ledger.get(rid) if isinstance(rid, str) else None
    if not isinstance(entry, dict) or rid in reviews:
        return None
    response = entry.get('response') if isinstance(entry.get('response'), dict) else {}
    identity = entry.get('identity') if isinstance(entry.get('identity'), dict) else {}
    proposal = identity.get('proposal')
    if (entry.get('status') == 'consumed' and response.get('action') == 'provide_information'
            and isinstance(proposal, dict) and proposal.get('scope') == 'operational_exhaustion'):
        return rid
    return None


def retired(state):
    """Why the current information can no longer be re-evaluated, or None.

    That is a review retired as stale, or information an older AutoCode accepted without scheduling
    one. autocode_run_actions asks again when the run still holds the answered frontier (only the
    records or evidence the answer was bound to changed, or no review exists): holding on that consumed
    request, which cannot be answered again, would be #486 again.
    """
    record = _current_record(state)
    if record is None:
        rid = _unscheduled(state)
        if rid is None:
            return None
        return (f"AutoResolver did not re-evaluate the information sent for request {rid[:12]}: it was "
                "accepted before AutoResolver re-evaluated corrective information, so it was never scheduled.")
    if record.get('status') != 'stale':
        return None
    return (f"AutoResolver did not re-evaluate the information sent for request "
            f"{str(record.get('request_id'))[:12]}: {record.get('retired_reason')}.")


def projection(state):
    """The status view's read of the current review; plain saved data, never authority."""
    record = _current_record(state)
    if record is None:
        return None
    decision = record.get('decision') if isinstance(record.get('decision'), dict) else {}
    status, reason = record.get('status'), decision.get('message') or record.get('retired_reason')
    if status == 'pending' and state.get('status') != record.get('pause_status'):
        # The run left that pause first (another control, or a newer request): reevaluate never takes it.
        status, reason = 'superseded', f"the run left {record.get('pause_status')} before the information was evaluated"
    return {'request_id': record.get('request_id'), 'status': status, 'cause': record.get('cause'),
            'scheduled_at': record.get('scheduled_at'), 'evaluated_at': record.get('evaluated_at'),
            'decision': decision.get('action'), 'reason': reason,
            'action': (decision.get('view_action') if record.get('status') == 'held'
                       and state.get('status') == record.get('evaluated_status')
                       else (member_stop.action(state) or '--resume-paused') if status == 'pending' else None),
            'receipt': record.get('receipt_output')}
