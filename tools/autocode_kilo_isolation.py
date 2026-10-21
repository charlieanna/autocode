"""Readonly Kilo native ownership, distinct from built-in OpenCode isolation.

The saved two-client profile supplies immutable compiled-client and source
inputs. Real debug-agent observations and the whole-client kernel boundary
are required before dispatch. A generic command provider cannot create this
owner, and build metadata alone grants no source or image acceptance.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import stat
import subprocess
import sys
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_kilo_linux_boundary as linux_boundary
    from . import autocode_kilo_native as rules
    from . import autocode_kilo_source_relay as relay
    from . import autocode_native_conformance as observations
    from . import autocode_native_locators as native_locators
    from . import autocode_opencode_snapshots as native_snapshots
    from . import autocode_output_cap as output_cap
    from . import autocode_process as processes
    from . import autocode_supervision as supervision
    from . import autocode_tool_containment as containment
    from . import autocode_util as util
    from . import autocode_visual_profile as files
except ImportError:
    import autocode_kilo_linux_boundary as linux_boundary
    import autocode_kilo_native as rules
    import autocode_kilo_source_relay as relay
    import autocode_native_conformance as observations
    import autocode_native_locators as native_locators
    import autocode_opencode_snapshots as native_snapshots
    import autocode_output_cap as output_cap
    import autocode_process as processes
    import autocode_supervision as supervision
    import autocode_tool_containment as containment
    import autocode_util as util
    import autocode_visual_profile as files


CONFIG = rules.CONFIG_ENV
FLAGS = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "npm_config_offline": "true",
}
DIRECTORIES = {
    "HOME": "home",
    "XDG_CONFIG_HOME": "config",
    "XDG_DATA_HOME": "data",
    "XDG_CACHE_HOME": "cache",
    "XDG_STATE_HOME": "state",
    "TMPDIR": "tmp",
    "OPENCODE_TEST_MANAGED_CONFIG_DIR": "managed",
}
MUTABLE_DIRECTORIES = {"XDG_DATA_HOME": "data", "XDG_CACHE_HOME": "cache", "XDG_STATE_HOME": "state", "TMPDIR": "tmp"}
BUILD_KIND = "ACTUAL_ORIGINAL_FULL_SDK_BUILD_AND_ORIGINAL_SMOKES_PROVISIONAL_CLOSED"
RECONCILED_BUILD_KIND = "ACTUAL_ORIGINAL_FULL_SDK_BUILD_AND_SMOKES_READONLY_RECONCILED_CLOSED"
TIMEZONE_FILES = (
    ("/usr/share/zoneinfo/Etc/UTC",)
    if sys.platform == "linux"
    else (
        "/private/var/db/timezone/zoneinfo/UTC",
        "/private/var/db/timezone/zoneinfo/posixrules",
        "/private/var/db/timezone/icutz/icutz44l.dat",
    )
)


def _require(value, reason):
    if not value:
        raise ValueError("Kilo native isolation: " + reason)


def _write(path, raw, pins, *, executable=False):
    files._write(path, raw, pins)
    if executable:
        path.chmod(0o500)


def validate_model_projection(observed, compiled):
    """Reinstate only fromModelsDevModel's proven missing limit.input value."""
    expected = deepcopy(compiled)
    limit = expected.get("limit")
    _require(isinstance(limit, dict), "compiled model has no original limit projection")
    if "input" not in limit:
        _require(set(limit) == {"context", "output"}, "unknown omitted model limit field")
        expected["limit"] = {"context": limit["context"], "input": relay.UNDEFINED, "output": limit["output"]}
    _require(relay.canonical(observed) == relay.canonical(expected), "actual compiled catalogue model changed")
    return expected


def validate_build_result(result, executable_sha256):
    """Preserve original worker failure in the one accepted reconciled shape."""
    _require(
        result.get("kind") in (BUILD_KIND, RECONCILED_BUILD_KIND)
        and result.get("genuine_compiled_binary", {}).get("sha256") == executable_sha256
        and result.get("actual_original_owner_native_wire_model_image_acceptance") is None,
        "missing actual full compiled SDK build; it confers no native acceptance",
    )
    if result["kind"] == RECONCILED_BUILD_KIND:
        exits = {
            "original_worker_actual_exit": 1,
            "original_build_actual_exit": 0,
            "original_compiled_models_actual_exit": 0,
            "readonly_reconciliation_actual_exit": 0,
        }
        _require(
            all(type(result.get(key)) is int and result[key] == value for key, value in exits.items()),
            "reconciled build must retain original worker1 and actual build/models/readonly0",
        )


def validate_copied_build_acceptance(proof, staged_result):
    """Copying evidence changes its locator, never its original source join."""
    original = proof["root"].get("result")
    _require(
        isinstance(original, dict)
        and set(original) == {"path", "sha256"}
        and isinstance(original["path"], str)
        and Path(original["path"]).is_absolute()
        and original == proof["peer"].get("result")
        and original["sha256"] == staged_result["sha256"],
        "Root and peer original result bytes must join the exact staged copy",
    )
    for key in ("root", "peer"):
        _require(
            proof[key].get("decision") == "ACCEPT_ACTUAL_FULL_SDK_BUILD_ONLY" and proof[key].get("errors") == [],
            "closed full build needs exact independent and Root acceptance",
        )


def _ancestor_reads(*paths):
    ancestors = {parent for path in paths for parent in Path(path).parents}
    return "".join("(allow file-read-data (literal " + json.dumps(str(path)) + "))\n" for path in sorted(ancestors))


def _timezone_inputs():
    """Pin only the public OS files required by this compiled Bun startup."""
    records = []
    for alias in TIMEZONE_FILES:
        path = Path(alias).resolve(strict=True)
        metadata = path.lstat()
        records.append(
            {
                "alias": alias,
                "path": str(path),
                "sha256": util.file_hash(path),
                "mode": stat.S_IMODE(metadata.st_mode),
                "bytes": metadata.st_size,
                "uid": metadata.st_uid,
                "nlink": metadata.st_nlink,
            }
        )
    _require_timezone_inputs(records)
    return records


def _require_timezone_inputs(records):
    _require(
        isinstance(records, list) and [row.get("alias") for row in records] == list(TIMEZONE_FILES),
        "public timezone input set changed",
    )
    for row in records:
        path = Path(row["path"])
        metadata = path.lstat()
        _require(
            set(row) == {"alias", "path", "sha256", "mode", "bytes", "uid", "nlink"}
            and Path(row["alias"]).resolve(strict=True) == path
            and not path.is_symlink()
            and stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == row["uid"] == 0
            and metadata.st_nlink == row["nlink"] == 1
            and metadata.st_mode & 0o022 == 0
            and stat.S_IMODE(metadata.st_mode) == row["mode"]
            and metadata.st_size == row["bytes"]
            and util.file_hash(path) == row["sha256"],
            "public timezone input bytes, mode or alias changed",
        )


def _timezone_policy(records):
    _require_timezone_inputs(records)
    return "".join("(allow file-read-data (literal " + json.dumps(row["path"]) + "))\n" for row in records)


def _prepare_config_directory(target, environment):
    """Satisfy Kilo's startup mkdir while keeping client config writes denied."""
    config = Path(environment["XDG_CONFIG_HOME"])
    _require(
        config == Path(target) / "config" and config.is_dir() and not config.is_symlink(),
        "foreign original Kilo configuration directory",
    )
    (config / "kilo").mkdir(mode=0o700)


