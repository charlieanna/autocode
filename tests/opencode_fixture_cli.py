"""Explicit offline CLI bootstrap for the checked-in fake OpenCode/Codex bundle.

Only that bundle may run through this transport. Nothing in production imports
this module or selects it from fixture env vars. It is no proof of OpenCode's own
tool dispatch. By default no launch is contained. With native_boundary and
AUTOCODE_FIXTURE_QUALIFIED_OPENCODE=1 (#709) the runner's side is real: run setup,
each launch's conformance checks and the kernel shell are the production ones, and
only the conformance commands and a planted AUTOCODE_FIXTURE_SHELL_ESCAPE write run
in that shell; the fake's own work (the scripted Codex delegate's edits and checks)
still runs outside it.
"""

import importlib
import os
import runpy
import shutil
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
TIMEOUT_ONCE = """
if data["stage"] == "astra_challenge":
    marker = Path(os.environ["AUTOCODE_FIXTURE_TIMEOUT_ONCE"])
    if not marker.exists():
        marker.write_text("first challenge attempt")
        import time
        time.sleep(60)
with tempfile.TemporaryDirectory(dir=fixture_root) as temp:"""
TRANSIENT_VALIDATOR_WRITE = """
    if data.get("stage") == "sol":
        probe = Path("validator-probe.tmp")
        emit("step_start", {"id": "probe-start", "type": "step-start", "snapshot": "a" * 40})
        probe.write_text("a forbidden reviewer probe")
        emit("step_finish", {"id": "probe-write", "type": "step-finish", "snapshot": "b" * 40,
                             "reason": "tool-calls", "tokens": {"input": 0, "output": 0, "reasoning": 0,
                                                               "cache": {"read": 0, "write": 0}}})
        probe.unlink()
        emit("step_start", {"id": "probe-restored", "type": "step-start", "snapshot": "a" * 40})
"""


NATIVE_BOUNDARY = "--native-boundary"
QUALIFIED_AT_SETUP = "--native-boundary-qualified-at-setup"


def entrypoint(entry, *, native_boundary=False, qualified_at_setup=False):
    """Retain the selected CLI script, including an installed console entrypoint.

    native_boundary keeps the runner's own tool-boundary checks (#413): only the
    client process is the checked fake, so its --version is what the run setup and
    every launch see. That is 1.18.31, which both refuse, unless the test sets
    AUTOCODE_FIXTURE_QUALIFIED_OPENCODE=1: then it is 1.18.33, the fake answers the
    model-free conformance checks, and each launch is contained by the real kernel
    boundary (tests/test_contained_fake_cli.py). qualified_at_setup also keeps every
    launch's checks but has the run-setup check report a qualified host, as when
    OpenCode changes after a run starts: the first contained launch then fails its
    boundary.
    """
    target = entry[1:] if entry[0] == sys.executable else entry
    if len(target) != 1:
        raise ValueError("Fixture bootstrap requires one CLI script")
    mode = [QUALIFIED_AT_SETUP] if qualified_at_setup else [NATIVE_BOUNDARY] if native_boundary else []
    return [sys.executable, str(Path(__file__).resolve()), *mode, target[0]]


def checked_fixture(executable="opencode", *, env=None):
    env = os.environ if env is None else env
    selected = shutil.which(str(executable), path=env.get("PATH", ""))
    if not selected:
        raise RuntimeError("Offline bootstrap requires the known fake OpenCode bundle")
    selected = Path(selected).resolve()
    original = (TOOLS / "fake_opencode.py").read_bytes()
    allowed = (
        original,
        original.replace(b"with tempfile.TemporaryDirectory(dir=fixture_root) as temp:", TIMEOUT_ONCE.encode()),
        original.replace(
            b"    final = report.read_text()", TRANSIENT_VALIDATOR_WRITE.encode() + b"    final = report.read_text()"
        ),
    )
    if selected.read_bytes() not in allowed:
        raise RuntimeError("Offline bootstrap refuses an unknown OpenCode executable")
    for name, source in (("codex", "fake_codex.py"), ("goal_fixtures.py", "goal_fixtures.py")):
        sibling = selected.with_name(name)
        if not sibling.is_file() or sibling.read_bytes() != (TOOLS / source).read_bytes():
            raise RuntimeError("Offline bootstrap refuses an unknown delegated fixture: " + name)
    return selected


def main():
    checked_fixture()  # Refuse real clients before even metadata/auth preflight.
    native = sys.argv[1] if sys.argv[1:2] in ([NATIVE_BOUNDARY], [QUALIFIED_AT_SETUP]) else None
    if native:
        del sys.argv[1]
    target = shutil.which(sys.argv[1]) or sys.argv[1]
    sys.argv = [target, *sys.argv[2:]]
    sys.path.insert(0, str(TOOLS))
    modules = [importlib.import_module("autocode_provider_launch")]
    supervision_modules = [importlib.import_module("autocode_supervision")]
    boundaries = [importlib.import_module("autocode_tool_containment")]
    # Installed CLI scripts import the package namespace instead of tools/ scripts. Any other
    # target — a tools/ script such as the Builder worker, or a copied bundle — runs on the
    # tools/ namespace above and must not require the installed package: without it installed,
    # the worker died at this import before writing any result and read as INTERRUPTED (#2442).
    if not Path(target).resolve().is_relative_to(TOOLS):
        modules.append(importlib.import_module("autocode_cli.autocode_provider_launch"))
        supervision_modules.append(importlib.import_module("autocode_cli.autocode_supervision"))
        boundaries.append(importlib.import_module("autocode_cli.autocode_tool_containment"))

    def offline_launch(original):
        @contextmanager
        def launch(args, *positional, **kwargs):
            if isinstance(args, (list, tuple)) and Path(str(args[0])).name == "opencode":
                fake = checked_fixture(args[0], env=kwargs.get("env"))
                # Authenticate and resolve the provider before the real guard
                # admits its bootstrap; the bootstrap executes this exact path.
                args = [str(fake), *args[1:]]
            with original(args, *positional, **kwargs) as child:
                yield child

        return launch

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

    if native:
        print("TEST-ONLY fake OpenCode client; the runner's launch boundary checks are unchanged", file=sys.stderr)
        with ExitStack() as stack:
            for module in supervision_modules:
                stack.enter_context(patch.object(module, "launch", offline_launch(module.launch)))
            if native == QUALIFIED_AT_SETUP:
                for module in boundaries:
                    stack.enter_context(patch.object(module, "unavailable", lambda *args, **kwargs: None))
            runpy.run_path(target, run_name="__main__")
        return
    print("TEST-ONLY simulated OpenCode transport; no kernel containment", file=sys.stderr)
    with ExitStack() as stack:
        for module in supervision_modules:
            stack.enter_context(patch.object(module, "launch", offline_launch(module.launch)))
        for module in modules:
            stack.enter_context(patch.object(module, "prepare", simulated_prepare(module.prepare)))
        # The run-setup check (#413) would refuse the fake's version; every launch above is
        # already simulated without the boundary, so setup is not asked about it either.
        for module in boundaries:
            stack.enter_context(patch.object(module, "unavailable", lambda *args, **kwargs: None))
        runpy.run_path(target, run_name="__main__")


if __name__ == "__main__":
    main()
