"""The environment AutoCode hands to agents and to the commands it runs on their code.

Agents have shell access, and under OpenCode and command-tool providers there is no
OS sandbox, so anything in the runner's environment is readable by a model and by
the tests it writes. Variables that look like credentials are withheld: a name word
such as TOKEN, SECRET, PASSWORD, KEY or AUTH, or a URL value with an embedded
password. Providers sign in from their own stored logins (OpenCode, Codex and Kilo
auth files), not from these variables.

Two kinds of variable are always kept: AutoCode's own ``AUTOCODE_*`` settings, and
proxy settings, without which a provider cannot reach its model. Git's
``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n``/``GIT_CONFIG_VALUE_n`` are kept or withheld
together, because git refuses to run with part of the set. A run that
genuinely needs a withheld variable names it in ``AUTOCODE_PASS_ENV``
(comma-separated). Pure functions over a mapping; nothing here reads files.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

PASS_VARIABLE = "AUTOCODE_PASS_ENV"
# A whole underscore-separated word of the name, e.g. SSH_AUTH_SOCK or OPENAI_API_KEY.
SECRET_WORDS = {"KEY", "AUTH", "PAT", "PRIVATE", "COOKIE"}
# The end of a word, e.g. GH_TOKEN, AZURE_CLIENT_SECRET, GOOGLE_APPLICATION_CREDENTIALS, MYAPIKEY.
# Not TOKENS: MAX_THINKING_TOKENS is a count.
SECRET_ENDINGS = ("TOKEN", "SECRET", "SECRETS", "PASSWORD", "PASSWD", "PASSPHRASE",
                  "CREDENTIAL", "CREDENTIALS", "APIKEY")
PROXY_NAMES = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY"}
URL_WITH_PASSWORD = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
GIT_CONFIG = re.compile(r"GIT_CONFIG_(COUNT|KEY_\d+|VALUE_\d+)")


def passed_through(env: Mapping[str, str]) -> set[str]:
    """Names the user asked to hand to agents unchanged."""
    return {name.strip() for name in env.get(PASS_VARIABLE, "").split(",") if name.strip()}


def is_secret(name: str, value: str) -> bool:
    upper = name.upper()
    if upper.startswith("AUTOCODE_") or upper in PROXY_NAMES:
        return False
    words = upper.split("_")
    return (any(word in SECRET_WORDS or word.endswith(SECRET_ENDINGS) for word in words)
            or bool(URL_WITH_PASSWORD.search(value)))


def withheld(env: Mapping[str, str]) -> list[str]:
    """Names (never values) that ``scrubbed`` removes from ``env``."""
    keep = passed_through(env)
    git_config = {name for name in env if GIT_CONFIG.fullmatch(name)}
    git_config_secret = any(URL_WITH_PASSWORD.search(env[name]) for name in git_config)
    return sorted(name for name, value in env.items() if name not in keep and (
        git_config_secret if name in git_config else is_secret(name, value)))


def scrubbed(env: Mapping[str, str]) -> dict[str, str]:
    """A copy of ``env`` without credential-like variables."""
    removed = set(withheld(env))
    return {name: value for name, value in env.items() if name not in removed}
