#!/usr/bin/env python3
"""Isolated #610 provider faults; all ordinary stages use the existing fixture."""
import io
import json
import os
import runpy
import shlex
import sys
import time
import uuid
from pathlib import Path


def main():
    original = runpy.run_path(os.environ['FAILURE_ROUTING_PROVIDER'])['main']
    if sys.argv[1:] == ['login', 'status']:
        return original()
    prompt = sys.stdin.read()
    data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
    if data.get('report_repair'):
        data['stage'] = data['original']['stage'] + '_report_repair'
    log = Path(os.environ['FAILURE_ROUTING_TRACE'])
    def record(event, **extra):
        with log.open('a') as stream:
            stream.write(json.dumps(dict(event=event, stage=data['stage'],
                model=sys.argv[sys.argv.index('--model') + 1],
                pid=os.getpid(), argv=sys.argv[1:], workspace=str(Path.cwd()), handoff=data, **extra)) + '\n')
    record('call')
    if data['stage'] == 'investigate_stuck_report_repair':
        result = json.loads(Path(data['original']['output']).read_text())
        if os.environ.get('FAILURE_ROUTING_MALFORMED_DIAGNOSIS'):
            # Repair report format only, preserving the pinned cause/ID/refs/probe.
            result['example'] = result['diagnosis']
        print(json.dumps({'type': 'thread.started', 'thread_id': str(uuid.uuid4())}), flush=True)
        Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
        record('preserved_diagnosis_repair', report=result)
        print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 10}}), flush=True)
        return
    failure = data.get('builder_failure')
    if data['stage'] == 'investigate_stuck' and failure:
        choice = os.environ.get('FAILURE_ROUTING_DIAGNOSIS', 'unknown')
        refs = failure['evidence_refs']
        report = Path(failure['record']['output'])
        if choice in ('execution', 'stale'):
            diagnosis = 'The prescribed check actually failed on the unchanged implementation.'
            condition = "assert 'check exit=1' in r['results']"
        elif choice == 'plan':
            diagnosis = 'The approved milestone prints goodbye, but the original goal requires hello. The milestone check passes without satisfying that goal.'
            condition = "assert 'check exit=0' in r['results']"
        else:
            diagnosis = 'The Builder returned without a source change. This alone does not establish an execution defect or a faulty approach.'
            condition = ''
        probe = shlex.join([sys.executable, '-c',
            "import json; r=json.load(open(" + repr('run/' + report.name) + ")); " + condition]) if condition else ''
        needs_user = bool(os.environ.get('FAILURE_ROUTING_NEEDS_USER'))
        routable = choice in ('execution', 'plan', 'stale') and not needs_user
        result = dict(diagnosis=diagnosis, cause='needs_user' if needs_user else 'other',
            guidance=diagnosis if routable else '', recommendation='retry' if routable else 'pause',
            user_question='', evidence_refs=[str(report)], example=diagnosis,
            probe=probe, untestable='' if probe else 'An empty source diff cannot distinguish the cause.',
            failure_class='execution' if choice == 'stale' else choice,
            failure_id='stale-incident' if choice == 'stale' else failure['failure_id'])
        if needs_user:
            result['user_question'] = 'Should this run stop or continue with the existing approved scope?'
        assert str(report) in refs
        record('diagnosis', report=result)
        session = sys.argv[sys.argv.index('resume') + 1] if 'resume' in sys.argv else str(uuid.uuid4())
        print(json.dumps({'type': 'thread.started', 'thread_id': session}), flush=True)
        if os.environ.get('FAILURE_ROUTING_HOLD'):
            (log.parent / 'investigator-ready').write_text(str(os.getpid()))
            deadline = time.monotonic() + 30
            while not (log.parent / 'release-investigator').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            if not (log.parent / 'release-investigator').exists():
                raise SystemExit(8)
        delivered = dict(result)
        if os.environ.get('FAILURE_ROUTING_MALFORMED_DIAGNOSIS'):
            delivered.pop('example')
        Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(delivered))
        print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 10}}), flush=True)
        return
    sys.stdin = io.StringIO(prompt)
    correcting = os.environ.get('FAILURE_ROUTING_REWORK') == 'correct'
    corrected_objective = 'Correct original goal: CLI prints hello instead of goodbye'
    corrected_check = "import subprocess,sys; assert subprocess.check_output([sys.executable,'main.py'],text=True) == 'hello\\n'"
    corrected = (data.get('current_task') or {}).get('objective') == corrected_objective
    if correcting and corrected:
        # Scripted model follows the fresh task's corrected approach. This is a
        # provider input fixture, never a contract or runner-state replacement.
        spec = json.loads(Path(os.environ['BUILD_AUDIT_SPEC']).read_text())
        spec['payloads']['M1'] = {'main.py': "print('hello')\n"}
        spec['checks']['M1'] = corrected_check
        spec['complete_product'] = True
        spec['contract']['milestones'][0]['objective'] = corrected_objective
        candidate_spec = log.parent / ('corrected-provider-' + str(os.getpid()) + '.json')
        candidate_spec.write_text(json.dumps(spec))
        os.environ['BUILD_AUDIT_SPEC'] = str(candidate_spec)
    original()
    rework = os.environ.get('FAILURE_ROUTING_REWORK')
    if (data['stage'] in ('astra_review', 'astra_resolve') and rework == 'unchanged'
            or data['stage'] in ('astra_review', 'astra_resolve') and correcting and not corrected):
        output = Path(sys.argv[sys.argv.index('-o') + 1])
        result = json.loads(output.read_text())
        result['status'] = 'REWORK'
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        investigation = next(row for row in rows if row['event'] == 'diagnosis')
        result['evidence'] = investigation['report']['evidence_refs']
        if correcting:
            result['next_objective'] = corrected_objective
            result['next_task'].update(kind='implement', milestone_id='M1',
                requirements=['Print hello to satisfy the original goal, not goodbye'],
                acceptance_criteria=['C1'], validation_plan=[corrected_check], findings=[])
        record('corrected_rework' if correcting else 'unchanged_rework', report=result)
        output.write_text(json.dumps(result))


if __name__ == '__main__':
    main()
