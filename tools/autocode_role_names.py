"""Display-only role names shared by terminal and dashboard; no route authority."""
import json
from pathlib import Path

CATALOGUE = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))
ROLES = {stage: value['role'] for stage, value in CATALOGUE['stages'].items()}


def role_name(stage, workflow_mode=None):
    """Name the job performed by this stage, independently of its model route."""
    if not stage:
        return ''
    stage = str(stage).removesuffix('_report_repair')
    entry = CATALOGUE['modes'].get(workflow_mode, {}).get(stage) or CATALOGUE['stages'].get(stage)
    return entry['role'] if entry else stage.replace('_', ' ').title()


def role_label(role):
    key = str(role).lower().replace('_', ' ')
    key = CATALOGUE['aliases'].get(key, role)
    return CATALOGUE['roles'].get(key, str(role).replace('_', ' ').title())
