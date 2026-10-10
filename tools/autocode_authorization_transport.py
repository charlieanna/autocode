"""Private, invocation-scoped CLI authorization input; never rewrite tokens into argv.

Literal token flags remain compatible. Internal callers use explicit @stdin
selectors and one bounded JSON envelope. Authority and freshness checks still
receive the same token strings after the final CLI parse.
"""

from __future__ import annotations

import contextvars
import functools
import json
import os
import select
import shlex
import sys
import tempfile
import time
from contextlib import contextmanager

FLAG = "--authorization-stdin"
SELECTOR = "@stdin"
MAX_BYTES = 65536
MAX_TOKEN_BYTES = 4096
READ_SECONDS = 5
OPTIONS = {
    "--approve-goal": "approve_goal",
    "--review-token": "review_token",
    "--resolver-token": "resolver_token",
    "--job-retry-token": "job_retry_token",
    "--recover-job-report": "recover_job_report",
    "--expected-goal-token": "expected_goal_token",
    "--expected-recovery-token": "expected_recovery_token",
    "--expected-token": "expected_token",
    "--token": "token",
}
TASK_FIELDS = set(OPTIONS.values()) - {"expected_token", "token"}
_CURRENT = contextvars.ContextVar("autocode_authorization_input", default=None)


def _error(reason):
    return ValueError("Authorization input: " + reason)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _error("duplicate JSON key")
        result[key] = value
    return result


def decode(data: bytes) -> dict[str, str]:
    """Decode without exposing submitted values in diagnostics."""
    if len(data) > MAX_BYTES:
        raise _error("envelope is too large")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise _error("expected one UTF-8 JSON envelope") from None
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "tokens"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
    ):
        raise _error("expected schema 1 and tokens only")
    tokens = value["tokens"]
    if not isinstance(tokens, dict) or not tokens or not set(tokens) <= set(OPTIONS.values()):
        raise _error("missing or unknown token fields")
    try:
        invalid = any(
            not isinstance(token, str)
            or not token.strip()
            or len(token.encode("utf-8")) > MAX_TOKEN_BYTES
            or "\0" in token
            for token in tokens.values()
        )
    except UnicodeError:
        invalid = True
    if invalid:
        raise _error("invalid token value")
    return tokens


def check_command_prefix(command):
    """Wrappers may use --, but must not preinstall CLI authorization options."""
    for argument in command:
        option = argument.partition("=")[0]
        if option != "--" and option.startswith("--") and any(flag.startswith(option) for flag in (*OPTIONS, FLAG)):
            raise _error("supply tokens through actions, not the command prefix")


def _fields(argv, context):
    if context == "checkpoint" or argv[:1] == ["checkpoint"]:
        return {"expected_token"} if any(arg.partition("=")[0] == "--restore" for arg in argv) else set()
    if context == "program_approve" or argv[:2] == ["program", "approve"]:
        return {"token"}
    if context == "program":
        return {"token"} if argv[:1] == ["approve"] else set()
    if argv and argv[0] in (
        "program",
        "capture",
        "registry",
        "intervention",
        "doctor",
        "output",
        "tasks",
        "components",
        "ui",
        "compare-baseline",
        "visual-capture",
        "visual-check",
        "clean-worktrees",
        "merge",
        "models",
    ):
        return set()
    return TASK_FIELDS


def _entries(argv):
    """Exact token flags only; never interpret task text or arguments after --."""
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            break
        option, equal, value = argument.partition("=")
        if option in OPTIONS:
            if not equal:
                index += 1
                if index == len(argv) or argv[index].startswith("--"):
                    raise _error("token selector needs a value")
                value = argv[index]
            yield index, option, OPTIONS[option], value, bool(equal)
        elif option.startswith("--") and option != FLAG and any(flag.startswith(option) for flag in OPTIONS):
            raise _error("use the full token option name for private transport")
        index += 1


def prepare(command) -> tuple[list[str], bytes | None]:
    """Remove literal token values before a subprocess or action record exists."""
    result = list(command)
    options = result[: result.index("--")] if "--" in result else result
    if FLAG in options or any(arg.startswith(FLAG + "=") for arg in options):
        raise _error("internal callers supply token values, not an existing stdin selector")
    tokens = {}
    for index, option, field, value, equal in _entries(result):
        if field in tokens or value == SELECTOR:
            raise _error("duplicate or unavailable token input")
        tokens[field] = value
        result[index] = option + "=" + SELECTOR if equal else SELECTOR
    if not tokens:
        return result, None
    data = json.dumps({"schema": 1, "tokens": tokens}).encode("utf-8")
    decode(data)
    result.insert(result.index("--") if "--" in result else len(result), FLAG)
    return result, data


