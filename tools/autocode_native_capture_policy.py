"""Exact readonly acquisition inputs and kernel policy for native capture.

No controller imports, process execution or proof adoption. The original native
owner supplies approval; this object retains one real socket capability and the
exact clean source/runtime/output boundary for its one acquisition.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import fcntl
import json
import os
import shutil
import socket
import stat
import time
import uuid
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_capture_broker as broker
    from . import autocode_command_receipt as receipts
    from . import autocode_tool_containment as containment
    from . import autocode_util as util
    from . import autocode_visual_capture as capture
    from . import autocode_visual_evidence as evidence
    from . import autocode_visual_profile as profiles
except ImportError:
    import autocode_capture_broker as broker
    import autocode_command_receipt as receipts
    import autocode_tool_containment as containment
    import autocode_util as util
    import autocode_visual_capture as capture
    import autocode_visual_evidence as evidence
    import autocode_visual_profile as profiles


def require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native capture: " + reason)


def runtime_inputs(workspace, profile):
    selected = profile.get("capture_runtime")
    require(isinstance(selected, dict), "original approved profile has no capture runtime")
    manifest = evidence.local_file(workspace, selected["path"])
    require(
        util.file_hash(manifest) == selected["sha256"] and manifest.stat().st_mode & 0o222 == 0,
        "capture runtime manifest changed or is writable",
    )
    value = profiles._json(manifest.read_bytes())
    require(
        isinstance(value, dict)
        and set(value) == {"version", "node", "playwright_module", "browser", "files"}
        and value["version"] == 1
        and isinstance(value["files"], dict)
        and value["files"],
        "invalid exact-file runtime manifest",
    )
    root = Path(workspace).resolve()
    for name, sha in value["files"].items():
        path = Path(name)
        require(
            path.is_absolute()
            and ".." not in path.parts
            and str(path) == name
            and not path.is_relative_to(root)
            and path.resolve(strict=True) == path
            and stat.S_ISREG(path.lstat().st_mode)
            and profiles._sha(sha)
            and util.file_hash(path) == sha,
            "runtime file is missing, aliased, foreign or changed",
        )
        require(
            path.name not in ("auth.json", ".npmrc", "id_rsa", "id_ed25519") and not path.name.startswith(".env"),
            "credential files cannot be runtime inputs",
        )
    for key in ("node", "playwright_module", "browser"):
        require(
            isinstance(value[key], str) and value[key] in value["files"],
            "runtime entry point lacks exact file identity",
        )
    require(
        os.access(value["node"], os.X_OK) and os.access(value["browser"], os.X_OK),
        "runtime entry point is not executable",
    )
    return value, {str(manifest): selected["sha256"], **value["files"]}


def browser_directories(runtime):
    """Only enumerate exact browser directories derived from pinned file paths."""
    root = Path(runtime["browser"]).parent
    selected = {root}
    for name in runtime["files"]:
        path = Path(name)
        if path.is_relative_to(root):
            selected.update(parent for parent in path.parents if parent.is_relative_to(root))
    for path in selected:
        require(
            path.resolve(strict=True) == path and stat.S_ISDIR(path.lstat().st_mode),
            "browser resource directory is missing or aliased",
        )
    return "".join("(allow file-read-data (literal " + json.dumps(str(path)) + "))\n" for path in sorted(selected))


BROWSER_ARGUMENTS = (
    "--disable-field-trial-config",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-back-forward-cache",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-component-extensions-with-background-pages",
    "--disable-component-update",
    "--no-default-browser-check",
    "--disable-default-apps",
    "--disable-dev-shm-usage",
    "--disable-edgeupdater",
    "--disable-extensions",
    "--disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,BoundaryEventDispatchTracksNodeRemoval,DestroyProfileOnBrowserClose,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,BlockOriginHeaderModificationOnRedirect,Translate,AutoDeElevate,OptimizationHints,msForceBrowserSignIn,msEdgeUpdateLaunchServicesPreferredVersion",
    "--enable-features=CDPScreenshotNewSurface",
    "--allow-pre-commit-input",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--disable-updater-scheduler",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
    "--no-first-run",
    "--password-store=basic",
    "--use-mock-keychain",
    "--no-service-autorun",
    "--export-tagged-pdf",
    "--disable-search-engine-choice-screen",
    "--unsafely-disable-devtools-self-xss-warnings",
    "--edge-skip-compat-layer-relaunch",
    "--disable-infobars",
    "--disable-search-engine-choice-screen",
    "--disable-sync",
    "--enable-unsafe-swiftshader",
    "--headless",
    "--hide-scrollbars",
    "--mute-audio",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
    "--no-sandbox",
    "--remote-debugging-pipe",
    "--no-startup-window",
)


def fixed_executable_pins():
    """Trusted OS trampoline and sandbox tool; no discovered executable input."""
    pins = {}
    for name in ("/bin/sh", "/usr/bin/sandbox-exec"):
        path = Path(name)
        info = path.lstat()
        require(
            path.resolve(strict=True) == path
            and stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and info.st_mode & 0o022 == 0,
            "fixed native executable is aliased or not administrator controlled",
        )
        pins[name] = util.file_hash(path)
    return pins


def socket_identity(sock):
    value = sock.fileno()
    info = os.fstat(value)
    require(
        value > 4 and sock.family == socket.AF_UNIX and sock.type == socket.SOCK_STREAM and stat.S_ISSOCK(info.st_mode),
        "capture IPC must be the original inherited Unix stream socket",
    )
    return {"fd": value, "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode}


def browser_arguments(scratch):
    # Exact prior pinned Playwright headless defaults, with only its private
    # user-data directory parent-derived. No remote-debugging TCP endpoint.
    return list(BROWSER_ARGUMENTS) + ["--user-data-dir=" + str(Path(scratch) / "writable" / "browser-profile")]


def parent_request(config, scratch, runtime, policy_path, ipc, listener, *, nonce, timeout_ms):
    scratch = Path(scratch)
    return {
        **config,
        "output": str(scratch / "writable" / "output"),
        "timeout_ms": timeout_ms,
        "native_transport": {
            "nonce": nonce,
            "listener": listener,
            "requests": str(scratch / "writable" / "requests"),
            "responses": str(scratch / "responses"),
            "cdp": {"nonce": nonce, "socket": ipc["fixture"]},
        },
        "native_bootstrap": {
            "fixture_script": str(Path(__file__).with_name("autocode_visual_browser.cjs").resolve()),
            "policy": str(policy_path),
            "policy_sha256": util.file_hash(policy_path),
            "ipc": ipc,
            "browser_arguments": browser_arguments(scratch),
            "record": str(scratch.parent / "capture-ipc.json"),
        },
    }


def declared_config(state: "RunState", snapshot, name, *, case):
    root = Path(state["workspace"]).resolve()
    path = evidence.local_file(root, name)
    relative = path.relative_to(root).as_posix()
    require(snapshot["files"].get(relative) == util.file_hash(path), "capture config is not approved current source")
    value = profiles._json(path.read_bytes())
    inputs = capture.config_inputs(root, path, value)
    require(
        value["case"] == evidence.case_binding(case)
        and value["reference_hash"] == evidence.reference_hash(state["settings"]),
        "capture config differs from approved case/reference",
    )
    require(not value["build_command"], "native build-producing capture is not yet supported")
    names = [*inputs, *(row["path"] for row in value["assets"])]
    for item in names:
        file = evidence.local_file(root, item)
        require(
            snapshot["files"].get(item) == util.file_hash(file), "capture input is outside approved current inventory"
        )
    # A config may describe its inventory but cannot narrow the parent source.
    if "source_paths" in value:
        require(
            value["source_paths"] == snapshot.get("source_paths", []),
            "capture config narrows or changes approved inventory",
        )
    return value, {name: util.file_hash(evidence.local_file(root, name)) for name in names}


class CaptureBoundary:
    def __init__(self, workspace, run_dir, snapshot, config, config_name, input_pins, profile, *, timeout):
        self.workspace, self.run = Path(workspace).resolve(), Path(run_dir).resolve()
        require(
            type(timeout) in (int, float) and 0 < timeout <= 120,
            "capture timeout must be within the original 120-second bound",
        )
        self.deadline = time.monotonic() + timeout
        self.snapshot, self.config = deepcopy(snapshot), deepcopy(config)
        self.config_name, self.inputs = config_name, deepcopy(input_pins)
        self.profile = deepcopy(profile)
        self.runtime, runtime_pins = runtime_inputs(self.workspace, profile)
        require(
            config.get("playwright_module") == self.runtime["playwright_module"]
            and config.get("executable") == self.runtime["browser"],
            "config and approved runtime entry points differ",
        )
        self.control = self.workspace / ".autocode" / ("tool-containment-" + uuid.uuid4().hex)
        require(not self.control.parent.is_symlink(), "aliased private workspace")
        self.control.mkdir(mode=0o700)
        self.scratch = self.control / "scratch"
        self.scratch.mkdir(mode=0o700)
        self.tree = self.scratch / "source"
        self.tree.mkdir(mode=0o700)
        self.writable = self.scratch / "writable"
        self.writable.mkdir(mode=0o700)
        self.requests = self.writable / "requests"
        self.requests.mkdir(mode=0o700)
        self.responses = self.scratch / "responses"
        self.responses.mkdir(mode=0o700)
        self.output = self.writable / "output"
        self.output.mkdir(mode=0o700)
        self.home = self.writable / "home"
        self.home.mkdir(mode=0o700)
        for name, identity in snapshot["files"].items():
            path = evidence.local_file(self.workspace, name)
            require(identity == util.file_hash(path), "source links/submodules are not supported by native capture")
            target = self.tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            target.chmod(0o400)
        self.broker = broker.AssetBroker(
            self.tree,
            config["assets"],
            self.requests,
            self.responses,
            deadline=self.deadline,
            preview_origin=config.get("preview_origin"),
        )
        self.ipc_sockets = []
        try:
            original_pair = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                for item in original_pair:
                    self.ipc_sockets.append(socket.socket(fileno=fcntl.fcntl(item.fileno(), fcntl.F_DUPFD_CLOEXEC, 10)))
            finally:
                for item in original_pair:
                    item.close()
        except BaseException:
            self.close()
            raise
        self.ipc = dict(zip(("bridge", "fixture"), map(socket_identity, self.ipc_sockets), strict=False))
        self.pass_fds = (self.broker.socket.fileno(), *(item.fileno() for item in self.ipc_sockets))
        try:
            self.pin_files = {**runtime_pins, **fixed_executable_pins()}
            for name in (
                "autocode_visual_capture.py",
                "autocode_visual_browser.cjs",
                "autocode_native_capture_browser.cjs",
                "autocode_capture_bootstrap.cjs",
            ):
                path = Path(__file__).with_name(name).resolve()
                self.pin_files[str(path)] = util.file_hash(path)
            self.log = self.run / ("capture-" + uuid.uuid4().hex + ".log")
            self.policy_path = self.control / "capture.sb"
            self.policy_path.write_text(self.policy())
            self.policy_path.chmod(0o400)
            self.pin_files[str(self.policy_path)] = util.file_hash(self.policy_path)
            self.request = self.scratch / "capture-request.json"
            # Config remains unmodified in source; only this exact parent request
            # adds its private capability transport and destination.
            request = parent_request(
                config,
                self.scratch,
                self.runtime,
                self.policy_path,
                self.ipc,
                self.broker.identity,
                nonce=self.broker.nonce,
                timeout_ms=max(1, int(self.remaining() * 1000)),
            )
            util.atomic_json(self.request, request)
            self.request.chmod(0o400)
            self.pin_files[str(self.request)] = util.file_hash(self.request)
            self.command = [
                self.runtime["node"],
                str(Path(__file__).with_name("autocode_capture_bootstrap.cjs").resolve()),
                str(self.request),
            ]
            self.environment = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(self.home),
                "TMPDIR": str(self.home),
                "TMP": str(self.home),
                "TEMP": str(self.home),
                "OPENSSL_CONF": "/dev/null",
                "LANG": "en_US.UTF-8",
                "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
            }
        except BaseException:
            self.close()
            raise

    def remaining(self):
        value = self.deadline - time.monotonic()
        require(value > 0, "original capture deadline expired")
        return value

    def policy(self):
        text = containment._render_policy(
            self.workspace,
            self.scratch,
            read_workspace=False,
            read_roots=tuple(self.runtime["files"])
            + tuple(
                str(Path(__file__).with_name(name).resolve())
                for name in ("autocode_visual_browser.cjs", "autocode_native_capture_browser.cjs")
            ),
            denied_read_paths=(self.workspace,),
            read_exceptions=(self.scratch,),
            write_roots=(self.writable,),
            denied_write_paths=(self.tree, self.responses),
            write_files=(self.log,),
        )
        text += browser_directories(self.runtime)
        port = self.broker.identity["address"][1]
        return (
            text
            + '(allow sysctl-read (sysctl-name "kern.osproductversion"))\n'
            + f'(allow network-inbound (local tcp "localhost:{port}"))\n(deny network-bind network-outbound)\n'
        )

    def require_current(self, *, live=True):
        if live:
            self.broker.require_current()
            require(
                self.ipc == dict(zip(("bridge", "fixture"), map(socket_identity, self.ipc_sockets), strict=False)),
                "original inherited CDP socketpair changed",
            )
        for name, sha in self.pin_files.items():
            require(util.file_hash(name) == sha, "sealed capture runtime/input changed")
        require(self.policy_path.read_text() == self.policy(), "capture kernel boundary changed")
        for name, sha in self.snapshot["files"].items():
            require(
                util.file_hash(evidence.local_file(self.workspace, name)) == sha
                and util.file_hash(evidence.local_file(self.tree, name)) == sha,
                "source changed during capture",
            )

    def transport_result(self, result):
        path = evidence.local_file(self.control, "capture-ipc.json")
        require(path.stat().st_mode & 0o222 == 0, "transport result must be immutable")
        actual = profiles._json(path.read_bytes())
        keys = {
            "version",
            "root_pid",
            "browser_pid",
            "fixture_pid",
            "policy_sha256",
            "mach_service",
            "browser_exit",
            "fixture_exit",
            "connected",
            "nonce",
            "status",
        }
        require(
            isinstance(actual, dict)
            and set(actual) == keys
            and actual["version"] == 1
            and actual["status"] == "TRANSPORT_COMPLETE"
            and actual["connected"] is True
            and actual["nonce"] == self.broker.nonce
            and actual["policy_sha256"] == self.pin_files[str(self.policy_path)]
            and actual["browser_exit"] == actual["fixture_exit"] == {"code": 0, "signal": None},
            "actual two-branch transport did not complete",
        )
        metadata = result.get("supervision")
        terminal = receipts.load(metadata)
        require(
            terminal is not None and actual["root_pid"] == metadata["provider"]["pid"],
            "transport root is not the original supervised child",
        )
        pids = [actual[key] for key in ("root_pid", "browser_pid", "fixture_pid")]
        require(
            all(type(pid) is int and pid > 0 for pid in pids)
            and len(set(pids)) == 3
            and actual["mach_service"]
            == "org.chromium.Chromium.MachPortRendezvousServer." + str(actual["browser_pid"]),
            "transport branch identity or exact service changed",
        )
        owned = [metadata["provider"], *terminal["processes"]]
        require(
            all(
                any(
                    row["pid"] == pid and type(row.get("birth_identity")) in (int, float) and row["birth_identity"] > 0
                    for row in owned
                )
                for pid in pids
            ),
            "original ProcessTree did not retain both actual branch births",
        )
        return path

    def descriptor(self):
        return {
            "kind": "native_capture",
            "scratch": str(self.scratch),
            "tree": str(self.tree),
            "output": str(self.log),
            "pins": deepcopy(self.pin_files),
            "config_ref": self.config_name,
            "inputs": deepcopy(self.inputs),
            "runtime": deepcopy(self.runtime),
            "listener": deepcopy(self.broker.identity),
            "request": str(self.request),
            "deadline_monotonic": self.deadline,
            "ipc": deepcopy(self.ipc),
        }

    def close(self):
        self.broker.close()
        for item in self.ipc_sockets:
            item.close()


def verify_boundary(workspace, run_dir, body):
    """Replay original retained bytes. This cannot reconstruct a live lease."""
    root, run = Path(workspace), Path(run_dir)
    boundary = body["boundary"]
    require(
        set(boundary)
        == {
            "kind",
            "scratch",
            "tree",
            "output",
            "pins",
            "config_ref",
            "inputs",
            "runtime",
            "listener",
            "request",
            "deadline_monotonic",
            "ipc",
        }
        and boundary["kind"] == "native_capture",
        "unknown capture boundary field",
    )
    scratch, tree = Path(boundary["scratch"]), Path(boundary["tree"])
    require(
        scratch.parent.parent == root / ".autocode"
        and containment.CONTROL_NAME.fullmatch(scratch.parent.name)
        and scratch.name == "scratch"
        and tree == scratch / "source"
        and scratch.resolve() == scratch
        and not scratch.is_symlink()
        and body["cwd"] == str(tree),
        "foreign capture copy",
    )
    state = {
        "workspace": str(root),
        "goal_contract": body["binding"]["contract"],
        "settings": body["binding"]["settings"],
        "user_events": deepcopy(body["user_events"]),
    }
    profile = profiles._profile(state)
    runtime, pins = runtime_inputs(root, profile)
    pins.update(fixed_executable_pins())
    require(boundary["runtime"] == runtime, "capture runtime differs from original approved profile")
    config_path = evidence.local_file(root, boundary["config_ref"])
    config = profiles._json(config_path.read_bytes())
    cases = state["settings"]["design_manifest"]["body"]["cases"]
    case = next((item for item in cases if item["id"] == config["case"]["id"]), None)
    require(case is not None, "capture has no approved reference case")
    _config, inputs = declared_config(state, body["binding"]["source"], boundary["config_ref"], case=case)
    require(inputs == boundary["inputs"], "capture source input identities changed")
    for name, sha in body["binding"]["source"]["files"].items():
        require(util.file_hash(evidence.local_file(tree, name)) == sha, "capture copy escaped approved source")
    for name in (
        "autocode_visual_capture.py",
        "autocode_visual_browser.cjs",
        "autocode_native_capture_browser.cjs",
        "autocode_capture_bootstrap.cjs",
    ):
        path = Path(__file__).with_name(name).resolve()
        pins[str(path)] = util.file_hash(path)
    output = Path(boundary["output"])
    require(
        output.parent == run and output.name.startswith("capture-") and output.suffix == ".log", "foreign output slot"
    )
    request = scratch / "capture-request.json"
    require(boundary["request"] == str(request), "foreign parent capture request")
    request_body = profiles._json(request.read_bytes())
    ipc = boundary["ipc"]
    require(
        isinstance(ipc, dict)
        and set(ipc) == {"bridge", "fixture"}
        and all(
            set(row) == {"fd", "device", "inode", "mode"}
            and type(row["fd"]) is int
            and row["fd"] > 4
            and stat.S_ISSOCK(row["mode"])
            for row in ipc.values()
        )
        and ipc["bridge"]["fd"] != ipc["fixture"]["fd"],
        "invalid original CDP socketpair",
    )
    expected = parent_request(
        config,
        scratch,
        runtime,
        scratch.parent / "capture.sb",
        ipc,
        boundary["listener"],
        nonce=request_body.get("native_transport", {}).get("nonce"),
        timeout_ms=request_body.get("timeout_ms"),
    )
    require(
        request_body == expected
        and type(expected["timeout_ms"]) is int
        and 0 < expected["timeout_ms"] <= 120000
        and isinstance(expected["native_transport"]["nonce"], str)
        and len(expected["native_transport"]["nonce"]) == 32,
        "parent capture request changed",
    )
    pins[str(request)] = util.file_hash(request)
    policy = scratch.parent / "capture.sb"
    text = containment._render_policy(
        root,
        scratch,
        read_workspace=False,
        read_roots=tuple(runtime["files"])
        + tuple(
            str(Path(__file__).with_name(name).resolve())
            for name in ("autocode_visual_browser.cjs", "autocode_native_capture_browser.cjs")
        ),
        denied_read_paths=(root,),
        read_exceptions=(scratch,),
        write_roots=(scratch / "writable",),
        denied_write_paths=(tree, scratch / "responses"),
        write_files=(output,),
    )
    lease = boundary["listener"]
    require(
        isinstance(lease, dict)
        and lease.get("address", [None])[0] == "127.0.0.1"
        and type(lease["address"][1]) is int
        and 0 < lease["address"][1] <= 65535
        and not lease.get("reuse_address")
        and not lease.get("reuse_port"),
        "invalid original listener facts",
    )
    broker.asset_targets(config["assets"], config.get("preview_origin"))
    require(
        config.get("preview_origin") is None or lease["address"][1] != 3000,
        "preview origin collides with original primary listener",
    )
    text += browser_directories(runtime)
    text += (
        '(allow sysctl-read (sysctl-name "kern.osproductversion"))\n'
        + f'(allow network-inbound (local tcp "localhost:{lease["address"][1]}"))\n(deny network-bind network-outbound)\n'
    )
    require(policy.read_text() == text, "capture policy changed or widened")
    pins[str(policy)] = util.file_hash(policy)
    require(boundary["pins"] == pins, "capture runtime/engine/policy pins changed")
    command = [runtime["node"], str(Path(__file__).with_name("autocode_capture_bootstrap.cjs").resolve()), str(request)]
    home = str(scratch / "writable" / "home")
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": home,
        "TMPDIR": home,
        "TMP": home,
        "TEMP": home,
        "OPENSSL_CONF": "/dev/null",
        "LANG": "en_US.UTF-8",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
    }
    require(
        body["command"] == command
        and body["command_sha256"] == util.digest(command)
        and body["environment_sha256"] == util.digest(environment),
        "capture command/environment changed",
    )
    return scratch / "writable" / "output"
