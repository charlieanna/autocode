"""Repository/PR readiness, resolved from operator configuration, never model text."""

import os
import shutil
import subprocess

try:
    from . import autocode_coordination_repositories as repositories
    from . import autocode_github as github
    from .autocode_coordination_store import CoordinationError
except ImportError:
    import autocode_coordination_repositories as repositories
    import autocode_github as github
    from autocode_coordination_store import CoordinationError


def client():
    result = github.Client()
    if not result.token and shutil.which("gh"):
        host, _ = github.web_authority(result.api)
        try:
            reply = subprocess.run(
                ["gh", "auth", "token", "--hostname", host], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CoordinationError("GitHub CLI authentication readiness is unavailable") from error
        if reply.returncode == 0:
            result.token = reply.stdout.strip() or None
    return result


def readiness():
    selected = client()
    host, _ = github.web_authority(selected.api)
    return {
        "github": {
            "host": host,
            "configured": bool(selected.token),
            "source": "environment"
            if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            else "gh"
            if selected.token
            else None,
        }
    }


def check(member):
    selected = client()
    if not selected.token:
        raise CoordinationError(
            "GitHub PR authentication is missing. Use gh auth login, GITHUB_TOKEN or GH_TOKEN, then reopen chat; /repos shows readiness"
        )
    owner, repository = member["repository"]
    info = selected.repository(owner, repository)
    if str(info.get("full_name", "")).casefold() != f"{owner}/{repository}".casefold() or not (
        info.get("permissions") or {}
    ).get("push"):
        raise CoordinationError("GitHub write access is unavailable for " + owner + "/" + repository)
    remote = repositories.git(
        member["origin"], "ls-remote", member["push_remote"], "refs/heads/" + member["base_branch"]
    )
    if not remote or remote.split()[0] != member["base"]:
        raise CoordinationError(
            "The local baseline does not match its remote delivery branch; reconcile it before automatic publication"
        )
