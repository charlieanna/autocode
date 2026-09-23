"""Final identity-checking child used by the untouched upstream process lifecycle."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from .compatibility import CompatibilityManifest
from .transport import GoCodeTransport, RoleRoute, RunRoleLaunchRequest


def _load(path: Path):
    document = json.loads(path.read_text(encoding="utf-8"))
    raw = dict(document["request"])
    for field in ("workspace", "run_dir", "schema", "output", "events"):
        raw[field] = Path(raw[field])
    return RunRoleLaunchRequest(**raw), RoleRoute(**document["route"]), document["checkpoint"]


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        raise SystemExit("usage: autocode-provider-child LAUNCH.json")
    request, route, checkpoint = _load(Path(arguments[0]))
    transport = GoCodeTransport(CompatibilityManifest.default())
    current, managed_route = transport._current(request.workspace, checkpoint, request.run_dir)
    executable_key = "claude_shim" if route.resolved_model == "claude-opus-5" else "codex"
    command = transport._command(request, route, str(dict(current[executable_key])["path"]), managed_route)
    environment = transport._environment(managed_route, request.run_dir)
    os.execvpe(command[0], command, environment)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
