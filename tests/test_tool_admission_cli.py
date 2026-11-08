"""Fresh source and package CLI launches enforce early and late selected-tool admission."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Run the actual source script or package __main__ in a fresh interpreter. Only
# provider discovery, paid traffic and native metadata/startup are fake. Importing
# another test module here would preload source-mode aliases in the package case.
ENTRYPOINT_FIXTURE = r"""
import contextlib
import importlib
import io
import json
import os
import runpy
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

mode, runtime, root = sys.argv[1:]
runtime, root = Path(runtime).resolve(), Path(root).resolve()
stems = ("autocode_model_preflight", "autocode_provider_launch", "autocode_tool_admission")
assert not any(name in sys.modules for stem in stems for name in (stem, "autocode_cli." + stem))
if mode == "package":
    packages = root / "package"
    packages.mkdir()
    (packages / "autocode_cli").symlink_to(runtime, target_is_directory=True)
    sys.path.insert(0, str(packages))
    prefix = "autocode_cli."
else:
    sys.path.insert(0, str(runtime))
    prefix = ""

def module(name):
    return importlib.import_module(prefix + name)

preflight = module("autocode_model_preflight")
launch = module("autocode_provider_launch")
admission = module("autocode_tool_admission")
containment = module("autocode_tool_containment")
support = module("autocode_support")
providers = module("autocode_providers")
KiloProvider = module("providers.kilocode").KiloProvider
opposite = "" if prefix else "autocode_cli."
assert not any(opposite + stem in sys.modules for stem in stems)