def _require_runtime_mapping(environment, runtime):
    runtime = Path(runtime)
    _require(
        runtime.name == "kilo-runtime"
        and runtime.parent.name == "scratch"
        and runtime.is_dir()
        and not runtime.is_symlink(),
        "foreign Kilo scratch runtime",
    )
    for key, name in MUTABLE_DIRECTORIES.items():
        path = Path(environment[key])
        _require(
            path == runtime / name and path.resolve() == path and path.is_dir() and not path.is_symlink(),
            "mutable Kilo path exceeds original scratch",
        )


def _relocate_client_runtime(boundary, environment):
    control = Path(boundary["profile"]).resolve().parent
    scratch = Path(boundary["scratch"]).resolve()
    _require(
        control.parent == Path(boundary["workspace"]).resolve() / ".autocode"
        and scratch == control / "scratch"
        and not Path(boundary["scratch"]).is_symlink(),
        "foreign original Kilo tool scratch",
    )
    runtime = scratch / "kilo-runtime"
    runtime.mkdir(mode=0o700)
    for key, name in MUTABLE_DIRECTORIES.items():
        path = runtime / name
        path.mkdir(mode=0o700)
        environment[key] = str(path)
    environment["npm_config_cache"] = str(runtime / "cache/npm")
    _require_runtime_mapping(environment, runtime)
    return runtime


def _inherited_shell_body(raw, profile):
    """Retain the prepared safe environment; apply no second Seatbelt profile."""
    lines = raw.decode("utf-8", errors="strict").splitlines()
    if Path(profile).suffix == ".bwrap":
        guard = 'if [ "$#" -ne 2 ] || [ "$1" != "-c" ]; then exit 126; fi'
        suffix = ' -c "$2" 3< ' + shlex.quote(str(profile))
        _require(
            len(lines) == 3
            and lines[:2] == ["#!/bin/sh -p", guard]
            and lines[2].startswith("exec ")
            and lines[2].endswith(suffix),
            "prepared Linux shell shape changed",
        )
        argv = shlex.split(lines[2][5 : -len(suffix)])
        _require(
            argv[:2] == ["/usr/bin/env", "-i"] and argv.count(containment.BWRAP) == 1,
            "prepared Linux shell environment changed",
        )
        index = argv.index(containment.BWRAP)
        _require(
            argv[index:] == [containment.BWRAP, "--args", "3", "/bin/bash", "--noprofile", "--norc"],
            "prepared Linux shell invocation changed",
        )
        return (
            "#!/bin/sh -p\n" + guard + "\nexec " + shlex.join(argv[:index] + argv[index + 3 :]) + ' -c "$2"\n'
        ).encode()
    guard = 'if [ "$#" -ne 2 ] || [ "$1" != "-c" ]; then exit 126; fi'
    _require(
        len(lines) == 3
        and lines[:2] == ["#!/bin/sh -p", guard]
        and lines[2].startswith("exec ")
        and lines[2].endswith(' -c "$2"'),
        "prepared shell shape changed",
    )
    argv = shlex.split(lines[2][5 : -len(' -c "$2"')])
    _require(
        argv[:2] == ["/usr/bin/env", "-i"] and argv.count("/usr/bin/sandbox-exec") == 1,
        "prepared shell environment changed",
    )
    index = argv.index("/usr/bin/sandbox-exec")
    _require(
        argv[index : index + 3] == ["/usr/bin/sandbox-exec", "-f", str(profile)]
        and argv[index + 3 :] == ["/bin/bash", "--noprofile", "--norc"],
        "prepared shell invocation changed",
    )
    return ("#!/bin/sh -p\n" + guard + "\nexec " + shlex.join(argv[:index] + argv[index + 3 :]) + ' -c "$2"\n').encode()


