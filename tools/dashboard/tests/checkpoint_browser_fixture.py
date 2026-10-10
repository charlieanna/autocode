"""Disposable real Git runs served by the production dashboard and checkpoint CLI."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

SOURCE = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(SOURCE), str(SOURCE / "tools/dashboard"), str(SOURCE / "tools")]
import autocode_registry as registry
from agent_console import Console, Handler, LoopbackHTTPServer

from tests.test_code_checkpoints import CodeCheckpoints

root = Path(os.environ["AUTOCODE_FIXTURE_ROOT"]).resolve()
root.mkdir(parents=True, exist_ok=True)
os.environ["AUTOCODE_HOME"] = str(root / "registry")
fixtures = []
cases = {}
for name in ("desktop", "tablet", "mobile"):
    fixture = CodeCheckpoints()
    fixture.setUp()
    fixtures.append(fixture)
    (fixture.workspace / "app.txt").write_text("later work must survive\n")
    fixture.git("add", "app.txt")
    registry.register_run(fixture.workspace, fixture.run, fixture.state)
    cases[name] = {
        "workspace": str(fixture.workspace),
        "run": str(fixture.run),
        "checkpoint_id": fixture.ident,
        "head": fixture.git("rev-parse", "HEAD"),
        "index": fixture.git("write-tree"),
    }


class OfflineConsole(Console):
    def model_catalogue(self, refresh=False):
        return {"models": [], "error": "Offline checkpoint fixture; no model requests"}


console = OfflineConsole(
    [f.workspace for f in fixtures],
    SOURCE / "tools/autocode.py",
    lambda: False,
    conversation_root=root / "dashboard/conversations",
    project_store_root=root / "dashboard",
)
server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
server.console = console
server.hosts = {"127.0.0.1:" + str(server.server_port)}
base = "http://127.0.0.1:" + str(server.server_port)
print(
    "FIXTURE="
    + json.dumps(
        {
            "url": base,
            "root": str(root),
            "cases": {
                name: {**case, "url": base + "/#" + urlencode({"task": case["workspace"], "run": case["run"]})}
                for name, case in cases.items()
            },
        }
    ),
    flush=True,
)
try:
    server.serve_forever()
finally:
    server.server_close()
    console.pool.shutdown(wait=True)
    console.conversations.close(wait=True)
    for fixture in fixtures:
        fixture.doCleanups()
