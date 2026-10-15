"""Native initial-validation acquisition through the original owner and journal."""

import json
import shlex
import uuid
from contextlib import contextmanager
from pathlib import Path

try:
    from . import autocode_command_supervision as commands
    from . import autocode_native_capture_policy as policy
    from . import autocode_native_launch as launch
    from . import autocode_native_parent as parent
    from . import autocode_runner_check as checks
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
    from . import autocode_visual_evidence as evidence
    from . import autocode_visual_profile as profiles
    from . import autocode_visual_protection as protection
except ImportError:
    import autocode_command_supervision as commands
    import autocode_native_capture_policy as policy  # noqa: F401 - preserve existing import behavior
    import autocode_native_launch as launch
    import autocode_native_parent as parent
    import autocode_runner_check as checks
    import autocode_source_scope as source_scope
    import autocode_util as util
    import autocode_visual_evidence as evidence
    import autocode_visual_profile as profiles
    import autocode_visual_protection as protection  # noqa: F401 - preserve existing import behavior


class _Activity:
    idle_limit = tool_limit = 0
    timeout = None

    def __init__(self, boundary):
        self.boundary = boundary

    def poll(self, *, processes, root_pid):
        # Notification only. Original retained process ownership stays in the
        # existing ProcessTree and the target never supplies authority here.
        names = sorted(path.name for path in self.boundary.requests.iterdir())
        return {
            "activity": "capture-asset-request",
            "detail": json.dumps(names),
            "timeout": None,
            "idle_seconds": 0,
            "tool_seconds": 0,
        }


class NativeCaptureExecution:
    def __init__(self, context, state, *, persist):
        policy.require(type(context) is launch.NativeRunContext, "original native context required")
        sink = getattr(persist, "__self__", None)
        policy.require(
            type(sink) is parent.ParentSink
            and getattr(persist, "__func__", None) is parent.ParentSink.write
            and sink.factory is context.factory,
            "original parent sink required",
        )
        self.context, self.state, self.sink = context, state, sink
        self.owner = context.factory.protection
        self.profile = profiles._profile(state)
        self._fresh()

    def _fresh(self):
        self.sink.require_ready()
        snapshot = source_scope.snapshot(self.owner.workspace, self.state)
        facts = self.owner.readonly_task_authority(self.state, snapshot=snapshot)
        policy.require(profiles._profile(self.state) == self.profile, "approved capture profile changed")
        return snapshot, facts

    def capture(self, config_name, *, case, timeout=120):
        snapshot, facts = self._fresh()
        config, pins = policy.declared_config(self.state, snapshot, config_name, case=case)
        boundary = policy.CaptureBoundary(
            self.owner.workspace, self.owner.run, snapshot, config, config_name, pins, self.profile, timeout=timeout
        )
        try:
            handle = self.owner.prepare_capture(self.state, snapshot, boundary)

            @contextmanager
            def held(**options):
                options["options"]["pass_fds"] = boundary.pass_fds
                with self.context.held_check(
                    handle, command=boundary.command, environment=boundary.environment, **options
                ) as original:
                    yield original

            def service(_observation):
                boundary.broker.service()

            with checks.track(
                self.state,
                self.owner.run,
                "native_capture",
                "Acquire approved reference case",
                self.sink.write,
                deferred=True,
            ):
                result = commands.run(
                    shlex.join(boundary.command),
                    boundary.tree,
                    boundary.log,
                    timeout=boundary.remaining(),
                    env=boundary.environment,
                    held_launch=held,
                    activity=_Activity(boundary),
                    activity_checkpoint=service,
                    deadline_monotonic=boundary.deadline,
                )
            policy.require(
                result["exit_code"] == 0 and not result["timed_out"] and not result.get("error"),
                f"actual capture failed (exit={result['exit_code']}, timeout={result['timed_out']}, error={result.get('error')}); browser evidence: {boundary.output / 'browser.json'}",
            )
            ipc_receipt = boundary.transport_result(result)
            broker_receipt = boundary.control / "broker.json"
            util.atomic_json(
                broker_receipt,
                {
                    "nonce": boundary.broker.nonce,
                    "listener": boundary.broker.identity,
                    "responses": boundary.broker.complete(),
                },
            )
            broker_receipt.chmod(0o400)
            selector = self.owner.finish_check(
                handle, result, after=source_scope.snapshot(self.owner.workspace, self.state)
            )
            # Native finish authenticates actual waitpid and terminal cleanup.
            # CAPTURED remains acquisition only, never an image PASS.
            boundary.require_current(live=False)
            folder = (
                self.owner.workspace
                / ".autocode"
                / "captures"
                / (util.now().replace(":", "-") + "-native-" + uuid.uuid4().hex)
            )
            policy.require(not folder.parent.is_symlink(), "aliased capture publication directory")
            folder.mkdir(parents=True, mode=0o700)
            artifacts = {}
            for key, name in (
                ("candidate", "candidate.png"),
                ("browser", "browser.json"),
                ("native_ipc", "native-ipc.json"),
            ):
                original = ipc_receipt if key == "native_ipc" else evidence.local_file(boundary.output, name)
                target = folder / name
                with target.open("xb") as stream:
                    stream.write(original.read_bytes())
                target.chmod(0o400)
                artifacts[key] = {
                    "path": str(target.relative_to(self.owner.workspace)),
                    "sha256": util.file_hash(target),
                }
            body = {
                "version": 1,
                "kind": "implementation_capture",
                "reference_hash": config["reference_hash"],
                "case": config["case"],
                "source_revision": snapshot["revision"],
                "config_ref": config_name,
                "inputs": pins,
                "engine": {
                    name: util.file_hash(Path(__file__).with_name(name))
                    for name in ("autocode_visual_capture.py", "autocode_visual_browser.cjs")
                },
                "build_executed": False,
                "artifacts": artifacts,
                "native_check": selector,
            }
            manifest = folder / "manifest.json"
            util.atomic_json(manifest, body)
            manifest.chmod(0o400)
            try:
                evidence.verify(self.state, str(manifest), case=case, current=snapshot)
                capture = {"capture_ref": str(manifest), "capture_sha256": util.file_hash(manifest)}
                self.owner.retain_capture(self.state, snapshot=snapshot, handle=handle, capture=capture)
                return capture
            except BaseException:
                manifest.rename(folder / "rejected-manifest.json")
                raise
        finally:
            boundary.close()

    def all_cases(self):
        snapshot, _facts = self._fresh()
        cases = self.state["settings"]["design_manifest"]["body"]["cases"]
        configs = {}
        for name in snapshot["files"]:
            if not name.endswith(".json"):
                continue
            path = evidence.local_file(self.owner.workspace, name)
            if path.stat().st_size > 1024 * 1024:
                continue
            try:
                value = profiles._json(path.read_bytes())
            except (ValueError, UnicodeError):
                continue
            if isinstance(value, dict) and value.get("reference_hash") == evidence.reference_hash(
                self.state["settings"]
            ):
                ident = (value.get("case") or {}).get("id")
                if ident in {case["id"] for case in cases}:
                    policy.require(ident not in configs, "ambiguous approved capture configuration")
                    configs[ident] = name
        policy.require(
            set(configs) == {case["id"] for case in cases},
            "every approved reference case needs one current source configuration",
        )
        return {case["id"]: self.capture(configs[case["id"]], case=case) for case in cases}