def _freeze_stage_files(target, environment, pins, *, journal, runtime_root=None):
    """Freeze authority inputs while leaving only declared runtime data mutable."""
    target = Path(target).resolve()
    mutable = [
        Path(environment[key]).resolve() for key in ("XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "TMPDIR")
    ]
    if runtime_root is None:
        _require(
            all(path.is_relative_to(target) and path != target for path in mutable),
            "runtime data exceeds the original private stage",
        )
    else:
        _require_runtime_mapping(environment, runtime_root)
    for path in target.rglob("*"):
        if not path.is_file() or path == journal or any(path.is_relative_to(root) for root in mutable):
            continue
        _require(not path.is_symlink(), "staged authority file became an alias")
        path.chmod(0o500 if path.stat().st_mode & 0o111 else 0o400)
        pins[str(path)] = util.file_hash(path)
    _require(
        not any(Path(name).is_relative_to(root) for name in pins for root in mutable),
        "mutable runtime file cannot become an immutable authority pin",
    )


def _verification_protected_paths(boundary):
    """Carry the original authenticated copy's literal input denials."""
    if "verification_copy" not in boundary:
        return []
    manifest = Path(boundary["verification_copy"])
    control = Path(boundary["profile"]).resolve().parent
    _require(
        manifest == control / "verification-copy.json"
        and not manifest.is_symlink()
        and util.file_hash(manifest) == boundary["verification_copy_sha256"],
        "verification copy input changed",
    )
    value = files._json(manifest.read_bytes())
    tree = Path(boundary["scratch"]) / "verification"
    _require(
        value.get("version") == 1
        and value.get("workspace") == boundary["workspace"]
        and value.get("tree") == str(tree)
        and isinstance(value.get("files"), dict)
        and isinstance(value.get("directories"), list),
        "verification copy input layout changed",
    )
    protected = [tree]
    for name in [*value["files"], *value["directories"]]:
        relative = Path(name)
        _require(
            isinstance(name, str)
            and not relative.is_absolute()
            and name != ""
            and ".." not in relative.parts
            and relative.as_posix() == name,
            "verification copy protected path changed",
        )
        protected.append(tree / relative)
    return sorted({str(containment._protected_path(path)) for path in protected})


def whole_client_policy(
    workspace, target, boundary, request, environment, socket_path, prompt_file=None, *, os_readonly_inputs=()
):
    """Separate client policy; the existing shell scratch contract is unchanged."""
    root, target = Path(workspace).resolve(), Path(target).resolve()
    control = Path(boundary["profile"]).resolve().parent
    _require(
        target.is_relative_to(root / ".autocode/runs") and control.parent == root / ".autocode",
        "foreign native client or shell namespace",
    )
    scratch = Path(boundary["scratch"]).resolve()
    _require(scratch == control / "scratch", "foreign original native shell scratch")
    _require_runtime_mapping(environment, scratch / "kilo-runtime")
    text = containment._render_policy(
        root,
        target,
        read_roots=[*request["read_roots"], target, control],
        protected_paths=[*request["protected_paths"], *_verification_protected_paths(boundary)],
        write_roots=(),
    )
    # The shell control and native client are siblings. Their exact read
    # exceptions belong to this whole-client policy, not the shell renderer.
    exceptions = ["(require-not (subpath " + json.dumps(str(path)) + "))" for path in (target, control)]
    if prompt_file is not None:
        prompt = Path(prompt_file).resolve()
        _require(prompt.is_relative_to(root / ".autocode/runs"), "original prompt is outside its owned run")
        exceptions.append("(require-not (literal " + json.dumps(str(prompt)) + "))")
    text += (
        "(deny file-read* (require-all (subpath "
        + json.dumps(str(root / ".autocode"))
        + ") "
        + " ".join(exceptions)
        + "))\n"
    )
    # The actual Bash child inherits this profile. Both Kilo runtime data and
    # tool writes live below the one original tool scratch.
    text += "(allow file-write* (subpath " + json.dumps(str(scratch)) + "))\n"
    text += "(deny network-outbound (with errno EACCES) (remote tcp))\n"
    text += "(allow network-outbound (remote unix-socket (path-literal " + json.dumps(str(socket_path)) + ")))\n"
    if os_readonly_inputs:
        text += _timezone_policy(os_readonly_inputs)
    return text + _ancestor_reads(root, target)


def _run(command, *, environment, cwd, timeout, receipt, input_bytes=None, checkpoint=None):
    """One model-free subprocess with original birth capture and tree cleanup."""
    started = time.monotonic()
    command = list(command)
    if command[:2] == ["/usr/bin/sandbox-exec", "-f"]:
        original_policy = Path(command[2])
        text = original_policy.read_text()
        logs = [Path(str(receipt) + suffix).absolute() for suffix in (".stdout", ".stderr")]
        _require(all(path.is_relative_to(Path(receipt).absolute().parent) for path in logs), "foreign model-free stdio")
        text += "(allow file-write-data " + " ".join("(literal " + json.dumps(str(path)) + ")" for path in logs) + ")\n"
        execution_policy = Path(str(receipt) + ".execution.sb")
        _write(execution_policy, text.encode(), {})
        command[2] = str(execution_policy)
    raw_births = []
    tree = child = None
    input_path = Path(str(receipt) + ".input")
    if input_bytes is not None:
        _write(input_path, input_bytes, {})

    def held(metadata):
        nonlocal tree
        tree = processes.ProcessTree(metadata["provider"]["pid"], lambda rows: raw_births.extend(rows))
        captured = tree.capture_root()
        _require(
            captured is not None and processes.matches(captured, metadata["provider"]),
            "model-free held child lost its original root birth",
        )
        checkpoint(tree, metadata, started + timeout)

    with open(str(receipt) + ".stdout", "xb") as out, open(str(receipt) + ".stderr", "xb") as err:
        stream = input_path.open("rb") if input_bytes is not None else None
        try:
            with supervision.launch(
                command,
                env=environment,
                cwd=cwd,
                stdin=stream or subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
                timeout=max(0.001, timeout - (time.monotonic() - started)),
                checkpoint=held if checkpoint is not None else None,
                receipt_path=str(receipt) + ".supervision",
            ) as child:
                if tree is None:
                    tree = processes.ProcessTree(child.pid, lambda rows: raw_births.extend(rows))
                _require(tree.capture_root() is not None, "model-free child has no original root birth")
                try:
                    while child.poll() is None:
                        tree.sample()
                        _require(time.monotonic() - started < timeout, "original model-free deadline expired")
                        time.sleep(0.02)
                finally:
                    tree.sample()
                    tree.stop(child)
                    child.wait()
        finally:
            if stream is not None:
                stream.close()
            record = {
                "command": command,
                "exit": None if child is None else child.returncode,
                "seconds": time.monotonic() - started,
                "original_births": raw_births,
                "expired": time.monotonic() - started >= timeout,
                "supervision_result": None if child is None else getattr(child, "supervision_result", None),
            }
            util.atomic_json(receipt, record)
    _require(
        child is not None and child.returncode == 0 and time.monotonic() - started < timeout,
        "model-free native command failed or expired; retained original outputs",
    )
    return Path(str(receipt) + ".stdout").read_bytes()


class KiloRoleIsolation:
    """Only the two explicitly pinned readonly Claude roles can construct this."""

    verify_boundary = staticmethod(lambda boundary: verify_boundary(boundary))

    def __init__(self, state: RunState, workspace, run_dir, route_role, *, profile, native):
        selected = rules.selected(profile, state["settings"], route_role)
        _require(selected is not None and route_role in rules.ROLES, "missing explicit Kilo reviewer")
        self._binding, self._identity = selected
        self._state, self._settings, self._role = state, deepcopy(state["settings"]), route_role
        self._root, self._run = Path(workspace).resolve(), Path(run_dir).resolve()
        _require(
            self._root == Path(state["workspace"]).resolve()
            and self._run.is_relative_to(self._root / ".autocode/runs"),
            "foreign original workspace/run",
        )
        _require(
            native.workspace.resolve() == self._root and native.run_dir.resolve() == self._run,
            "foreign original native caller",
        )
        self._native, self._profile = native, deepcopy(profile)
        self._route = deepcopy(self._settings["roles"][route_role])
        self._sdk = files._owned(self._root, self._binding["sdk_path"])
        self._executable = Path(self._identity["executable"]).resolve()
        self._sdk_hash, self._executable_hash = (
            self._binding[key] for key in ("sdk_manifest_sha256", "executable_sha256")
        )
        self._prepared = self._descriptor = self._image_preparation = self._source = None
        self._writer_restriction = self._writer_execution = None
        self._check(state)

    def _check(self, state: RunState):
        _require(
            state["settings"] == self._settings and Path(state["workspace"]).resolve() == self._root,
            "saved routes, caps or source changed",
        )
        self._native.require_initial_input(state)
        if self._prepared is not None and "os_readonly_inputs" in self._prepared:
            _require_timezone_inputs(self._prepared["os_readonly_inputs"])
        if self._prepared is not None and "runtime_root" in self._prepared:
            _require_runtime_mapping(self._prepared["environment"], self._prepared["runtime_root"])
        manifest = files._owned(self._sdk, files.MANIFEST)
        _require(util.file_hash(manifest) == self._sdk_hash, "compiled SDK manifest changed")
        body = files._json(manifest.read_bytes())
        _require(
            set(body) == {"version", "files", "executables"}
            and body["version"] == 1
            and isinstance(body["files"], dict)
            and isinstance(body["executables"], list),
            "unsupported compiled SDK manifest",
        )
        _require(set(body["executables"]) <= set(body["files"]), "unlisted compiled executable")
        actual = {str(path.relative_to(self._sdk)) for path in self._sdk.rglob("*") if path.is_file()}
        _require(actual == set(body["files"]) | {files.MANIFEST}, "compiled SDK resources omitted or added")
        for name, sha in body["files"].items():
            member = files._owned(self._sdk, name)
            _require(
                member.is_file()
                and not member.is_symlink()
                and util.file_hash(member) == sha
                and stat.S_IMODE(member.stat().st_mode) == (0o500 if name in body["executables"] else 0o400),
                "compiled SDK bytes/modes changed",
            )
        _require(
            self._executable.is_relative_to(self._sdk) and util.file_hash(self._executable) == self._executable_hash,
            "saved executable is not the accepted compiled SDK",
        )
        hooks = self._binding["source_hooks"]
        hook_path = files._owned(self._root, hooks["path"])
        _require(util.file_hash(hook_path) == hooks["sha256"], "source-hook pins changed")
        source = files._json(hook_path.read_bytes())
        _require(
            set(source) == {"version", "sites", "catalogue", "model", "derivation"}
            and source["version"] == 1
            and isinstance(source["sites"], dict)
            and len(source["sites"]) == 2,
            "missing actual two-site source, catalogue/model or derivation inputs",
        )
        for name, sha in source["sites"].items():
            _require(body["files"].get(name) == sha, "source site is outside authenticated compiled SDK inputs")
        catalogue = source["catalogue"]
        _require(
            set(catalogue) == {"path", "sha256"} and body["files"].get(catalogue["path"]) == catalogue["sha256"],
            "catalogue is not the accepted actual compiled model output",
        )
        _require(
            isinstance(source["model"], dict)
            and source["model"].get("providerID") == "anthropic"
            and source["model"].get("api", {}).get("npm") == "@ai-sdk/anthropic",
            "wrong actual model projection",
        )
        for key in ("runtime", "entry", "sdk_entry", "decoder", "deriver"):
            _require(source["derivation"].get(key) in body["files"], "unbound whole-request derivation dependency")
        for key, name in (
            ("entry", "autocode_kilo_request_derivation_cli.mjs"),
            ("deriver", "autocode_kilo_request_derivation.mjs"),
            ("decoder", "autocode_kilo_image_delivery.mjs"),
        ):
            _require(
                body["files"][source["derivation"][key]] == util.file_hash(Path(__file__).with_name(name)),
                "whole-request helper differs from original current CODE input",
            )
        refs = self._binding["build_acceptance"]
        proof = {}
        for key, ref in refs.items():
            path = files._owned(self._root, ref["path"])
            _require(util.file_hash(path) == ref["sha256"], "closed build acceptance reference changed")
            proof[key] = files._json(path.read_bytes())
        result = proof["result"]
        validate_build_result(result, self._executable_hash)
        validate_copied_build_acceptance(proof, refs["result"])
        self._files, self._executables, self._hooks = body["files"], body["executables"], source

    def containment_request(self, *, allow_write):
        _require(allow_write is False, "Claude/Kilo reviewer cannot acquire writer authority")
        self._check(self._state)
        return {}

    def launch(
        self,
        adapter,
        role,
        workspace,
        run_dir,
        session,
        model,
        effort,
        allow_write,
        *,
        planning=False,
        report=None,
        schema=None,
        prompt_file=None,
        sandbox=None,
        env=None,
        containment=None,
        image_preparation=None,
    ):
        _require(
            self._prepared is None
            and role == self._role
            and session is None
            and allow_write is False
            and model == rules.MODEL
            and effort == "high"
            and isinstance(containment, dict),
            "foreign, repeated, resumed or writable Kilo launch",
        )
        _require(
            role == "completion" or image_preparation is not None,
            "native Sol requires its original byte-bound visual packet",
        )
        _require(
            str(adapter._path) == self._identity["config_path"]
            and util.file_hash(adapter._path) == self._identity["config_sha256"],
            "saved provider configuration changed",
        )
        self._check(self._state)
        target = self._run / ("native-kilo-" + uuid.uuid4().hex)
        target.mkdir(mode=0o700)
        pins, child = (
            {},
            {
                **native_snapshots.git_config_env(env),  # the snapshot excludes file (#1630)
                **{
                    key: env[key]
                    for key in (
                        "PATH",
                        "LANG",
                        "LC_CTYPE",
                        "LC_ALL",
                        "TZ",
                        "AUTOCODE_OUTPUT_MODE",
                        "AUTOCODE_OUTPUT_STORE",
                        "AUTOCODE_OUTPUT_ATTEMPT",
                        "AUTOCODE_CAPTURE_CONTEXT",
                        "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX",
                    )
                    if key in env
                },
            },
        )
        for key, name in DIRECTORIES.items():
            path = target / name
            path.mkdir(mode=0o700)
            child[key] = str(path)
        _prepare_config_directory(target, child)
        staged = target / "sdk"
        for name, sha in self._files.items():
            raw = files._owned(self._sdk, name).read_bytes()
            _require(hashlib.sha256(raw).hexdigest() == sha, "SDK changed during isolated staging")
            _write(staged / name, raw, pins, executable=name in self._executables)
        _write(staged / files.MANIFEST, (self._sdk / files.MANIFEST).read_bytes(), pins)
        agent = "autocode_" + role
        command = [
            str(staged / self._executable.relative_to(self._sdk)),
            "run",
            "--dir",
            str(self._root),
            "--format",
            "json",
            "--agent",
            agent,
            "--model",
            model,
            "--variant",
            effort,
        ]
        values = {"model": model, "effort": effort, "workspace": str(self._root), "role": role}
        template = adapter._config["command"]
        _require(
            isinstance(template, list) and [adapter._fill(part, values) for part in template[1:]] == command[1:],
            "native isolation cannot discard a custom configured Kilo command",
        )
        output_cap.apply(child)
        plugin = target / "source-delivery.mjs"
        helper = Path(__file__).with_name("autocode_kilo_image_delivery.mjs")
        _write(plugin, helper.read_bytes(), pins)
        empty = target / "empty-config.json"
        _write(empty, b"{}\n", pins)
        config = {
            "$schema": "https://opencode.ai/config.json",
            "share": "disabled",
            "autoupdate": False,
            "snapshot": False,
            "model": model,
            "small_model": model,
            "enabled_providers": ["anthropic"],
            "plugin": [plugin.as_uri()],
            "provider": {
                "anthropic": {"options": {"apiKey": relay.PUBLIC_API_KEY, "baseURL": "https://api.anthropic.com/v1"}}
            },
            "agent": {
                agent: {
                    "description": "AutoCode readonly " + role,
                    "mode": "primary",
                    "model": model,
                    "permission": {"*": "deny", "bash": "allow", "external_directory": "deny"},
                },
                **{name: {"disable": True} for name in ("title", "summary", "compaction")},
            },
        }
        child.update(FLAGS)
        child.update(
            KILO_CONFIG=str(empty),
            KILO_CONFIG_DIR=str(target / "config"),
            KILO_CONFIG_CONTENT=json.dumps(config),
            npm_config_cache=str(target / "cache/npm"),
        )
        self._prepared = {
            "target": target,
            "files": pins,
            "agent": agent,
            "allow_write": False,
            "command": command,
            "environment": child,
            "config": config,
            "sdk": staged,
            "schema": Path(schema),
            "prompt_file": None if prompt_file is None else Path(prompt_file),
            "data_home": env.get("XDG_DATA_HOME", str(Path.home() / ".local/share")),
            "os_readonly_inputs": _timezone_inputs(),
        }
        if image_preparation is not None:
            self.stage_image(image_preparation, command=command, environment=child)
            command, child = self._prepared["command"], self._prepared["environment"]
        else:
            _require(prompt_file is not None, "readonly completion requires the original protected prompt file")
            options = {
                "path": str(target / "delivery.jsonl"),
                "attempt_id": uuid.uuid4().hex,
                "binding_sha256": util.digest({"role": role, "schema": util.file_hash(schema)}),
                "plugin_sha256": util.file_hash(plugin),
                "reviewer": {"provider": "kilocode", "model": model, "agent": agent},
                "max_requests": self._profile["max_requests"],
            }
            child["AUTOCODE_IMAGE_AUDIT"] = json.dumps(options)
        try:
            self._configure_source(command, child)
            command, child, boundary = self._configure(command, child, containment)
            self.render_image_prompt(command=command, environment=child, boundary=boundary)
            descriptor = self.finalize(command=command, environment=child, boundary=boundary)
            boundary["native_role_isolation"] = descriptor
            child.update(
                AUTOCODE_NATIVE_ROLE_ISOLATION=json.dumps(native_locators.descriptor_value(descriptor)),
                AUTOCODE_TOOL_CONTAINMENT=json.dumps(native_locators.boundary_value(boundary)),
            )
            self.freeze_image(command=command, environment=child, descriptor=descriptor)
        except BaseException:
            if self._source is not None:
                self._source.close(original_exit=None, launch_seal=None)
            raise
        return command, child, deepcopy(config)

    def _configure_source(self, command, child):
        _ = self._prepared["target"]
        options = json.loads(child["AUTOCODE_IMAGE_AUDIT"])
        expected_plugin = util.file_hash(Path(self._prepared["config"]["plugin"][0].removeprefix("file://")))
        _require(options.get("plugin_sha256") == expected_plugin, "original source plugin pin changed")
        audit_directory = Path(options["path"]).parent
        hooks = {
            "path": str(files._owned(self._root, self._binding["source_hooks"]["path"])),
            "sha256": self._binding["source_hooks"]["sha256"],
        }
        self._source = relay.KiloSourceRelay(
            audit_directory,
            protected_inputs=self._prepared,
            source_hooks=hooks,
            derive=self._derive,
            validate_arm=self._arm,
            data_home=self._prepared["data_home"],
            max_requests=self._profile["max_requests"],
            frame_limit=32 * 1024 * 1024,
            response_limit=16 * 1024 * 1024,
            current_source=lambda: self._check(self._state),
            audit_options=options,
            parse_request=self._parse_request,
            parse_response=self._parse_response,
            executable=command[0],
        )
        child["AUTOCODE_KILO_SOURCE_RELAY"] = json.dumps(self._source.child_channel())

    def _derivation_policy_text(self):
        target, sdk = self._prepared["target"], self._prepared["sdk"]
        scratch = Path(self._prepared["environment"]["TMPDIR"])
        _require_runtime_mapping(self._prepared["environment"], self._prepared["runtime_root"])
        text = containment._render_policy(
            self._root,
            target,
            read_workspace=False,
            read_roots=[sdk, target, scratch],
            protected_paths=[sdk],
            write_roots=(),
        )
        return (
            text
            + "(deny process-fork)\n(allow file-write* (subpath "
            + json.dumps(str(scratch))
            + "))\n"
            + _ancestor_reads(target, sdk, scratch)
            + _timezone_policy(self._prepared["os_readonly_inputs"])
        )

    def _prepare_derivation_policy(self):
        profile = self._prepared["target"] / ("derivation.json" if sys.platform == "linux" else "derivation.sb")
        if sys.platform == "linux":
            target, sdk = self._prepared["target"], self._prepared["sdk"]
            scratch = Path(self._prepared["environment"]["TMPDIR"])
            value = linux_boundary.policy(
                profile, kind="derivation", read_paths=[sdk, target], write_paths=[scratch], cwd=target
            )
            program = linux_boundary.write(profile, value)
            self._prepared["files"].update({str(path): util.file_hash(path) for path in (profile, program)})
        else:
            _write(profile, self._derivation_policy_text().encode(), self._prepared["files"])

    def _pure(self, entry, arguments, raw):
        """Actual SDK conversion/byte parsing, isolated from network and auth."""
        target, sdk = self._prepared["target"], self._prepared["sdk"]
        derivation = self._hooks["derivation"]
        compiler = files._owned(sdk, derivation["runtime"])
        helper = files._owned(sdk, entry)
        profile = target / ("derivation.json" if sys.platform == "linux" else "derivation.sb")
        if sys.platform == "linux":
            linux_boundary.verify(profile)
            prefix = linux_boundary.prefix(profile)
        else:
            _require(profile.read_text() == self._derivation_policy_text(), "original sealed pure SDK policy changed")
            prefix = ["/usr/bin/sandbox-exec", "-f", str(profile)]
        _require(
            util.file_hash(profile) == self._prepared["files"].get(str(profile)),
            "original sealed pure SDK policy changed",
        )
        deadline = self._source._deadline
        _require(deadline is not None and time.monotonic() < deadline, "missing original source deadline")
        receipt = Path(self._prepared["environment"]["TMPDIR"]) / ("derive-" + uuid.uuid4().hex + ".json")
        result = _run(
            [*prefix, str(compiler), str(helper), *arguments],
            environment=self._prepared["environment"],
            cwd=target,
            timeout=min(30, deadline - time.monotonic()),
            receipt=receipt,
            input_bytes=raw,
        )
        self._check(self._state)
        return files._json(result)

    def _derive(self, envelope):
        sdk = self._prepared["sdk"]
        d = self._hooks["derivation"]
        result = self._pure(
            d["entry"],
            ["--sdk-entry", str(files._owned(sdk, d["sdk_entry"]))],
            json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode(),
        )
        _require(
            result["sourceSha"] == envelope["source_digest"]
            and result["ownerDigest"] == envelope["owner_digest"]
            and result["sourceIndex"] == envelope["source_index"]
            and result["modelId"] == "claude-opus-5-5"
            and result["sdkVersion"] == "3.0.111",
            "whole SDK derivation identity changed",
        )
        body = result["expectedBody"].encode()
        _require(
            len(body) == result["expectedBodyBytes"]
            and hashlib.sha256(body).hexdigest() == result["expectedBodySha256"],
            "whole SDK derivation byte fact changed",
        )
        headers = result["expectedHeaders"]
        headers["user-agent"] = result["expectedUserAgentPrefix"] + " " + relay.RUNTIME_USER_AGENT
        source = relay.decode_source(envelope["canonical_record"])
        return relay.DerivedRequest(
            body,
            headers,
            envelope["source_digest"],
            envelope["owner_digest"],
            envelope["source_index"],
            source["prepared"]["headers"]["x-autocode-image-request"],
        )

    def _parse_request(self, raw):
        entry = self._hooks["derivation"]["decoder"]
        mode = "request" if self._image_preparation is not None else "source-request"
        return self._pure(entry, [mode], raw)

    def _parse_response(self, raw, headers):
        return self._pure(self._hooks["derivation"]["decoder"], ["response"], raw)

    def _arm(self, projection, protected):
        """Authenticate source observations against actual original inputs."""
        prepared = projection["prepared"]
        _require(
            projection["requestedRuntime"] == "ai-sdk"
            and projection["mode"] == "primary"
            and projection["agent"] == self._prepared["agent"],
            "foreign original source context",
        )
        validate_model_projection(projection["model"], self._hooks["model"])
        messages, images, texts = prepared["messages"], [], []
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
                continue
            _require(isinstance(content, list), "unsupported original user message")
            for part in content:
                if part.get("type") == "text":
                    texts.append(part["text"])
                elif part.get("type") in ("image", "file"):
                    raw = part.get("image", part.get("data"))
                    _require(isinstance(raw, bytes), "original image is not exact source bytes")
                    images.append(hashlib.sha256(raw).hexdigest())
        prompt = (
            self._image_preparation.prompt
            if self._image_preparation is not None
            else protected["prompt_file"].read_text()
        )
        _require(texts.count(prompt) == 1, "protected original prompt is absent or changed in source")
        expected_images = (
            []
            if self._image_preparation is None
            else [row["sha256"] for row in self._image_preparation._staged["images"]]
        )
        _require(images == expected_images, "original source image bytes/order changed")
        schemas = {}
        for name, tool in prepared["tools"].items():
            if name == "invalid":
                continue
            _require(
                name == "bash"
                and isinstance(tool.get("inputSchema"), dict)
                and set(tool["inputSchema"]) <= {"jsonSchema", "validate"},
                "non-native or changed original tool schema",
            )
            schemas[name] = {"description": tool["description"], "inputSchema": tool["inputSchema"]["jsonSchema"]}
        _require(set(schemas) == {"bash"}, "original native tools are not Bash-only")
        peer = self._source._peer
        owner = {
            "run": str(self._run),
            "stage": self._source._prepared.row_id,
            "role": self._role,
            "session": projection["sessionID"],
            "user": projection["userMessageID"],
            "agent": projection["agent"],
            "mode": projection["mode"],
            "peerPID": peer["pid"],
            "peerBirth": peer["birth_identity"],
            "binarySHA256": self._executable_hash,
            "sourceSHA256": util.digest(self._hooks["sites"]),
            "catalogueSHA256": self._hooks["catalogue"]["sha256"],
            "modelSHA256": hashlib.sha256(relay.canonical(projection["model"]).encode()).hexdigest(),
        }
        return {
            "owner": owner,
            "model": projection["model"],
            "system": prepared["system"],
            "messages": messages,
            "toolDefinitions": schemas,
            "images": images,
        }

    def _direct(self, command):
        if sys.platform == "linux":
            return linux_boundary.unwrap(command, self._prepared["target"] / "client.json")
        return command[3:] if command[:2] == ["/usr/bin/sandbox-exec", "-f"] else command

    def _observe(self, command, environment, actual):
        direct = self._direct(command)
        translated = {key: value for key, value in environment.items() if key != CONFIG}
        translated["OPENCODE_CONFIG_CONTENT"] = environment[CONFIG]
        value = observations.observe(direct, translated, direct[0], actual)
        value.update(
            command_sha256=util.digest(command),
            environment_sha256=util.digest(
                {key: value for key, value in environment.items() if key not in observations.ENVELOPES}
            ),
        )
        value.update(executable=command[0], executable_sha256=util.file_hash(command[0]))
        return value

    def _client_binding(self):
        target = self._prepared["target"]
        environment = self._prepared["environment"]
        plugin = Path(json.loads(environment[CONFIG])["plugin"][0].removeprefix("file://"))
        return {
            "executable": str(self._prepared["sdk"] / self._executable.relative_to(self._sdk)),
            "executable_sha256": self._executable_hash,
            "config_sha256": hashlib.sha256(environment[CONFIG].encode()).hexdigest(),
            "plugin_sha256": util.file_hash(plugin),
            "client_profile_sha256": util.file_hash(
                target / ("client.json" if sys.platform == "linux" else "client.sb")
            ),
            "source_channel_sha256": util.digest(self._source.child_channel()),
            "source_hooks": self._binding["source_hooks"],
            **({"kernel_backend": "linux-bwrap-pid1-v1"} if sys.platform == "linux" else {}),
        }

    def _client_probe(self, command, child):
        """One actual compiled client; the diagnostic protocol cannot infer."""
        deadline = time.monotonic() + 60
        target = self._prepared["target"]
        foreign = Path(child["TMPDIR"]) / ("foreign-client-" + uuid.uuid4().hex)
        foreign.mkdir(mode=0o700)
        directories = {}
        for key, name in DIRECTORIES.items():
            path = foreign / name
            path.mkdir(mode=0o700)
            directories[key] = str(path)
        _prepare_config_directory(foreign, directories)
        directories["KILO_CONFIG_DIR"] = directories["XDG_CONFIG_HOME"]
        control = self._source.prepare_client_conformance(
            relay._REGISTRATION,
            current_binding=self._client_binding,
            receipt=target / "client-conformance.json",
            agent=self._prepared["agent"],
            foreign_environment=directories,
        )
        environment = {**child, "AUTOCODE_KILO_CLIENT_CONFORMANCE": json.dumps(control.options)}
        receipt = target / "client-conformance-call.json"
        try:
            _run(
                [*command[: len(command) - len(self._direct(command)) + 1], "debug", "agent", self._prepared["agent"]],
                environment=environment,
                cwd=self._root,
                timeout=max(0.001, deadline - time.monotonic()),
                receipt=receipt,
                checkpoint=control.start,
            )
        finally:
            result = files._json(receipt.read_bytes()) if receipt.exists() else {"exit": None, "expired": True}
            control.finish(result=result)
        return control.require()

    def _configure(self, command, child, request):
        target = self._prepared["target"]
        boundary = containment.prepare(
            self._root,
            allow_write=False,
            environment=child,
            read_roots=request["read_roots"],
            protected_paths=request["protected_paths"],
            verification_copy=True,
            source_paths=request.get("source_paths", ()),
        )
        runtime = _relocate_client_runtime(boundary, child)
        self._prepared.update(runtime_root=runtime, environment=deepcopy(child))
        self._prepare_derivation_policy()
        prepared_shell = Path(boundary["shell"])
        inherited = target / "inherited-shell"
        _write(
            inherited,
            _inherited_shell_body(prepared_shell.read_bytes(), boundary["profile"]),
            self._prepared["files"],
            executable=True,
        )
        boundary.update(
            prepared_shell=str(prepared_shell),
            prepared_shell_sha256=util.file_hash(prepared_shell),
            shell=str(inherited),
            shell_enforcement="inherited_whole_client",
            client_runtime_root=str(runtime),
        )
        config = json.loads(child[CONFIG])
        config["shell"] = boundary["shell"]
        child[CONFIG], child["SHELL"] = json.dumps(config), boundary["shell"]
        policy_path = target / ("client.json" if sys.platform == "linux" else "client.sb")
        prompt = (
            self._image_preparation.prompt_path
            if self._image_preparation is not None and hasattr(self._image_preparation, "prompt_path")
            else self._prepared["prompt_file"]
        )
        policy = whole_client_policy(
            self._root,
            target,
            boundary,
            request,
            child,
            self._source.socket_path,
            prompt,
            os_readonly_inputs=self._prepared["os_readonly_inputs"],
        )
        if sys.platform == "linux":
            _require(
                not containment._secret_files(self._root, Path(boundary["profile"]).parent.name),
                "Linux native Kilo refuses visible credential-named source inputs",
            )
            reads = [
                self._root,
                target,
                Path(boundary["profile"]).parent,
                self._source.socket_path,
                *request["read_roots"],
                Path(boundary["scratch"]) / "verification",
            ]
            if prompt is not None:
                reads.append(Path(prompt))
            value = linux_boundary.policy(
                policy_path,
                kind="client",
                read_paths=reads,
                write_paths=[runtime.parent],
                hidden_paths=[self._root / ".autocode"],
                cwd=self._root,
            )
            program = linux_boundary.write(policy_path, value)
            self._prepared["files"].update({str(path): util.file_hash(path) for path in (policy_path, program)})
            prefix = linux_boundary.prefix(policy_path)
        else:
            _write(policy_path, policy.encode(), self._prepared["files"])
            prefix = ["/usr/bin/sandbox-exec", "-f", str(policy_path)]
        version = _run(
            [*prefix, command[0], "--version"],
            environment=child,
            cwd=self._root,
            timeout=15,
            receipt=target / "version.json",
        )
        _require(version.decode().strip() == rules.VERSION, "actual compiled Kilo version differs")

        def debug(args=()):
            raw = _run(
                [*prefix, command[0], "debug", "agent", self._prepared["agent"], *args],
                environment=child,
                cwd=self._root,
                timeout=60,
                receipt=target / ("debug-" + uuid.uuid4().hex + ".json"),
            )
            return files._json(raw)

        actual = debug()
        permission = containment.shell_permissions(actual.get("permission"))
        _require(
            not any(value == "ask" for value in permission["bash"].values()), "native asks cannot be auto-approved"
        )
        config["agent"][self._prepared["agent"]]["permission"] = permission
        child[CONFIG] = json.dumps(config)
        actual = debug()
        final_command = [*prefix, *command]
        observed = self._observe(final_command, child, actual)
        control = Path(boundary["profile"]).parent
        sentinel = control / "conformance-forbidden"
        sentinel.write_text("runner-owned sentinel\n")
        scratch = Path(boundary["scratch"]) / "conformance-capture"
        probe = (
            'test "$AUTOCODE_TOOL_CONTAINMENT_ID" = '
            + shlex.quote(control.name)
            + " && ! (: > "
            + shlex.quote(str(sentinel))
            + ") && (: > "
            + shlex.quote(str(scratch))
            + ")"
        )
        response = debug(
            [
                "--tool",
                "bash",
                "--params",
                json.dumps(
                    {
                        "command": probe,
                        "description": "AutoCode native Kilo kernel conformance (no model)",
                        "workdir": str(self._root),
                        "timeout": 10000,
                    }
                ),
            ]
        )
        native = containment._check_kernel_receipt(response, probe, control, scratch, sentinel)
        self._prepared.update(command=final_command, environment=deepcopy(child), config=config)
        client = self._client_probe(final_command, child)
        proof = {
            "version": 1,
            "native_version": rules.VERSION,
            "launch_identity": observed,
            "native_receipt": native,
            "client_profile": str(policy_path),
            "client_profile_sha256": util.file_hash(policy_path),
            "client_command": final_command,
            "command": probe,
            "exit": response["result"]["metadata"]["exit"],
            "client_conformance": client,
            "os_readonly_inputs": self._prepared["os_readonly_inputs"],
            "shell_enforcement": boundary["shell_enforcement"],
            "client_runtime_root": str(runtime),
            "client_runtime_paths": {key: child[key] for key in MUTABLE_DIRECTORIES},
            "verification_copy": {
                "path": boundary["verification_copy"],
                "sha256": boundary["verification_copy_sha256"],
            },
            "prepared_tool_profile": {"path": boundary["profile"], "sha256": util.file_hash(boundary["profile"])},
            **{
                key: {"path": boundary[key], "sha256": util.file_hash(boundary[key])}
                for key in ("shell", "prepared_shell")
            },
        }
        proof_path = control / "conformance.json"
        proof_path.write_text(json.dumps(proof) + "\n")
        proof_path.chmod(0o400)
        boundary.update(
            native_backend="kilocode",
            native_version=rules.VERSION,
            conformance=str(proof_path),
            conformance_sha256=util.file_hash(proof_path),
            client_profile=str(policy_path),
            client_profile_sha256=util.file_hash(policy_path),
            client_conformance=client,
            client_executable_sha256=self._executable_hash,
            os_readonly_inputs=deepcopy(self._prepared["os_readonly_inputs"]),
            profile_sha256=util.file_hash(boundary["profile"]),
            shell_sha256=util.file_hash(boundary["shell"]),
        )
        _require_timezone_inputs(boundary["os_readonly_inputs"])
        self._prepared.update(command=final_command, environment=deepcopy(child), config=config)
        return final_command, child, boundary

    def require_image_profile(self, profile):
        _require(
            self._role == "sol" and profile == self._profile and profile["mode"] == rules.MODE,
            "image extension differs from original Claude reviewer",
        )
        self._check(self._state)

    def stage_image(self, preparation, *, command, environment):
        _require(
            preparation.isolation is self
            and self._image_preparation is None
            and command == self._prepared["command"]
            and environment == self._prepared["environment"],
            "foreign or repeated original image preparation",
        )
        self.require_image_profile(preparation.profile)
        argv, child, pins = preparation.stage(self, command, environment)
        self._prepared.update(command=argv, environment=child, config=json.loads(child[CONFIG]))
        self._prepared["files"].update(pins)
        self._image_preparation = preparation
        return argv, child

    def render_image_prompt(self, *, command, environment, boundary):
        if self._image_preparation is not None:
            self._prepared["files"].update(self._image_preparation.render(command, environment, boundary))

    def finalize(self, *, command, environment, boundary):
        _require(self._descriptor is None and command == self._prepared["command"], "foreign or repeated finalization")
        self._check(self._state)
        verify_boundary(boundary)
        proof = files._json(Path(boundary["conformance"]).read_bytes())
        observed = self._observe(
            command,
            environment,
            {
                "name": proof["launch_identity"]["effective_agent"],
                "tools": proof["launch_identity"]["effective_tools"],
                "permission": proof["launch_identity"]["effective_permission"],
            },
        )
        _require(observed == proof["launch_identity"], "actual final Kilo inputs changed")
        target = self._prepared["target"]
        _freeze_stage_files(
            target,
            environment,
            self._prepared["files"],
            journal=self._source.journal_path,
            runtime_root=self._prepared["runtime_root"],
        )
        inline = target / "kilo-final.json"
        _write(inline, environment[CONFIG].encode(), self._prepared["files"])
        descriptor = {
            "version": 1,
            "backend": "kilocode",
            "role": self._role,
            "model": rules.MODEL,
            "reasoning_effort": "high",
            "files": dict(self._prepared["files"]),
            "child_identity": {
                key: observed[key]
                for key in ("executable", "executable_sha256", "command_sha256", "environment_sha256")
            },
            "compiled_identity": {
                "executable": str(self._prepared["sdk"] / self._executable.relative_to(self._sdk)),
                "sha256": self._executable_hash,
            },
            "configuration_sources": {
                **{key: environment[key] for key in DIRECTORIES},
                "inline": {"path": str(inline), "sha256": util.file_hash(inline)},
            },
            "conformance": {"path": boundary["conformance"], "sha256": boundary["conformance_sha256"]},
            "client_conformance": self._source.require_client_conformance(),
            "writer_restriction": None,
            "source_relay": self._source.public_locator(),
        }
        manifest = {
            **descriptor,
            "saved_transport_sha256": util.digest(self._identity),
            "saved_settings_sha256": util.digest(self._settings),
            "sdk_manifest_sha256": self._sdk_hash,
            "sdk_source": str(self._sdk),
            "source_hooks": self._binding["source_hooks"],
            "build_acceptance": self._binding["build_acceptance"],
            "image_acceptance": "NOT_VERIFIED",
        }
        if self._image_preparation is not None:
            manifest["image_review"] = descriptor["image_review"] = {
                "prompt": self._image_preparation.prompt_binding,
                "schema": self._image_preparation.schema_binding,
            }
        path = target / "isolation.json"
        _write(path, (json.dumps(manifest, sort_keys=True) + "\n").encode(), {})
        descriptor.update(manifest=str(path), manifest_sha256=util.file_hash(path))
        self._descriptor = descriptor
        return deepcopy(descriptor)

    def environment_descriptor(self, environment):
        """Recover no authority: compare the locator to this original owner."""
        _require(self._descriptor is not None, "owner has no finalized native descriptor")
        _require(
            native_locators.descriptor_matches(
                environment.get("AUTOCODE_NATIVE_ROLE_ISOLATION", "null"), self._descriptor
            ),
            "foreign isolation environment locator",
        )
        return deepcopy(self._descriptor)

    def verify_launch(self, *, state: RunState, command, environment, boundary, writer_execution=None):
        _require(self._descriptor is not None and writer_execution is None, "missing readonly original Kilo owner")
        self._check(state)
        verify_boundary(boundary)
        prepared = cast(dict, self._prepared)
        source = cast(relay.KiloSourceRelay, self._source)
        _require(
            source.require_client_conformance() == boundary["client_conformance"],
            "original compiled-client conformance changed",
        )
        descriptor = cast(dict, self._descriptor)
        _require(
            command == prepared["command"]
            and boundary.get("native_role_isolation") == descriptor
            and native_locators.descriptor_matches(
                environment.get("AUTOCODE_NATIVE_ROLE_ISOLATION", "null"), descriptor
            ),
            "final Kilo owner locators or command changed",
        )
        _require(
            json.loads(environment.get("AUTOCODE_KILO_SOURCE_RELAY", "null")) == source.child_channel(),
            "original parent Unix source channel changed",
        )
        _require(
            util.file_hash(descriptor["manifest"]) == descriptor["manifest_sha256"], "original Kilo manifest changed"
        )
        for name, sha in descriptor["files"].items():
            path = files._owned(self._run, name)
            _require(
                path.is_file()
                and not path.is_symlink()
                and path.stat().st_mode & 0o222 == 0
                and util.file_hash(path) == sha,
                "staged original SDK/config/plugin bytes changed",
            )
        proof = files._json(Path(boundary["conformance"]).read_bytes())
        actual = self._observe(
            command,
            environment,
            {
                "name": proof["launch_identity"]["effective_agent"],
                "tools": proof["launch_identity"]["effective_tools"],
                "permission": proof["launch_identity"]["effective_permission"],
            },
        )
        _require(actual == proof["launch_identity"], "actual Kilo conformance no longer matches final launch")
        return deepcopy(descriptor)

    def freeze_image(self, *, command, environment, descriptor):
        if self._image_preparation is not None:
            return self._image_preparation.freeze(command, environment, descriptor)

    def image_pins(self, context):
        if self._image_preparation is None:
            _require(context is None, "image context has no original packet")
            return {}
        return self._image_preparation.pins(context)


def verify_boundary(boundary):
    """Recheck Kilo-specific bytes without pretending it is OpenCode 1.18.33."""
    _require(
        isinstance(boundary, dict)
        and boundary.get("version") == 1
        and boundary.get("native_backend") == "kilocode"
        and boundary.get("native_version") == rules.VERSION
        and boundary.get("allow_write") is False
        and boundary.get("tools") == ["bash"],
        "missing exact readonly Kilo whole-client boundary",
    )
    root = Path(boundary["workspace"]).resolve()
    if boundary.get("platform") == "linux":
        _require(
            sys.platform == "linux" and boundary.get("backend") == containment.BUBBLEWRAP,
            "Linux Kilo boundary is not on its supported original backend",
        )
        linux_boundary.verify(boundary["client_profile"])
    for key in ("profile", "shell", "conformance", "client_profile"):
        path = files._owned(root, boundary[key])
        _require(
            path.is_file() and not path.is_symlink() and util.file_hash(path) == boundary[key + "_sha256"],
            "Kilo boundary bytes changed",
        )
    proof = files._json(Path(boundary["conformance"]).read_bytes())
    _require(
        boundary.get("shell_enforcement") == proof.get("shell_enforcement") == "inherited_whole_client",
        "Kilo Bash is not bound to its inherited client policy",
    )
    runtime = Path(boundary["scratch"]) / "kilo-runtime"
    _require(
        boundary.get("client_runtime_root") == proof.get("client_runtime_root") == str(runtime),
        "original Kilo scratch runtime changed",
    )
    _require_runtime_mapping({key: str(runtime / name) for key, name in MUTABLE_DIRECTORIES.items()}, runtime)
    _require(
        proof.get("client_runtime_paths") == {key: str(runtime / name) for key, name in MUTABLE_DIRECTORIES.items()},
        "protected Kilo scratch runtime paths changed",
    )
    original = files._owned(root, boundary["prepared_shell"])
    _require(
        util.file_hash(original) == boundary["prepared_shell_sha256"]
        and Path(boundary["shell"]).read_bytes() == _inherited_shell_body(original.read_bytes(), boundary["profile"]),
        "prepared inherited shell changed",
    )
    _require(
        proof.get("prepared_tool_profile") == {"path": boundary["profile"], "sha256": boundary["profile_sha256"]},
        "prepared tool profile template changed",
    )
    for key in ("shell", "prepared_shell"):
        _require(
            proof.get(key) == {"path": boundary[key], "sha256": boundary[key + "_sha256"]},
            "protected Kilo shell/profile proof changed",
        )
    _require(proof.get("os_readonly_inputs") == boundary.get("os_readonly_inputs"), "Kilo OS input proof changed")
    _require_timezone_inputs(boundary.get("os_readonly_inputs"))
    native = proof.get("native_receipt", {})
    _require(
        proof.get("native_version") == rules.VERSION
        and native.get("schema") == 1
        and native.get("category") == "native_kernel_receipt"
        and native.get("status") == "passed"
        and native.get("failed_predicates") == []
        and proof.get("exit") == 0
        and proof.get("client_profile_sha256") == boundary["client_profile_sha256"],
        "actual Kilo native kernel conformance is missing",
    )
    client = boundary.get("client_conformance", {})
    _require(
        isinstance(client, dict) and set(client) == {"path", "sha256"} and proof.get("client_conformance") == client,
        "compiled-client proof locator is missing",
    )
    path = files._owned(root, client["path"])
    _require(
        path.is_file() and not path.is_symlink() and util.file_hash(path) == client["sha256"],
        "compiled-client proof bytes changed",
    )
    body = files._json(path.read_bytes())
    relay.client_conformance.validate_retained_receipt(
        body,
        executable_sha256=boundary["client_executable_sha256"],
        client_profile_sha256=boundary["client_profile_sha256"],
    )
    if boundary.get("verification_copy"):
        _require(
            proof.get("verification_copy")
            == {"path": boundary["verification_copy"], "sha256": boundary["verification_copy_sha256"]},
            "protected Kilo verification copy proof changed",
        )
        if boundary.get("platform") == "linux":
            policy = linux_boundary.verify(boundary["client_profile"])
            tree = Path(boundary["scratch"]) / "verification"
            protected = _verification_protected_paths(boundary)
            arguments = policy["arguments"]
            mounts = [arguments[i : i + 3] for i, value in enumerate(arguments) if value in ("--bind", "--ro-bind")]
            _require(
                policy["kind"] == "client"
                and policy["inputs"]["write_paths"] == [boundary["scratch"]]
                and ["--ro-bind", str(tree), str(tree)] in mounts
                and all(Path(path).is_relative_to(tree) for path in protected),
                "installed Kilo profile omitted protected verification inputs",
            )
        else:
            policy = Path(boundary["client_profile"]).read_text()
            _require(
                all(
                    "(deny file-write* (literal " + json.dumps(path, ensure_ascii=False) + "))" in policy
                    for path in _verification_protected_paths(boundary)
                ),
                "installed Kilo profile omitted protected verification inputs",
            )
        try:
            from . import autocode_verification_copy as copies
        except ImportError:
            import autocode_verification_copy as copies
        copies.execution(boundary["verification_copy"], boundary["verification_copy_sha256"], root)
    return boundary
