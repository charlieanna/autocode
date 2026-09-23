#!/usr/bin/env python3
"""Role-based planning and Figma execution through the shared Autocode driver."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import shutil
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

DEFAULT_MODELS = {'astra': 'gpt-5.6-sol', 'terra': 'gpt-5.6-terra', 'sol': 'gpt-5.6-sol'}
DEFAULT_PLANNER_MODEL = 'gpt-5.6-sol'
DEFAULT_ASTRA_REASONING_EFFORT = 'high'


def report_schema(statuses):
    return {'type': 'object', 'additionalProperties': False,
            'required': ['status', 'figma_file', 'summary', 'evidence', 'required_changes'],
            'properties': {'status': {'type': 'string', 'enum': statuses},
                           'figma_file': {'type': 'string'}, 'summary': {'type': 'string'},
                           'evidence': {'type': 'array', 'items': {'type': 'string'}},
                           'required_changes': {'type': 'array', 'items': {'type': 'string'}}}}


def plan_schema(statuses):
    return {'type': 'object', 'additionalProperties': False,
            'required': ['status', 'summary', 'evidence', 'required_changes'],
            'properties': {'status': {'type': 'string', 'enum': statuses},
                           'summary': {'type': 'string'},
                           'evidence': {'type': 'array', 'items': {'type': 'string'}},
                           'required_changes': {'type': 'array', 'items': {'type': 'string'}}}}


def prompt(role, task, run_dir, target, inputs):
    shared = (f'Autocode UI task: {task}\nRun artifacts: {run_dir}\n'
              f'Figma target: {target or "Create a new editable Figma Design file"}\n'
              'Use the connected Figma plugin through Codex and the configured ChatGPT model. '
              'Load the applicable Figma skills before using its tools. Build native editable Figma nodes, '
              'components and variables. Figma Make is not assumed to be callable. '
              'Only the Figma Builder may modify Figma. Do not build a local application or change project source. '
              'Return the complete deliverable in your final response; the runner saves it. '
              'Do not replace the deliverable with a completion note or a link to a file. '
              'Only write temporary evidence under the run directory.\n'
              + '\n'.join(f'{name}: {path}' for name, path in inputs.items()) + '\n')
    if role == 'requirements_planner':
        return shared + ('Act as the Requirements Planner. Return a complete UI brief covering users, outcomes, '
                         'primary flow, screens, states, hierarchy, visual direction, responsive behavior, components, '
                         'content, accessibility, failure states and observable acceptance checks. Resolve ordinary '
                         'design choices with documented defaults. When redesigning an existing product, inspect its '
                         'current UI and require a visibly more usable, polished composition rather than a sparse '
                         'wireframe or component inventory. Do not edit Figma.')
    if role == 'plan_reviewer':
        return shared + ('Act as the independent Plan Reviewer. Inspect the requirements draft and the selected '
                         'Figma file when one exists. Check completeness, contradictions, unnecessary scope, missing '
                         'states, responsive behavior, accessibility and whether each acceptance check is observable. '
                         'Return PASS only when no revision is required, REVISE with concrete required_changes, or '
                         'BLOCKED when the task cannot be planned safely. Do not edit Figma.')
    if role == 'requirements_revision':
        return shared + ('Act as the Requirements Planner. Read the draft, plan review and any prior finalization. '
                         'Return a complete revised UI brief, resolving every required change rather than writing a '
                         'delta. Cover users, primary flow, screens, states, hierarchy, visual direction, responsive '
                         'behavior, components, accessibility and acceptance checks. Do not edit Figma.')
    if role == 'plan_finalizer':
        return shared + ('Act as the Plan Finalizer. Independently check the revised brief against the task and plan '
                         'review. Return ACCEPT only when the builder has a coherent, testable design assignment and '
                         'no required changes remain. Otherwise return REWORK with bounded changes or BLOCKED. '
                         'Do not edit Figma.')
    if role == 'builder':
        return shared + ('Act as the Figma Builder. Read the finalized brief and any previous validation/decision. '
                         'Create or refine the actual Figma file, reuse design-system components and tokens, '
                         'and inspect screenshots plus node structure. Compose realistic full product screens with '
                         'scenario-specific content; placeholder labels, tiny hidden instances, and skeletal wireframes '
                         'are incomplete even if node counts pass. Return COMPLETE only after the edits succeed. '
                         'Report the exact Figma Design URL, inspected node IDs/screens and evidence. '
                         'For COMPLETE, leave required_changes empty when the edits are complete; the independent '
                         'validator is the next workflow stage, not a remaining builder change. Return BLOCKED if '
                         'plugin access or edits fail; do not substitute local HTML or claim success.')
    if role == 'validator':
        return shared + ('Act as the independent Design Validator. Read the finalized brief and builder result, then '
                         'inspect the actual Figma screenshots and nodes. Audit all required screens/states, visual '
                         'hierarchy, consistency, component reuse, responsiveness and accessibility. Compare the '
                         'rendered screens to the requested product quality and existing UI. Reject sparse wireframes, '
                         'placeholder content, detached or hidden replicas, and any structurally valid but visually '
                         'unusable composition. Do not edit '
                         'Figma. Return PASS only with concrete evidence and no required_changes. Use FAIL for '
                         'repairable defects or BLOCKED if inspection is unavailable.')
    if role == 'decision_owner':
        return shared + ('Act as the Completion Owner. Read the finalized brief, builder result and current validation. '
                         'This role is strictly read-only: never create, edit, move, delete, or rebind Figma nodes, '
                         'variables, styles, or annotations. Return a decision and repair instructions only. '
                         'Return ACCEPT only if validation passed the actual Figma result and the complete brief is '
                         'satisfied, including screenshot-level visual quality. Otherwise give bounded REWORK '
                         'instructions or BLOCKED. Include the exact accepted '
                         'Figma URL and evidence. No additional human visual approval is needed for this design task.')
    if role == 'astra-brief':  # Compatibility for pre-0.7 callers.
        return shared + ('Act as the Requirements Planner. Return a concise UI brief covering users, primary flow, screens, states, '
                         'hierarchy, visual direction, responsive behavior, components, accessibility and acceptance '
                         'checks. Use reasonable defaults for this design task. Do not edit Figma.')
    raise ValueError(f'Unknown UI role: {role}')


def run_stage(name, model, text, workspace, run_dir, schema=None, dry_run=False, reasoning_effort=None):
    output = run_dir / (name + ('.json' if schema else '.md'))
    capture = output if schema else run_dir / (name + '.last-message.md')
    prompt_path = run_dir / (name + '.prompt.md')
    events = run_dir / (name + '.jsonl')
    prompt_path.write_text(text)
    command = ['codex', 'exec', '-C', str(workspace), '--skip-git-repo-check', '--sandbox', 'workspace-write',
               '--model', model, '-c', 'model_provider="openai"', '--json', '--output-last-message', str(capture)]
    if reasoning_effort:
        command += ['-c', f'model_reasoning_effort="{reasoning_effort}"']
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
    if not schema and not output.exists():
        capture.replace(output)
    report = json.loads(output.read_text()) if schema else output.read_text()
    if schema:
        support.validate_schema(report, schema)
        if report.get('figma_file'):
            figma.design_url(report['figma_file'])
    elif not report.strip():
        raise ValueError(f'{name} returned an empty UI brief')
    print(f'{name}: completed; output={output}', flush=True)
    return output, report


def seed_accepted_plan(state, source_dir, run_dir):
    source_dir = source_dir.resolve()
    prior = json.loads((source_dir / 'state.json').read_text())
    finalizer = prior.get('reports', {}).get('plan_finalizer', {})
    if finalizer.get('status') != 'ACCEPT' or finalizer.get('required_changes'):
        raise ValueError('Source run has no accepted UI plan')
    if prior.get('figma_file') != state.get('figma_file'):
        raise ValueError('Source plan targets a different Figma file')
    keys = ('requirements_draft', 'plan_reviewer', 'brief', 'plan_finalizer')
    for key in keys:
        source = Path(prior['outputs'][key]).resolve()
        if source.parent != source_dir or not source.is_file():
            raise ValueError(f'Source plan artifact is missing or outside its run: {key}')
        target = run_dir / source.name
        shutil.copyfile(source, target)
        state['outputs'][key] = str(target)
    state['reports']['plan_reviewer'] = prior['reports']['plan_reviewer']
    state['reports']['plan_finalizer'] = finalizer
    state['next_stage'] = 'builder'
    state['planning_iteration'] = prior.get('planning_iteration', 0)
    state['plan_source'] = str(source_dir)


def execute(args, workspace, run_dir):
    state = {'version': 2, 'task': args.task, 'workspace': str(workspace), 'run_dir': str(run_dir),
             'figma_file': args.figma_file, 'created_at': support.now(), 'status': 'RUNNING',
             'next_stage': 'requirements_planner', 'iteration': 0, 'planning_iteration': 0, 'target': 'figma',
             'models': {**{role: getattr(args, role + '_model') for role in DEFAULT_MODELS},
                        'planner': args.planner_model},
             'reasoning_efforts': {'astra': args.astra_reasoning_effort},
             'stages': [], 'outputs': {}, 'reports': {}}
    if args.from_plan_run:
        seed_accepted_plan(state, args.from_plan_run, run_dir)
    def save():
        support.atomic_json(run_dir / 'state.json', state)
    names = {'requirements_planner': lambda: 'requirements-draft', 'plan_reviewer': lambda: 'plan-review',
             'requirements_revision': lambda: f'requirements-revision-{state["planning_iteration"]:02}',
             'plan_finalizer': lambda: f'plan-finalization-{state["planning_iteration"]:02}',
             'builder': lambda: f'builder-{state["iteration"]:02}',
             'validator': lambda: f'validator-{state["iteration"]:02}',
             'decision_owner': lambda: f'decision-{state["iteration"]:02}'}
    model_roles = {'requirements_planner': 'planner', 'plan_reviewer': 'astra',
                   'requirements_revision': 'planner', 'plan_finalizer': 'astra',
                   'builder': 'terra', 'validator': 'sol', 'decision_owner': 'astra'}
    schemas = {'plan_reviewer': plan_schema(['PASS', 'REVISE', 'BLOCKED']),
               'plan_finalizer': plan_schema(['ACCEPT', 'REWORK', 'BLOCKED']),
               'builder': report_schema(['COMPLETE', 'BLOCKED']),
               'validator': report_schema(['PASS', 'FAIL', 'BLOCKED']),
               'decision_owner': report_schema(['ACCEPT', 'REWORK', 'BLOCKED'])}

    def dispatch(current, stage):
        name, role = names[stage](), stage
        current['active_stage'] = {'stage': stage, 'name': name, 'iteration': current['iteration']}
        save()
        inputs = {key: Path(value) for key, value in current['outputs'].items()}
        return name, role, run_stage(name, current['models'][model_roles[stage]],
                                    prompt(role, args.task, run_dir, current['figma_file'], inputs),
                                    run_dir, run_dir, schemas.get(stage),
                                    args.dry_run,
                                    args.astra_reasoning_effort if model_roles[stage] == 'astra' else None)

    def apply(current, stage, outcome):
        name, role, (output, report) = outcome
        current['stages'].append({'stage': stage, 'role': role, 'name': name,
                                  'iteration': current['iteration'], 'output': str(output),
                                  'finished_at': support.now()})
        current.pop('active_stage', None)
        if stage == 'requirements_planner':
            current['outputs']['requirements_draft'] = str(output)
            current['next_stage'] = 'plan_reviewer'
            return
        if stage == 'requirements_revision':
            current['outputs']['brief'] = str(output)
            current['next_stage'] = 'plan_finalizer'
            return
        current['reports'][stage] = report
        current['outputs'][stage] = str(output)
        if args.dry_run:
            if stage == 'decision_owner':
                current.update(status='DRY_RUN', next_stage=None, finished_at=support.now())
            else:
                current['next_stage'] = {'plan_reviewer': 'requirements_revision',
                                         'plan_finalizer': 'builder', 'builder': 'validator',
                                         'validator': 'decision_owner'}[stage]
            return
        if stage == 'plan_reviewer':
            if report['status'] == 'BLOCKED':
                raise ValueError('UI requirements planning is blocked; inspect the saved review')
            current['next_stage'] = 'requirements_revision'
            return
        if stage == 'plan_finalizer':
            if report['status'] == 'BLOCKED':
                raise ValueError('UI requirements finalization is blocked; inspect the saved review')
            if report['status'] == 'ACCEPT' and report['evidence'] and not report['required_changes']:
                current['next_stage'] = 'builder'
            elif current['planning_iteration'] >= args.max_plan_reworks:
                current.update(status='PLAN_REWORK_REQUIRED', next_stage=None, finished_at=support.now())
            else:
                current['planning_iteration'] += 1
                current['next_stage'] = 'requirements_revision'
            return
        if stage == 'builder':
            if report['status'] != 'COMPLETE' or not report['figma_file'] or not report['evidence'] or report['required_changes']:
                raise ValueError('The Figma Builder did not produce a completed editable result')
            if current['figma_file'] and url_key(report['figma_file']) != url_key(current['figma_file']):
                raise ValueError('Terra returned a different Figma file than the selected target')
            current.update(figma_file=report['figma_file'], next_stage='validator')
            return
        if stage == 'validator':
            if report['status'] == 'BLOCKED':
                raise ValueError('Figma review is blocked; inspect the saved reports')
            current['next_stage'] = 'decision_owner'
            return
        audit = current['reports']['validator']
        if report['status'] == 'BLOCKED':
            raise ValueError('Figma review is blocked; inspect the saved reports')
        accepted = (audit['status'] == 'PASS' and report['status'] == 'ACCEPT'
                    and all(item['evidence'] and not item['required_changes']
                            and item['figma_file'] == current['figma_file'] for item in (audit, report)))
        if accepted:
            refs = {key: Path(current['outputs'][key]) for key in
                    ('requirements_draft', 'plan_reviewer', 'brief', 'plan_finalizer',
                     'builder', 'validator', 'decision_owner')}
            handoff = {'version': 2, 'task': args.task, 'figma_file': current['figma_file'],
                       'artifacts': {key: {'path': path.name, 'sha256': support.file_hash(path)}
                                     for key, path in refs.items()}}
            support.atomic_json(run_dir / 'handoff.json', handoff)
            current.update(status='COMPLETE', next_stage=None, finished_at=support.now())
        elif current['iteration'] >= args.max_reworks:
            current.update(status='REWORK_REQUIRED', next_stage=None, finished_at=support.now())
        else:
            current['iteration'] += 1
            current['next_stage'] = 'builder'

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
            command += ['--astra-reasoning-effort', args.astra_reasoning_effort]
            return subprocess.run(command, cwd=workspace).returncode
    return 0 if state['status'] in ('COMPLETE', 'DRY_RUN') else 3


def url_key(url):
    return url.split('/design/', 1)[1].split('/', 1)[0].split('?', 1)[0].split('#', 1)[0]


def cli(argv=None):
    parser = argparse.ArgumentParser(description='Plan, build and validate editable Figma UI through the shared Autocode workflow.')
    parser.add_argument('task')
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--figma-file')
    parser.add_argument('--run-dir', type=Path, help='New artifact directory (existing runs are never overwritten)')
    parser.add_argument('--from-plan-run', type=Path,
                        help='Start a new run at Builder using a saved accepted UI plan')
    for role, default in DEFAULT_MODELS.items():
        parser.add_argument(f'--{role}-model', default=default)
    parser.add_argument('--astra-reasoning-effort', choices=('low', 'medium', 'high', 'xhigh', 'max'),
                        default=DEFAULT_ASTRA_REASONING_EFFORT,
                        help='Reasoning effort for plan review, plan finalization and completion decision')
    parser.add_argument('--planner-model', default=DEFAULT_PLANNER_MODEL)
    parser.add_argument('--max-reworks', type=int, default=2)
    parser.add_argument('--max-plan-reworks', type=int, default=1)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--build', action='store_true', help='Pass the accepted Figma result to Autocode for implementation')
    parser.add_argument('--no-chat', action='store_true', help='Present the implementation brief without interactive input')
    args = parser.parse_args(argv)
    if args.max_reworks < 0 or args.max_plan_reworks < 0:
        parser.error('rework limits must be nonnegative')
    if any(not re.fullmatch(r'gpt-[a-zA-Z0-9.-]+', model)
           for model in [args.planner_model, *(getattr(args, role + '_model') for role in DEFAULT_MODELS)]):
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
