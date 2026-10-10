"""Kernel containment for OpenCode tool subprocesses, not its authenticated client.

Strict mode deliberately exposes only the native shell tool. In-process file,
plugin and MCP tools cannot inherit a child-process sandbox and are not covered.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

SUPPORTED_VERSION = "1.18.33"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SYSTEM_READ_ROOTS = (
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/usr/lib",
    "/usr/share",
    "/System/Library",
    "/Library/Apple/System/Library",
)
# prepare() names each launch's control directory uuid4().hex; nothing else matches.
CONTROL_NAME = re.compile("tool-containment-[0-9a-f]{32}")


def unavailable(workspace=None, environment=None):
    """Why strict containment cannot be established on this machine, or None.

    Cheap run-setup check of what every launch's configure() refuses first: the
    platform, sandbox-exec and the installed OpenCode version. It runs no
    conformance; None is not a proof, and each launch still runs configure().
    """
    if sys.platform != "darwin":
        return f"strict tool containment requires macOS sandbox-exec; this machine is {sys.platform}"
    if not Path(SANDBOX_EXEC).is_file():
        return f"strict tool containment requires macOS sandbox-exec, which is missing at {SANDBOX_EXEC}"
    env = dict(os.environ if environment is None else environment)
    executable = shutil.which("opencode", path=env.get("PATH", ""))
    if not executable:
        return "strict tool containment cannot find the opencode executable on PATH"
    try:
        result = subprocess.run(
            [executable, "--version"], env=env, cwd=workspace, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"strict tool containment could not read the OpenCode version: {error}"
    version = result.stdout.strip()
    if result.returncode or version != SUPPORTED_VERSION:
        found = version if version and not result.returncode else f"unknown (exit {result.returncode})"
        return (
            f"strict tool containment is qualified only for OpenCode {SUPPORTED_VERSION}; "
            f"this machine has OpenCode {found}"
        )
    return None


def _path(value):
    path = Path(value).absolute()
    if path != path.resolve():
        # /var is the standard macOS alias, not a caller-controlled symlink.
        if str(path).startswith("/var/") and str(path.resolve()) == "/private" + str(path):
            return path.resolve()
        raise ValueError(f"Tool containment refuses a symlinked authority path: {path}")
    return path


def _protected_path(value):
    # A copied symlink itself must be protected from unlink/replace. Resolve
    # its parent only: resolving the final link would protect a different file.
    path = Path(value).absolute()
    if path.parent.resolve() != path.parent:
        raise ValueError("Symlinked parent of protected authority path")
    return path


def policy(workspace, scratch, *, allow_write=False, read_roots=(), protected_paths=()):
    """Return a deny-default Seatbelt profile. No model command is interpreted here."""
    root, scratch = _path(workspace), _path(scratch)
    private = root / ".autocode"
    if not scratch.is_relative_to(private) or scratch == private:
        raise ValueError("Tool scratch must be a fresh owned child of workspace/.autocode")
    reads = {root, scratch, *(Path(p).resolve() for p in SYSTEM_READ_ROOTS)}
    reads.update(_path(p) for p in read_roots)
    if any(p == Path("/") or p == Path.home() for p in reads):
        raise ValueError("Tool containment refuses an unscoped read root")
    protected = {
        private,
        root / ".git",
        root / ".opencode",
        root / "opencode.json",
        root / "opencode.jsonc",
        *(_protected_path(p) for p in protected_paths),
        *(_path(p) for p in read_roots if _path(p).is_relative_to(root)),
    }
    # Seatbelt accepts UTF-8 paths, not JSON's ASCII-only Unicode escapes.
    # JSON quoting still escapes literal quotes and backslashes in SBPL strings.
    literal = lambda p: "(literal " + json.dumps(str(p), ensure_ascii=False) + ")"
    subpath = lambda p: "(subpath " + json.dumps(str(p), ensure_ascii=False) + ")"
    lines = [
        "(version 1)",
        "(deny default)",
        # KERN_PROCARGS2 is not gated by sysctl-read. Explicitly deny
        # cross-process inspection; npm needs its own process metadata.
        "(deny process-info*)",
        "(allow process-info* (target self))",
        "(allow process-exec process-fork)",
        "(allow signal (target same-sandbox))",
        # Native allocators and language runtimes query page size, CPU
        # capabilities and uname. Never grant sysctl-write or process data.
        '(allow sysctl-read (sysctl-name-prefix "hw.") '
        '(sysctl-name "kern.ostype" "kern.osrelease" "kern.osversion" '
        '"kern.version" "kern.hostname" "kern.osrevision" "kern.hostid"))',
        "(allow file-read-metadata)",
        '(allow file-read* (literal "/"))',
        "(allow file-read* " + " ".join(subpath(p) for p in sorted(reads)) + ")",
        '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))',
        '(allow file-write* (literal "/dev/null") ' + subpath(scratch) + ")",
    ]
    if allow_write:
        lines.append(
            "(allow file-write* (require-all "
            + subpath(root)
            + " "
            + " ".join("(require-not " + subpath(p) + ")" for p in sorted(protected))
            + "))"
        )
    # Deny metadata changes on authority directories too: renaming an ancestor
    # could otherwise move a writable subtree over the runner's evidence.
    lines.extend("(deny file-write* " + literal(p) + ")" for p in sorted(protected))
    lines.append('(deny file-read* (regex "(^|/)\\\\.env($|\\\\.)") (regex "(^|/)(auth\\\\.json|id_rsa|id_ed25519)$"))')
    return "\n".join(lines) + "\n"


def _reject_loopback_request(checks, authority):
    # Seatbelt's "localhost" matches non-loopback addresses belonging to this
    # host too. Exact-command dispatch cannot make that an exact-IP sandbox.
    if not isinstance(checks, (list, tuple)):
        raise ValueError("Loopback checks must be an explicit command list")
    if checks or authority is not None:
        raise RuntimeError(
            "Exact-IP loopback containment is unsupported: macOS Seatbelt localhost "
            "also permits non-loopback host addresses; use a runner-mediated approved check"
        )


def prepare(
    workspace,
    *,
    allow_write=False,
    read_roots=(),
    protected_paths=(),
    environment=None,
    loopback_checks=(),
    loopback_authority=None,
    verification_copy=False,
    source_paths=(),
):
    """Create a fresh runner-owned policy and shell; return only nonsecret metadata."""
    _reject_loopback_request(loopback_checks, loopback_authority)
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise RuntimeError("Strict native tool containment requires macOS sandbox-exec; no provider launched")
    root = _path(workspace)
    parent = root / ".autocode"
    if parent.is_symlink():
        raise ValueError("Tool containment refuses a symlinked .autocode directory")
    parent.mkdir(exist_ok=True)
    control = parent / ("tool-containment-" + uuid.uuid4().hex)
    control.mkdir(mode=0o700)
    scratch = control / "scratch"
    scratch.mkdir(mode=0o700)
    verification = None
    if verification_copy:
        if allow_write:
            raise ValueError("Writable stages must execute in the original workspace")
        try:
            from . import autocode_verification_copy as copies
        except ImportError:
            import autocode_verification_copy as copies
        verification = copies.create(root, scratch, source_paths=source_paths)
        protected_paths = [*protected_paths, *verification["protected_paths"]]
    profile = control / "policy.sb"
    profile.write_text(
        policy(root, scratch, allow_write=allow_write, read_roots=read_roots, protected_paths=protected_paths),
        encoding="utf-8",
    )
    shell = control / "shell"
    # env -i separates model-client credentials and startup hooks from tool
    # authority. The authenticated OpenCode parent's environment is unchanged.
    env = environment or {}
    # Keep package/config startup within the same isolated HOME authority.
    # npm rejects a single path used for both its user and global configs.
    npm_user, npm_global = scratch / "npm-user.conf", scratch / "npm-global.conf"
    npm_user.write_text("")
    npm_global.write_text("")
    safe = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "OPENSSL_CONF": "/dev/null",
        "NPM_CONFIG_USERCONFIG": str(npm_user),
        "NPM_CONFIG_GLOBALCONFIG": str(npm_global),
        "PATH": env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "TMP": str(scratch),
        "TEMP": str(scratch),
        "SHELL": "/bin/bash",
        "BASH_ENV": "/dev/null",
        "ENV": "/dev/null",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "CI": "1",
        "LANG": "en_US.UTF-8",
        "AUTOCODE_TOOL_CONTAINMENT_ID": control.name,
        "AUTOCODE_OUTPUT_STORE": str(scratch / "output-store"),
    }
    if verification:
        safe["AUTOCODE_VERIFICATION_COPY"] = verification["manifest"]
        safe["AUTOCODE_VERIFICATION_COPY_SHA256"] = verification["sha256"]
    if env.get("AUTOCODE_CAPTURE_CONTEXT"):
        context = json.loads(env["AUTOCODE_CAPTURE_CONTEXT"])
        if not isinstance(context, dict) or not all(
            isinstance(context.get(key), str) for key in ("attempt", "nonce", "source_revision")
        ):
            raise ValueError("Invalid runner capture context for tool containment")
        safe["AUTOCODE_CAPTURE_CONTEXT"] = json.dumps(context)
    argv = [
        "/usr/bin/env",
        "-i",
        *(key + "=" + value for key, value in safe.items()),
        "/usr/bin/sandbox-exec",
        "-f",
        str(profile),
        "/bin/bash",
        "--noprofile",
        "--norc",
    ]
    shell.write_text(
        "#!/bin/sh -p\n"
        'if [ "$#" -ne 2 ] || [ "$1" != "-c" ]; then exit 126; fi\n'
        "exec " + shlex.join(argv) + ' -c "$2"\n'
    )
    shell.chmod(0o500)
    profile.chmod(0o400)
    probe = subprocess.run([str(shell), "-c", "exit 0"], cwd=root, capture_output=True, text=True, timeout=10)
    if probe.returncode:
        raise RuntimeError(
            f"Strict native tool sandbox is unavailable (exit {probe.returncode}): " + probe.stderr.strip()
        )
    return {
        **(
            {"verification_copy": verification["manifest"], "verification_copy_sha256": verification["sha256"]}
            if verification
            else {}
        ),
        "version": 1,
        "platform": sys.platform,
        "workspace": str(root),
        "shell": str(shell),
        "profile": str(profile),
        "scratch": str(scratch),
        "allow_write": allow_write,
        "tools": ["bash"],
    }


def shell_permissions(rules):
    """Intersect effective OpenCode permissions with the contained tool surface.

    OpenCode uses ordered wildcard rules, not shell-command substring filters.
    Reconstruct ONLY the already effective bash rules; never add an allow.
    """
    if not isinstance(rules, list) or not rules:
        raise RuntimeError("Strict containment needs an auditable effective permission policy")
    bash = {}
    for rule in rules:
        if not isinstance(rule, dict) or not all(
            isinstance(rule.get(k), str) for k in ("permission", "pattern", "action")
        ):
            raise RuntimeError("Strict containment received an unsupported permission rule")
        if rule["action"] not in ("allow", "deny", "ask"):
            raise RuntimeError("Strict containment received an unknown permission action")
        pattern = re.escape(rule["permission"]).replace(r"\*", ".*").replace(r"\?", ".")
        if re.fullmatch(pattern, "bash"):
            # Re-insertion retains last-match ordering, including repeated keys.
            bash.pop(rule["pattern"], None)
            bash[rule["pattern"]] = rule["action"]
    if not bash:
        raise RuntimeError("Strict containment cannot establish the native bash permission")
    return {"*": "deny", "bash": bash, "external_directory": "deny"}


def configure(command, environment, workspace, *, allow_write, request):
    """Constrain one launch and prove its *actual* native bash path before dispatch.

    Only model-free debug commands run here. No approval is auto-granted: an
    approval-bearing shell policy blocks conformance rather than using debug's
    auto-allow-ask behavior. The original model, effort and command are untouched.
    """
    if sys.platform != "darwin":
        raise RuntimeError("Strict native tool containment requires macOS; no provider launched")
    if not isinstance(request, dict) or set(request) - {
        "read_roots",
        "protected_paths",
        "loopback_checks",
        "loopback_authority",
        "tool_commands",
        "source_paths",
    }:
        raise ValueError("Unsupported strict tool containment request")
    _reject_loopback_request(request.get("loopback_checks", ()), request.get("loopback_authority"))
    if "--session" in command:
        raise RuntimeError("Strict native tool containment requires a fresh session; resumed transport is unqualified")
    executable = shutil.which(command[0], path=environment.get("PATH", ""))
    if not executable:
        raise RuntimeError("Strict native tool containment cannot find the provider executable")
    version = subprocess.run(
        [executable, "--version"], env=environment, cwd=workspace, capture_output=True, text=True, timeout=15
    )
    if version.returncode or version.stdout.strip() != SUPPORTED_VERSION:
        raise RuntimeError(
            "Strict native tool containment supports only conformance-tested OpenCode " + SUPPORTED_VERSION
        )
    agent = command[command.index("--agent") + 1]
    child = dict(environment)
    config = json.loads(child["OPENCODE_CONFIG_CONTENT"])
    definition = config["agent"][agent]
    definition["model"] = command[command.index("--model") + 1]
    child["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    prefix = [executable, "debug", "agent", agent]

    def debug(args=()):
        result = subprocess.run([*prefix, *args], cwd=workspace, env=child, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(
                "Strict native tool conformance failed before provider launch (exit " + str(result.returncode) + ")"
            )
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise RuntimeError("Native tool conformance did not return auditable JSON") from error

    original = debug()
    if not isinstance(original, dict):
        raise RuntimeError("Native tool conformance returned an unsupported agent object")
    permissions = shell_permissions(original.get("permission"))
    try:
        from . import autocode_toolchain as toolchain
    except ImportError:
        import autocode_toolchain as toolchain
    tools_ready = toolchain.discover(workspace, request.get("tool_commands", ()), child)
    boundary = prepare(
        workspace,
        allow_write=allow_write,
        environment=child,
        read_roots=[*request.get("read_roots", ()), *tools_ready["read_roots"]],
        protected_paths=request.get("protected_paths", ()),
        loopback_checks=request.get("loopback_checks", ()),
        loopback_authority=request.get("loopback_authority"),
        verification_copy=not allow_write,
        source_paths=request.get("source_paths", ()),
    )
    config["shell"] = boundary["shell"]
    definition["permission"] = permissions
    definition.pop("tools", None)
    child["SHELL"] = boundary["shell"]
    child["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    actual = debug()
    if not isinstance(actual, dict):
        raise RuntimeError("Native tool conformance returned an unsupported agent object")
    if shell_permissions(actual.get("permission")) != permissions:
        raise RuntimeError("Strict containment could not preserve the effective shell restrictions")
    tools = actual.get("tools", {})
    if (
        not isinstance(tools, dict)
        or tools.get("bash") is not True
        or any(value is not False for name, value in tools.items() if name != "bash")
    ):
        raise RuntimeError("Strict containment could not disable every uncontained native tool")
    # The final restrictive wildcard shadows all non-shell asks. A shell ask
    # must remain a real approval, never be silently approved by debug agent.
    if any(action == "ask" for action in permissions["bash"].values()):
        raise RuntimeError("Native debug would auto-approve a shell ask; strict conformance is blocked")
    control = Path(boundary["profile"]).parent
    sentinel = control / "conformance-forbidden"
    sentinel.write_text("runner-owned sentinel\n")
    scratch = Path(boundary["scratch"]) / "conformance-capture"
    probe = (
        'test "$AUTOCODE_TOOL_CONTAINMENT_ID" = '
        + shlex.quote(control.name)
        + " && ! (: > "
        + shlex.quote(str(sentinel))
        + ")"
        + " && (: > "
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
                    "description": "AutoCode kernel containment conformance (no model)",
                    "workdir": str(Path(workspace).resolve()),
                    "timeout": 10000,
                }
            ),
        ]
    )
    native_receipt = _check_kernel_receipt(response, probe, control, scratch, sentinel)
    result = response["result"]
    tool_receipts = []
    for tool_probe in tools_ready["probes"]:
        observed = debug(
            [
                "--tool",
                "bash",
                "--params",
                json.dumps(
                    {
                        "command": tool_probe,
                        "description": "AutoCode selected toolchain startup check (no model)",
                        "workdir": str(Path(workspace).resolve()),
                        "timeout": 10000,
                    }
                ),
            ]
        )
        checked = observed.get("result", {})
        if (
            observed.get("input", {}).get("command") != tool_probe
            or type(checked.get("metadata", {}).get("exit")) is not int
            or checked["metadata"]["exit"] != 0
        ):
            raise RuntimeError(
                "Selected toolchain failed native prelaunch check: "
                + tool_probe
                + "; "
                + str(checked.get("output", ""))[-2000:]
            )
        tool_receipts.append({"command": tool_probe, "exit": 0, "output": checked.get("output")})
    proof = {
        "toolchain_checks": tool_receipts,
        "command": probe,
        "tool": "bash",
        "exit": result["metadata"]["exit"],
        "output": result.get("output") if isinstance(result.get("output"), str) else None,
        "native_receipt": native_receipt,
        "native_version": SUPPORTED_VERSION,
        "profile_sha256": hashlib.sha256(Path(boundary["profile"]).read_bytes()).hexdigest(),
        "shell_sha256": hashlib.sha256(Path(boundary["shell"]).read_bytes()).hexdigest(),
    }
    proof_path = control / "conformance.json"
    proof_path.write_text(json.dumps(proof, indent=2) + "\n")
    boundary.update(
        {
            "conformance": str(proof_path),
            "profile_sha256": proof["profile_sha256"],
            "shell_sha256": proof["shell_sha256"],
            "native_version": SUPPORTED_VERSION,
            "conformance_sha256": hashlib.sha256(proof_path.read_bytes()).hexdigest(),
        }
    )
    verify(boundary)
    child["AUTOCODE_TOOL_CONTAINMENT"] = json.dumps(boundary)
    return child, boundary


def _check_kernel_receipt(response, probe, control, scratch, sentinel):
    """Require direct containment evidence; tool display text is informational."""
    document = response if isinstance(response, dict) else {}
    input_value, result_value = document.get("input"), document.get("result")
    input_record = input_value if isinstance(input_value, dict) else {}
    result = result_value if isinstance(result_value, dict) else {}
    metadata_value = result.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    command, exit_code, output = input_record.get("command"), metadata.get("exit"), result.get("output")
    scratch_created = sentinel_untouched = False
    scratch_io_error = sentinel_io_error = False
    try:
        scratch_created = scratch.is_file()
    except OSError:
        scratch_io_error = True
    try:
        sentinel_untouched = sentinel.read_text() == "runner-owned sentinel\n"
    except (OSError, UnicodeError):
        sentinel_io_error = True
    checks = {
        "response_object": isinstance(response, dict),
        "input_object": isinstance(input_value, dict),
        "result_object": isinstance(result_value, dict),
        "metadata_object": isinstance(metadata_value, dict),
        "output_string": isinstance(output, str),
        "input_command_match": isinstance(command, str) and command == probe,
        "metadata_exit_int": type(exit_code) is int,
        "exit_zero": type(exit_code) is int and exit_code == 0,
        "scratch_created": scratch_created,
        "sentinel_untouched": sentinel_untouched,
        "denial_present": isinstance(output, str)
        and bool(re.search(r"Operation not permitted|Permission denied", output)),
    }
    # OpenCode can omit tool display text. It cannot replace the command/exit
    # identity and direct filesystem witnesses required for containment.
    required = (
        "response_object",
        "input_object",
        "result_object",
        "metadata_object",
        "input_command_match",
        "metadata_exit_int",
        "exit_zero",
        "scratch_created",
        "sentinel_untouched",
    )
    failed = [name for name in required if not checks[name]]
    # JSON value types are an allowlist, never provider-controlled names or text.
    names = {
        dict: "object",
        list: "array",
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }
    receipt = {
        "schema": 1,
        "category": "native_kernel_receipt",
        "status": "rejected" if failed else "passed",
        "checks": checks,
        "failed_predicates": failed,
        "types": {
            name: names.get(type(value), "other")
            for name, value in (
                ("response", response),
                ("input", input_value),
                ("result", result_value),
                ("metadata", metadata_value),
                ("command", command),
                ("exit", exit_code),
                ("output", output),
            )
        },
        "exit": exit_code if type(exit_code) is int and -(2**31) <= exit_code < 2**31 else None,
        "expected_command_sha256": hashlib.sha256(probe.encode("utf-8", "surrogatepass")).hexdigest(),
        "observed_command_sha256": (
            hashlib.sha256(command.encode("utf-8", "surrogatepass")).hexdigest()
            if isinstance(command, str) and len(command) <= 16384
            else None
        ),
        "scratch_io_error": scratch_io_error,
        "sentinel_io_error": sentinel_io_error,
    }
    if not failed:
        return receipt
    retained = False
    try:
        descriptor = os.open(control / "conformance-failure.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")
        retained = True
    except OSError:
        pass
    raise RuntimeError(
        "The actual native bash tool did not demonstrate kernel containment; "
        + "failed required predicates: "
        + ", ".join(failed)
        + "; diagnostic "
        + ("retained" if retained else "unavailable")
    )


def verify(boundary):
    """Recheck runner-owned containment bytes immediately before provider launch."""
    if not isinstance(boundary, dict) or boundary.get("version") != 1:
        raise RuntimeError("Missing or unsupported native tool containment manifest")
    if sys.platform != "darwin" or boundary.get("native_version") != SUPPORTED_VERSION:
        raise RuntimeError("Native tool containment platform or transport is unsupported")
    try:
        workspace = _path(boundary["workspace"])
        profile, shell, scratch = (_path(boundary[name]) for name in ("profile", "shell", "scratch"))
        control = profile.parent
        if (
            control.parent != workspace / ".autocode"
            or not CONTROL_NAME.fullmatch(control.name)
            or profile.name != "policy.sb"
            or shell != control / "shell"
            or scratch != control / "scratch"
            or not scratch.is_dir()
        ):
            raise ValueError("Unexpected containment authority layout")
        if any(key.startswith("loopback_") for key in boundary):
            raise ValueError("Unsupported loopback capability in containment manifest")
        for name in ("profile", "shell", "conformance"):
            path = _path(boundary[name])
            expected = control / {"profile": "policy.sb", "shell": "shell", "conformance": "conformance.json"}[name]
            if (
                path != expected
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != boundary[name + "_sha256"]
            ):
                raise ValueError("Containment authority bytes changed")
    except (KeyError, OSError, ValueError) as error:
        raise RuntimeError("Native tool containment authority changed; no provider launched") from error
    if boundary.get("verification_copy"):
        try:
            from . import autocode_verification_copy as copies
        except ImportError:
            import autocode_verification_copy as copies
        copies.execution(boundary["verification_copy"], boundary["verification_copy_sha256"], workspace)
    return boundary


def recorded_scratch(records, workspace):
    """The scratch directories these launch records say prepare() made for them, in its exact layout.

    A contained stage is told to capture evidence there (provider_launch.containment_prompt), so such
    evidence belongs to the run whose stage records are passed; another run's scratch is not in them.
    Lexical only: callers still refuse symlinks on the evidence path itself.
    """
    private = Path(workspace) / ".autocode"
    found = []
    for record in records:
        boundary = record.get("tool_containment") if isinstance(record, dict) else None
        scratch = boundary.get("scratch") if isinstance(boundary, dict) else None
        if not isinstance(scratch, str):
            continue
        path = Path(scratch)
        if path.name == "scratch" and path.parent.parent == private and CONTROL_NAME.fullmatch(path.parent.name):
            found.append(path)
    return tuple(dict.fromkeys(found))
