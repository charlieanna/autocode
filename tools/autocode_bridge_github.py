"""The GitHub channel: which labeled issues to take, which replies are ours to act on.

A thin adapter over ``autocode_github.Client`` with the bridge's channel rules:
discovery reads one label on one repository, and an issue is taken only when the
login that applied the label is on the allowlist (GitHub authenticated it, so it is
the run's requester); a reply counts only when its author
is on the configured allowlist and its comment id is newer than everything the
bridge had consumed when it relayed the current gate, so a message written before
a plan was posted can never approve it. Everything posted goes through the
client, which redacts.

The bridge often posts as an allowlisted account: one person's token both relays
the gates and writes the replies. So every comment it posts carries ``OWN_COMMENT``,
and a comment carrying it is never a reply, wherever it sits and whether or not
the bridge remembered posting it. Position alone cannot tell the two apart: a
notice posted outside a gate, or one whose id was never saved, would read as the
person's words.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    from . import autocode_github as github
except ImportError:
    import autocode_github as github


# The last line of every comment the bridge posts. GitHub does not render it.
OWN_COMMENT = "<!-- autocode-bridge -->"


@dataclass
class Inbound:
    """One reply comment the bridge may act on."""

    id: int
    actor: str
    text: str


class GitHubChannel:
    def __init__(
        self,
        client: github.Client,
        repo: str,
        label: str,
        actors: tuple[str, ...] | list[str],
        deciders: tuple[str, ...] | list[str] | None = None,
    ):
        owner, _, name = repo.partition("/")
        if not owner or not name:
            raise ValueError(f"AUTOCODE_BRIDGE_GITHUB_REPO must be OWNER/REPO, not {repo!r}")
        self.client = client
        self.owner, self.repo = owner, name
        self.label = label
        self.actors = {str(actor).lower() for actor in actors}
        # Who may answer, approve and steer; unset, the same logins that may start runs.
        self.deciders = self.actors if deciders is None else {str(login).lower() for login in deciders}

    @property
    def ref_template(self) -> str:
        return f"{self.owner}/{self.repo}#"

    def allowed(self, login: str) -> bool:
        """Whether this login may start a run by labeling an issue."""
        return str(login or "").lower() in self.actors

    def may_decide(self, login: str) -> bool:
        """Whether this login's reply may answer, approve or otherwise steer a run."""
        return str(login or "").lower() in self.deciders

    def heard(self, login: str) -> bool:
        """Whether a reply from this login is read at all: either list."""
        return self.allowed(login) or self.may_decide(login)

    def requester(self, number: int) -> str | None:
        """The GitHub login that applied the bridge's label to an issue: who asked for the run."""
        return self.client.label_actor(github.IssueRef(self.owner, self.repo, number), self.label)

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
            body = str(comment.get("body") or "")
            if not isinstance(identity, int) or identity <= floor or not self.heard(author) or OWN_COMMENT in body:
                continue
            inbound.append(Inbound(identity, author, body))
        return sorted(inbound, key=lambda row: row.id)

    def floor(self, record: dict, issue: dict) -> int:
        """The newest comment id this issue carries, so a fresh gate ignores older words."""
        ids = [int(row.get("id")) for row in issue.get("comments_list") or [] if isinstance(row.get("id"), int)]
        return max([int(record.get("initial_comment_id") or 0)] + ids)

    def post(self, number: int, body: str) -> int | None:
        """One comment on the issue, marked as the bridge's own; returns its id, which a gate takes as its floor."""
        created = self.client.issue_comment(
            github.IssueRef(self.owner, self.repo, number), f"{body.rstrip()}\n\n{OWN_COMMENT}"
        )
        identity = created.get("id") if isinstance(created, dict) else None
        return identity if isinstance(identity, int) else None
