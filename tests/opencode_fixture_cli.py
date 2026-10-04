"""Explicit offline CLI bootstrap, never a native/kernel conformance test.

Only the checked-in fake OpenCode/Codex bundle may run through this transport.
Nothing in production imports this module or selects it from fixture env vars.
"""
from contextlib import ExitStack
import importlib
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[1] / "tools"
TIMEOUT_ONCE = '''
if data["stage"] == "astra_challenge":
    marker = Path(os.environ["AUTOCODE_FIXTURE_TIMEOUT_ONCE"])
    if not marker.exists():
        marker.write_text("first challenge attempt")
        import time
        time.sleep(60)
with tempfile.TemporaryDirectory() as temp:'''


def entrypoint(entry):
    """Retain the selected CLI script, including an installed console entrypoint."""
    target = entry[1:] if entry[0] == sys.executable else entry
    if len(target) != 1:
        raise ValueError("Fixture bootstrap requires one CLI script")
    return [sys.executable, str(Path(__file__).resolve()), target[0]]


def checked_fixture(executable="opencode", *, env=None):
    env = os.environ if env is None else env
    selected = shutil.which(str(executable), path=env.get("PATH", ""))
    if not selected:
        raise RuntimeError("Offline bootstrap requires the known fake OpenCode bundle")
    selected = Path(selected).resolve()
    original = (TOOLS / "fake_opencode.py").read_bytes()
    allowed = (original, original.replace(b"with tempfile.TemporaryDirectory() as temp:",
                                         TIMEOUT_ONCE.encode()))
    if selected.read_bytes() not in allowed:
        raise RuntimeError("Offline bootstrap refuses an unknown OpenCode executable")
    for name, source in (("codex", "fake_codex.py"), ("goal_fixtures.py", "goal_fixtures.py")):
        sibling = selected.with_name(name)
        if not sibling.is_file() or sibling.read_bytes() != (TOOLS / source).read_bytes():
            raise RuntimeError("Offline bootstrap refuses an unknown delegated fixture: " + name)
    return selected


def main():
    checked_fixture()  # Refuse real clients before even metadata/auth preflight.
    target = shutil.which(sys.argv[1]) or sys.argv[1]
    sys.argv = [target, *sys.argv[2:]]
    sys.path.insert(0, str(TOOLS))
    modules = [importlib.import_module("autocode_provider_launch")]
    # Installed CLI scripts import the package namespace instead of tools/ scripts.
    if Path(target).resolve() != (TOOLS / "autocode.py").resolve():
        modules.append(importlib.import_module("autocode_cli.autocode_provider_launch"))
    popen = subprocess.Popen

    def offline_popen(args, *positional, **kwargs):
        if isinstance(args, (list, tuple)) and Path(str(args[0])).name == "opencode":
            fake = checked_fixture(args[0], env=kwargs.get("env"))
            # Resolve again at the actual process boundary; never fall back to PATH.
            args = [str(fake), *args[1:]]
            kwargs["executable"] = str(fake)
        return popen(args, *positional, **kwargs)

    def simulated_prepare(original):
        def prepare(**kwargs):
            if kwargs["engine"] == "opencode":
                checked_fixture()
                if getattr(kwargs["adapter"], "CONFIGURED", False):
                    raise RuntimeError("Offline bootstrap only supports the builtin fake OpenCode adapter")
                kwargs["enforce_tool_boundary"] = False
            result = original(**kwargs)
            if kwargs["engine"] == "opencode":
                worker = result[3]
                if worker.get("tool_containment"):
                    raise RuntimeError("Simulated transport must not claim native containment")
                worker["test_transport"] = {"simulated": True, "kernel_protected": False}
            return result
        return prepare

    print("TEST-ONLY simulated OpenCode transport; no kernel containment", file=sys.stderr)
    with ExitStack() as stack:
        stack.enter_context(patch.object(subprocess, "Popen", offline_popen))
        for module in modules:
            stack.enter_context(patch.object(module, "prepare", simulated_prepare(module.prepare)))
        runpy.run_path(target, run_name="__main__")


if __name__ == "__main__":
    main()
