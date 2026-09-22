"""Validated Figma references and accepted UI-to-implementation handoffs."""
import json
from pathlib import Path
from urllib.parse import urlparse
try:
    from . import autocode_support as support
except ImportError:
    import autocode_support as support


def design_url(value):
    parsed = urlparse(value)
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or parsed.netloc not in ('figma.com', 'www.figma.com')
            or len(parts) < 2 or parts[0] != 'design' or not parts[1].isalnum()
            or any(c.isspace() for c in value)):
        raise ValueError('Expected an https://www.figma.com/design/<file-key> URL')
    return value


def load_handoff(run_dir):
    root = Path(run_dir).resolve()
    state = json.loads((root / 'state.json').read_text())
    handoff = json.loads((root / 'handoff.json').read_text())
    version = handoff.get('version')
    if state.get('status') != 'COMPLETE' or version not in (1, 2):
        raise ValueError('Only a completed, accepted Autocode UI run can be built')
    design_url(handoff['figma_file'])
    refs = handoff['artifacts']
    required = (('brief', 'terra', 'sol', 'astra') if version == 1 else
                ('requirements_draft', 'plan_reviewer', 'brief', 'plan_finalizer',
                 'builder', 'validator', 'decision_owner'))
    for role in required:
        ref = refs[role]
        path = root / ref['path']
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError('UI handoff artifact is missing or outside the run')
        if support.file_hash(path) != ref['sha256']:
            raise ValueError('UI handoff changed after acceptance; rerun its review')
    execution = (('terra', 'COMPLETE'), ('sol', 'PASS'), ('astra', 'ACCEPT')) if version == 1 else (
        ('builder', 'COMPLETE'), ('validator', 'PASS'), ('decision_owner', 'ACCEPT'))
    if version == 2:
        for role, allowed in (('plan_reviewer', ('PASS', 'REVISE')), ('plan_finalizer', ('ACCEPT',))):
            report = json.loads((root / refs[role]['path']).read_text())
            if (report.get('status') not in allowed or not report.get('evidence')
                    or (role == 'plan_finalizer' and report.get('required_changes'))):
                raise ValueError('UI handoff does not contain an accepted requirements plan')
    for role, expected in execution:
        report = json.loads((root / refs[role]['path']).read_text())
        if (report.get('status') != expected or report.get('figma_file') != handoff['figma_file']
                or report.get('required_changes') or not report.get('evidence')):
            raise ValueError('UI handoff does not have a consistent accepted Figma result')
    return {**handoff, 'brief': (root / refs['brief']['path']).read_text()}


def instructions(settings):
    target = settings.get('figma_file')
    if not target:
        return ''
    policy = ('Use independent screenshot and node comparisons plus functional/accessibility checks as the visual '
              'acceptance gate. Do not add a human visual approval requirement unless the user explicitly requests it; '
              'use human_review=false for criteria verified this way. Do not invent a human approval event.'
              if settings.get('figma_review', 'automatic') == 'automatic' else
              'Include a human visual review criterion after independent technical validation.')
    return ('\nFIGMA DESIGN INPUT\nInspect the editable design using the connected Figma plugin before planning, '
            'implementation or validation: ' + target + '\nLoad the Figma design-to-code skill before get_design_context. '
            'Use its screenshots, component structure, variables and assets as the visual source of truth. '
            'Implement local code; do not edit the reference Figma file. Compare every required screen and state, '
            'not just one component. If a reference is absent, derive a consistent responsive layout and document it. '
            + policy + '\n')


def require_chatgpt(settings):
    if (settings.get('auth_mode') != 'ChatGPT' or settings.get('environment_auth_present')
            or settings.get('environment_base_url_present') or settings.get('openai_base_url')):
        raise ValueError('Figma workflow requires Codex ChatGPT login without API-key or base-URL overrides')
