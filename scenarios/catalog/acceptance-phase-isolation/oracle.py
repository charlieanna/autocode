"""Execute stats and compatibility through declared oracle environments."""

import json
import socket
import sys
import tempfile
from pathlib import Path

from harness.oracle import Check
from harness.phase_env import GREEN, PhaseSequence

STATS = r"""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.environ['PHASE_HARNESS_ROOT'])
from harness.phase_env import guard, write_synthetic_credentials
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status": "ok"}'
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
port = int(os.environ['STATS_PORT'])
server = HTTPServer(('127.0.0.1', port), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
lifecycle = {'started': True, 'host': '127.0.0.1', 'port': port}
try:
    write_synthetic_credentials(os.environ['PHASE_CREDENTIAL_ROOT'], 'synthetic-stats-token')
    with guard().get(f'http://127.0.0.1:{port}/stats') as response:
        lifecycle.update(status=response.status, body=json.load(response), polled=True)
finally:
    server.shutdown(); thread.join(timeout=5); server.server_close()
    lifecycle['stopped'] = not thread.is_alive()
print(json.dumps(lifecycle))
assert lifecycle['status'] == 200 and lifecycle['body'] == {'status': 'ok'} and lifecycle['stopped']
"""

COMPAT = r"""
import json, os, shutil, sys
sys.path.insert(0, os.environ['PHASE_HARNESS_ROOT'])
from harness.phase_env import DEFAULT_USAGE_URL, RefusedTransportError, find_credentials, guard, write_synthetic_credentials
policy = json.load(open('phase_policy.json'))
client = guard()
def startup():
    if find_credentials(os.environ['PHASE_CREDENTIAL_ROOT']):
        try: client.get(DEFAULT_USAGE_URL)
        except RefusedTransportError:
            if not policy['swallow']: raise
startup()
if policy['teardown']:
    write_synthetic_credentials(os.environ['PHASE_CREDENTIAL_ROOT'], 'synthetic-teardown-token')
    startup()
if policy.get('cleanup_state'):
    shutil.rmtree(os.environ['PHASE_STATE_ROOT'])
print('compatibility assertions passed')
"""


def check(project, scenario):
    policy = json.loads((project / "phase_policy.json").read_text())
    evidence = project.parent / ".phase-evidence"
    evidence.mkdir(exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="sequence-", dir=evidence))
    sequence = PhaseSequence("acceptance-phase-isolation", base)
    # A bind race fails the smoke; it cannot become acceptance or silently
    # retry another destination. The child starts, polls and stops the server.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    stats = sequence.phase("stats", allowed_endpoints=(f"127.0.0.1:{port}",))
    compat = sequence.phase("compat", share_credential_root_with=None if policy["isolate"] else stats)
    smoke = stats.run([sys.executable, "-c", STATS], cwd=project, env={"STATS_PORT": str(port)})
    lifecycle = json.loads(smoke.stdout) if smoke.returncode == 0 else {}
    compat.run([sys.executable, "-c", COMPAT], cwd=project)
    record = sequence.finish()
    record["stats_lifecycle"] = lifecycle
    (base / "sequence-record.json").write_text(json.dumps(record, indent=2) + "\n")
    return [
        Check("acceptance_without_contamination", record["outcome"] == GREEN, record["reason"]),
        Check(
            "stats_started_polled_stopped",
            bool(
                smoke.returncode == 0
                and lifecycle.get("started")
                and lifecycle.get("polled")
                and lifecycle.get("stopped")
                and lifecycle.get("status") == 200
                and lifecycle.get("body") == {"status": "ok"}
            ),
            json.dumps(lifecycle),
        ),
        Check("distinct_phase_credentials", stats.credential_root != compat.credential_root),
        Check(
            "phase_records",
            len(record["phases"]) == 2
            and all(
                Path(phase[key]).is_relative_to(base.resolve())
                for phase in record["phases"]
                for key in ("credential_root", "config_root", "state_root", "cache_root", "requests_log")
            ),
            json.dumps(record),
        ),
    ]
