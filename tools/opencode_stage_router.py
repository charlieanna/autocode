#!/usr/bin/env python3
"""Test-only, fail-closed router for hybrid OpenCode workflow fixtures.

Install this file as ``opencode`` at the front of a disposable fixture PATH and
set the following environment variables:

``AUTOCODE_OPENCODE_ROUTER_CONFIG``
    Absolute path to a JSON routing configuration.
``AUTOCODE_OPENCODE_ROUTER_CONFIG_SHA256``
    Expected SHA-256 of that file. Configuration edits require an explicit
    re-pin and cannot silently change a test already in progress.
``AUTOCODE_OPENCODE_REAL``
    Absolute path to the real OpenCode executable. It must differ from this
    router and match ``real_executable_sha256`` in the configuration.
``AUTOCODE_OPENCODE_ROUTER_RECEIPTS``
    Absolute path to the router-owned JSONL receipt file.

Example configuration::

    {"version": 1,
     "real_executable_sha256": "<sha256>",
     "routes": {
       "terra": {"mode": "scripted_fixture",
                 "command": ["/tmp/fixture-helper"],
                 "executable_sha256": "<sha256>"},
       "sol": {"mode": "live_opencode"}}}

Discovery commands (for example ``models``, ``auth list`` and ``--version``)
go directly to the pinned real executable. Only ``run`` is stage-routed. This
tool is test infrastructure, not a way to clear native-Codex live coverage.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:  # the router runs as a standalone fixture binary
    import autocode_util as util

MARKER = b"CURRENT HANDOFF DATA\n"
MAX_STDIN_BYTES = 64 * 1024 * 1024
ENV_CONFIG = "AUTOCODE_OPENCODE_ROUTER_CONFIG"
ENV_CONFIG_HASH = "AUTOCODE_OPENCODE_ROUTER_CONFIG_SHA256"
ENV_REAL = "AUTOCODE_OPENCODE_REAL"
ENV_RECEIPTS = "AUTOCODE_OPENCODE_ROUTER_RECEIPTS"
ENV_ACTIVE = "AUTOCODE_OPENCODE_ROUTER_ACTIVE"


class Denied(RuntimeError):
    pass


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _absolute_file(value: str | None, label: str, *, executable: bool = False) -> Path:
    if not value:
        raise Denied(f"missing {label}")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise Denied(f"{label} must be an absolute existing file")
    if executable and not os.access(path, os.X_OK):
        raise Denied(f"{label} is not executable")
    return path


def _load() -> tuple[dict, str, Path, str, Path]:
    config_path = _absolute_file(os.environ.get(ENV_CONFIG), ENV_CONFIG)
    expected = os.environ.get(ENV_CONFIG_HASH)
    actual = _hash(config_path)
    if not expected or expected != actual:
        raise Denied("routing configuration drift")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise Denied("invalid routing configuration") from error
    if not isinstance(config, dict) or config.get("version") != 1 or not isinstance(config.get("routes"), dict):
        raise Denied("unsupported routing configuration")
    real = _absolute_file(os.environ.get(ENV_REAL), ENV_REAL, executable=True)
    if real.resolve() == Path(__file__).resolve():
        raise Denied("recursive real executable")
    real_hash = _hash(real)
    if config.get("real_executable_sha256") != real_hash:
        raise Denied("real executable configuration drift")
    receipt_value = os.environ.get(ENV_RECEIPTS)
    if not receipt_value or not Path(receipt_value).is_absolute():
        raise Denied(f"{ENV_RECEIPTS} must be absolute")
    receipts = Path(receipt_value)
    if not receipts.parent.is_dir():
        raise Denied("receipt directory does not exist")
    if receipts.resolve() in (config_path.resolve(), real.resolve()):
        raise Denied("receipt file must be separate from router inputs")
    return config, actual, real, real_hash, receipts


def _option(argv: list[str], name: str, *, required: bool) -> str | None:
    values = []
    for index, item in enumerate(argv):
        if item == name:
            if index + 1 >= len(argv):
                raise Denied(f"missing value for {name}")
            values.append(argv[index + 1])
        elif item.startswith(name + "="):
            values.append(item.split("=", 1)[1])
    if len(values) > 1 or (required and not values):
        raise Denied(f"{name} must occur exactly once")
    return values[0] if values else None


def _handoff(stdin: bytes) -> tuple[str, bool]:
    if len(stdin) > MAX_STDIN_BYTES:
        raise Denied("run input exceeds router limit")
    position = stdin.rfind(MARKER)
    if position < 0:
        raise Denied("run input has no CURRENT HANDOFF DATA")
    payload = stdin[position + len(MARKER) :]
    try:
        text = payload.decode("utf-8")
        data, end = json.JSONDecoder().raw_decode(text.lstrip())
        if text.lstrip()[end:].strip():
            raise ValueError("trailing input")
    except (UnicodeError, ValueError) as error:
        raise Denied("invalid CURRENT HANDOFF DATA") from error
    if not isinstance(data, dict):
        raise Denied("handoff data must be an object")
    repairing = data.get("report_repair") is True
    if repairing:
        original = data.get("original")
        stage = original.get("stage") if isinstance(original, dict) else None
    else:
        stage = data.get("stage")
    if not isinstance(stage, str) or not stage or stage.endswith("_report_repair"):
        raise Denied("unknown semantic stage")
    return stage, repairing


def _deny_billing_override(env: dict[str, str]) -> None:
    prohibited = {key for key in env if key.endswith("_API_KEY") or key.endswith("_BASE_URL")}
    inline = env.get("OPENCODE_CONFIG_CONTENT")
    if inline:
        try:
            parsed = json.loads(inline)
        except ValueError as error:
            raise Denied("invalid OPENCODE_CONFIG_CONTENT") from error
        if not isinstance(parsed, dict) or "provider" in parsed:
            prohibited.add("OPENCODE_CONFIG_CONTENT.provider")
    if prohibited:
        raise Denied("provider or billing route override is not allowed: " + ", ".join(sorted(prohibited)))


def _executable(route: dict, real: Path, real_hash: str) -> tuple[list[str], str, str]:
    mode = route.get("mode")
    if mode == "live_opencode":
        if set(route) != {"mode"}:
            raise Denied("live route contains unsupported configuration")
        return [str(real)], real_hash, mode
    if mode != "scripted_fixture":
        raise Denied("route mode must be scripted_fixture or live_opencode")
    command = route.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
        raise Denied("scripted fixture command must be a nonempty string array")
    helper = _absolute_file(command[0], "scripted fixture executable", executable=True)
    if helper.resolve() == Path(__file__).resolve():
        raise Denied("recursive scripted fixture executable")
    helper_hash = _hash(helper)
    if route.get("executable_sha256") != helper_hash:
        raise Denied("scripted fixture executable configuration drift")
    if set(route) != {"mode", "command", "executable_sha256"}:
        raise Denied("scripted route contains unsupported configuration")
    return [str(helper), *command[1:]], helper_hash, mode


def _receipt(path: Path, value: dict) -> None:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    value["receipt_id"] = hashlib.sha256(canonical).hexdigest()
    line = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = path.read_bytes() if path.exists() else b""
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(old)
                stream.write(line.encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)


def recorded_variant(argv: list[str]) -> str | None:
    """1.x passes ``--variant``. 2.x puts the same effort in ``provider/model#effort``."""
    explicit = _option(argv, "--variant", required=False)
    if explicit is not None:
        return explicit
    model = _option(argv, "--model", required=False)
    if not model or "#" not in model:
        return None
    variant = model.rsplit("#", 1)[1]
    if not variant or any(character.isspace() for character in variant):
        raise Denied("invalid model variant")
    return variant


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if os.environ.get(ENV_ACTIVE):
            raise Denied("recursive router invocation")
        config, config_hash, real, real_hash, receipts = _load()
        if not argv or argv[0] != "run":
            return subprocess.run([str(real), *argv]).returncode
        model = _option(argv, "--model", required=True)
        variant = recorded_variant(argv)
        if not model or any(c.isspace() for c in model):
            raise Denied("invalid model identifier")
        _deny_billing_override(dict(os.environ))
        stdin = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        stage, repairing = _handoff(stdin)
        route = config["routes"].get(stage)
        if not isinstance(route, dict):
            raise Denied(f"unknown stage: {stage}")
        command, executable_hash, mode = _executable(route, real, real_hash)
        if receipts.resolve() == Path(command[0]).resolve():
            raise Denied("receipt file must be separate from routed executable")
        child_env = dict(os.environ)
        if mode == "scripted_fixture":
            child_env.update(
                AUTOCODE_STAGE_ROUTER_MODE="scripted_fixture",
                AUTOCODE_STAGE_ROUTER_LIVE="0",
                AUTOCODE_STAGE_ROUTER_TOKEN_CLASS="non-live",
            )
        started = time.time_ns()
        result = util.run_process([*command, *argv], input=stdin, env=child_env)
        request = {
            "argv": argv,
            "config_sha256": config_hash,
            "cwd": os.getcwd(),
            "executable_sha256": executable_hash,
            "mode": mode,
            "model": model,
            "repair": repairing,
            "stage": stage,
            "stdin_sha256": hashlib.sha256(stdin).hexdigest(),
            "variant": variant,
        }
        request_identity = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        _receipt(
            receipts,
            {
                **request,
                "exit_code": result.returncode,
                "request_identity": request_identity,
                "started_unix_ns": started,
                "finished_unix_ns": time.time_ns(),
                "token_class": "live" if mode == "live_opencode" else "non-live",
            },
        )
        return result.returncode
    except (Denied, OSError) as error:
        print(f"opencode stage router denied request: {error}", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
