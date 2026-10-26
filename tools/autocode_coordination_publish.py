"""Publish exactly the jointly checked member trees, with durable PR identities."""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_coordination_access as access
    from . import autocode_coordination_repositories as repos
    from . import autocode_coordination_store as store
    from . import autocode_github as github
    from . import autocode_issue_delivery as delivery
    from . import autocode_taskrun as taskrun
except ImportError:
    import autocode_coordination_access as access
    import autocode_coordination_repositories as repos
    import autocode_coordination_store as store
    import autocode_github as github
    import autocode_issue_delivery as delivery
    import autocode_taskrun as taskrun


def marker(document):
    return "<!-- autocode-coordination:" + document["id"] + " -->"


def merge_order(document):
    remaining = {row["target"] for row in document["members"]}
    dependencies = document.get("dependencies") or {}
    ordered = []
    while remaining:
        ready = sorted(target for target in remaining if not (set(dependencies.get(target, [])) & remaining))
        if not ready:
            return (
                "Dependency cycle: coordinate deployment of "
                + ", ".join(sorted(remaining))
                + "; review compatibility before merging."
            )
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ", then ".join(ordered) + "."


def body(document, member):
    joint = document["joint"]
    lines = [marker(document), "## Coordinated bug fix", "", document["task"], "", "### Related changes", ""]
    for row in document["members"]:
        published = row.get("publication") or {}
        reference = published.get("url") or (
            "No source change" if published.get("unchanged") else "Publication pending"
        )
        lines.append(
            f"- {row['target']}: {reference}; base `{row['base']}`, candidate `{published.get('commit', 'pending')}`"
        )
    lines += [
        "",
        "### Joint regression",
        "",
        "The same generated regression fails on the combined original code and passes on the exact candidate trees. Independent review and runner-executed proof passed.",
        f"Verification record: `{joint['verification']['record']}`",
        f"Evidence binding: `{joint['verification'].get('evidence_report', {}).get('binding', '')}`",
        "",
        "Merge order: " + merge_order(document),
        "No PR has been merged automatically.",
    ]
    evidence = (joint["verification"].get("evidence_report") or {}).get("document") or {}
    commands = list(dict.fromkeys(row["command"] for row in evidence.get("checks", []) if row.get("command")))
    if commands:
        lines += ["", "Executed joint checks:", *["- `" + command + "`" for command in commands]]
    cases = (evidence.get("regression_proof") or {}).get("fail_to_pass") or []
    if cases:
        lines += [
            "",
            "Reproduced on the original code and passing on the candidate:",
            *["- `" + name + "`" for name in cases],
        ]
    return "\n".join(lines)


def prepare(document, member, *, command=taskrun.AUTOCODE):
    repos.require_identity(member)
    access.check(member)
    workspace = Path(member["workspace"])
    run = taskrun.TaskRun(workspace, Path(member["run_dir"]), command=command)
    view = run.status(inspect_evidence=True)
    published = member.setdefault("publication", {})
    if published.get("unchanged"):
        return
    if not view.get("done") and not published.get("receipt"):
        raise store.CoordinationError("A member no longer passes its own completion gate")
    if not published.get("receipt"):
        report = run.evidence_report()
        published["receipt"] = delivery.capture(workspace, view, report)
        published["phase"] = "captured"
        store.save(document)
    receipt = published["receipt"]
    delivery.unchanged(workspace, receipt)
    delivery.require_safe_public_names(workspace, member["base"])
    if not receipt.get("delivery_commit"):
        delivery.stage_source(workspace)
        delivery.unchanged(workspace, receipt)
        if repos.git(workspace, "diff", "--cached", "--name-only"):
            if repos.git(workspace, "rev-parse", "HEAD") != receipt["tested_source"]["head"]:
                raise store.CoordinationError("Member HEAD moved before its delivery commit")
            repos.git(workspace, "commit", "-m", "Fix coordinated bug " + document["id"])
        published["receipt"] = receipt = delivery.seal(workspace, receipt)
        published["commit"] = receipt["delivery_commit"]
        published["phase"] = "committed"
        store.save(document)
    report = run.evidence_report(require_current=False)
    delivery.verify(workspace, run.status(inspect_evidence=True), report, receipt)
    if not repos.git(workspace, "diff", "--name-only", member["base"], published["commit"]):
        published.update(unchanged=True, phase="unchanged")
        store.save(document)


