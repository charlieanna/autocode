"""AutoCode-owned connection settings. Secrets never enter the public view.

The private auth file is independent of metadata, so key rotation does not change
a saved run's transport identity. Kilo credentials are never imported.
"""

from __future__ import annotations

import getpass
import json
import os
import stat
import tempfile
import warnings
from pathlib import Path
from urllib.parse import urlsplit

PROVIDERS = {
    "mimo": ("compatible", ""),
    "zai": ("compatible", ""),
    "openai": ("compatible", "https://api.openai.com/v1"),
    "claude": ("anthropic", "https://api.anthropic.com/v1"),
    "compatible": ("compatible", ""),
}

ROLES = ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")


def directory():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "autocode" / "direct"


def _read(name):
    path = directory() / name
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Direct connection files must not be symlinks")
    if not path.exists():
        return {}
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError("Direct connection files must be private, owned by this user (chmod 600)")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Invalid direct connection file")
    return value


def endpoint(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise ValueError("API base URL must be a URL string without whitespace")
    parsed = urlsplit(value)
    if (
        (
            parsed.scheme != "https"
            and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))
        )
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Use an HTTPS API base URL without credentials, query or fragment (HTTP is allowed only on loopback)"
        )
    return value.rstrip("/")


def validate(value):
    if value.get("version") != 1 or value.get("transport") not in ("compatible", "anthropic"):
        raise ValueError("Configure an AutoCode connection first")
    roles = value.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise ValueError("Direct connection needs every role's model")
    for model in roles.values():
        if not isinstance(model, str) or not model.strip() or any(c.isspace() for c in model):
            raise ValueError("Model IDs must be nonempty and contain no whitespace")
    provider = value.get("api_provider", "claude" if value["transport"] == "anthropic" else "compatible")
    if provider not in PROVIDERS or PROVIDERS[provider][0] != value["transport"]:
        raise ValueError("Choose MiMo, Z.ai, OpenAI, Claude or a compatible API")
    return {
        "version": 1,
        "transport": value["transport"],
        "api_provider": provider,
        "roles": roles,
        "endpoint": endpoint(value.get("endpoint", "")),
    }


def load():
    return validate(_read("connection.json"))


def api_key(settings=None):
    record = _read("auth.json")
    key = record.get("api_key")
    # Bind a key to the endpoint the user entered it for. A metadata edit or a
    # crash between the two atomic writes must never send it to another server.
    if record.get("endpoint") != (settings or load()).get("endpoint"):
        raise ValueError("Enter an AutoCode API key for this endpoint in connections setup")
    if not isinstance(key, str) or not key.strip() or any(c in key for c in "\r\n"):
        raise ValueError("AutoCode API key is missing; run autocode connections setup")
    return key


def _write(name, value):
    root = directory()
    if root.is_symlink():
        raise ValueError("Direct connection directory must not be a symlink")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError("Direct connection directory must be private (chmod 700)")
    descriptor, temporary = tempfile.mkstemp(prefix=".setup-", dir=root)
    try:
        with os.fdopen(descriptor, "w") as out:
            json.dump(value, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, root / name)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save(value, key=None):
    value = validate(value)
    if key is None:
        api_key(value)
    elif not key.strip() or any(c in key for c in "\r\n"):
        raise ValueError("API key must be nonempty and contain no line breaks")
    else:
        _write("auth.json", {"api_key": key, "endpoint": value["endpoint"]})
    _write("connection.json", value)


def describe(*, check=False):
    row = {
        "id": "autocode",
        "label": "AutoCode · Direct connection",
        "connection_status": "setup_required",
        "detail": "Connect MiMo, Z.ai, OpenAI or Claude with an API key. Kilo is optional.",
        "actions": [{"id": "setup", "label": "Set up AutoCode", "next_command": ["connections", "setup"]}],
    }
    row["actions"].append(
        {
            "id": "check",
            "label": "Check setup",
            "next_command": ["connections", "check", "--provider", "autocode", "--json"],
        }
    )
    try:
        value = load()
        if check:
            api_key(value)
        elif not (directory() / "auth.json").is_file():
            return row
        detail = f"{value['api_provider']} API configured. Credentials and model access have not been live-verified."
        row.update(connection_status="configured", detail=detail)
        row["actions"][0]["label"] = "Edit AutoCode setup"
    except (OSError, ValueError):
        pass
    return row


def setup():
    """Interactive terminal setup only: never accept a credential in argv or chat."""
    try:
        current = load()
    except (OSError, ValueError):
        current = {}
    print("AutoCode direct connection. Setup makes no model calls. Secrets are stored in a private local auth file.")
    provider = input(
        f"API provider: mimo, zai, openai, claude or compatible [{current.get('api_provider', 'openai')}]: "
    ).strip().lower() or current.get("api_provider", "openai")
    if provider not in PROVIDERS:
        raise ValueError("Choose mimo, zai, openai, claude or compatible")
    transport, default_endpoint = PROVIDERS[provider]
    same_provider = provider == current.get("api_provider")
    if provider in ("mimo", "zai"):
        print("Enter the exact API base URL for your billing route; API and subscription credentials are separate.")
        if provider == "mimo":
            print(
                "MiMo API: https://api.xiaomimimo.com/v1; Token Plan: use the exact base URL from your Token Plan console."
            )
        else:
            print("Z.ai API: https://api.z.ai/api/paas/v4; Coding Plan: https://api.z.ai/api/coding/paas/v4")
    default_endpoint = current.get("endpoint", "") if same_provider else default_endpoint
    value = {"version": 1, "transport": transport, "api_provider": provider}
    value["endpoint"] = input(f"API base URL [{default_endpoint}]: ").strip() or default_endpoint
    endpoint(value["endpoint"])
    # Never fall back to echoed stdin for credentials.
    if not os.isatty(0):
        raise ValueError("API-key setup requires an interactive terminal")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = getpass.getpass("API key (hidden; blank keeps an existing key for this endpoint): ") or None
        except getpass.GetPassWarning:
            raise ValueError("A terminal with hidden input is required; no key was saved") from None
    old_roles = current.get("roles", {}) if same_provider else {}
    producer = input(f"Planner/Builder/Requirements model ID [{old_roles.get('glm', '')}]: ").strip()
    reviewer = input(f"Independent reviewer/tester model ID [{old_roles.get('sol', '')}]: ").strip()
    if not producer and not reviewer and old_roles:
        value["roles"] = old_roles
    else:
        value["roles"] = {
            role: (reviewer or old_roles.get("sol", ""))
            if role in ("sol", "completion", "plan_reviewer")
            else (producer or old_roles.get("glm", ""))
            for role in ROLES
        }
    save(value, key)
    print(
        "AutoCode connection saved. Select AutoCode in Connections for new chats; live model access remains unverified."
    )
    return 0
