"""TEST ONLY: die immediately before or after committing the first direct assignment."""
import json
import os
from pathlib import Path

marker_name = os.environ.get("SCENARIO_REWORK_CRASH_ON_ASSIGNMENT")
if marker_name:
    marker = Path(marker_name)
    original = os.replace

    def replace(source, destination, *args, **kwargs):
        target = Path(destination)
        if target.name == "state.json" and target.is_relative_to(marker.parent / "project") and not marker.exists():
            saved = json.loads(Path(source).read_text())
            assignments = saved.get("direct_rework_assignments") or []
            if assignments:
                if os.environ.get("SCENARIO_REWORK_CRASH_WHEN") == "before":
                    marker.write_text(json.dumps({"boundary": "before_assignment_commit"}))
                    os._exit(97)
                original(source, destination, *args, **kwargs)
                marker.write_text(json.dumps({"boundary": "after_assignment_commit", "count": len(assignments),
                                              "assigned_task_id": assignments[0]["assigned_task_id"]}))
                os._exit(97)
        return original(source, destination, *args, **kwargs)

    os.replace = replace
