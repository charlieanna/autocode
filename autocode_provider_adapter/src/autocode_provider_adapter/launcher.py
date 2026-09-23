"""Run unchanged, pinned Autocode with the external GoCode transport."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .compatibility import CompatibilityError, CompatibilityManifest
from .runtime import GoCodeFacade, load_native_upstream_runner, load_upstream_runner
from .sync import SyncError, UpstreamSynchronizer
from .transport import GoCodeTransport, TransportError


def _transport() -> GoCodeTransport:
    return GoCodeTransport(CompatibilityManifest.default())


def _verify_record(checkout: Path, record: Path, manifest: CompatibilityManifest) -> None:
    try:
        document = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SyncError(f"cannot read the required upstream pin record: {record}") from error
    expected_commit = document.get("upstream_commit") if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get("compatibility_manifest") != manifest.identity
            or not isinstance(expected_commit, str)):
        raise SyncError("upstream pin record does not match this adapter manifest")
    completed = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True, capture_output=True,
    )
    current = completed.stdout.strip()
    if completed.returncode or current != expected_commit:
        raise SyncError(f"upstream checkout must remain pinned to {expected_commit}; found {current or 'unknown'}")


def _runner(checkout: Path, record: Path, provider: str):
    checkout = checkout.resolve()
    manifest = CompatibilityManifest.default()
    _verify_record(checkout, record.resolve(), manifest)
    manifest.verify(checkout)
    if provider == "opencode":
        return load_native_upstream_runner(checkout)
    if provider == "gocode":
        return load_upstream_runner(checkout, GoCodeFacade(_transport()))
    try:
        plugin = importlib.import_module("autocode_provider_" + provider)
        facade = plugin.create_facade(manifest)
    except (ImportError, AttributeError) as error:
        raise TransportError(
            f"provider plugin {provider!r} is unavailable; install autocode-provider-{provider}"
        ) from error
    return load_upstream_runner(checkout, facade)


def _run(checkout: Path, record: Path, provider: str, arguments: list[str]) -> int:
    module = _runner(checkout, record, provider)
    sys.argv = [str(checkout.resolve() / "tools/autocode.py"), *arguments]
    return int(module.cli())


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Preserve the original sync-only CLI for existing local scripts.
    if argv and argv[0].startswith("--") and argv[0] not in {"--help", "-h"}:
        argv.insert(0, "sync")
    parser = argparse.ArgumentParser(description="Use unchanged Autocode through a selectable provider")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync", help="fetch and compatibility-check an upstream pin")
    sync.add_argument("--upstream", type=Path, required=True, help="clean Git checkout with the configured upstream remote")
    sync.add_argument("--checkout", type=Path, required=True, help="new or matching detached worktree path")
    sync.add_argument("--manifest", type=Path, help="versioned compatibility manifest (defaults to bundled v1)")
    sync.add_argument("--record", type=Path, required=True, help="adapter-owned pin record path")
    run = subparsers.add_parser("run", help="run or resume the pinned upstream CLI")
    run.add_argument("--checkout", type=Path, required=True)
    run.add_argument("--record", type=Path, required=True)
    run.add_argument("--provider", default="opencode", help="opencode, gocode, or an installed provider plugin")
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    models = subparsers.add_parser("models", help="print dashboard-compatible GoCode models")
    models.add_argument("--provider", default="gocode")
    models.add_argument("--workspace", type=Path, default=Path.cwd())
    dashboard = subparsers.add_parser("dashboard", help="run the unchanged upstream dashboard")
    dashboard.add_argument("--checkout", type=Path, required=True)
    dashboard.add_argument("--record", type=Path, required=True)
    dashboard.add_argument("--provider", default="opencode")
    dashboard.add_argument("arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "sync":
            manifest = CompatibilityManifest.from_file(arguments.manifest) if arguments.manifest else CompatibilityManifest.default()
            prepared = UpstreamSynchronizer(manifest).prepare(
                arguments.upstream, arguments.checkout, record_path=arguments.record
            )
            print(json.dumps({"checkout": str(prepared.checkout), "manifest": prepared.manifest_identity, "commit": prepared.commit}))
            return 0
        if arguments.command == "run":
            forwarded = arguments.arguments[1:] if arguments.arguments[:1] == ["--"] else arguments.arguments
            return _run(arguments.checkout, arguments.record, arguments.provider, forwarded)
        if arguments.command == "models":
            if arguments.provider != "gocode":
                raise TransportError("model listing currently requires the selected provider's plug-in")
            for model in _transport().dashboard_catalogue(arguments.workspace.resolve()):
                print("openai/" + model)
            return 0
        if arguments.command == "dashboard":
            return _dashboard(arguments.checkout, arguments.record, arguments.provider, arguments.arguments)
        raise AssertionError(arguments.command)
    except (CompatibilityError, SyncError, TransportError, OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))


def _dashboard(checkout: Path, record: Path, provider: str, arguments: list[str]) -> int:
    checkout = checkout.resolve()
    manifest = CompatibilityManifest.default()
    _verify_record(checkout, record.resolve(), manifest)
    manifest.verify(checkout)
    if provider == "opencode":
        load_native_upstream_runner(checkout)
    elif provider == "gocode":
        facade = GoCodeFacade(_transport())
        load_upstream_runner(checkout, facade)
    else:
        raise TransportError(f"dashboard provider plugin {provider!r} is unavailable")
    module = __import__("tools.dashboard.agent_console", fromlist=["main"])
    conversations = __import__("tools.dashboard.dashboard_conversations", fromlist=["_prompt"])
    if provider == "opencode":
        forwarded = arguments[1:] if arguments[:1] == ["--"] else arguments
        sys.argv = [str(checkout / "tools/dashboard/agent_console.py"), *forwarded]
        return int(module.main() or 0)
    os.environ["AUTOCODE_GOCODE_CHECKOUT"] = str(checkout)
    os.environ["AUTOCODE_GOCODE_PIN_RECORD"] = str(record.resolve())
    runner = Path(__file__).with_name("dashboard_runner.py")
    base_console = module.Console
    catalogue_command = (sys.executable, str(runner), "--models")
    class GoCodeConsole(base_console):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("catalogue_command", catalogue_command)
            kwargs.setdefault(
                "conversation_provider",
                lambda messages, model, workdir: facade.conversation_provider(
                    conversations._prompt(messages), model, workdir
                ),
            )
            super().__init__(*args, **kwargs)
    module.Console = GoCodeConsole
    forwarded = arguments[1:] if arguments[:1] == ["--"] else arguments
    sys.argv = [str(checkout / "tools/dashboard/agent_console.py"), "--runner", str(runner), *forwarded]
    return int(module.main() or 0)