workspace = root / "project"
workspace.mkdir()
subprocess.run(["git", "init", "-q", str(workspace)], check=True)
subprocess.run(["git", "-C", str(workspace), "-c", "user.name=Fixture", "-c",
                "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
run = workspace / ".autocode/runs/entrypoint"
binary = workspace / ".venv/bin"
binary.mkdir(parents=True)
selected_python = binary / "python"
sentinel = root / "wrapper-executed"
wrapper = "#!/bin/sh\ntouch " + shlex.quote(str(sentinel)) + "\n"

def write_wrapper():
    selected_python.write_text(wrapper)
    selected_python.chmod(0o755)

write_wrapper()
models = {role: "fixture/reviewer" for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")}
models["terra"] = models["glm"] = "fixture/builder"
adapter = KiloProvider({
    "name": "entryfixture",
    "command": [sys.executable, "-c", "raise AssertionError('no native provider may spawn')"],
    "roles": {role: {"model": model, "effort": "high"} for role, model in models.items()},
    "auth": {"command": ["fixture", "auth", "list"]},
}, root / "provider.toml")
identity = {"engine": "entryfixture", "identity_version": 1, "executable": sys.executable,
            "config_sha256": "a" * 64}
probes, native = [], []

def probe(**kwargs):
    probes.append({"model": kwargs["model"], "effort": kwargs["effort"]})
    # Only after early admission succeeds, invalidate the selected tool. The
    # production late discovery must still reject it before Requirements spend.
    selected_python.unlink()
    write_wrapper()
    directory = kwargs["directory"]
    directory.mkdir(parents=True, exist_ok=True)
    events = [{"type": "step_finish", "sessionID": "fixture", "part": {
        "id": "finish", "type": "step-finish", "sessionID": "fixture", "reason": "stop", "cost": 0.01,
        "tokens": {"input": 100, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}},
    }}]
    events_path = directory / "events.jsonl"
    events_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    return {"exit_code": 0, "timed_out": False, "report": {"ok": True}, "rows": events,
            "report_error": None, "metrics": support.event_metrics(events_path)}

def version(command, **_kwargs):
    native.append("version")
    return subprocess.CompletedProcess(command, 0, "7.8.3", "")

def policy():
    native.append("policy")
    return {"permission": [{"permission": "bash", "pattern": "*", "action": "allow"}]}

def provider_launch(_role, workspace, _run_dir, _session, model, _effort, _write, **kwargs):
    command = [sys.executable, "run", "--agent", "fixture", "--model", model]
    child = {**kwargs["env"], "KILO_CONFIG_CONTENT": json.dumps({"agent": {"fixture": {}}})}
    with (
        patch.object(containment, "backend", return_value=containment.BUBBLEWRAP),
        patch.object(containment.subprocess, "run", side_effect=version),
        patch.object(containment, "_native_debug", return_value=policy),
        patch.object(containment, "prepare", side_effect=AssertionError("late discovery admitted a wrapper")),
    ):
        return containment.configure(command, child, workspace, allow_write=False,
                                     request=kwargs["containment"], transport="kilocode")

def invoke(arguments):
    output, errors = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["autocode", "--workspace", str(workspace), *arguments]), \
         contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        try:
            if mode == "package":
                runpy.run_module("autocode_cli", run_name="__main__")
            else:
                runpy.run_path(str(runtime / "autocode.py"), run_name="__main__")
        except SystemExit as stopped:
            code = stopped.code
        else:
            raise AssertionError("The actual CLI entrypoint did not exit")
    return {"code": code, "stdout": output.getvalue(), "stderr": errors.getvalue()}

environment = {"PATH": str(binary) + os.pathsep + os.environ["PATH"], "AUTOCODE_HOME": str(root / "registry"),
               "XDG_CONFIG_HOME": str(root / "config"), "CODEX_HOME": str(root / "codex"),
               "AUTOCODE_PROVIDER": "entryfixture"}
with contextlib.ExitStack() as stack:
    stack.enter_context(patch.dict(os.environ, environment))
    stack.enter_context(patch.object(providers, "resolve", return_value=adapter))
    for name, value in (("local_settings", identity), ("available_models", set(models.values())),
                        ("check_models", None), ("check_subscription_routes", None)):
        stack.enter_context(patch.object(adapter, name, return_value=value))
    stack.enter_context(patch.object(preflight.transport, "execute", side_effect=probe))
    stack.enter_context(patch.object(adapter, "launch", side_effect=provider_launch))
    exact = [arg for role in ("astra", "terra", "sol", "glm", "plan_reviewer", "requirements", "completion", "resolver")
             for arg in ("--" + role.replace("_", "-") + "-reasoning-effort", "high")]
    initial = invoke(["--brief", "Build a greeting CLI", "--in-place", "--new-run-dir", str(run),
                      "--provider", "entryfixture", "--no-chat", "--evidence-provenance", "fake",
                      "--joint-planning", "--no-adaptive-planning", "--no-auto-select",
                      "--requirements-model", "fixture/reviewer", "--max-cost-usd", "0", *exact])
    initial_view = invoke(["--run-dir", str(run), "--status"])
    initial.update(view=json.loads(initial_view["stdout"])["view"], probes=list(probes), native=list(native))
    initial["wrapper_executed"] = sentinel.exists()
    selected_python.unlink()
    selected_python.symlink_to(Path(sys.executable).resolve())
    resumed = invoke(["--run-dir", str(run), "--resume-paused", "--no-chat"])
    resumed_view = invoke(["--run-dir", str(run), "--status"])
    resumed.update(view=json.loads(resumed_view["stdout"])["view"], probes=list(probes), native=list(native))
    resumed["wrapper_executed"] = sentinel.exists()

assert not any(opposite + stem in sys.modules for stem in stems)
origins = {stem: {"file": str(Path(sys.modules[prefix + stem].__file__).resolve()),
                  "package": sys.modules[prefix + stem].__package__} for stem in stems}
print(json.dumps({"mode": mode, "initial": initial, "resumed": resumed, "origins": origins}))
"""


@unittest.skipUnless(sys.platform == "linux", "selected ELF admission requires supported Linux")
class FreshEntrypointToolAdmissionTests(unittest.TestCase):
    def test_source_entrypoint_refuses_before_probe_and_preserves_late_admission(self):
        self.check_entrypoint("source")

    def test_package_entrypoint_refuses_before_probe_and_preserves_late_admission(self):
        self.check_entrypoint("package")

    def check_entrypoint(self, mode):
        runtime = Path(__file__).resolve().parents[1] / "tools"
        with tempfile.TemporaryDirectory(prefix="tool-admission-entrypoint-") as directory:
            # Keep the normal environment, including CI's coverage startup
            # instrumentation; the child supplies only its own project/account roots.
            result = subprocess.run(
                [sys.executable, "-c", ENTRYPOINT_FIXTURE, mode, str(runtime), directory],
                cwd=directory,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                text=True,
                timeout=180,
            )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        observation = json.loads(result.stdout)
        initial, resumed = observation["initial"], observation["resumed"]
        for launch_result in (initial, resumed):
            self.assertEqual(2, launch_result["code"], launch_result)
            view = launch_result["view"]
            self.assertEqual("PAUSED_TOOL_CONTAINMENT", view["needs"].get("pause_origin") or view["status"])
            self.assertIn("not a native ELF executable", view["stop_reason"])
            self.assertFalse(launch_result["wrapper_executed"])
        self.assertEqual([], initial["probes"], initial)
        self.assertEqual([], initial["native"], initial)
        self.assertEqual([], initial["view"]["usage"]["accounting"]["attempts"])
        self.assertEqual([{"model": "fixture/builder", "effort": "high"}], resumed["probes"])
        self.assertEqual(["version", "policy"], resumed["native"])
        attempts = resumed["view"]["usage"]["accounting"]["attempts"]
        self.assertEqual(["model_preflight"], [row["stage"] for row in attempts if not row["runner_owned"]])
        for stem, origin in observation["origins"].items():
            self.assertEqual(str(runtime / (stem + ".py")), origin["file"])
            self.assertEqual("autocode_cli" if mode == "package" else "", origin["package"])
