"""A minimal GitHub REST client: read one issue, open one pull request.

Standard library only, and imports nothing from AutoCode. The token comes from
GITHUB_TOKEN or GH_TOKEN; reading a public issue works without one. Set
AUTOCODE_GITHUB_API for GitHub Enterprise (https://HOST/api/v3).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

API = "https://api.github.com"
_URL = re.compile(r"https?://[^/]+/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/(?:issues|pull)/(?P<number>\d+)/?(?:[?#].*)?$")
_SHORT = re.compile(r"(?:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)#|#)?(?P<number>\d+)$")
_REMOTE = re.compile(r"(?:[:/])(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$")


class GitHubError(RuntimeError):
    """The issue reference is invalid, or GitHub refused a request."""


@dataclass(frozen=True)
class IssueRef:
    owner: str
    repo: str
    number: int

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def slug(self) -> str:
        return f"{self.owner}-{self.repo}-{self.number}".lower()


def parse_ref(text: str, default_repo: tuple[str, str] | None = None) -> IssueRef:
    """Accept an issue URL, OWNER/REPO#N, or #N / N (with default_repo)."""
    text = text.strip()
    match = _URL.match(text) or _SHORT.match(text)
    if not match:
        raise GitHubError(f"not an issue reference: {text!r} (use a URL, OWNER/REPO#N or #N)")
    owner, repo = match.group("owner"), match.group("repo")
    if not owner:
        if not default_repo:
            raise GitHubError(f"{text!r} names no repository, and the project has no GitHub remote to infer it from")
        owner, repo = default_repo
    return IssueRef(owner, repo, int(match.group("number")))


def repo_from_remote(url: str) -> tuple[str, str] | None:
    """OWNER, REPO from a git remote URL (https or ssh), or None."""
    match = _REMOTE.search(url.strip())
    return (match.group("owner"), match.group("repo")) if match else None


Transport = Callable[[str, str, dict, bytes | None], tuple[int, object]]


def _urllib(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, object]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"null")
        except ValueError:
            return error.code, None
    except urllib.error.URLError as error:
        raise GitHubError(f"{method} {url}: {error.reason}") from None


class Client:
    def __init__(self, token: str | None = None, api: str | None = None, transport: Transport = _urllib):
        self.token = token if token is not None else (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
        self.api = (api or os.environ.get("AUTOCODE_GITHUB_API") or API).rstrip("/")
        self.transport = transport

    def issue(self, ref: IssueRef, max_comments: int = 30) -> dict:
        """The issue with up to max_comments of its most recent comments under "comments_list"."""
        issue = self._call("GET", f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}")
        if "pull_request" in issue:
            raise GitHubError(f"{ref} is a pull request, not an issue")
        comments = []
        if issue.get("comments") and max_comments:
            # Ask for the last page that holds the newest comments.
            per_page = min(max_comments, 100)
            page = max(1, -(-int(issue["comments"]) // per_page))
            comments = self._call("GET", f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}/comments"
                                         f"?per_page={per_page}&page={page}")
        return {**issue, "comments_list": comments[-max_comments:] if max_comments else []}

    def open_pull_request(self, owner: str, repo: str, *, head: str, base: str, title: str, body: str,
                          draft: bool = True) -> dict:
        if not self.token:
            raise GitHubError("opening a pull request needs GITHUB_TOKEN or GH_TOKEN")
        # Credentials must not leave the run (#712). The raw report in the run
        # directory is untouched; only this export is redacted.
        return self._call("POST", f"/repos/{owner}/{repo}/pulls",
                          {"head": head, "base": base, "title": title,
                           "body": util.redact(body), "draft": draft})

    def _call(self, method: str, path: str, payload: dict | None = None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "autocode-issue"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        status, data = self.transport(method, self.api + path, headers, body)
        if not 200 <= status < 300:
            message = data.get("message") if isinstance(data, dict) else None
            detail = "; ".join(str(e.get("message", e)) for e in data.get("errors", [])) if isinstance(data, dict) else ""
            raise GitHubError(f"{method} {path} returned {status}: {message or 'no message'}"
                              + (f" ({detail})" if detail else ""))
        return data
