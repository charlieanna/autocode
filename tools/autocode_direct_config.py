"""AutoCode-owned connection settings. Secrets never enter the public view.

The private auth file is independent of metadata, so key rotation does not change
a saved run's transport identity. Kilo credentials are never imported.

Version 1 keeps one provider per connection; version 2 holds several providers,
each with its own endpoint-bound key, and names the provider behind every role.
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

try:
    from . import autocode_verifier_independence as verifier_independence
except ImportError:
    import autocode_verifier_independence as verifier_independence

PROVIDERS = {
    "mimo": ("compatible", ""),
    "zai": ("compatible", ""),
    "openai": ("compatible", "https://api.openai.com/v1"),
    "openrouter": ("compatible", "https://openrouter.ai/v1"),
    "claude": ("anthropic", "https://api.anthropic.com/v1"),
    "compatible": ("compatible", ""),
}

ALIASES = {"anthropic": "claude"}

ROLES = ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
PRODUCER_ROLES = ("astra", "glm", "terra")
REVIEWER_ROLES = ("completion", "plan_reviewer", "sol")


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


def validate_v2(value):
    """Several providers at once, each role naming its own provider and model."""
    if not isinstance(value, dict) or value.get("version") != 2:
        raise ValueError("Configure an AutoCode connection first")
    providers = value.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ValueError("A multi-provider connection needs at least one API provider")
    normalized_providers = {}
    for name, record in providers.items():
        if name not in PROVIDERS or not isinstance(record, dict) or record.get("transport") != PROVIDERS[name][0]:
            raise ValueError("Choose MiMo, Z.ai, OpenAI, OpenRouter, Claude or a compatible API")
        if set(record) != {"transport", "endpoint"}:
            raise ValueError("A connection provider is an API transport and base URL")
        normalized_providers[name] = {
            "transport": record["transport"],
            "endpoint": endpoint(record.get("endpoint", "")),
        }
    roles = value.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise ValueError("Direct connection needs every role's model")
    normalized_roles = {}
    for role, spec in roles.items():
        if (
            not isinstance(spec, dict)
            or set(spec) != {"provider", "model"}
            or spec["provider"] not in normalized_providers
        ):
            raise ValueError(f"Role {role} must name one of the connection's API providers and its model")
        model = spec["model"]
        if not isinstance(model, str) or not model.strip() or any(c.isspace() for c in model):
            raise ValueError("Model IDs must be nonempty and contain no whitespace")
        normalized_roles[role] = {"provider": spec["provider"], "model": model}
    return {"version": 2, "providers": normalized_providers, "roles": normalized_roles}


def load():
    value = _read("connection.json")
    if value and value.get("version") == 2:
        return validate_v2(value)
    return validate(value)


def provider_names(settings):
    """The API providers a connection (either version) holds."""
    if settings.get("version") == 2:
        return sorted(settings["providers"])
    return [settings["api_provider"]]


def provider_settings(settings, name):
    """One provider's transport record: never the key, which only api_key reads."""
    if settings.get("version") == 2:
        record = (settings["providers"] or {}).get(name)
        if not isinstance(record, dict):
            raise ValueError(f"No {name} API is configured in this direct connection")
        return {"transport": record["transport"], "endpoint": record["endpoint"], "api_provider": name}
    if name not in (None, "", settings.get("api_provider")):
        raise ValueError("This direct connection serves one API provider")
    return {
        "transport": settings["transport"],
        "endpoint": settings["endpoint"],
        "api_provider": settings["api_provider"],
    }


def role_providers(settings):
    """Every role's provider, including the ``requirements`` alias for the Planner helper."""
    result = {role: provider for role, (provider, _model) in assignment(settings).items()}
    result["requirements"] = result["glm"]
    return result


def assignment(settings):
    """Each role as (provider, model)."""
    if settings.get("version") == 2:
        return {role: (spec["provider"], spec["model"]) for role, spec in settings["roles"].items()}
    return {role: (settings["api_provider"], model) for role, model in settings["roles"].items()}


def model_allowed(settings, name, model):
    if settings.get("version") == 2:
        return any(spec["provider"] == name and spec["model"] == model for spec in settings["roles"].values())
    return model in settings["roles"].values()


