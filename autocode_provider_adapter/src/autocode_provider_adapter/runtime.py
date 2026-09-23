"""External composition layer for an untouched, pinned Autocode checkout."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import threading
import types
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .transport import GoCodeTransport, RunRoleLaunchRequest, TRANSPORT_CONTRACT_VERSION


class GoCodeFacade(types.ModuleType):
    """Present GoCode through Autocode's existing external transport seam."""

    DEFAULT_MODELS = {
        "glm": "openai/gpt-5.6-sol", "astra": "openai/gpt-5.6-sol",
        "terra": "openai/gpt-5.6-terra", "sol": "openai/gpt-5.6-sol",
        "completion": "openai/gpt-5.6-sol",
    }
    DEFAULT_REASONING_EFFORTS = {"astra": "high", "terra": "medium", "sol": "high", "completion": "high"}

    def __init__(self, transport: GoCodeTransport, *, urlopen=urlopen) -> None:
        super().__init__("tools.autocode_opencode")
        self.transport = transport
        self._pending = threading.local()
        self._urlopen = urlopen

    def local_settings(self, workspace):
        return self.transport.identity(Path(workspace))

    def transport_drift(self, current, checkpoint):
        return self.transport.transport_drift(current, checkpoint)

    def _routes(self, roles):
        normalized = {}
        for role, config in roles.items():
            model = config.get("model")
            normalized[role] = {**config, "model": model.removeprefix("openai/") if isinstance(model, str) else model}
        return self.transport.validate_roles(normalized)

    def check_models(self, roles, workspace=None):
        routes = self._routes(roles)
        if workspace is not None:
            available = set(self.transport.dashboard_catalogue(Path(workspace)))
            missing = {route.resolved_model for route in routes.values() if route.resolved_model not in available}
            if missing:
                raise RuntimeError("Models unavailable in GoCode: " + ", ".join(sorted(missing)))

    def check_subscription_routes(self, roles, workspace=None):
        self._routes(roles)
        self.transport.identity(Path(workspace or Path.cwd()))

    def launch(self, role, workspace, run_dir, session, model, effort, allow_write, *, planning=False):
        if getattr(self._pending, "value", None) is not None:
            raise RuntimeError("a GoCode launch is already pending prompt adaptation")
        workspace, run_dir = Path(workspace).resolve(), Path(run_dir).resolve()
        requested = model or self.DEFAULT_MODELS[role]
        effective_effort = effort or ("medium" if role == "glm" else self.DEFAULT_REASONING_EFFORTS[role])
        route = self._routes({role: {"model": requested, "reasoning_effort": effective_effort}})[role]
        directory = run_dir / ".gocode-adapter"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = directory / ("launch-" + uuid.uuid4().hex + ".json")
        command = [sys.executable, "-m", "autocode_provider_adapter.child", str(descriptor)]
        environment = dict(os.environ)
        source_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_root + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        checkpoint = self.transport.identity(workspace)
        self._pending.value = {
            "role": role, "workspace": workspace, "run_dir": run_dir, "session": session,
            "requested_model": requested, "effort": effective_effort, "allow_write": bool(allow_write),
            "planning": bool(planning), "route": route, "checkpoint": checkpoint,
            "descriptor": descriptor,
        }
        return command, environment, {"transport": "gocode", "contract_version": TRANSPORT_CONTRACT_VERSION}

    def prompt_for_schema(self, prompt, schema, events):
        pending = getattr(self._pending, "value", None)
        if pending is None:
            raise RuntimeError("GoCode prompt adaptation has no matching launch")
        self._pending.value = None
        events = Path(events).resolve()
        schema_path = pending["descriptor"].with_suffix(".schema.json")
        schema_path.write_text(json.dumps(schema, sort_keys=True) + "\n", encoding="utf-8")
        request = RunRoleLaunchRequest(
            version=TRANSPORT_CONTRACT_VERSION, role=pending["role"], prompt=prompt,
            sandbox="workspace-write" if pending["allow_write"] else "read-only",
            workspace=pending["workspace"], run_dir=pending["run_dir"], session=pending["session"],
            model=pending["route"].requested_model, effort=pending["effort"],
            allow_write=pending["allow_write"], planning=pending["planning"], report_repair=False,
            schema=schema_path, output=events.with_suffix(".json"), events=events,
            child_options={"start_new_session": True},
        )
        prepared = self.transport.prepare_launch(request, pending["route"], pending["checkpoint"])
        document = {
            "request": {key: (str(value) if isinstance(value, Path) else value)
                        for key, value in vars(request).items()},
            "route": vars(pending["route"]), "checkpoint": pending["checkpoint"],
            "route_fingerprint": prepared.route_fingerprint,
        }
        pending["descriptor"].write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(pending["descriptor"], 0o600)
        return prepared.prompt

    @staticmethod
    def raw_events(path):
        rows = []
        for line in Path(path).read_text(errors="replace").splitlines() if Path(path).exists() else ():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    @staticmethod
    def normalized_events(rows):
        return list(rows)

    @staticmethod
    def final_report(path):
        output = Path(path).with_suffix(".json")
        try:
            report = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError("GoCode final structured report is unavailable") from error
        if not isinstance(report, dict):
            raise RuntimeError("GoCode final structured report is not an object")
        return report

    def conversation_provider(self, prompt, model, workdir):
        """Send an upstream-authored conversation prompt with no model tools."""
        workdir = Path(workdir).resolve()
        _identity, route = self.transport._current(workdir, None, workdir)
        endpoint = route["OPENAI_BASE_URL"].rstrip("/") + "/responses"
        payload = json.dumps({
            "model": model.removeprefix("openai/"), "input": prompt,
            "tools": [], "store": False,
        }).encode("utf-8")
        request = Request(endpoint, data=payload, method="POST", headers={
            "Authorization": "Bearer " + route["OPENAI_API_KEY"],
            "Content-Type": "application/json",
        })
        try:
            with self._urlopen(request, timeout=180) as response:
                document = json.loads(response.read())
        except (HTTPError, URLError, OSError, ValueError) as error:
            raise RuntimeError("GoCode planning conversation request failed") from error
        text = document.get("output_text") if isinstance(document, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("GoCode planning conversation returned no text")
        return text.strip()


def load_upstream_runner(checkout: Path, facade: GoCodeFacade):
    """Import the pinned runner with only its transport module substituted."""
    checkout = Path(checkout).resolve()
    sys.path.insert(0, str(checkout))
    sys.modules["tools.autocode_opencode"] = facade
    return importlib.import_module("tools.autocode")


def load_native_upstream_runner(checkout: Path):
    """Load the untouched built-in OpenCode provider path."""
    checkout = Path(checkout).resolve()
    sys.path.insert(0, str(checkout))
    return importlib.import_module("tools.autocode")
