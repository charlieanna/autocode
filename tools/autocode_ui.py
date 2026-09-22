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
except ImportError:
    import autocode_figma as figma
    import autocode_support as support
    import autocode_workspaces as workspaces

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
             'models': {role: getattr(args, role + '_model') for role in DEFAULT_MODELS}, 'stages': []}
    def save():
        support.atomic_json(run_dir / 'state.json', state)
    def stage(name, role, inputs, statuses=None):
        state['active_stage'] = name
        save()
        output, report = run_stage(name, state['models']['astra' if role.startswith('astra') else role],
                                   prompt(role, args.task, run_dir, state['figma_file'], inputs),
                                   run_dir, run_dir, report_schema(statuses) if statuses else None, args.dry_run)
        state['stages'].append({'name': name, 'output': str(output), 'finished_at': support.now()})
        state.pop('active_stage')
        save()
        return output, report
    save()
    try:
        brief, _ = stage('astra-brief', 'astra-brief', {})
        inputs = {'brief': brief}
        for round_number in range(args.max_reworks + 1):
            terra, built = stage(f'terra-{round_number:02}', 'terra', inputs, ['COMPLETE', 'BLOCKED'])
            if built:
                if built['status'] != 'COMPLETE' or not built['figma_file'] or not built['evidence'] or built['required_changes']:
                    raise ValueError('Terra did not produce a completed editable Figma result')
                if state['figma_file'] and url_key(built['figma_file']) != url_key(state['figma_file']):
                    raise ValueError('Terra returned a different Figma file than the selected target')
                state['figma_file'] = built['figma_file']
            inputs.update(terra=terra)
            sol, audit = stage(f'sol-{round_number:02}', 'sol', inputs, ['PASS', 'FAIL', 'BLOCKED'])
            inputs.update(sol=sol)
            decision, accepted = stage(f'astra-decision-{round_number:02}', 'astra-decision', inputs, ['ACCEPT', 'REWORK', 'BLOCKED'])
            inputs.update(astra=decision)
            if args.dry_run:
                state['status'] = 'DRY_RUN'
                break
            if audit['status'] == 'BLOCKED' or accepted['status'] == 'BLOCKED':
                raise ValueError('Figma review is blocked; inspect the saved reports')
            if (audit['status'] == 'PASS' and accepted['status'] == 'ACCEPT'
                    and all(r['evidence'] and not r['required_changes'] and r['figma_file'] == state['figma_file']
                            for r in (audit, accepted))):
                refs = {'brief': brief, 'terra': terra, 'sol': sol, 'astra': decision}
                handoff = {'version': 1, 'task': args.task, 'figma_file': state['figma_file'],
                           'artifacts': {role: {'path': path.name, 'sha256': support.file_hash(path)} for role, path in refs.items()}}
                support.atomic_json(run_dir / 'handoff.json', handoff)
                state['status'] = 'COMPLETE'
                break
        else:
            state['status'] = 'REWORK_REQUIRED'
        state['finished_at'] = support.now()
        save()
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
