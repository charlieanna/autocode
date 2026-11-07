"""Persistent Linux user-service transport for one ordinary core invocation.

This adapter neither owns a task loop nor judges task completion. A successful
service start is only an exec acknowledgement; the core must bind its ordinary
controller receipt before advertising durable readiness. A missing service is
not evidence that a provider never accepted a request.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

TOOLS = {name: f"/usr/bin/{name}" for name in ("systemd-run", "systemctl", "loginctl")}
PROPERTIES = ("LoadState", "ActiveState", "SubState", "MainPID", "InvocationID", "ExecMainStatus")
INHERITED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
INVOCATION = re.compile(r"[0-9a-f]{32}\Z")


class ServiceUnavailable(RuntimeError):
    """The required host capability is unavailable; no service launch was attempted."""


class ServiceOutcomeUnknown(RuntimeError):
    """Inspect this exact service/launch identity before any further dispatch."""


def unit_name(launch_id: str) -> str:
    if not isinstance(launch_id, str) or not INVOCATION.fullmatch(launch_id):
        raise ValueError("Invalid durable launch identity")
    return f"autocode-{launch_id}.service"


def _properties(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in result:
            raise ValueError("Invalid service property response")
        result[key] = value
    return result


class UserServices:
    """The existing local user manager, never a service installed by the client."""

    def __init__(self, *, env=None, run=subprocess.run, platform=None, uid=None):
        self.env = dict(os.environ if env is None else env)
        self.run = run
        self.platform = sys.platform if platform is None else platform
        self.uid = os.getuid() if uid is None and hasattr(os, "getuid") else uid

    def _read(self, command):
        return self.run(command, env=self.env, capture_output=True, text=True, timeout=5, check=False)

    def capability(self) -> dict:
        """Read-only admission; no manager, linger policy or unit is created here."""
        result = {
            "available": False,
            "adapter": "systemd_user",
            "reason": "Persistent Linux user services are required",
        }
        if self.platform != "linux" or type(self.uid) is not int or self.uid < 0:
            return result
        try:
            version = self._read([TOOLS["systemd-run"], "--version"])
            found = re.match(r"systemd\s+(\d+)\b", version.stdout)
            # 254 adds explicit argument-expansion disabling. Literal task text
            # must not become a service-manager environment substitution.
            if version.returncode or not found or int(found[1]) < 254:
                return {**result, "reason": "systemd 254 or newer is required for literal task arguments"}
            manager = self._read([TOOLS["systemctl"], "--user", "show", "--property=Version", "--value"])
            if manager.returncode or not re.match(r"\d+\b", manager.stdout.strip()):
                return {**result, "reason": "The existing user service manager is unavailable"}
            linger = self._read([TOOLS["loginctl"], "show-user", str(self.uid), "--property=Linger", "--value"])
            if linger.returncode or linger.stdout.strip() != "yes":
                return {**result, "reason": "Existing user linger must already preserve the manager after logout"}
            return {"available": True, "adapter": "systemd_user", "version": int(found[1]), "reason": None}
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
            return {**result, "reason": "User-service capability could not be verified"}

    def start(self, launch_id: str, argv: list[str], *, workspace: Path, log: Path) -> None:
        unit = unit_name(launch_id)
        if not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv):
            raise ValueError("A durable invocation requires an argument array")
        if not Path(argv[0]).is_absolute():
            raise ValueError("A durable invocation requires an absolute runtime executable")
        if not workspace.is_absolute() or not log.is_absolute():
            raise ValueError("Durable invocation paths must be absolute")
        if any(
            not isinstance(key, str) or not INHERITED.fullmatch(key) or not isinstance(value, str) or "\0" in value
            for key, value in self.env.items()
        ):
            raise ValueError("Invalid durable invocation environment")
        if not self.capability()["available"]:
            raise ServiceUnavailable("Durable user-service capability is unavailable; no launch was attempted")
        inherited = []
        for key in sorted(self.env):
            if key in ("INVOCATION_ID", "NOTIFY_SOCKET", "MAINPID") or key.startswith(("LISTEN_", "WATCHDOG_")):
                continue
            # A name without '=' copies from this process's environment. Values
            # (including backend credentials) never appear in the argument list.
            inherited.append("--setenv=" + key)
        command = [
            TOOLS["systemd-run"],
            "--user",
            "--unit=" + unit,
            "--description=AutoCode task invocation",
            "--service-type=exec",
            "--collect",
            "--expand-environment=no",
            "--working-directory=" + str(workspace).replace("%", "%%"),
            "--property=StandardInput=null",
            "--property=StandardOutput=append:" + str(log).replace("%", "%%"),
            "--property=StandardError=inherit",
            "--property=Restart=no",
            "--property=UMask=0077",
            *inherited,
            "--",
            *argv,
        ]
        try:
            reply = self._read(command)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ServiceOutcomeUnknown(
                "Service dispatch outcome is unknown; inspect the retained launch identity"
            ) from error
        if reply.returncode:
            # A duplicate unit or a lost prior response can also report failure.
            # Never turn stderr, a timeout or a nonzero receipt into replay permission.
            raise ServiceOutcomeUnknown("Service dispatch did not return success; inspect the exact launch identity")

    def inspect(self, launch_id: str) -> dict:
        unit = unit_name(launch_id)
        unknown = {"checked": False, "present": None, "unit": unit}
        try:
            reply = self._read(
                [
                    TOOLS["systemctl"],
                    "--user",
                    "show",
                    unit,
                    "--property=" + ",".join(PROPERTIES),
                    "--no-pager",
                ]
            )
            values = _properties(reply.stdout)
            if reply.returncode:
                return unknown
            if values.get("LoadState") == "not-found":
                return {"checked": True, "present": False, "unit": unit}
            if values.get("LoadState") != "loaded" or not set(PROPERTIES) <= values.keys():
                return unknown
            pid, code = values["MainPID"], values["ExecMainStatus"]
            if not pid.isdecimal() or not code.isdecimal() or int(pid) > 2**31 - 1 or int(code) > 255:
                return unknown
            invocation = values["InvocationID"]
            if invocation and not INVOCATION.fullmatch(invocation):
                return unknown
            if values["ActiveState"] not in ("active", "activating", "deactivating", "inactive", "failed"):
                return unknown
            return {
                "checked": True,
                "present": True,
                "unit": unit,
                "pid": int(pid),
                "invocation_id": invocation or None,
                "active_state": values["ActiveState"],
                "sub_state": values["SubState"],
                "exec_status": int(code),
            }
        except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
            return unknown
