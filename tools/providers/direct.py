"""First-party AutoCode runtime behind the existing command-provider contract."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

try:
    from .. import autocode_direct_config as connection
except ImportError:
    import autocode_direct_config as connection
from . import command


class DirectProvider(command.CommandProvider):
    RESUME_ACROSS_STAGES = False

    def __init__(self):
        self.settings = connection.load()
        self.role_provider = connection.role_providers(self.settings)
        models = {role: model for role, (_provider, model) in connection.assignment(self.settings).items()}
        runtime = Path(__file__).resolve().parents[1] / "autocode_direct_runtime.py"
        config = {
            "name": "autocode",
            "models": sorted(set(models.values())),
            "command": [
                sys.executable,
                str(runtime),
                "--workspace",
                "{workspace}",
                "--run-dir",
                "{run_dir}",
                "--model",
                "{model}",
                "--effort={effort}",
                "-o",
                "{report}",
            ],
            "roles": {role: {"model": model, "effort": ""} for role, model in models.items()},
        }
        super().__init__(config, connection.directory() / "connection.json")

    def local_settings(self, workspace=None, *, env=None):
        files = [
            Path(__file__),
            *(
                Path(__file__).resolve().parents[1] / name
                for name in ("autocode_direct_runtime.py", "autocode_direct_transport.py", "autocode_direct_config.py")
            ),
        ]
        return {
            "engine": "autocode",
            "identity_version": 1,
            "executable": sys.executable,
            "version": "direct-1",
            "config_sha256": hashlib.sha256(json.dumps(connection.load(), sort_keys=True).encode()).hexdigest(),
            "runtime_sha256": hashlib.sha256(b"".join(path.read_bytes() for path in files)).hexdigest(),
        }

    def check_subscription_routes(self, roles, workspace=None, *, env=None):
        used = {self.role_provider[role] for role in roles if role in self.role_provider}
        for name in sorted(used or set(self.role_provider.values())):
            connection.api_key(self.settings, name)
        # There is no Kilo/OpenCode gateway or billing-route substitution here.

    def launch(self, role, workspace, run_dir, session, model, effort, allow_write, *, planning=False, **kwargs):
        argv, env, metadata = super().launch(
            role, workspace, run_dir, None, model, effort, allow_write, planning=planning, **kwargs
        )
        provider = self.role_provider.get(role)
        if not provider:
            raise ValueError(f"The direct connection has no API provider for role {role!r}")
        argv.append("--provider=" + provider)
        if planning:
            argv.append("--planning")
        if allow_write and not planning:
            argv.append("--allow-write")
        argv.append(
            "--connection-hash=" + hashlib.sha256(json.dumps(self.settings, sort_keys=True).encode()).hexdigest()
        )
        return argv, env, {**metadata, "runtime": "autocode-direct-1"}

    def normalized_events(self, rows):
        rows = super().normalized_events(rows)
        if not rows or rows[-1].get("type") != "turn.completed":
            return rows
        usage = [row["usage"] for row in rows if row.get("type") == "usage.partial"]
        keys = set.intersection(*(set(row) for row in usage)) if usage else set()
        totals = {key: sum(row[key] for row in usage) for key in keys}
        return [row for row in rows[:-1] if row.get("type") != "usage.partial"] + [{**rows[-1], "usage": totals}]

    def final_report(self, path, **kwargs):
        rows = self.raw_events(path)
        if not rows or rows[-1].get("type") != "turn.completed":
            raise RuntimeError("Direct agent did not finish; preserve its attempt without automatic replay")
        return super().final_report(path, **kwargs)


def load():
    return DirectProvider()