def authenticate(member, *, command=taskrun.AUTOCODE):
    """A delivery commit may move HEAD, but never the checked bytes or plan."""
    repos.require_identity(member)
    run = taskrun.TaskRun(Path(member["workspace"]), Path(member["run_dir"]), command=command)
    delivery.verify(
        member["workspace"],
        run.status(inspect_evidence=True),
        run.evidence_report(require_current=False),
        member["publication"]["receipt"],
    )


def publish_one(document, member, *, client=None):
    repos.require_identity(member)
    published = member["publication"]
    if published.get("unchanged"):
        return
    client = client or access.client()
    owner, repository = member["repository"]
    branch = member["branch"]
    base = member["base_branch"]
    commit = published["commit"]
    if repos.fingerprint(member["workspace"]) != member["tested_fingerprint"]:
        raise store.CoordinationError("Member changed after joint verification; publication refused")
    # A normal push cannot overwrite someone else's conflicting history.
    if published.get("phase") == "committed":
        published["phase"] = "pushing"
        store.save(document)
        repos.git(member["workspace"], "push", "origin", f"{commit}:refs/heads/{branch}")
        published["phase"] = "pushed"
        store.save(document)
    if published.get("phase") == "pushing":
        remote = repos.git(member["workspace"], "ls-remote", member["push_remote"], f"refs/heads/{branch}")
        if not remote or remote.split()[0] != commit:
            raise store.CoordinationError("Push outcome is uncertain; no push or PR was replayed")
        published["phase"] = "pushed"
        store.save(document)
    candidates = client.pulls_for_branch(owner, repository, head=branch, base=base)
    if candidates:
        if len(candidates) != 1:
            raise store.CoordinationError("More than one PR matches the reserved publication branch")
        pull = candidates[0]
        if marker(document) not in (pull.get("body") or "") or pull.get("head", {}).get("sha") != commit:
            raise store.CoordinationError("Existing PR does not match this coordination and exact tested commit")
        if pull.get("state") != "open":
            raise store.CoordinationError("The reserved PR is already closed or merged; no replacement was created")
    else:
        if published.get("phase") in ("creating", "published"):
            raise store.CoordinationError(
                "PR publication is uncertain; reconciliation found no PR and will not create a duplicate"
            )
        published["phase"] = "creating"
        store.save(document)
        pull = client.open_pull_request(
            owner,
            repository,
            head=branch,
            base=base,
            title="Fix coordinated bug: " + document["task"].splitlines()[0][:150],
            body=body(document, member),
            draft=False,
        )
    if not pull.get("number") or not pull.get("html_url"):
        raise store.CoordinationError("GitHub returned no durable PR identity; reconcile before retrying")
    if (
        pull.get("head", {}).get("ref") != branch
        or pull.get("head", {}).get("sha") != commit
        or pull.get("base", {}).get("ref") != base
        or pull.get("base", {}).get("sha") != member["base"]
    ):
        raise store.CoordinationError("The PR head or delivery base differs from the exact verified revisions")
    identity = github.parse_pull_request_url(pull["html_url"], api=client.api)
    if (identity.owner.casefold(), identity.repo.casefold(), identity.number) != (
        owner.casefold(),
        repository.casefold(),
        pull["number"],
    ):
        raise store.CoordinationError("GitHub returned a pull request for a different repository")
    published.update(phase="published", number=pull["number"], url=pull["html_url"])
    store.save(document)


def crosslink(document, *, client=None):
    client = client or access.client()
    for member in document["members"]:
        published = member["publication"]
        if not published.get("unchanged"):
            client.update_pull_body(*member["repository"], published["number"], body(document, member))