def api_key(settings=None, provider=None):
    settings = settings or load()
    record = _read("auth.json")
    if record.get("version") == 2:
        if settings.get("version") != 2:
            raise ValueError(
                "The multi-provider key store belongs to a multi-provider connection; run connections setup"
            )
        if provider not in settings["providers"]:
            raise ValueError(f"No {provider} API is configured in this direct connection")
        entry = record["keys"].get(provider)
        if not isinstance(entry, dict):
            raise ValueError(f"AutoCode API key for {provider} is missing; run autocode connections add {provider}")
        expected = settings["providers"][provider]["endpoint"]
        key = entry.get("api_key")
    else:
        if provider not in (None, "", settings.get("api_provider")):
            raise ValueError("This direct connection serves one API provider")
        entry = record
        expected = settings["endpoint"]
        key = record.get("api_key")
    # Bind a key to the endpoint the user entered it for. A metadata edit or a
    # crash between the two atomic writes must never send it to another server.
    if entry.get("endpoint") != expected:
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
    existing = _read("auth.json")
    if existing.get("version") == 2:
        raise ValueError("Several API providers are configured; run `autocode connections add PROVIDER` to change one")
    value = validate(value)
    if key is None:
        api_key(value)
    elif not key.strip() or any(c in key for c in "\r\n"):
        raise ValueError("API key must be nonempty and contain no line breaks")
    else:
        _write("auth.json", {"api_key": key, "endpoint": value["endpoint"]})
    _write("connection.json", value)


def _existing_keys(current):
    """The keys already on disk, with a version-1 store attributed to its one provider."""
    record = _read("auth.json")
    if not record:
        return {}
    if record.get("version") == 2:
        return {name: dict(entry) for name, entry in record["keys"].items()}
    if current.get("version") == 1 and record.get("endpoint") == current.get("endpoint"):
        return {current["api_provider"]: {"api_key": record["api_key"], "endpoint": record["endpoint"]}}
    return {}


def save_v2(value, keys):
    """Write a multi-provider connection and its complete, endpoint-bound key set."""
    value = validate_v2(value)
    if not isinstance(keys, dict):
        raise ValueError("Connection keys must name each provider's key")
    stored = {}
    for name in value["providers"]:
        entry = keys.get(name)
        key = entry.get("api_key") if isinstance(entry, dict) else None
        if not isinstance(key, str) or not key.strip() or any(c in key for c in "\r\n"):
            raise ValueError(f"AutoCode API key for {name} is missing; run autocode connections add {name}")
        if entry.get("endpoint") != value["providers"][name]["endpoint"]:
            raise ValueError(f"The {name} key is bound to another endpoint; enter it again for this one")
        stored[name] = {"api_key": key, "endpoint": entry["endpoint"]}
    _write("auth.json", {"version": 2, "keys": stored})
    _write("connection.json", value)


