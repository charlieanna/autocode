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
from urllib.parse import urlencode, urlsplit

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


def web_authority(api: str) -> tuple[str, int]:
    """The HTTPS web authority represented by a GitHub API configuration."""
    try:
        server = urlsplit(api)
        port = 443 if server.port is None else server.port
    except ValueError as error:
        raise GitHubError("invalid GitHub API authority") from error
    fixture = server.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        not server.hostname
        or server.username
        or server.password
        or (server.scheme != "https" and not (fixture and server.scheme == "http"))
    ):
        raise GitHubError("GitHub API must be HTTPS (HTTP is allowed only for explicit loopback fixtures)")
    return ("github.com", 443) if server.hostname == "api.github.com" or fixture else (server.hostname, port)


def parse_pull_request_url(text: str, *, api: str = API) -> IssueRef:
    """Bind an HTTPS PR web authority to the configured GitHub/Enterprise API.

    An explicitly configured loopback recorded API accepts github.com/443 only.
    Benign query/fragment anchors do not change the repository or head binding.
    """
    expected_host, expected_port = web_authority(api)
    try:
        value = urlsplit(text.strip())
        port = 443 if value.port is None else value.port
    except ValueError as error:
        raise GitHubError("invalid pull request authority") from error
    match = re.fullmatch(r"/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>[1-9]\d*)/?", value.path)
    expected = f"https://{expected_host}{'' if expected_port == 443 else f':{expected_port}'}/OWNER/REPO/pull/N"
    if value.scheme != "https" or not value.hostname or value.username or value.password or not match:
        # A number, OWNER/REPO#N or an issue link: name the form, and the value unless it carries a credential.
        given = "" if value.username or value.password or len(text) > 200 else f", not {text.strip()!r}"
        raise GitHubError(f"pull request URL must be {expected}{given}")
    if value.hostname != expected_host or port != expected_port:
        raise GitHubError(f"pull request URL must match the configured GitHub API web host and port: {expected}")
    return IssueRef(match["owner"], match["repo"], int(match["number"]))


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

    def pulls_for_branch(self, owner: str, repo: str, *, head: str, base: str) -> list[dict]:
        query = urlencode({"state": "all", "head": f"{owner}:{head}", "base": base, "per_page": 100})
        result = self._call("GET", f"/repos/{owner}/{repo}/pulls?{query}")
        if not isinstance(result, list) or len(result) >= 100 or any(not isinstance(row, dict) for row in result):
            raise GitHubError("Cannot reconcile pull-request identity for this branch")
        return result

    def repository(self, owner: str, repo: str) -> dict:
        result = self._call("GET", f"/repos/{owner}/{repo}")
        if not isinstance(result, dict):
            raise GitHubError("Cannot inspect repository access")
        return result

    def update_pull_body(self, owner: str, repo: str, number: int, body: str) -> dict:
        if not self.token:
            raise GitHubError("updating a pull request needs GITHUB_TOKEN or GH_TOKEN")
        return self._call("PATCH", f"/repos/{owner}/{repo}/pulls/{number}", {"body": util.redact(body)})

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
            comments = self._call(
                "GET", f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}/comments?per_page={per_page}&page={page}"
            )
        return {**issue, "comments_list": comments[-max_comments:] if max_comments else []}

    def open_issues(self, owner: str, repo: str, label: str) -> list[dict]:
        """Open, non-PR issues carrying one label, newest first."""
        rows = self._call("GET", f"/repos/{owner}/{repo}/issues?labels={label}&state=open&per_page=100")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise GitHubError("GitHub returned an invalid issue list")
        return [row for row in rows if "pull_request" not in row]

    def issue_comment(self, ref: IssueRef, body: str) -> dict:
        """Post one comment on an issue; reading needs no token, posting does."""
        if not self.token:
            raise GitHubError("posting an issue comment needs GITHUB_TOKEN or GH_TOKEN")
        return self._call(
            "POST", f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}/comments", {"body": util.redact(body)}
        )

    def pull_request(self, ref: IssueRef) -> dict:
        """Read the current pull request, including its exact head and base commits."""
        result = self._call("GET", f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}")
        if not isinstance(result, dict):
            raise GitHubError("GitHub returned an invalid pull request")
        return result

    def pull_request_files(self, ref: IssueRef) -> list[dict]:
        """Every changed file, including GitHub's new-side patch anchors."""
        files = []
        for page in range(1, 31):
            result = self._call(
                "GET", f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/files?per_page=100&page={page}"
            )
            if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
                raise GitHubError("GitHub returned an invalid pull request file list")
            files.extend(result)
            if len(result) < 100:
                return files
        raise GitHubError("Pull request file listing exceeds GitHub's 3,000-file limit")

    def comment_review(self, ref: IssueRef, *, commit_id: str, body: str, comments: list[dict]) -> dict:
        """Submit COMMENT only; this method cannot approve or request changes."""
        if not self.token:
            raise GitHubError("posting a review needs GITHUB_TOKEN or GH_TOKEN")
        return self._call(
            "POST",
            f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/reviews",
            {
                "event": "COMMENT",
                "commit_id": commit_id,
                "body": util.redact(body),
                "comments": [{**row, "body": util.redact(row["body"])} for row in comments],
            },
        )

    def open_pull_request(
        self, owner: str, repo: str, *, head: str, base: str, title: str, body: str, draft: bool = True
    ) -> dict:
        if not self.token:
            raise GitHubError("opening a pull request needs GITHUB_TOKEN or GH_TOKEN")
        # Credentials must not leave the run (#712). The raw report in the run
        # directory is untouched; only this export is redacted.
        return self._call(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            {"head": head, "base": base, "title": title, "body": util.redact(body), "draft": draft},
        )

    def _call(self, method: str, path: str, payload: dict | None = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "autocode-issue",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        status, data = self.transport(method, self.api + path, headers, body)
        if not 200 <= status < 300:
            message = data.get("message") if isinstance(data, dict) else None
            detail = (
                "; ".join(str(e.get("message", e)) for e in data.get("errors", [])) if isinstance(data, dict) else ""
            )
            raise GitHubError(
                f"{method} {path} returned {status}: {message or 'no message'}" + (f" ({detail})" if detail else "")
            )
        return data
