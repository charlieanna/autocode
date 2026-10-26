"""Kernel containment for OpenCode tool subprocesses, not its authenticated client.

Strict mode deliberately exposes only the native shell tool. In-process file,
plugin and MCP tools cannot inherit a child-process sandbox and are not covered.

Two backends enforce the same authority (#709): macOS Seatbelt (sandbox-exec)
and Linux bubblewrap (bwrap). Each launch's manifest names the backend and its
version, so stage records say which kernel boundary applied.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

try:
    from . import autocode_native_conformance as native_conformance
    from . import autocode_util as util
    from .autocode_source_privacy import SECRET_NAME
except ImportError:
    import autocode_native_conformance as native_conformance
    import autocode_util as util
    from autocode_source_privacy import SECRET_NAME


SUPPORTED_VERSION = "1.18.33"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
# The distribution package's path; a PATH lookup would let the caller pick the sandbox.
BWRAP = "/usr/bin/bwrap"
SEATBELT, BUBBLEWRAP = "seatbelt", "bubblewrap"
POLICY_FILES = {SEATBELT: "policy.sb", BUBBLEWRAP: "policy.bwrap"}
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
# Linux runtime roots mounted read-only; a merged-/usr symlink is recreated, not followed.
LINUX_READ_ROOTS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
# Nonsecret loader, alternatives and account files that ordinary tools read.
LINUX_SYSTEM_FILES = (
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/alternatives",
    "/etc/localtime",
    "/etc/passwd",
    "/etc/group",
    "/etc/nsswitch.conf",
)
# prepare() names each launch's control directory uuid4().hex; nothing else matches.
CONTROL_NAME = re.compile("tool-containment-[0-9a-f]{32}")
# .autocode directories, besides the run store, where AutoCode keeps other runs, programs and task
# flows, or the workspaces, archives, conversation records and indexes that hold them (#1250):
# archived task-worktree runs, `autocode arena` attempts, parallel Builder workspaces, run bundles,
# requirements conversation handoffs, conversation start markers, bug investigation workspaces,
# `autocode issue` worktrees, program state, `autocode review-pr` checkouts, the run registry,
# replay recordings, task-flow state, `autocode verify` checkouts and reports, and task or program
# worktrees. A native planning shell reads none of those that exist when its launch is prepared
# (private_run_reads()). Not every .autocode directory is listed: other launches' control directories
# and the rest stay readable. The modules that make these directories own their names;
# tests/test_tool_containment.py binds each entry to them. Program state and program worktrees
# come from the retired `autocode program` (archived on branch archive/program); they stay denied
# because older runs may have left them.
PRIVATE_STORES = (
    "archive",
    "arena",
    "builders",
    "bundles",
    "conversation-handoffs",
    "conversation-starts",
    "investigation",
    "issues",
    "programs",
    "pull-reviews",
    "registry",
    "replay",
    "task-flows",
    "verification",
    "worktrees",
)


class NativePermissionDenied(RuntimeError):
    """Effective provider policy denied a model-free proof; no permission is widened."""


class UnusableStore(ValueError):
    """A PRIVATE_STORES path that a planning boundary cannot deny as it stands (#1250).

    A symlink, even a dangling one, or anything else that is not a directory: bubblewrap hides a
    store under a tmpfs, which needs a directory to mount on, and denying a symlink's own path does
    not cover what it points to. The message names the path and what clears the refusal.
    """


class StorePause(util.Paused):
    """PAUSED_TOOL_CONTAINMENT about a private store, whose reason names it and what clears the stop (#1250).

    A store made after the launch's boundary was prepared, or one that became unusable since: the
    sandbox and the native setup are not the cause, so a caller adds no advice about them.
    """

    def __init__(self, reason):
        super().__init__("PAUSED_TOOL_CONTAINMENT", reason)


# Errors that name a private store and their own remedy; provider_launch.prepare adds none of its own.
STORE_ERRORS = (UnusableStore, StorePause)
DENIAL_CHANGED = "Private run-state read denial changed; no provider launched"
DENIAL_TEXT = re.compile(r"Operation not permitted|Permission denied|Read-only file system")
NO_OPENCODE = "strict tool containment cannot find the opencode executable on PATH"


def backend():
    """This host's containment backend name, or None where AutoCode has none."""
    return {"darwin": SEATBELT, "linux": BUBBLEWRAP}.get(sys.platform)


def _bubblewrap_problem():
    """Why bwrap cannot create the namespaces a contained shell needs, or None."""
    if not Path(BWRAP).is_file():
        return (
            "strict tool containment requires macOS sandbox-exec or Linux bubblewrap at "
            f"{BWRAP}; this machine is {sys.platform} without {BWRAP}"
        )
    try:
        probe = subprocess.run(
            [BWRAP, "--unshare-all", "--cap-drop", "ALL", "--ro-bind", "/", "/", "/bin/sh", "-c", "exit 0"],
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"strict tool containment could not run {BWRAP}; this machine is {sys.platform}: {error}"
    if probe.returncode:
        detail = (probe.stderr.strip().splitlines() or ["no message"])[-1][:300]
        # "this machine is <platform>", as every other refusal says it (the CLI names the host the same way).
        return (
            f"strict tool containment found {BWRAP} but it cannot create unprivileged namespaces; "
            f"this machine is {sys.platform}, where the probe exited {probe.returncode}: {detail}"
        )
    return None


def sandbox_problem():
    """Why this host has no usable kernel sandbox for contained shells, or None.

    Runs no OpenCode: on Linux it starts one bwrap probe, because an installed bwrap
    can still be denied unprivileged user namespaces.
    """
    host = backend()
    if host is None:
        return (
            f"strict tool containment requires macOS sandbox-exec or Linux bubblewrap; this machine is {sys.platform}"
        )
    if host == SEATBELT and not Path(SANDBOX_EXEC).is_file():
        return f"strict tool containment requires macOS sandbox-exec, which is missing at {SANDBOX_EXEC}"
    return _bubblewrap_problem() if host == BUBBLEWRAP else None


def backend_version(name):
    """The backend's own version for the transport identity: bwrap's, or macOS's for Seatbelt."""
    if name == BUBBLEWRAP:
        try:
            result = subprocess.run([BWRAP, "--version"], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        version = result.stdout.strip()
        return version if version and not result.returncode else None
    if name == SEATBELT:
        return "macOS " + platform.mac_ver()[0] if platform.mac_ver()[0] else None
    return None


def unavailable(workspace=None, environment=None):
    """Why strict containment cannot be established on this machine, or None.

    Cheap run-setup check of what every launch's configure() refuses first: the
    platform, its sandbox (sandbox-exec, or bwrap able to create namespaces) and
    the installed OpenCode version. It runs no conformance; None is not a proof,
    and each launch still runs configure().
    """
    problem = sandbox_problem()
    if problem:
        return problem
    env = dict(os.environ if environment is None else environment)
    executable = shutil.which("opencode", path=env.get("PATH", ""))
    if not executable:
        return opencode_problem(None)
    try:
        result = subprocess.run(
            [executable, "--version"], env=env, cwd=workspace, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"strict tool containment could not read the OpenCode version: {error}"
    return opencode_problem(result)


def opencode_problem(result):
    """Why the OpenCode whose ``opencode --version`` gave ``result`` cannot run contained, or None.

    ``result`` is that command's CompletedProcess, or None when no opencode is on PATH.
    unavailable() and `autocode doctor` (which runs the command its own way) share this rule.
    """
    if result is None:
        return NO_OPENCODE
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


def _authority(workspace, scratch, read_roots, protected_paths):
    """The validated paths every backend enforces: (root, scratch, extra reads, protected)."""
    root, scratch = _path(workspace), _path(scratch)
    private = root / ".autocode"
    if not scratch.is_relative_to(private) or scratch == private:
        raise ValueError("Tool scratch must be a fresh owned child of workspace/.autocode")
    reads = {_path(p) for p in read_roots}
    if any(p == Path("/") or p == Path.home() for p in (root, *reads)):
        raise ValueError("Tool containment refuses an unscoped read root")
    protected = {
        private,
        root / ".git",
        root / ".opencode",
        root / "opencode.json",
        root / "opencode.jsonc",
        *(_protected_path(p) for p in protected_paths),
        *(p for p in reads if p.is_relative_to(root)),
    }
    return root, scratch, reads, protected


def writer_footprint(workspace, paths):
    """Normalize an exact literal task footprint without widening glob semantics.

    This helper does not authenticate the approved task. The runtime supplies
    its exact bounded paths; unsupported globs fail before native launch.
    Kernel enforcement, native shell-only conformance and actual launch seals
    remain necessary. A normalized path list is not a write-provenance receipt.
    """
    root = _path(workspace)
    if (
        not isinstance(paths, (list, tuple))
        or not paths
        or any(not isinstance(value, str) or not value for value in paths)
        or len(paths) != len(set(paths))
    ):
        raise ValueError("Task footprint requires nonempty unique literal paths")
    result = []
    for value in paths:
        part = Path(value)
        if (
            part.is_absolute()
            or ".." in part.parts
            or "\\" in value
            or "\x00" in value
            or value == "."
            or any(char in value for char in "*?[")
            or str(part) != value.rstrip("/")
            or set(part.parts) & {".git", ".autocode", ".autocode-ui", ".opencode"}
        ):
            raise ValueError("Task footprint needs portable relative literal paths; globs are unsupported")
        path = _path(root / part)
        if not path.is_relative_to(root) or path == root:
            raise ValueError("Task footprint escapes the owned project")
        result.append(value.rstrip("/"))
    if len(result) != len(set(result)):
        raise ValueError("Task footprint has duplicate normalized paths")
    return result


def _writer_path_kinds(workspace, paths):
    """Measure file/directory kind and conservatively refuse shared file inodes.

    A file grant uses literal, never subpath. Existing shared regular-file
    inodes cannot be separated by path-based enforcement. Actual native
    conformance must also deny creation of outside-source hard links.
    """
    root = _path(workspace)
    kinds = {}
    for name in paths:
        path = _path(root / name)
        kinds[name] = "directory" if path.is_dir() else "file"
        files = path.rglob("*") if path.is_dir() else (path,)
        for candidate in files:
            if not candidate.is_symlink() and candidate.is_file() and candidate.stat().st_nlink != 1:
                raise ValueError("Task footprint refuses a shared regular-file inode")
    return kinds


def _bound_path_kinds(footprint, kinds):
    """Copy the exact measured map; rendering must not measure kinds again."""
    if footprint is None:
        if kinds is not None:
            raise ValueError("An unscoped profile cannot receive writer kinds")
        return None
    if (
        not isinstance(kinds, dict)
        or set(kinds) != set(footprint)
        or any(value not in ("file", "directory") for value in kinds.values())
    ):
        raise ValueError("Writer kinds must match the exact footprint keys and values")
    return dict(kinds)


def _policy_inputs(read_roots, protected_paths):
    """Pin the full caller policy inputs without resolving final protected links."""
    return {
        "read_roots": [str(_path(value)) for value in read_roots],
        "protected_paths": [str(_protected_path(value)) for value in protected_paths],
    }


def private_run_reads(workspace, run_dir):
    """The private-read denial for a readonly planning shell of the run at ``run_dir`` (#1250).

    Unreadable, in this order: the workspace's whole run store (``.autocode/runs``: this run's
    state, events and receipts, and every sibling run, whatever its name), then each PRIVATE_STORES
    directory that exists now. Only this run's ``planning/`` directory, the immutable predecessor
    artifacts and deltas the planner is handed, is read back in. A readable path is only an
    exception to this denial, never a grant: the rest of ``.autocode`` (project settings, retained
    design inputs, capture bundles, the shared evidence and output areas, launches' control
    directories) and the workspace keep their ordinary read-only authority.

    A missing store is not created: prepare() adds only the launch's own control directory to the
    workspace. A store path that is a symlink, even a dangling one, or not a directory raises
    UnusableStore. verify() computes this again before launch, so a store made after the profile
    was rendered pauses the launch (StorePause). Each entry is one mount and one rule, so the list
    stays this short however many runs and launches the workspace has held.
    """
    root = _path(workspace)
    private = root / ".autocode"
    store, run = private / "runs", _path(run_dir)  # _path refuses symlinked authority paths.
    if run.parent != store or not run.is_dir():
        raise ValueError("A private-read denial needs an existing run directly inside workspace/.autocode/runs")
    stores = []
    for path in (private / name for name in PRIVATE_STORES):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise UnusableStore(
                f"Planning tool containment cannot deny reading the private .autocode store {path}, which is "
                + ("a symlink" if path.is_symlink() else "not a directory")
                + "; replace it with a real directory, or remove it, then --resume-paused"
            )
        if path.exists():
            stores.append(_path(path))
    return {
        "run_dir": str(run),
        "denied": [str(path) for path in (store, *stores)],
        "readable": [str(_path(run / "planning"))],
    }


def _reparsed_string(path):
    """``path`` as a string in a Seatbelt profile that verify() parses again (_seatbelt_forms()).

    The same bytes json.dumps writes, refused where those bytes would hold an escape other than
    ``\\"`` and ``\\\\``: json.dumps writes a control character as another escape (``\\t``,
    ``\\u0001``), which the strict parse refuses and Seatbelt may read differently.
    """
    text = str(path)
    if any(ord(char) < 0x20 for char in text):
        raise ValueError(f"Seatbelt private-read containment refuses a control character in a path: {text!r}")
    return json.dumps(text, ensure_ascii=False)


def _private_read_rules(reads):
    """The Seatbelt lines enforcing private_run_reads(); verify() looks for these exact lines."""
    subpath = lambda p: "(subpath " + _reparsed_string(p) + ")"
    lines = []
    for value in reads["denied"]:
        denied = Path(value)
        exemption = [Path(p) for p in reads["readable"] if Path(p).is_relative_to(denied)]
        lines.append(
            "(deny file-read* (require-all "
            + subpath(denied)
            + "".join(" (require-not " + subpath(p) + ")" for p in exemption)
            + "))"
        )
    return lines


# Operand counts of every bwrap option bubblewrap_arguments() writes, and the ones that show a source.
BWRAP_OPERANDS = {
    "--unshare-all": 0,
    "--die-with-parent": 0,
    "--new-session": 0,
    "--cap-drop": 1,
    "--dev": 1,
    "--proc": 1,
    "--tmpfs": 1,
    "--remount-ro": 1,
    "--symlink": 2,
    "--bind": 2,
    "--ro-bind": 2,
    "--ro-bind-try": 2,
}
BWRAP_BINDS = ("--bind", "--ro-bind", "--ro-bind-try")
# Every word _render_policy() writes outside a string. The private-read check refuses any
# other word, so no comment, definition or quoting can change what a later rule means.
SEATBELT_WORDS = frozenset(
    [
        "version",
        "1",
        "deny",
        "allow",
        "default",
        "process-info*",
        "target",
        "self",
        "process-exec",
        "process-fork",
        "signal",
        "same-sandbox",
        "sysctl-read",
        "sysctl-name-prefix",
        "sysctl-name",
        "file-read-metadata",
        "file-read*",
        "file-write*",
        "file-write-data",
        "literal",
        "subpath",
        "regex",
        "require-all",
        "require-any",
        "require-not",
    ]
)
# The write operations _render_policy() grants; an allow naming one must keep its shape.
SEATBELT_WRITES = ("file-write*", "file-write-data")
SEATBELT_TOKEN = re.compile(r'\s+|[()]|"(?:[^"\\]|\\["\\])*"|[^\s()"]+')


def _seatbelt_forms(text):
    """The top-level forms of a profile in _render_policy()'s vocabulary, or None for anything else.

    A form is a list of words, ``("string", raw)`` literals and nested forms. An unknown
    word (a comment or a definition among them), an unbalanced form or string, or a string
    escape other than ``\\"`` and ``\\\\`` is refused.
    """
    stack, index = [[]], 0
    while index < len(text):
        match = SEATBELT_TOKEN.match(text, index)
        if match is None:
            return None
        token, index = match.group(), match.end()
        if token == "(":
            stack.append([])
        elif token == ")":
            if len(stack) == 1:
                return None
            form = stack.pop()
            stack[-1].append(form)
        elif token.startswith('"'):
            stack[-1].append(("string", token))
        elif not token.isspace():
            if token not in SEATBELT_WORDS:
                return None
            stack[-1].append(token)
    return stack[0] if len(stack) == 1 else None


def _write_allow_is_confined(form, denied_dirs):
    """Whether a Seatbelt ``(allow ...)`` write grant keeps the shape the renderer writes (#1250).

    A write grant over a denied directory, inside it, or over an ancestor of it lets the shell
    move or hard-link private run state to a readable path, so it defeats the read denial. The
    read-only planning profile grants writes only to scratch, ``/dev/null`` and the recorded
    check outputs, as ``file-write*`` or ``file-write-data`` with positive ``(literal "<path>")``
    and ``(subpath "<path>")`` filters. Only that shape is accepted: write operations alone, at
    least one filter, and each filter's path absolute, canonical as written (as _path() renders
    it) and neither inside nor holding a denied directory. Anything else can reach the store
    without naming a path near it, so it is refused: an unfiltered grant writes everywhere, a
    ``require-not``, ``require-any`` or ``require-all`` filter can match the store through a
    path it does not name, a ``regex`` is not reasoned about, and a symlink or ``..`` in a path
    may not mean what the path's text says.
    """
    words = [item for item in form[1:] if isinstance(item, str)]
    filters = [item for item in form[1:] if not isinstance(item, str)]
    if not filters or any(word not in SEATBELT_WRITES for word in words):
        return False
    for item in filters:
        if not (isinstance(item, list) and len(item) == 2 and item[0] in ("literal", "subpath")):
            return False
        if not isinstance(item[1], tuple):  # ("string", raw JSON token)
            return False
        value = json.loads(item[1][1])
        if not value.startswith("/") or os.path.realpath(value) != value:
            return False
        target = Path(value)
        if any(target.is_relative_to(d) or d.is_relative_to(target) for d in denied_dirs):
            return False
    return True


def _enforces_private_reads(host, profile, reads):
    """Whether these exact profile bytes enforce a private_run_reads() denial.

    Seatbelt: a strict parse in the renderer's own vocabulary. Every top-level form is a
    version, allow or deny rule, those words appear nowhere else, and each denial rule is a
    top-level form after every allow rule, so it is the last match. No ``allow`` may grant
    ``default``, and a write grant must keep the renderer's shape: positive literal or subpath
    filters over canonical paths that neither lie inside nor hold a denied directory
    (_write_allow_is_confined()). A write grant over the store or an ancestor defeats the
    denial as surely as a read grant, by moving or hard-linking private state to a readable
    path. bubblewrap: a strict parse of the arguments. Each denied directory is one tmpfs,
    sealed read-only after it. A bind's source must be exactly as the renderer wrote it --
    absolute, canonical as written (no symlink, ``.``, ``..`` or doubled slash, not even one a
    lexical normalisation would cancel), and never a per-process ``/proc`` or ``/dev/fd``
    link, which the kernel resolves differently in this verifier than in bwrap -- so the
    source is judged as the kernel opens it: one inside a denied directory must be
    inside a readable exception, one holding a denied directory must be mounted read-only
    onto itself before the tmpfs, no writable bind may cover the store or an ancestor, and no
    bind after the tmpfs may cover the denied directory again.
    """
    if host == SEATBELT:
        heads = ("version", "allow", "deny")
        forms = _seatbelt_forms(profile.decode("utf-8"))
        rules = [_seatbelt_forms(rule) for rule in _private_read_rules(reads)]
        if forms is None or None in rules or any(not form or form[0] not in heads for form in forms):
            return False
        pending, named = list(forms), 0
        while pending:
            part = pending.pop()
            if isinstance(part, list):
                pending.extend(part)
            else:
                named += part in heads
        if named != len(forms):  # A rule word inside a rule.
            return False
        allowed = [index for index, form in enumerate(forms) if form[0] == "allow"]
        if not all(rule[0] in forms[max(allowed, default=-1) + 1 :] for rule in rules):
            return False
        denied_dirs = [Path(os.path.normpath(value)) for value in reads["denied"]]
        for form in forms:
            if form[0] != "allow":
                continue
            words = [item for item in form[1:] if isinstance(item, str)]
            if "default" in words:  # (allow default ...) grants writes over the store too.
                return False
            if any(word in SEATBELT_WRITES for word in words) and not _write_allow_is_confined(form, denied_dirs):
                return False
        return True
    values = profile.split(b"\0")
    if values[-1] != b"":
        return False
    arguments, operations, index = [os.fsdecode(value) for value in values[:-1]], [], 0
    while index < len(arguments):
        flag, width = arguments[index], BWRAP_OPERANDS.get(arguments[index])
        operands = arguments[index + 1 : index + 1 + (width or 0)]
        if width is None or len(operands) != width:
            return False
        if flag not in ("--cap-drop", "--symlink"):
            if any(not value.startswith("/") for value in operands):
                return False
            normal = [os.path.normpath(value) for value in operands]
            # A bind's source stays as written: normalising it first would cancel a symlink
            # followed by "..", which the kernel resolves through the link's target (#1250).
            operands = [operands[0], *normal[1:]] if flag in BWRAP_BINDS else normal
        operations.append((flag, operands))
        index += 1 + width
    readable = [Path(os.path.normpath(value)) for value in reads["readable"]]
    for value in reads["denied"]:
        denied = Path(os.path.normpath(value))
        hidden = [i for i, (flag, paths) in enumerate(operations) if flag == "--tmpfs" and Path(paths[0]) == denied]
        sealed = [
            i for i, (flag, paths) in enumerate(operations) if flag == "--remount-ro" and Path(paths[0]) == denied
        ]
        if len(hidden) != 1 or len(sealed) != 1 or sealed[0] < hidden[0]:
            return False
        for i, (flag, paths) in enumerate(operations):
            if flag not in BWRAP_BINDS:
                continue
            raw = paths[0]  # Absolute and exactly as written, never normalised (see above).
            # A per-process /proc or /dev/fd link resolves to one path in this verifier and
            # another in bwrap (launched from the workspace), so its source cannot be judged
            # here. Every source the renderer writes is already canonical as written (_path
            # refuses symlinked authority paths), so require exactly that -- which refuses a
            # symlink, and a symlink followed by "..", in the source as written -- and refuse
            # the magic links.
            if raw == "/proc" or raw == "/dev/fd" or raw.startswith(("/proc/", "/dev/fd/")):
                return False
            if os.path.realpath(raw) != raw:
                return False
            source, target = Path(raw), Path(paths[1])
            if source.is_relative_to(denied) and not any(source.is_relative_to(p) for p in readable):
                return False  # It shows private state at its target.
            if denied.is_relative_to(source) and (target != source or i > hidden[0]):
                return False  # It shows the denied directory where the tmpfs does not cover it.
            if flag == "--bind" and (source.is_relative_to(denied) or denied.is_relative_to(source)):
                return False  # A writable bind of the store or an ancestor defeats the tmpfs.
            if i > hidden[0] and denied.is_relative_to(target):
                return False  # It covers the tmpfs again.
    return True


def _authority(workspace, scratch, read_roots, protected_paths):
    """The validated paths every backend enforces: (root, scratch, extra reads, protected)."""
    root, scratch = _path(workspace), _path(scratch)
    private = root / ".autocode"
    if not scratch.is_relative_to(private) or scratch == private:
        raise ValueError("Tool scratch must be a fresh owned child of workspace/.autocode")
    reads = {_path(p) for p in read_roots}
    if any(p == Path("/") or p == Path.home() for p in (root, *reads)):
        raise ValueError("Tool containment refuses an unscoped read root")
    protected = {
        private,
        root / ".git",
        root / ".opencode",
        root / "opencode.json",
        root / "opencode.jsonc",
        *(_protected_path(p) for p in protected_paths),
        *(p for p in reads if p.is_relative_to(root)),
    }
    return root, scratch, reads, protected


def policy(workspace, scratch, *, allow_write=False, read_roots=(), protected_paths=(), write_paths=None):
    """Measure once, then render a deny-default Seatbelt profile from that map."""
    root = _path(workspace)
    footprint = writer_footprint(root, write_paths) if write_paths is not None else None
    if footprint is not None and not allow_write:
        raise ValueError("Read-only stages cannot receive a writer footprint")
    kinds = _writer_path_kinds(root, footprint) if footprint is not None else None
    return _render_policy(
        root,
        scratch,
        allow_write=allow_write,
        read_roots=read_roots,
        protected_paths=protected_paths,
        write_paths=footprint,
        writer_path_kinds=kinds,
    )


def _render_policy(
    workspace,
    scratch,
    *,
    allow_write=False,
    read_roots=(),
    protected_paths=(),
    write_paths=None,
    writer_path_kinds=None,
    read_workspace=True,
    denied_read_paths=(),
    read_exceptions=(),
    write_roots=None,
    denied_write_paths=(),
    write_files=(),
    private_reads=None,
):
    """Render from the supplied exact map; this is not approved-task authority.

    ``private_reads`` is a private_run_reads() denial for a readonly planning shell.
    """
    root, scratch = _path(workspace), _path(scratch)
    footprint = writer_footprint(root, write_paths) if write_paths is not None else None
    if footprint is not None and not allow_write:
        raise ValueError("Read-only stages cannot receive a writer footprint")
    if private_reads is not None and allow_write:
        raise ValueError("Only a read-only shell takes the private run-store read denial")
    kinds = _bound_path_kinds(footprint, writer_path_kinds)
    private = root / ".autocode"
    if not scratch.is_relative_to(private) or scratch == private:
        raise ValueError("Tool scratch must be a fresh owned child of workspace/.autocode")
    reads = {scratch, *(Path(p).resolve() for p in SYSTEM_READ_ROOTS)}
    if read_workspace:
        reads.add(root)
    extra_reads = {_path(p) for p in read_roots}
    reads.update(extra_reads)
    if any(p == Path("/") or p == Path.home() for p in reads):
        raise ValueError("Tool containment refuses an unscoped read root")
    protected = {
        private,
        root / ".git",
        root / ".opencode",
        root / "opencode.json",
        root / "opencode.jsonc",
        *(_protected_path(p) for p in protected_paths),
        *(p for p in extra_reads if p.is_relative_to(root)),
    }
    exceptions = tuple(_path(p) for p in read_exceptions)
    if any(not p.is_relative_to(scratch) for p in exceptions):
        raise ValueError("Private read exceptions must remain in the exact owned scratch")
    writes = (scratch,) if write_roots is None else tuple(_path(p) for p in write_roots)
    if any(not p.is_relative_to(scratch) or p == scratch for p in writes) and write_roots is not None:
        raise ValueError("Check writes need exact descendants of the owned scratch")
    # Seatbelt accepts UTF-8 paths, not JSON's ASCII-only Unicode escapes.
    # JSON quoting still escapes literal quotes and backslashes in SBPL strings.
    # verify() parses a private-read profile again, so each of its paths must parse back (#1250).
    quoted = _reparsed_string if private_reads is not None else lambda p: json.dumps(str(p), ensure_ascii=False)
    literal = lambda p: "(literal " + quoted(p) + ")"
    subpath = lambda p: "(subpath " + quoted(p) + ")"
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
        '(allow file-write* (literal "/dev/null") ' + " ".join(subpath(p) for p in writes) + ")",
    ]
    for name in write_files:
        output = _path(name)
        if not output.is_relative_to(private) or output.name in ("state.json", "writer.lock"):
            raise ValueError("Check output must be an exact non-authority private file")
        lines.append("(allow file-write-data " + literal(output) + ")")
    if allow_write:
        scoped = (
            "(require-any "
            + " ".join((subpath if kinds[path] == "directory" else literal)(root / path) for path in footprint)
            + ") "
            if footprint is not None
            else ""
        )
        lines.append(
            "(allow file-write* (require-all "
            + subpath(root)
            + " "
            + scoped
            + " ".join("(require-not " + subpath(p) + ")" for p in sorted(protected))
            + "))"
        )
    # Deny metadata changes on authority directories too: renaming an ancestor
    # could otherwise move a writable subtree over the runner's evidence.
    lines.extend("(deny file-write* " + literal(p) + ")" for p in sorted(protected))
    lines.extend("(deny file-write* " + subpath(_path(p)) + ")" for p in denied_write_paths)
    for value in denied_read_paths:
        denied = _path(value)
        exemption = " ".join("(require-not " + subpath(p) + ")" for p in exceptions if p.is_relative_to(denied))
        condition = "(require-all " + subpath(denied) + " " + exemption + ")" if exemption else subpath(denied)
        lines.append("(deny file-read* " + condition + ")")
    if private_reads is not None:
        lines.extend(_private_read_rules(private_reads))
    lines.append('(deny file-read* (regex "(^|/)\\\\.env($|\\\\.)") (regex "(^|/)(auth\\\\.json|id_rsa|id_ed25519)$"))')
    return "\n".join(lines) + "\n"


def _secret_files(root, own_control):
    """Existing files under root whose names the Seatbelt profile refuses to read.

    Other launches' control directories are skipped; this launch's (which may hold a
    verification copy of the sources) is searched like the workspace.
    """
    found = []
    for directory, names, files in os.walk(root):
        names[:] = [
            name for name in names if name != ".git" and (name == own_control or not CONTROL_NAME.fullmatch(name))
        ]
        found.extend(Path(directory) / name for name in files if SECRET_NAME.fullmatch(name))
    return found


def bubblewrap_arguments(
    workspace,
    scratch,
    *,
    allow_write=False,
    read_roots=(),
    protected_paths=(),
    links=(),
    write_paths=None,
    writer_path_kinds=None,
    private_reads=None,
):
    """bwrap arguments for the Seatbelt profile's authority, and the paths it can only watch.

    No model command is interpreted here. An empty read-only root holds only the
    system runtime, the workspace (writable for the Builder), the stage scratch and
    the caller's read roots: no home, /tmp, /run or network; new PID, IPC, UTS,
    cgroup, network and user namespaces; every capability dropped. Later mounts cover
    earlier ones, so each mount follows its ancestors, and a protected path is
    mounted read-only only where the mount above it is writable (a verification
    copy is one mount, not one per file).

    Seatbelt lets a contained shell read symlink metadata anywhere. Here ``links``, the
    (path, target) symlinks toolchain discovery saw while selecting tools, are recreated
    where no mount already shows them, so a tool found through a link outside the mounts
    (a toolchain's bin/npm) still resolves into its mounted files.

    Returns (arguments, watched). A mount point must exist, so a protected path that
    does not exist yet inside writable space cannot be mounted without bwrap creating
    it in the real workspace; prepare()'s shell refuses to run once one appears.

    ``private_reads`` (private_run_reads(), read-only shells only) covers each denied
    directory with an empty tmpfs, binds each readable exception back read-only after
    it, and remounts the tmpfs read-only once those mount points exist.
    """
    if private_reads is not None and allow_write:
        raise ValueError("Only a read-only shell takes the private run-store read denial")
    root, scratch, extra, protected = _authority(workspace, scratch, read_roots, protected_paths)
    hidden = [_path(p) for p in (private_reads or {}).get("denied", ())]
    shown_again = [_path(p) for p in (private_reads or {}).get("readable", ())]
    arguments = ["--unshare-all", "--cap-drop", "ALL", "--die-with-parent", "--new-session"]
    system = []
    for path in (*LINUX_READ_ROOTS, *LINUX_SYSTEM_FILES):
        if os.path.islink(path):
            arguments += ["--symlink", os.readlink(path), path]
        elif os.path.exists(path):
            arguments += ["--ro-bind", os.path.realpath(path), path]
        if path in LINUX_READ_ROOTS and os.path.isdir(path):
            system.append(Path(path).resolve())
    arguments += ["--dev", "/dev", "--proc", "/proc"]
    # path -> bwrap mount flag. The system runtime is mounted already.
    mounts = {
        path: "--ro-bind-try"
        for path in extra
        if not path.is_relative_to(root) and not any(path.is_relative_to(base) for base in system)
    }
    footprint = writer_footprint(root, write_paths) if write_paths is not None else None
    kinds = _bound_path_kinds(footprint, writer_path_kinds)
    if footprint is not None and not allow_write:
        raise ValueError("Read-only stages cannot receive a writer footprint")
    mounts[root] = "--bind" if allow_write and footprint is None else "--ro-bind"
    for name in footprint or ():
        path = root / name
        # A literal mount cannot safely create a new path through a read-only
        # parent. Refuse before launch rather than widening to that parent.
        if not path.exists() or path.is_symlink():
            raise ValueError("Linux scoped writers require existing physical literal paths")
        if ("directory" if path.is_dir() else "file") != kinds[name]:
            raise ValueError("Linux writer kind differs from its pinned authority")
        mounts[path] = "--bind"
    mounts[scratch] = "--bind"
    # Neither is "--bind", so protected paths below them are never mounted again.
    mounts.update({path: "--tmpfs" for path in hidden})
    mounts.update({path: "--ro-bind-try" for path in shown_again})
    watched = []
    for path in sorted(protected, key=lambda item: (len(item.parts), str(item))):
        above = [mount for mount in mounts if path.is_relative_to(mount)]
        writable = max(above, key=lambda mount: len(mount.parts)) if above else None
        if writable is None or mounts[writable] != "--bind":
            continue  # Not visible in the sandbox, or already read-only there.
        if path.is_symlink():
            raise ValueError(f"Linux tool containment cannot protect a symlink in writable space: {path}")
        if path.exists():
            mounts[path] = "--ro-bind"
            # A mount follows its directory when that is renamed, and Seatbelt denies the
            # protected name, not the file. A mount point cannot be renamed, so each writable
            # directory between the mount and the protected path is bound onto itself: still
            # writable, but the protected path cannot be moved away and its name recreated.
            for ancestor in path.parents:
                if ancestor == writable:
                    break
                mounts.setdefault(ancestor, "--bind")
        else:
            watched.append(path)
    # Names the Seatbelt profile refuses to read: a device node on bwrap's nodev bind
    # mounts cannot be opened, so each read fails with "Permission denied".
    # A secret inside a hidden directory is not shown at all (masking it would create its name there).
    secrets = [
        (path, "/dev/null")
        for path in _secret_files(root, scratch.parent.name)
        if not any(path.is_relative_to(denied) for denied in hidden)
        or any(path.is_relative_to(readable) for readable in shown_again)
    ]
    ordered = [(path, str(path), flag) for path, flag in mounts.items()]
    ordered += [(path, source, "--ro-bind") for path, source in secrets]
    # Stable by depth: an ancestor is mounted before anything inside it.
    for path, source, flag in sorted(ordered, key=lambda mount: len(mount[0].parts)):
        arguments += [flag, str(path)] if flag == "--tmpfs" else [flag, source, str(path)]
    shown = [*system, *(Path(path) for path in (*LINUX_READ_ROOTS, *LINUX_SYSTEM_FILES)), *mounts]
    for link, target in links:
        link = Path(link)
        if not link.is_absolute() or not isinstance(target, str) or not target:
            raise ValueError("Tool containment received an invalid symlink")
        if not any(link.is_relative_to(path) for path in shown):
            arguments += ["--symlink", target, str(link)]
    # After every mount point inside them exists: a write there fails as everywhere read-only.
    for path in hidden:
        arguments += ["--remount-ro", str(path)]
    arguments += ["--remount-ro", "/"]
    for value in arguments:
        if "\0" in value:
            raise ValueError("Tool containment refuses a NUL byte in an authority path")
    return arguments, watched


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
    write_paths=None,
    links=(),
    private_run=None,
):
    """Create a fresh runner-owned policy and shell; return only nonsecret metadata.

    ``links`` are toolchain symlinks to show inside a bubblewrap boundary; Seatbelt
    already lets the shell read symlink metadata, so it ignores them. ``private_run``,
    a readonly planning stage's run directory, makes the run store unreadable except
    that run's planning/ artifacts, and each existing private store unreadable
    (private_run_reads(), #1250); the manifest records the denial as ``private_reads``.
    """
    _reject_loopback_request(loopback_checks, loopback_authority)
    host = backend()
    if host is None or not Path(SANDBOX_EXEC if host == SEATBELT else BWRAP).is_file():
        raise RuntimeError(
            "Strict native tool containment requires macOS sandbox-exec or Linux bubblewrap; no provider launched"
        )
    root = _path(workspace)
    footprint = writer_footprint(root, write_paths) if write_paths is not None else None
    if footprint is not None and not allow_write:
        raise ValueError("Read-only stages cannot receive a writer footprint")
    if private_run is not None and allow_write:
        raise ValueError("Only a read-only shell takes the private run-store read denial")
    private_reads = private_run_reads(root, private_run) if private_run is not None else None
    kinds = _writer_path_kinds(root, footprint) if footprint is not None else None
    policy_inputs = _policy_inputs(read_roots, protected_paths) if footprint is not None else None
    if policy_inputs is not None:
        read_roots, protected_paths = policy_inputs["read_roots"], policy_inputs["protected_paths"]
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
    profile = control / POLICY_FILES[host]
    watched = []
    if host == SEATBELT:
        profile.write_text(
            _render_policy(
                root,
                scratch,
                allow_write=allow_write,
                read_roots=read_roots,
                protected_paths=protected_paths,
                write_paths=footprint,
                writer_path_kinds=kinds,
                private_reads=private_reads,
            ),
            encoding="utf-8",
        )
    else:
        arguments, watched = bubblewrap_arguments(
            root,
            scratch,
            allow_write=allow_write,
            read_roots=read_roots,
            protected_paths=protected_paths,
            links=links,
            write_paths=footprint,
            writer_path_kinds=kinds,
            private_reads=private_reads,
        )
        profile.write_bytes(("\0".join(arguments) + "\0").encode())
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
    sandbox = [SANDBOX_EXEC, "-f", str(profile)] if host == SEATBELT else [BWRAP, "--args", "3"]
    argv = [
        "/usr/bin/env",
        "-i",
        *(key + "=" + value for key, value in safe.items()),
        *sandbox,
        "/bin/bash",
        "--noprofile",
        "--norc",
    ]
    command = shlex.join(argv) + ' -c "$2"' + ("" if host == SEATBELT else " 3< " + shlex.quote(str(profile)))
    script = '#!/bin/sh -p\nif [ "$#" -ne 2 ] || [ "$1" != "-c" ]; then exit 126; fi\n'
    if not watched:
        script += "exec " + command + "\n"
    else:
        # A protected name the mounts could not cover: once it exists, this launch's shell
        # fails that command and refuses every later one, naming the path (bubblewrap only).
        script += (
            "guard() { for path in " + " ".join(shlex.quote(str(path)) for path in watched) + "; do "
            'if [ -e "$path" ] || [ -h "$path" ]; then '
            'printf "%s\\n" "AutoCode tool containment: protected path $path appeared after this shell was '
            'prepared; it runs no further command" >&2; exit 126; fi; done; }\n'
            "guard\n" + command + '\nstatus=$?\nguard\nexit "$status"\n'
        )
    shell.write_text(script)
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
        "backend": host,
        "backend_version": backend_version(host),
        **({"links": [list(link) for link in links]} if host == BUBBLEWRAP else {}),
        **({"watched_paths": [str(path) for path in watched]} if watched else {}),
        "workspace": str(root),
        "shell": str(shell),
        "profile": str(profile),
        "scratch": str(scratch),
        "allow_write": allow_write,
        "tools": ["bash"],
        **(
            {"write_paths": footprint, "writer_path_kinds": kinds, "writer_policy_inputs": policy_inputs}
            if footprint is not None
            else {}
        ),
        **({"private_reads": private_reads} if private_reads is not None else {}),
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


def configure(
    command,
    environment,
    workspace,
    *,
    allow_write,
    request,
    retain_launch_identity=False,
    transport="opencode",
):
    """Constrain one launch and prove its *actual* native bash path before dispatch.

    Only model-free debug commands run here. No approval is auto-granted: an
    approval-bearing shell policy blocks conformance rather than using debug's
    auto-allow-ask behavior. The original model, effort and command are untouched.
    A readonly request's ``private_run`` (prepare()) is proven through the actual
    native bash too, and that proof is retained in conformance.json.
    """
    if transport not in ("opencode", "kilocode"):
        raise ValueError("Unsupported tool containment transport")
    if transport == "kilocode" and (allow_write or retain_launch_identity):
        raise ValueError("Ordinary Kilo containment supports readonly stages only")
    supported_version = "7.8.3" if transport == "kilocode" else SUPPORTED_VERSION
    config_variable = "KILO_CONFIG_CONTENT" if transport == "kilocode" else "OPENCODE_CONFIG_CONTENT"
    if type(retain_launch_identity) is not bool:
        raise ValueError("Native launch observation option must be an explicit boolean")
    if backend() is None:
        raise RuntimeError(
            "Strict native tool containment needs macOS Seatbelt or Linux bubblewrap; no provider launched"
        )
    if not isinstance(request, dict) or set(request) - {
        "read_roots",
        "protected_paths",
        "loopback_checks",
        "loopback_authority",
        "tool_commands",
        "source_paths",
        "write_paths",
        "private_run",
    }:
        raise ValueError("Unsupported strict tool containment request")
    _reject_loopback_request(request.get("loopback_checks", ()), request.get("loopback_authority"))
    footprint = writer_footprint(workspace, request["write_paths"]) if "write_paths" in request else None
    if footprint is not None and not allow_write:
        raise ValueError("Read-only stages cannot receive a writer footprint")
    if "private_run" in request and allow_write:
        raise ValueError("Only a read-only shell takes the private run-store read denial")
    opt_in_writer = retain_launch_identity and allow_write
    if opt_in_writer and footprint is None:
        raise ValueError("Native isolated writer requires exact literal paths before setup")
    initial_kinds = None
    if footprint is not None:
        initial_kinds = _writer_path_kinds(workspace, footprint)  # Refuse aliases before any native setup/debug.
    if "--session" in command:
        raise RuntimeError("Strict native tool containment requires a fresh session; resumed transport is unqualified")
    executable = shutil.which(command[0], path=environment.get("PATH", ""))
    if not executable:
        raise RuntimeError("Strict native tool containment cannot find the provider executable")
    version = subprocess.run(
        [executable, "--version"], env=environment, cwd=workspace, capture_output=True, text=True, timeout=15
    )
    if version.returncode or version.stdout.strip() != supported_version:
        raise RuntimeError("Strict native tool containment requires qualified " + transport + " " + supported_version)
    agent = command[command.index("--agent") + 1]
    child = dict(environment)
    config = json.loads(child[config_variable])
    definition = config["agent"][agent]
    definition["model"] = command[command.index("--model") + 1]
    child[config_variable] = json.dumps(config)
    prefix = [executable, "debug", "agent", agent]

    def debug(args=()):
        result = subprocess.run([*prefix, *args], cwd=workspace, env=child, capture_output=True, text=True, timeout=60)
        if result.returncode:
            if "The user has specified a rule which prevents you from using this specific tool call" in result.stderr:
                # Native diagnostics can include private config/rules. Classify the known
                # refusal without copying stderr, changing grants, or blaming the sandbox.
                raise NativePermissionDenied(
                    "Native tool permissions deny the model-free conformance probe. "
                    "Review the provider's effective shell permissions; ask/deny rules remain enforced. "
                    "No provider launched"
                )
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
        allow_write=False if opt_in_writer else allow_write,
        environment=child,
        read_roots=[*request.get("read_roots", ()), *tools_ready["read_roots"]],
        protected_paths=request.get("protected_paths", ()),
        loopback_checks=request.get("loopback_checks", ()),
        loopback_authority=request.get("loopback_authority"),
        verification_copy=not allow_write,
        source_paths=request.get("source_paths", ()),
        write_paths=None if opt_in_writer else footprint,
        links=tools_ready.get("links", ()),
        private_run=request.get("private_run"),
    )
    config["shell"] = boundary["shell"]
    definition["permission"] = permissions
    definition.pop("tools", None)
    child["SHELL"] = boundary["shell"]
    child[config_variable] = json.dumps(config)
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

    def shell_probe(target):
        control = Path(target["profile"]).parent
        sentinel = control / "conformance-forbidden"
        sentinel.write_text("runner-owned sentinel\n")
        scratch = Path(target["scratch"]) / "conformance-capture"
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
        return control, probe, result, native_receipt

    control, probe, result, native_receipt = shell_probe(boundary)

    def private_read_probe(target):
        # A parent-created sentinel in the planning run's own directory stands for its state, and
        # one in each private store the denial names for what that store holds: the actual native
        # bash must fail to read every one, and must list the run's planning/ artifacts when they
        # exist. Their random content never appears in the retained output.
        reads = target["private_reads"]
        name = Path(target["profile"]).parent.name + "-private-read-sentinel"
        places = [
            Path(reads["run_dir"]),
            *(Path(path) for path in reads["denied"] if Path(path).name in PRIVATE_STORES),
        ]
        if any(not place.is_dir() or place.is_symlink() for place in places):
            raise RuntimeError("A private store for the read-denial sentinel is missing; no provider launched")
        sentinels = [place / name for place in places]
        if any(sentinel.exists() or sentinel.is_symlink() for sentinel in sentinels):
            raise RuntimeError("Private run-state read sentinel already exists")
        token, made = uuid.uuid4().hex, []
        try:
            for sentinel in sentinels:
                with sentinel.open("x") as stream:
                    made.append(sentinel)
                    stream.write(token + "\n")
            denied = " && ".join("! /bin/cat " + shlex.quote(str(sentinel)) for sentinel in sentinels)
            readable = [path for path in reads["readable"] if Path(path).is_dir()]
            command = denied + "".join(" && /bin/ls " + shlex.quote(path) + " > /dev/null" for path in readable)
            observed = debug(
                [
                    "--tool",
                    "bash",
                    "--params",
                    json.dumps(
                        {
                            "command": command,
                            "description": "AutoCode private run-state read denial (no model)",
                            "workdir": str(Path(workspace).resolve()),
                            "timeout": 10000,
                        }
                    ),
                ]
            )
            checked = observed.get("result", {})
            output = checked.get("output")
            if (
                observed.get("input", {}).get("command") != command
                or type(checked.get("metadata", {}).get("exit")) is not int
                or checked["metadata"]["exit"] != 0
                or not isinstance(output, str)
                or token in output
                or not re.search(r"Operation not permitted|Permission denied|No such file or directory", output)
                or any(sentinel.read_text() != token + "\n" for sentinel in sentinels)
            ):
                raise RuntimeError("Actual native bash did not deny reading private run state; no provider launched")
        finally:
            for sentinel in made:
                sentinel.unlink()
        return {**reads, "listed": readable, "command": command, "tool": "bash", "exit": 0, "output": output}

    private_proof = private_read_probe(boundary) if "private_reads" in boundary else None

    def footprint_probe():
        # This parent-created temporary sentinel is outside every declared task
        # path. It tests the actual native bash route without modifying product
        # source. It is removed before any stage snapshot/admitted writer.
        forbidden = Path(workspace).resolve() / (control.name + "-workspace-sentinel")
        if forbidden.exists() or forbidden.is_symlink():
            raise RuntimeError("Task-footprint conformance sentinel already exists")
        raw = b"parent task-footprint sentinel\n"
        with forbidden.open("xb") as target:
            target.write(raw)
        try:
            denied = "! (: > " + shlex.quote(str(forbidden)) + ")"
            observed = debug(
                [
                    "--tool",
                    "bash",
                    "--params",
                    json.dumps(
                        {
                            "command": denied,
                            "description": "AutoCode task-footprint denial check (no model)",
                            "workdir": str(Path(workspace).resolve()),
                            "timeout": 10000,
                        }
                    ),
                ]
            )
            checked = observed.get("result", {})
            if (
                observed.get("input", {}).get("command") != denied
                or type(checked.get("metadata", {}).get("exit")) is not int
                or checked["metadata"]["exit"] != 0
                or forbidden.read_bytes() != raw
                or not re.search(
                    r"Operation not permitted|Permission denied|Read-only file system|Invalid cross-device link",
                    checked.get("output", ""),
                )
            ):
                raise RuntimeError("Actual native bash did not enforce the task writer footprint")
            footprint_proof = {
                "write_paths": footprint,
                "command": denied,
                "tool": "bash",
                "exit": 0,
                "output": checked.get("output"),
                "sentinel_sha256": hashlib.sha256(raw).hexdigest(),
            }
            linked = Path(boundary["scratch"]) / "conformance-outside-hardlink"
            link_denied = "! " + shlex.join(["/bin/ln", str(forbidden), str(linked)])
            observed_link = debug(
                [
                    "--tool",
                    "bash",
                    "--params",
                    json.dumps(
                        {
                            "command": link_denied,
                            "description": "AutoCode outside-source hardlink denial (no model)",
                            "workdir": str(Path(workspace).resolve()),
                            "timeout": 10000,
                        }
                    ),
                ]
            )
            link_result = observed_link.get("result", {})
            if (
                observed_link.get("input", {}).get("command") != link_denied
                or type(link_result.get("metadata", {}).get("exit")) is not int
                or link_result["metadata"]["exit"] != 0
                or linked.exists()
                or linked.is_symlink()
                or forbidden.read_bytes() != raw
                or forbidden.stat().st_nlink != 1
                or not re.search(
                    r"Operation not permitted|Permission denied|Read-only file system|Invalid cross-device link",
                    link_result.get("output", ""),
                )
            ):
                raise RuntimeError("Actual native bash did not deny an outside-source hardlink; footprint unsupported")
            footprint_proof.update(
                writer_path_kinds=boundary["writer_path_kinds"],
                writer_policy_inputs=boundary["writer_policy_inputs"],
                outside_hardlink={"command": link_denied, "exit": 0, "output": link_result.get("output")},
            )
        finally:
            forbidden.unlink()
        return footprint_proof

    footprint_proof = footprint_probe() if footprint is not None and not opt_in_writer else None
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

    def retain(target, probe, result, native_receipt, *, writer_proof=None, startup=None):
        # Legacy opt-out fields and JSON insertion order are deliberately kept.
        proof = {
            **({"writer_footprint": writer_proof} if writer_proof is not None else {}),
            "toolchain_checks": tool_receipts,
            "command": probe,
            "tool": "bash",
            "exit": result["metadata"]["exit"],
            "output": result.get("output") if isinstance(result.get("output"), str) else None,
            "native_receipt": native_receipt,
            "native_version": supported_version,
            "backend": target.get("backend"),
            "backend_version": target.get("backend_version"),
            "profile_sha256": hashlib.sha256(Path(target["profile"]).read_bytes()).hexdigest(),
            "shell_sha256": hashlib.sha256(Path(target["shell"]).read_bytes()).hexdigest(),
        }
        if transport == "kilocode":
            proof["transport"] = transport
        if "private_reads" in target:
            proof["private_reads"] = private_proof
        if retain_launch_identity:
            proof["launch_identity"] = native_conformance.observe(command, child, executable, actual)
        if startup is not None:
            proof["readonly_startup_boundary"] = startup
        proof_path = Path(target["profile"]).parent / "conformance.json"
        proof_path.write_text(json.dumps(proof, indent=2) + "\n")
        target.update(
            {
                "conformance": str(proof_path),
                "profile_sha256": proof["profile_sha256"],
                "shell_sha256": proof["shell_sha256"],
                "native_version": supported_version,
                "conformance_sha256": hashlib.sha256(proof_path.read_bytes()).hexdigest(),
            }
        )
        if transport == "kilocode":
            target["transport"] = transport
        verify(target)
        return target

    startup = None
    if opt_in_writer:
        # Toolchain startup and fixed native shell probes run readonly. No task
        # source grant exists until those actual native checks have completed.
        startup = retain(boundary, probe, result, native_receipt)
        if _writer_path_kinds(workspace, footprint) != initial_kinds:
            raise RuntimeError("Task path kind changed before native writer target setup")
        boundary = prepare(
            workspace,
            allow_write=True,
            environment=child,
            read_roots=[*request.get("read_roots", ()), *tools_ready["read_roots"]],
            protected_paths=request.get("protected_paths", ()),
            loopback_checks=request.get("loopback_checks", ()),
            loopback_authority=request.get("loopback_authority"),
            verification_copy=False,
            write_paths=footprint,
            links=tools_ready.get("links", ()),
        )
        if boundary["writer_path_kinds"] != initial_kinds:
            raise RuntimeError("Native writer target differs from the initial exact kind map")
        config["shell"] = boundary["shell"]
        child["SHELL"] = boundary["shell"]
        child[config_variable] = json.dumps(config)
        # A changed target shell needs its own final bounded native observation.
        # Reusing readonly conformance would not describe the actual launch.
        actual = debug()
        if shell_permissions(actual.get("permission")) != permissions:
            raise RuntimeError("Native writer target changed effective shell restrictions")
        tools = actual.get("tools", {})
        if (
            not isinstance(tools, dict)
            or tools.get("bash") is not True
            or any(value is not False for name, value in tools.items() if name != "bash")
        ):
            raise RuntimeError("Native writer target enabled an uncontained native tool")
        control, probe, result, native_receipt = shell_probe(boundary)
        footprint_proof = footprint_probe()
    boundary = retain(boundary, probe, result, native_receipt, writer_proof=footprint_proof, startup=startup)
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
        "denial_present": isinstance(output, str) and bool(DENIAL_TEXT.search(output)),
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


def _stores_made_since(paths):
    """The pause for private stores made after a planning launch's profile was rendered (#1250).

    Ordinary activity, such as another AutoCode command in the same checkout making its store for the
    first time: a fresh boundary denies the store, so a plain resume clears it.
    """
    one = len(paths) == 1
    return StorePause(
        f"{DENIAL_CHANGED}: the private .autocode store{'' if one else 's'} {', '.join(paths)} "
        f"{'was' if one else 'were'} made after this launch's boundary was prepared, so that boundary does "
        f"not deny reading {'it' if one else 'them'}. A plain --resume-paused prepares the boundary again "
        f"with {'it' if one else 'them'} denied"
    )


def verify(boundary):
    """Recheck runner-owned containment bytes immediately before provider launch."""
    if not isinstance(boundary, dict) or boundary.get("version") != 1:
        raise RuntimeError("Missing or unsupported native tool containment manifest")
    # A manifest written before #709 names no backend; only Seatbelt existed then.
    host = backend()
    transport = boundary.get("transport", "opencode")
    expected_version = "7.8.3" if transport == "kilocode" else SUPPORTED_VERSION
    if (
        transport not in ("opencode", "kilocode")
        or transport == "kilocode"
        and boundary.get("allow_write") is not False
        or host is None
        or boundary.get("backend", SEATBELT) != host
        or boundary.get("native_version") != expected_version
    ):
        raise RuntimeError("Native tool containment platform or transport is unsupported")
    try:
        workspace = _path(boundary["workspace"])
        profile, shell, scratch = (_path(boundary[name]) for name in ("profile", "shell", "scratch"))
        control = profile.parent
        if (
            control.parent != workspace / ".autocode"
            or not CONTROL_NAME.fullmatch(control.name)
            or profile.name != POLICY_FILES[host]
            or shell != control / "shell"
            or scratch != control / "scratch"
            or not scratch.is_dir()
        ):
            raise ValueError("Unexpected containment authority layout")
        if any(key.startswith("loopback_") for key in boundary):
            raise ValueError("Unsupported loopback capability in containment manifest")
        for name in ("profile", "shell", "conformance"):
            path = _path(boundary[name])
            expected = (
                control / {"profile": POLICY_FILES[host], "shell": "shell", "conformance": "conformance.json"}[name]
            )
            if (
                path != expected
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != boundary[name + "_sha256"]
            ):
                raise ValueError("Containment authority bytes changed")
    except (KeyError, OSError, ValueError) as error:
        raise RuntimeError("Native tool containment authority changed; no provider launched") from error
    if transport == "kilocode":
        proof = json.loads(Path(boundary["conformance"]).read_text())
        if proof.get("transport") != transport or proof.get("native_version") != expected_version:
            raise RuntimeError("Kilo kernel conformance does not match the recorded transport")
    if "write_paths" in boundary:
        try:
            footprint = writer_footprint(workspace, boundary["write_paths"])
            kinds = _writer_path_kinds(workspace, footprint)
            pinned_kinds = _bound_path_kinds(footprint, boundary["writer_path_kinds"])
            inputs = boundary["writer_policy_inputs"]
            if (
                not isinstance(inputs, dict)
                or set(inputs) != {"read_roots", "protected_paths"}
                or any(
                    not isinstance(inputs[name], list) or any(not isinstance(value, str) for value in inputs[name])
                    for name in ("read_roots", "protected_paths")
                )
                or inputs != _policy_inputs(inputs["read_roots"], inputs["protected_paths"])
            ):
                raise ValueError("Missing exact pinned writer policy inputs")
            expected_profile = _render_policy(
                workspace,
                scratch,
                allow_write=True,
                read_roots=inputs["read_roots"],
                protected_paths=inputs["protected_paths"],
                write_paths=footprint,
                writer_path_kinds=pinned_kinds,
            )
            expected_bytes = expected_profile.encode()
            if host == BUBBLEWRAP:
                arguments, watched = bubblewrap_arguments(
                    workspace,
                    scratch,
                    allow_write=True,
                    read_roots=inputs["read_roots"],
                    protected_paths=inputs["protected_paths"],
                    links=boundary.get("links", ()),
                    write_paths=footprint,
                    writer_path_kinds=pinned_kinds,
                )
                expected_bytes = ("\0".join(arguments) + "\0").encode()
                if boundary.get("watched_paths", []) != [str(path) for path in watched]:
                    raise ValueError("Writer protected-path watch differs from its pinned authority")
            if profile.read_bytes() != expected_bytes:
                raise ValueError("Writer profile does not match the exact pinned kind map and policy inputs")
            original = json.loads(Path(boundary["conformance"]).read_text()).get("writer_footprint")
            hardlink = original.get("outside_hardlink") if isinstance(original, dict) else None
            if (
                boundary.get("allow_write") is not True
                or not isinstance(original, dict)
                or original.get("write_paths") != footprint
                or original.get("tool") != "bash"
                or original.get("writer_path_kinds") != kinds
                or pinned_kinds != kinds
                or original.get("writer_policy_inputs") != inputs
                or not isinstance(hardlink, dict)
                or type(hardlink.get("exit")) is not int
                or hardlink["exit"] != 0
                or not isinstance(hardlink.get("command"), str)
                or not hardlink["command"]
                or not re.search(
                    r"Operation not permitted|Permission denied|Read-only file system|Invalid cross-device link",
                    hardlink.get("output", ""),
                )
                or type(original.get("exit")) is not int
                or original["exit"] != 0
                or not isinstance(original.get("command"), str)
                or not original["command"]
                or not re.search(
                    r"Operation not permitted|Permission denied|Read-only file system|Invalid cross-device link",
                    original.get("output", ""),
                )
            ):
                raise ValueError("Missing exact native writer-footprint conformance")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RuntimeError("Task writer-footprint authority changed; no provider launched") from error
    if "private_reads" in boundary:
        try:
            reads = boundary["private_reads"]
            if not isinstance(reads, dict):
                raise ValueError("Missing private run-store read denial")
            # Computed again now: a store made since the profile was rendered is only in the current denial.
            current = private_run_reads(workspace, reads["run_dir"])
            pinned = profile.read_bytes()
            if boundary.get("allow_write") is not False or not _enforces_private_reads(host, pinned, reads):
                raise ValueError("Profile does not enforce the exact recorded private run-store denial")
            proof = json.loads(Path(boundary["conformance"]).read_text()).get("private_reads")
            if (
                not isinstance(proof, dict)
                or {key: proof.get(key) for key in reads} != reads
                or proof.get("tool") != "bash"
                or type(proof.get("exit")) is not int
                or proof["exit"] != 0
                or not isinstance(proof.get("command"), str)
                or not proof["command"]
            ):
                raise ValueError("Missing native private run-state read conformance")
            if reads != current:
                # The pause names the stores only in the current denial, with their remedy, when they are
                # the whole difference and this boundary really leaves each one readable: the pinned
                # profile denies none of them, and none is the run store, which holds this run and so
                # existed when the boundary was prepared. A proof that matches the record does not show
                # the record is prepare()'s, since both can be pinned again together. For a boundary as
                # prepare() pinned it, whose record named every store that existed then, such a store was
                # made since. Any other difference, such as a record without a store the profile still
                # denies, is the plain refusal below.
                appeared = [path for path in current["denied"] if path not in reads["denied"]]
                if (
                    appeared
                    and str(Path(current["run_dir"]).parent) not in appeared
                    and reads == {**current, "denied": [p for p in current["denied"] if p not in appeared]}
                    and not any(_enforces_private_reads(host, pinned, {**reads, "denied": [p]}) for p in appeared)
                ):
                    raise _stores_made_since(appeared)
                raise ValueError("The recorded private run-store denial is not the current one")
        except UnusableStore as error:
            raise StorePause(DENIAL_CHANGED + ": " + str(error)) from error
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            # A pause, not a plain RuntimeError: another AutoCode command can make a store at any
            # time, and verify() also runs after the stage's first check (in native preparation and
            # in the held launch's seal), where a plain RuntimeError would end the command with exit
            # code 2 instead of pausing the run.
            raise util.Paused("PAUSED_TOOL_CONTAINMENT", DENIAL_CHANGED) from error
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


def recorded_backend(records):
    """The kernel boundary the latest contained launch among these stage records ran in, or None.

    Read from that launch's own manifest (prepare()), never from this host, so a run moved to
    another machine still says where each launch was contained: ``backend``, ``backend_version``
    and the launch's ``stage``, ``iteration`` and ``started_at``. Launches without a manifest
    (planning, uncontained, configured providers, Codex) are passed over. A manifest saved
    before #709 names no backend; only Seatbelt existed then, as verify() reads it. A name that
    is not a backend, or a version that is not text, reads as None.
    """
    for record in reversed(records):
        boundary = record.get("tool_containment") if isinstance(record, dict) else None
        if not isinstance(boundary, dict):
            continue
        name, version = boundary.get("backend", SEATBELT), boundary.get("backend_version")
        return {
            "backend": name if isinstance(name, str) and name in POLICY_FILES else None,
            "backend_version": version if isinstance(version, str) else None,
            **{key: record.get(key) for key in ("stage", "iteration", "started_at")},
        }
    return None
