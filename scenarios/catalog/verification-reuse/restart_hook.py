"""TEST ONLY: interrupt the controller after proof, before validation commit."""
import json
import os
from pathlib import Path

marker_name = os.environ.get("SCENARIO_VERIFICATION_CRASH")
if marker_name:
    marker = Path(marker_name)
    original = os.replace

    def replace(source, destination, *args, **kwargs):
        target = Path(destination)
        if target.name == "state.json" and target.is_relative_to(marker.parent / "project") and not marker.exists():
            value = json.loads(Path(source).read_text())
            replay = (value.get("validation") or {}).get("check_replay") or {}
            if replay.get("verdict") == "PASS":
                marker.write_text(json.dumps({"boundary": "before_validation_commit", "replay": replay}))
                os._exit(97)
        return original(source, destination, *args, **kwargs)

    os.replace = replace