def describe(*, check=False):
    row = {
        "id": "autocode",
        "label": "AutoCode · Direct connection",
        "connection_status": "setup_required",
        "detail": "Connect MiMo, Z.ai, OpenAI, OpenRouter or Claude with an API key. Kilo is optional.",
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
            for name in provider_names(value):
                api_key(value, name)
        elif not (directory() / "auth.json").is_file():
            return row
        if value.get("version") == 2:
            row["actions"][0] = {
                "id": "add",
                "label": "Add or rotate an API provider",
                "next_command": ["connections", "add"],
            }
            detail = " + ".join(provider_names(value)) + " APIs configured."
        else:
            row["actions"][0]["label"] = "Edit AutoCode setup"
            detail = f"{value['api_provider']} API configured."
        detail += " Credentials and model access have not been live-verified."
        row.update(connection_status="configured", detail=detail)
    except (OSError, ValueError):
        pass
    return row


ROLE_GROUPS = {
    "producer": PRODUCER_ROLES,
    "reviewer": REVIEWER_ROLES,
    "both": PRODUCER_ROLES + REVIEWER_ROLES,
}


def add(name=None):
    """Interactive multi-provider setup only: never accept a credential in argv or chat."""
    try:
        current = load()
    except (OSError, ValueError):
        current = {}
    if current.get("version") == 2:
        providers = {name: dict(record) for name, record in current["providers"].items()}
        roles = {role: dict(spec) for role, spec in current["roles"].items()}
    else:
        providers = {}
        roles = {}
        if current.get("version") == 1:
            providers[current["api_provider"]] = {
                "transport": current["transport"],
                "endpoint": current["endpoint"],
            }
            roles = {
                role: {"provider": current["api_provider"], "model": model} for role, model in current["roles"].items()
            }
    print("AutoCode direct connections. Setup makes no model calls. Secrets stay in a private local auth file.")
    if providers:
        print("Configured: " + ", ".join(f"{n} ({spec['endpoint']})" for n, spec in sorted(providers.items())))
    if name is None:
        name = input(f"API provider: {', '.join(PROVIDERS)} [{current.get('api_provider', 'openai')}]: ")
    name = ALIASES.get(str(name).strip().lower(), str(name).strip().lower())
    if name == "google":
        print("Google's Gemini API needs its own protocol, which is not delivered yet; use openrouter meanwhile.")
        return 2
    if name not in PROVIDERS:
        raise ValueError("Choose mimo, zai, openai, openrouter, claude or compatible")
    transport, default_endpoint = PROVIDERS[name]
    if name in ("mimo", "zai"):
        print("Enter the exact API base URL for your billing route; API and subscription credentials are separate.")
        if name == "mimo":
            print(
                "MiMo API: https://api.xiaomimimo.com/v1; Token Plan: use the exact base URL from your Token Plan console."
            )
        else:
            print("Z.ai API: https://api.z.ai/api/paas/v4; Coding Plan: https://api.z.ai/api/coding/paas/v4")
    default_endpoint = providers.get(name, {}).get("endpoint", "") or default_endpoint
    providers[name] = {
        "transport": transport,
        "endpoint": endpoint(input(f"API base URL [{default_endpoint}]: ").strip() or default_endpoint),
    }
    # Never fall back to echoed stdin for credentials.
    if not os.isatty(0):
        raise ValueError("API-key setup requires an interactive terminal")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = getpass.getpass("API key (hidden; blank keeps an existing key for this endpoint): ") or None
        except getpass.GetPassWarning:
            raise ValueError("A terminal with hidden input is required; no key was saved") from None
    keys = _existing_keys(current)
    if key is None:
        if keys.get(name, {}).get("endpoint") != providers[name]["endpoint"]:
            raise ValueError(f"Enter an API key for {name} at this endpoint, or re-enter its saved endpoint")
    else:
        keys[name] = {"api_key": key, "endpoint": providers[name]["endpoint"]}
    groups = ""
    if providers.keys() != {name}:
        groups = (
            input(
                "Serve which roles: producer (Planner/Builder/Requirements), reviewer (Tester/Completion/Plan reviewer) "
                "or both? [both]: "
            )
            .strip()
            .lower()
        )
    serving = {
        "p": "producer",
        "producer": "producer",
        "r": "reviewer",
        "reviewer": "reviewer",
        "": "both",
        "both": "both",
        "b": "both",
    }.get(groups)
    if serving is None:
        raise ValueError("Answer producer, reviewer or both")
    moving = [role for role in ROLE_GROUPS[serving] if roles.get(role, {}).get("provider") not in (None, name)]
    if moving:
        print("These roles move to " + name + ": " + ", ".join(moving))
    groups = (
        ("producer", PRODUCER_ROLES, "Planner/Builder/Requirements"),
        ("reviewer", REVIEWER_ROLES, "Independent reviewer/tester"),
    )
    for group, group_roles, label in groups:
        if serving not in (group, "both"):
            # The user narrowed this provider's duties: roles it no longer serves
            # become unassigned, and the completeness check below names what is missing.
            roles = {
                role: spec for role, spec in roles.items() if role not in group_roles or spec.get("provider") != name
            }
            continue
        default = next(
            (roles[role]["model"] for role in group_roles if roles.get(role, {}).get("provider") == name), ""
        )
        model = input(f"{label} model ID [{default}]: ").strip() or default
        if not model:
            if not all(roles.get(role, {}).get("provider") == name for role in group_roles):
                raise ValueError(f"Name the {label.lower()} model {name} will serve")
            continue  # blank answers keep this provider's saved per-role assignments
        for role in group_roles:
            roles[role] = {"provider": name, "model": model}
    missing = [role for role in ROLES if role not in roles]
    if missing:
        raise ValueError(
            f"Every role needs a provider; still unassigned: {', '.join(missing)}. "
            "Add another provider with `autocode connections add PROVIDER`."
        )
    value = {"version": 2, "providers": providers, "roles": roles}
    problems = verifier_independence.violations(
        {"roles": {role: {"model": spec["model"]} for role, spec in roles.items()}}
    )
    if problems:
        print("These routes are not independent: " + "; ".join(problems))
        print("A run will refuse to let one model family check its own work; add another provider for the checkers.")
    else:
        print("Producer and checkers are on different model families.")
    save_v2(value, keys)
    print(
        "AutoCode connection saved. Select AutoCode in Connections for new chats; live model access remains unverified."
    )
    return 0


def setup():
    """Interactive terminal setup only: never accept a credential in argv or chat."""
    try:
        current = load()
    except (OSError, ValueError):
        current = {}
    if current.get("version") == 2:
        print("Several API providers are configured; run `autocode connections add PROVIDER` to add or change one.")
        return 2
    print("AutoCode direct connection. Setup makes no model calls. Secrets are stored in a private local auth file.")
    provider = input(
        f"API provider: mimo, zai, openai, openrouter, claude or compatible [{current.get('api_provider', 'openai')}]: "
    ).strip().lower() or current.get("api_provider", "openai")
    provider = ALIASES.get(provider, provider)
    if provider not in PROVIDERS:
        raise ValueError("Choose mimo, zai, openai, openrouter, claude or compatible")
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
