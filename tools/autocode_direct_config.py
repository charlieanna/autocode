"""AutoCode-owned connection settings. Secrets never enter the public view.

The private auth file is independent of metadata, so key rotation does not change
a saved run's transport identity. AWS credentials stay in the AWS credential chain.
"""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import stat
import tempfile
import warnings
from pathlib import Path
from urllib.parse import urlsplit

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
    if value.get("version") != 1 or value.get("transport") not in ("compatible", "bedrock"):
        raise ValueError("Configure an AutoCode connection first")
    roles = value.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise ValueError("Direct connection needs every role's model")
    for model in roles.values():
        if not isinstance(model, str) or not model.strip() or any(c.isspace() for c in model):
            raise ValueError("Model IDs must be nonempty and contain no whitespace")
    if value["transport"] == "compatible":
        endpoint(value.get("endpoint", ""))
    elif not isinstance(value.get("region"), str) or not value["region"].strip():
        raise ValueError("Bedrock needs an AWS region")
    if not isinstance(value.get("profile", ""), str):
        raise ValueError("AWS profile must be a string")
    # These fixed limits bound a single provider invocation. The existing stage,
    # tool, wall-time and spend gates remain outer limits; no retries are hidden.
    result = {
        key: value[key] for key in ("version", "transport", "roles", "endpoint", "profile", "region") if key in value
    }
    if result["transport"] == "compatible":
        result["endpoint"] = endpoint(result["endpoint"])
    return result


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
    if value["transport"] == "compatible":
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
        "detail": "Connect directly with an API endpoint/key or AWS Bedrock. Kilo and OpenCode are optional.",
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
        if value["transport"] == "compatible":
            if check:
                api_key()
            elif not (directory() / "auth.json").is_file():
                return row
            detail = "API connection configured. Credentials and model access have not been live-verified."
        elif importlib.util.find_spec("boto3") is None:
            row.update(
                connection_status="sdk_required",
                detail="Bedrock is configured. Install AutoCode's bedrock extra in the CLI environment: pip install 'autocode-supervisor[bedrock]'.",
            )
            return row
        else:
            detail = "Bedrock region/profile configured. AWS resolves credentials when a task starts; access is not live-verified."
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
    transport = input(
        f"Transport: compatible or bedrock [{current.get('transport', 'compatible')}]: "
    ).strip() or current.get("transport", "compatible")
    value = {"version": 1, "transport": transport}
    key = None
    if transport == "compatible":
        value["endpoint"] = input(f"API base URL [{current.get('endpoint', '')}]: ").strip() or current.get(
            "endpoint", ""
        )
        endpoint(value["endpoint"])
        # getpass refuses no terminal; do not fall back to echoed stdin.
        if not os.isatty(0):
            raise ValueError("API-key setup requires an interactive terminal")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                key = getpass.getpass("API key (hidden; blank keeps an existing key): ") or None
            except getpass.GetPassWarning:
                raise ValueError("A terminal with hidden input is required; no key was saved") from None
    elif transport == "bedrock":
        value["region"] = input(f"AWS region [{current.get('region', '')}]: ").strip() or current.get("region", "")
        profile = input(
            f"AWS profile (blank keeps current; '-' uses the default credential chain) [{current.get('profile', '')}]: "
        ).strip()
        value["profile"] = "" if profile == "-" else profile or current.get("profile", "")
        print("Use your organization's AWS SSO/profile/role setup. AutoCode does not collect AWS access keys.")
    else:
        raise ValueError("Choose compatible or bedrock")
    old_roles = current.get("roles", {}) if transport == current.get("transport") else {}
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