def guidance(command) -> str:
    """Display an executable token-free command and a separate private document."""
    safe, data = prepare(command)
    text = shlex.join(safe)
    if data is None:
        return text
    return (
        text + " < /path/to/private-authorization.json\n"
        "Private authorization JSON (save separately in that file, mode 0600):\n" + data.decode("utf-8")
    )


def replay_hint(argv) -> str:
    """Keep admitted selectors usable in run-selection hints, without token values."""
    safe = list(argv)
    if _CURRENT.get() is None:
        return shlex.join(safe)
    safe.insert(safe.index("--") if "--" in safe else len(safe), FLAG)
    return shlex.join(safe) + " < /path/to/private-authorization.json"


@contextmanager
def input_stream(data):
    """An anonymous private stream, never a model-readable named token file."""
    if data is None:
        yield None
        return
    with tempfile.TemporaryFile() as stream:
        stream.write(data)
        stream.seek(0)
        yield stream


def _read(stream):
    if stream.isatty():
        raise _error("redirect a private envelope; interactive stdin is not accepted")
    try:
        fd = stream.fileno()
    except (AttributeError, OSError):
        return stream.read(MAX_BYTES + 1)
    deadline = time.monotonic() + READ_SECONDS
    chunks = []
    size = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise _error("stdin did not close within the admission deadline")
        chunk = os.read(fd, min(4096, MAX_BYTES + 1 - size))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_BYTES:
            raise _error("envelope is too large")


@contextmanager
def invocation(argv, *, context="task", stream=None):
    """Consume once before dispatch; nested entry points reuse this admission."""
    argv = list(argv)
    if _CURRENT.get() is not None:
        if FLAG in argv:
            raise _error("authorization stdin was already admitted")
        yield argv
        return
    options_end = argv.index("--") if "--" in argv else len(argv)
    count = argv[:options_end].count(FLAG)
    if count == 0:
        if any(arg.startswith(FLAG + "=") for arg in argv[:options_end]):
            raise _error("transport switch takes no value")
        yield argv
        return
    if count != 1:
        raise _error("duplicate transport switch")
    argv.remove(FLAG)
    entries = list(_entries(argv))
    fields = [field for _, _, field, _, _ in entries]
    if len(set(fields)) != len(fields) or not entries or any(value != SELECTOR for _, _, _, value, _ in entries):
        raise _error("use one @stdin selector per supplied token")
    if not set(fields) <= _fields(argv, context):
        raise _error("token fields do not belong to this command")
    stream = sys.stdin.buffer if stream is None else stream
    tokens = decode(_read(stream))
    if set(tokens) != set(fields):
        raise _error("envelope fields and selectors must match exactly")
    saved_fd = None
    try:
        try:
            try:
                input_fd = stream.fileno()
            except (AttributeError, OSError):
                input_fd = None
            if input_fd == 0:
                saved_fd = os.dup(0)
                os.set_inheritable(saved_fd, False)
                with open(os.devnull, "rb") as empty:
                    os.dup2(empty.fileno(), 0)
        except (AttributeError, OSError):
            if saved_fd is not None:
                os.close(saved_fd)
                saved_fd = None
            raise _error("cannot detach authorization input") from None
        token = _CURRENT.set(tokens)
        try:
            yield argv
        finally:
            _CURRENT.reset(token)
    finally:
        if saved_fd is not None:
            os.dup2(saved_fd, 0)
            os.close(saved_fd)


def bind(args, parser):
    """Resolve selectors after final parsing, before run lookup or mutation."""
    tokens = _CURRENT.get()
    for field in OPTIONS.values():
        if getattr(args, field, None) == SELECTOR:
            if tokens is None or field not in tokens:
                parser.error("Authorization input: @stdin requires --authorization-stdin")
            setattr(args, field, tokens[field])


def entrypoint(context):
    """Give standalone subcommand entry points the same one-shot input scope."""

    def decorate(function):
        @functools.wraps(function)
        def wrapped(argv=None):
            try:
                with invocation(sys.argv[1:] if argv is None else argv, context=context) as safe:
                    return function(safe)
            except ValueError as error:
                print("Input rejected: " + str(error), file=sys.stderr)
                return 2

        return wrapped

    return decorate
