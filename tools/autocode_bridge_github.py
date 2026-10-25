"""The GitHub channel: which labeled issues to take, which replies are ours to act on.

A thin adapter over ``autocode_github.Client`` with the bridge's channel rules:
discovery reads one label on one repository; a reply counts only when its author
is on the configured allowlist and its comment id is newer than everything the
bridge had consumed when it relayed the current gate, so a message written before
a plan was posted can never approve it. Everything posted goes through the
client, which redacts.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    from . import autocode_github as github
except ImportError:
    import autocode_github as github


@dataclass
class Inbound:
    """One reply comment the bridge may act on."""

    id: int
    actor: str
    text: str


class GitHubChannel:
    def __init__(self, client: github.Client, repo: str, label: str, actors: tuple[str, ...] | list[str]):
        owner, _, name = repo.partition("/")
        if not owner or not name:
            raise ValueError(f"AUTOCODE_BRIDGE_GITHUB_REPO must be OWNER/REPO, not {repo!r}")
        self.client = client
        self.owner, self.repo = owner, name
        self.label = label
        self.actors = {str(actor).lower() for actor in actors}

    @property
    def ref_template(self) -> str:
        return f"{self.owner}/{self.repo}#"

    def allowed(self, login: str) -> bool:
        return str(login or "").lower() in self.actors

    def open_labeled_issues(self) -> list[dict]:
        return self.client.open_issues(self.owner, self.repo, self.label)

    def issue(self, number: int) -> dict:
        return self.client.issue(github.IssueRef(self.owner, self.repo, number))

    def new_comments(self, record: dict, issue: dict) -> list[Inbound]:
        """Allowlisted, unconsumed replies above the gate's floor, oldest first."""
        gate = record.get("gate") or {}
        floor = max(
            int(record.get("initial_comment_id") or 0),
            int(gate.get("floor") or 0),
            max((int(value) for value in record.get("comments_seen") or () if isinstance(value, int)), default=0),
        )
        inbound = []
        for comment in issue.get("comments_list") or []:
            identity = comment.get("id")
            author = (comment.get("user") or {}).get("login") or ""
            if not isinstance(identity, int) or identity <= floor or not self.allowed(author):
                continue
            inbound.append(Inbound(identity, author, str(comment.get("body") or "")))
        return sorted(inbound, key=lambda row: row.id)

    def floor(self, record: dict, issue: dict) -> int:
        """The newest comment id this issue carries, so a fresh gate ignores older words."""
        ids = [int(row.get("id")) for row in issue.get("comments_list") or [] if isinstance(row.get("id"), int)]
        return max([int(record.get("initial_comment_id") or 0)] + ids)

    def post(self, number: int, body: str) -> int | None:
        """One comment on the issue; returns its id so the bridge can count it as seen."""
        created = self.client.issue_comment(github.IssueRef(self.owner, self.repo, number), body)
        identity = created.get("id") if isinstance(created, dict) else None
        return identity if isinstance(identity, int) else None
