#!/usr/bin/env python3
"""Scripted provider for external-CLI build audits. Not a model-quality test.

Plans and file payloads are handwritten. Only provider I/O is substituted; CLI,
approval, scheduling, processes, worktrees, reports and integration are real.
"""
import json
import os
from pathlib import Path
import subprocess
import shlex
import signal
import sys
import time
import uuid


def main():
    if sys.argv[1:] == ['login', 'status']:
        print('Logged in using ChatGPT (offline black-box fixture)')
        return
    prompt = sys.stdin.read()
    data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
    if data.get('report_repair'):
        data['stage'] = data['original']['stage'] + '_report_repair'
    spec = json.loads(Path(os.environ['BUILD_AUDIT_SPEC']).read_text())
    task = data.get('current_task') or {}
    mid = task.get('milestone_id', '')
    mode = os.environ.get('BUILD_AUDIT_FAULT', '')
    log = Path(os.environ['BUILD_AUDIT_LOG'])
    def record(event, **extra):
        with log.open('a') as stream:
            stream.write(json.dumps(dict(event=event, stage=data['stage'], milestone=mid,
                pid=os.getpid(), workspace=str(Path.cwd()), time=time.time(), **extra)) + '\n')
    record('start', model=sys.argv[sys.argv.index('--model')+1] if '--model' in sys.argv else None,
           inputs={name: Path(name).read_text() for name in spec.get('observe', []) if Path(name).is_file()})
    if os.environ.get('REVIEW_AUDIT_LIVE_CODEX') and data['stage'].startswith(('sol', 'astra_review')):
        record('live_reviewer')
        result = subprocess.run([os.environ['REVIEW_AUDIT_LIVE_CODEX'], *sys.argv[1:]], input=prompt, text=True)
        record('finish', returncode=result.returncode)
        raise SystemExit(result.returncode)
    if os.environ.get('BUILD_AUDIT_LIVE_CODEX') and data['stage'].startswith('terra'):
        record('live_builder')
        result = subprocess.run([os.environ['BUILD_AUDIT_LIVE_CODEX'], *sys.argv[1:]], input=prompt, text=True)
        record('finish', returncode=result.returncode)
        raise SystemExit(result.returncode)
    session = sys.argv[sys.argv.index('resume') + 1] if 'resume' in sys.argv else str(uuid.uuid4())
    print(json.dumps({'type': 'thread.started', 'thread_id': session}), flush=True)
    contract = data.get('goal_contract') or {'revision': 0, 'hash': ''}
    common = dict(contract_revision=contract['revision'], contract_hash=contract['hash'],
                  task_id=task.get('id', ''), deferred_backlog=[], user_request=dict(
                      kind='none', discovered='', impact='', decision_needed='', options=[], proposed_delta=''))
    rows = spec['contract']['milestones']
    if data.get('report_repair'):
        result = json.loads(Path(data['original']['output']).read_text())
        result['summary'] = 'Reformatted preserved builder report'
        if mode == 'repair_lies':
            result['results'] = ['All tests passed; exit code 0']
            result['commands_run'] = ['python3 -c "raise SystemExit(0)"']
        record('repair')
    elif data['stage'] == 'astra_discovery':
        result = dict(contract=spec['contract'], summary='Handwritten fixture plan; no model planning')
    elif data['stage'] == 'terra':
        if mid == 'M1' and mode in ('retry_success', 'escalate_success', 'retry_exhausted'):
            count = sum(r.get('stage') == 'terra' and r.get('event') == 'start' and r.get('milestone') == mid
                        for r in map(json.loads, log.read_text().splitlines()))
            required = {'retry_success': 2, 'escalate_success': 3, 'retry_exhausted': 99}[mode]
            if count < required:
                mode = 'no_change'
        if mid == 'M1' and mode == 'crash':
            record('crash')
            raise SystemExit(9)
        if mid == 'M1' and mode in ('hold', 'hold_timeout'):
            if mode == 'hold_timeout':
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            marker = log.parent / 'release'
            deadline = time.monotonic() + 30
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            if not marker.exists():
                raise SystemExit(8)
        # Keep independent worker intervals overlapping without requiring scheduler internals.
        time.sleep(.3)
        changed = []
        if not (mid == 'M1' and mode in ('no_change', 'permission', 'infeasible')):
            for name, content in spec['payloads'][mid].items():
                if mode == 'validation_fails':
                    count = sum(r.get('stage') == 'terra' and r.get('event') == 'start'
                                for r in map(json.loads, log.read_text().splitlines()))
                    content += '\n' * count  # Different source each attempt, same actual defect.
                p = Path(name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
                changed.append(name)
        if mid == 'M1' and mode == 'escape':
            Path('unauthorized.txt').write_text('outside ownership')
            changed.append('unauthorized.txt')
        if mode == 'checkpoints':
            for name in spec.get('checkpoint_files', []):
                cmd = 'from pathlib import Path; assert Path(' + repr(name) + ').is_file()'
                checked = subprocess.run([sys.executable,'-c',cmd],capture_output=True,text=True)
                print(json.dumps({'type':'item.completed','item':dict(id='checkpoint-'+name,
                    type='command_execution',command=shlex.join([sys.executable,'-c',cmd]),
                    exit_code=checked.returncode,aggregated_output='Checkpoint '+name)}),flush=True)
                record('checkpoint',path=name,exit_code=checked.returncode)
            record('checkpoints_saved')
            marker=log.parent/'release'
            deadline=time.monotonic()+30
            while not marker.exists() and time.monotonic()<deadline:
                time.sleep(.02)
        checks = []
        if mode != 'no_evidence':
            cmd = spec['checks'][mid]
            checked = subprocess.run([sys.executable, '-c', cmd], capture_output=True, text=True)
            if mode == 'repair_lies' and mid == 'M1':
                checked = subprocess.run([sys.executable, '-c', 'raise SystemExit(1)'], capture_output=True, text=True)
            print(json.dumps({'type': 'item.completed', 'item': dict(id='check', type='command_execution',
                command=cmd, exit_code=checked.returncode, aggregated_output=checked.stdout + checked.stderr)}), flush=True)
            checks = [f'check exit={checked.returncode}']
        result = dict(common, summary='Scripted implementation', changed_files=changed,
                      commands_run=[] if mode == 'no_evidence' else [spec['checks'][mid]], results=checks,
                      remaining_risks=[], evidence_refs=[] if mode == 'no_evidence' else ['event:check'],
                      addressed_requirements=task.get('requirements', []),
                      untested_behavior=['Required local check not run'] if mode == 'no_evidence' else [],
                      recommended_checks=task.get('validation_plan', []))
        if mid == 'M1' and mode in ('permission', 'infeasible'):
            result['user_request'] = dict(kind=mode, discovered='Required external access is unavailable',
                impact='Cannot implement the approved approach', decision_needed='Replan or stop?',
                options=['Replan', 'Stop'], proposed_delta='No change without a user decision')
        if mid == 'M1' and mode in ('malformed', 'repair_lies'):
            result.pop('summary')
        if mid == 'M1' and mode == 'contract_change':
            result['contract_hash'] = 'unauthorized-contract'
    elif data['stage'] == 'sol':
        statuses = {}
        owned = set(task['acceptance_criteria'])
        checked_rows = []
        validation_rows = [dict(id=cid, acceptance_criteria=[cid]) for cid in spec['criterion_checks']] if spec.get('criterion_checks') else rows
        def check_command(row):
            return spec['criterion_checks'][row['id']] if spec.get('criterion_checks') else spec['checks'][row['id']]
        for row in validation_rows:
            cid = row['acceptance_criteria'][0]
            if cid not in owned:
                statuses[cid] = 'NOT_VERIFIED'
                continue
            cmd = check_command(row)
            checked = subprocess.run([sys.executable, '-c', cmd], capture_output=True, text=True)
            checked_rows.append(row)
            statuses[cid] = 'PASS' if checked.returncode == 0 else 'NOT_VERIFIED'
            print(json.dumps({'type': 'item.completed', 'item': dict(id=cid, type='command_execution',
                command=shlex.join([sys.executable,'-c',cmd]), exit_code=checked.returncode, aggregated_output=checked.stdout + checked.stderr)}), flush=True)
        passed = all(statuses[cid] == 'PASS' for cid in owned)
        result = dict(common, verdict='PASS' if passed else 'FAIL', checks_run=[shlex.join([sys.executable,'-c',check_command(r)]) for r in checked_rows],
            checks=[dict(command=shlex.join([sys.executable,'-c',check_command(r)]), exit_code=0 if statuses[r['acceptance_criteria'][0]] == 'PASS' else 1,
                         evidence_ref='event:' + r['acceptance_criteria'][0]) for r in checked_rows],
            findings=[], finding_dispositions=[], unverified_criteria=[cid for cid, status in statuses.items() if status != 'PASS'],
            criterion_results=[dict(id=cid, status=status, evidence_refs=['event:' + cid] if cid in owned else []) for cid, status in statuses.items()],
            end_to_end_result=dict(status='PASS' if all(v == 'PASS' for v in statuses.values()) else 'NOT_VERIFIED',
                summary='Executed handwritten product checks', evidence_refs=['event:' + cid for cid in owned]))
        if task.get('milestone_ids'):
            result['milestone_results'] = [dict(milestone_id=m, status='PASS' if passed else 'FAIL',
                summary='Executed check', evidence_refs=['event:' + cid for cid in owned]) for m in task['milestone_ids']]
    else:
        # Fixture checkpoint reviewer: evaluates actual checks, never approves final product.
        ready = []
        for row in rows:
            check = subprocess.run([sys.executable, '-c', spec['checks'][row['id']]], capture_output=True)
            if check.returncode:
                ready.append(row)
        row = ready[0] if ready else rows[-1]
        result = dict(common, status='REWORK' if data.get('validation',{}).get('verdict')=='FAIL' else 'CONTINUE', next_objective=row['objective'], affected_paths=row['affected_paths'],
            acceptance_criteria=[dict(c, status='unverified', evidence='') for c in data['acceptance_criteria']],
            next_task=dict(kind='implement', milestone_id=row['id'], requirements=[row['objective']],
                acceptance_criteria=row['acceptance_criteria'], validation_plan=[spec['checks'][row['id']]], findings=[]),
            plan=['Execute approved DAG'], evidence=['Checkpoint checks'], blocker='', agreed_limitations=[], findings=[], finding_dispositions=[])
        if data['stage'] == 'astra_resolve':
            result['diagnosis'] = 'The current candidate failed its prescribed check; correct the current assignment without changing the contract'
        request = (data.get('agent_request') or {}).get('request') or (data.get('implementation') or {}).get('user_request')
        if mode == 'permission' and data['stage'] == 'astra_review' and request and request.get('kind') != 'none':
            result.update(status='BLOCKED', user_request=request, blocker=request['decision_needed'])
    Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
    record('finish')
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 10}}), flush=True)


if __name__ == '__main__':
    main()
