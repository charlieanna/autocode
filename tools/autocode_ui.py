#!/usr/bin/env python3
"""Editable Figma design with Astra → Terra → Sol → Astra, then Autocode handoff."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
try:
    from . import autocode_figma as figma, autocode_support as support, autocode_workspaces as workspaces
    from . import autocode_orchestrator as orchestrator
except ImportError:
    import autocode_figma as figma
    import autocode_support as support
    import autocode_workspaces as workspaces
    import autocode_orchestrator as orchestrator

DEFAULT_MODELS = {'astra': 'gpt-6-astra', 'terra': 'gpt-5.6-terra', 'sol': 'gpt-5.6-sol'}


def report_schema(statuses):
    return {'type': 'object', 'additionalProperties': False,
            'required': ['status', 'figma_file', 'summary', 'evidence', 'required_changes'],
            'properties': {'status': {'type': 'string', 'enum': statuses},
                           'figma_file': {'type': 'string'}, 'summary': {'type': 'string'},
                           'evidence': {'type': 'array', 'items': {'type': 'string'}},
                           'required_changes': {'type': 'array', 'items': {'type': 'string'}}}}


def prompt(role, task, run_dir, target, inputs):
    shared = (f'Autocode UI task: {task}\nRun artifacts: {run_dir}\n'
              f'Figma target: {target or "Create a new editable Figma Design file"}\n'
              'Use the connected Figma plugin through Codex and the configured ChatGPT model. '
              'Load the applicable Figma skills before using its tools. Build native editable Figma nodes, '
              'components and variables. Figma Make is not assumed to be callable. '
              'Only Terra may modify Figma. Do not build a local application or change project source. '
              'The runner saves your final output. Only write temporary evidence under the run directory.\n'
              + '\n'.join(f'{name}: {path}' for name, path in inputs.items()) + '\n')
    if role == 'astra-brief':
        return shared + ('Act as Astra. Return a concise UI brief covering users, primary flow, screens, states, '
                         'hierarchy, visual direction, responsive behavior, components, accessibility and acceptance '
                         'checks. Use reasonable defaults for this design task. Do not edit Figma.')
    if role == 'terra':
        return shared + ('Act as Terra. Read the brief and any previous Sol review/Astra rework instructions. '
                         'Create or refine the actual Figma file, reuse design-system components and tokens, '
                         'and inspect screenshots plus node structure. Return COMPLETE only after the edits succeed. '
                         'Report the exact Figma Design URL, inspected node IDs/screens and evidence. '
                         'Return BLOCKED if plugin access or edits fail; do not substitute local HTML or claim success.')
    if role == 'sol':
        return shared + ('Act as Sol. Read the brief and Terra result, then independently inspect the actual Figma '
                         'screenshots and nodes. Audit all required screens/states, visual hierarchy, consistency, '
                         'component reuse, responsiveness and accessibility. Do not edit Figma. Return PASS only '
                         'with concrete evidence and no required_changes. Use FAIL for repairable defects or BLOCKED '
                         'if inspection is unavailable.')
    return shared + ('Act as Astra. Read the brief, Terra result and current Sol audit. Return ACCEPT only if Sol '
                     'passed the actual Figma result and the complete brief is satisfied. Otherwise give bounded '
                     'REWORK instructions or BLOCKED. Include the exact accepted Figma URL and evidence. '
                     'No additional human visual approval is needed for this design task.')


def run_stage(name, model, text, workspace, run_dir, schema=None, dry_run=False):
    output = run_dir / (name + ('.json' if schema else '.md'))
    prompt_path = run_dir / (name + '.prompt.md')
    events = run_dir / (name + '.jsonl')
    prompt_path.write_text(text)
    command = ['codex', 'exec', '-C', str(workspace), '--skip-git-repo-check', '--sandbox', 'workspace-write',
               '--model', model, '-c', 'model_provider="openai"', '--json', '--output-last-message', str(output)]
    if schema:
        schema_path = run_dir / (name + '.schema.json')
        support.atomic_json(schema_path, schema)
        command += ['--output-schema', str(schema_path)]
    command.append('-')
    if dry_run:
        print(f'{name}: dry run; model={model}', flush=True)
        return output, None
    figma.require_chatgpt(support.local_settings())
    print(f'{name}: started; model={model}', flush=True)
    with prompt_path.open() as source, events.open('w') as log:
        result = subprocess.run(command, cwd=workspace, stdin=source, stdout=log, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise ValueError(f'{name} failed (exit {result.returncode}); inspect {events}')
    report = json.loads(output.read_text()) if schema else output.read_text()
    if schema:
        support.validate_schema(report, schema)
        if report['figma_file']:
            figma.design_url(report['figma_file'])
    elif not report.strip():
        raise ValueError('Astra returned an empty UI brief')
    print(f'{name}: completed; output={output}', flush=True)
    return output, report


def execute(args, workspace, run_dir):
    state = {'version': 2, 'task': args.task, 'workspace': str(workspace), 'run_dir': str(run_dir),
             'figma_file': args.figma_file, 'created_at': support.now(), 'status': 'RUNNING',
             'next_stage': 'astra_brief', 'iteration': 0, 'target': 'figma',
             'models': {role: getattr(args, role + '_model') for role in DEFAULT_MODELS},
             'stages': [], 'outputs': {}, 'reports': {}}
    def save():
        support.atomic_json(run_dir / 'state.json', state)
    names = {'astra_brief': lambda: 'astra-brief',
             'terra': lambda: f'terra-{state["iteration"]:02}',
             'sol': lambda: f'sol-{state["iteration"]:02}',
             'astra_review': lambda: f'astra-decision-{state["iteration"]:02}'}
    roles = {'astra_brief': 'astra-brief', 'terra': 'terra', 'sol': 'sol', 'astra_review': 'astra-decision'}
    statuses = {'terra': ['COMPLETE', 'BLOCKED'], 'sol': ['PASS', 'FAIL', 'BLOCKED'],
                'astra_review': ['ACCEPT', 'REWORK', 'BLOCKED']}

    def dispatch(current, stage):
        name, role = names[stage](), roles[stage]
        current['active_stage'] = {'stage': stage, 'name': name, 'iteration': current['iteration']}
        save()
        inputs = {key: Path(value) for key, value in current['outputs'].items()}
        model_role = 'astra' if role.startswith('astra') else role
        return name, role, run_stage(name, current['models'][model_role],
                                    prompt(role, args.task, run_dir, current['figma_file'], inputs),
                                    run_dir, run_dir, report_schema(statuses[stage]) if stage in statuses else None,
                                    args.dry_run)

    def apply(current, stage, outcome):
        name, role, (output, report) = outcome
        current['stages'].append({'stage': stage, 'role': role, 'name': name,
                                  'iteration': current['iteration'], 'output': str(output),
                                  'finished_at': support.now()})
        current.pop('active_stage', None)
        if stage == 'astra_brief':
            current['outputs']['brief'] = str(output)
            current['next_stage'] = 'terra'
            return
        current['reports'][stage] = report
        current['outputs'][{'terra': 'terra', 'sol': 'sol', 'astra_review': 'astra'}[stage]] = str(output)
        if args.dry_run:
            if stage == 'astra_review':
                current.update(status='DRY_RUN', next_stage=None, finished_at=support.now())
            else:
                current['next_stage'] = {'terra': 'sol', 'sol': 'astra_review'}[stage]
            return
        if stage == 'terra':
            if report['status'] != 'COMPLETE' or not report['figma_file'] or not report['evidence'] or report['required_changes']:
                raise ValueError('Terra did not produce a completed editable Figma result')
            if current['figma_file'] and url_key(report['figma_file']) != url_key(current['figma_file']):
                raise ValueError('Terra returned a different Figma file than the selected target')
            current.update(figma_file=report['figma_file'], next_stage='sol')
            return
        if stage == 'sol':
            if report['status'] == 'BLOCKED':
                raise ValueError('Figma review is blocked; inspect the saved reports')
            current['next_stage'] = 'astra_review'
            return
        audit = current['reports']['sol']
        if report['status'] == 'BLOCKED':
            raise ValueError('Figma review is blocked; inspect the saved reports')
        accepted = (audit['status'] == 'PASS' and report['status'] == 'ACCEPT'
                    and all(item['evidence'] and not item['required_changes']
                            and item['figma_file'] == current['figma_file'] for item in (audit, report)))
        if accepted:
            refs = {key: Path(current['outputs'][key]) for key in ('brief', 'terra', 'sol', 'astra')}
            handoff = {'version': 1, 'task': args.task, 'figma_file': current['figma_file'],
                       'artifacts': {key: {'path': path.name, 'sha256': support.file_hash(path)}
                                     for key, path in refs.items()}}
            support.atomic_json(run_dir / 'handoff.json', handoff)
            current.update(status='COMPLETE', next_stage=None, finished_at=support.now())
        elif current['iteration'] >= args.max_reworks:
            current.update(status='REWORK_REQUIRED', next_stage=None, finished_at=support.now())
        else:
            current['iteration'] += 1
            current['next_stage'] = 'terra'

    save()
    try:
        orchestrator.drive(state, dispatch, apply=apply, persist=lambda _: save())
    except (OSError, ValueError, KeyError, KeyboardInterrupt) as error:
        state.update(status='BLOCKED', error=str(error), finished_at=support.now())
        save()
        print(f'Autocode UI stopped: {error}. Saved artifacts: {run_dir}', file=sys.stderr)
        return 2
    print(f'Autocode UI {state["status"]}: {run_dir}', flush=True)
    if state['status'] == 'COMPLETE':
        figma.load_handoff(run_dir)
        print(f'Figma: {state["figma_file"]}', flush=True)
        if args.build:
            command = [sys.executable, str(Path(__file__).with_name('autocode.py')), args.task,
                       '--workspace', str(workspace), '--ui-run', str(run_dir), '--engine', 'codex']
            if args.no_chat:
                command.append('--no-chat')
            for role in DEFAULT_MODELS:
                command += [f'--{role}-model', getattr(args, role + '_model')]
            return subprocess.run(command, cwd=workspace).returncode
    return 0 if state['status'] in ('COMPLETE', 'DRY_RUN') else 3


def url_key(url):
    return url.split('/design/', 1)[1].split('/', 1)[0].split('?', 1)[0].split('#', 1)[0]


def cli(argv=None):
    parser = argparse.ArgumentParser(description='Create editable Figma UI with Astra, Terra and Sol; optionally hand it to Autocode.')
    parser.add_argument('task')
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--figma-file')
    parser.add_argument('--run-dir', type=Path, help='New artifact directory (existing runs are never overwritten)')
    for role, default in DEFAULT_MODELS.items():
        parser.add_argument(f'--{role}-model', default=default)
    parser.add_argument('--max-reworks', type=int, default=2)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--build', action='store_true', help='Pass the accepted Figma result to Autocode for implementation')
    parser.add_argument('--no-chat', action='store_true', help='Present the implementation brief without interactive input')
    args = parser.parse_args(argv)
    if args.max_reworks < 0:
        parser.error('--max-reworks must be nonnegative')
    if any(not re.fullmatch(r'gpt-[a-zA-Z0-9.-]+', getattr(args, role + '_model')) for role in DEFAULT_MODELS):
        parser.error('Autocode UI requires ChatGPT GPT models through Codex')
    if args.figma_file:
        try:
            args.figma_file = figma.design_url(args.figma_file)
        except ValueError as error:
            parser.error(str(error))
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        parser.error('workspace must be an existing directory')
    if args.build:
        try:
            if Path(workspaces.git(workspace, 'rev-parse', '--show-toplevel')).resolve() != workspace:
                raise ValueError('Select the root of a Git checkout')
            workspaces.git(workspace, 'rev-parse', '--verify', 'HEAD')
        except ValueError:
            parser.error('--build requires a committed Git workspace')
    name = dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:10]
    run_dir = args.run_dir.resolve() if args.run_dir else workspace / '.autocode-ui/runs' / name
    if run_dir.exists():
        parser.error('Run directory already exists; use autocode --ui-run to build an accepted result')
    run_dir.mkdir(parents=True)
    return execute(args, workspace, run_dir)


if __name__ == '__main__':
    raise SystemExit(cli())
