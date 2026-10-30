"""Full-report transport for providers that cannot express arbitrary JSON edit values.

This selects an existing supported response form. The saved schema and its pins
stay unchanged; the runner still reconstructs and validates the original report.
"""

import json
from pathlib import Path

try:
    from . import autocode_report_edits as edits
    from . import autocode_stage_roles as stage_roles
    from .autocode_util import atomic_json
except ImportError:
    import autocode_report_edits as edits
    import autocode_stage_roles as stage_roles
    from autocode_util import atomic_json


def full_report(schema_path):
    """Return the exact original full-report schema without its optional edit offer."""
    source = Path(schema_path)
    schema = json.loads(source.read_text())
    if edits.META not in schema:
        return source
    original = edits.validation_schema(schema)
    target = source.with_name(source.stem + ".full-report.schema.json")
    atomic_json(target, original)
    return target


def for_route(schema_path, settings, role):
    """Choose a report-repair schema the selected transport can represent."""
    route = settings["roles"][role]
    if stage_roles.engine_for(settings, role) == "opencode" and route.get("provider") == "gocode":
        return full_report(schema_path)
    return Path(schema_path)
