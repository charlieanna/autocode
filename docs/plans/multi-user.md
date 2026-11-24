# Multi-user and team use: design

Status: **proposed, not approved or implemented.** Written 2026-11-24 against master
`6d4488e`. Merging this document authorizes no runtime change. The first slice below
needs the owner's decision, because it grows the channel bridge's approved contract
(`AGENTS.md`, "Channel bridge").

AutoCode today is one person on one machine. This note says what team use would need
for four things (shared run history, per-person cost caps and credentials, access
control, and the OS sandbox) and recommends a small first slice. It builds on, and is
deliberately smaller than, the [single-host team service proposal](../team-service-contract.md)
(#2216), whose roadmap (#2022 to #2033) the owner closed as not planned on 2026-11-08.

## Where AutoCode is today

| Area | Today | Why it is single-user |
| --- | --- | --- |
| Run history | Runs live in `<project>/.autocode/`; the registry is a per-user pointer index (`~/.autocode/registry.json`, `projects.json`; [registry API](../registry-api.md)) | Records hold host paths and private state; nothing is shared or attributed to a person |
| Cost limits | `--max-cost-usd` and `--max-planning-calls` per run; usage records keep unknowns explicit ([cost reporting](../cost-reporting.md)) | No total across runs, and no person to total it for |
| Credentials | Owned by the backend (Claude Code, Codex, OpenCode, Kilo) in the person's own home; AutoCode stores none | Whoever runs the process spends on that account |
| Who may approve | `require-approver` compares the workspace's Git email (`autocode_required_approver`) | Its own docstring says it is not authentication; `--as` is a claim |
| Remote people | The [channel bridge](../channels.md) takes replies only from allowlisted GitHub logins and keeps control tokens on the host | Closest thing to multi-user, but one allowlist for every action, and the run does not record who acted |
| OS sandbox | Tool subprocesses run inside macOS Seatbelt or Linux bubblewrap (`autocode_tool_containment`); `--allow-uncontained-tools` is an explicit opt-out | Protects the person's files and credentials from the model's shell. It runs as the person's own user and does not cover in-process file, plugin or MCP tools or the provider client |

## Principles

1. **Borrow identity; do not build it.** A person is a forge account that the forge
   already authenticated (a GitHub login on an issue comment), or an OS account on a
   shared host. AutoCode adds no passwords, sessions or web login. Git email stays an
   unverified claim, recorded as such.
2. **The OS user is the boundary between people. The sandbox is not.** Seatbelt and
   bubblewrap contain a stage's shell inside one person's authority. They are not
   tenancy: they run as the same user, and they do not contain the backend client that
   holds the credentials. People who must not read each other's runs or credentials
   get separate OS accounts, with the sandbox still inside each.
3. **Share results, not state.** History that other people read is a published, redacted
   artifact (the run report, `autocode plan --export`, an `autocode bundle` manifest
   hash), never `state.json` or a run directory.
4. **Spending caps stay the owner's.** A per-person cap is a spending cap like
   `--max-cost-usd`: set by the owner or operator in host configuration outside every
   workspace, never raised by AutoResolver, and an unknown cost is unknown, not zero.
5. **Same gates for everyone.** Requirements, exact-plan approval, the Tester and the
   Completion Reviewer are unchanged. Team use changes who may take an action, never
   what the action proves.

## What each area needs

### Shared run history

People need to see what ran, for whom, what it cost and what it delivered, without
access to the host. The pull request and the issue are already that record when a run
goes through the bridge. Needed: the run's attributed requester and deciders (below)
and its final cost, posted with the result, and a host-side `bridge status` listing that
can be filtered by person. A cross-host team store (a database, a shared registry) is
not needed until there is more than one host.

### Per-person cost caps

A ledger, keyed by the verified person, sums the existing per-run usage records across
runs in a period. At admission the remaining allowance becomes the new run's
`--max-cost-usd`, so the existing stage-boundary cap does the enforcing and no second
cost check is written. Concurrent runs reserve their cap from the allowance until they
end. A run whose usage is unpriced or unknown is reported as such; the owner decides
whether unknown usage blocks admission. Estimates for subscription engines are not
invoices, as today.

### Per-person credentials

Two models, in order:

1. **Shared team credential, attributed spend.** The bridge host's backend logins pay;
   the ledger says who spent what. Cheap, and honest about whose account pays.
2. **Each person's own credential.** One OS account per person on the shared host. The
   bridge (or a successor) starts that person's run as their user, so their backend
   login, run directory and sandbox are theirs. This needs a small privileged launcher,
   and is where the host's containment must already be qualified for untrusted setup.

AutoCode never copies one person's backend login into another person's process, and
never into a task workspace.

### Access control

Four permissions per repository, deny by default, read from host configuration:

| Permission | Allows | Default source |
| --- | --- | --- |
| read | See the run's issue, plan and result | The forge's own repository read access |
| submit | Start a run (label an issue) | Bridge allowlist |
| decide | Answer questions, approve the exact displayed plan, act on a named pause | Bridge allowlist, optionally a narrower `deciders` list |
| operate | Change the registry, caps or allowlists | Host owner only, never through an issue |

`submit` does not imply `decide`, so a team can have a reviewer approve plans its
members request. Permission is checked when the action is applied, not when it was
posted, so removing someone stops their pending replies.

### Fit with the OS sandbox work

- Team use must not relax containment. On a host serving more than one person, the
  bridge refuses a run configured with `--allow-uncontained-tools`, and refuses a host
  whose containment backend is unavailable, instead of starting uncontained.
- The sandbox's current guarantees (the shell cannot write outside the workspace or
  read the home directory, credential files or protected run files) are what makes model 1 safe enough on
  a trusted team host: a model's shell cannot read the bridge's backend login.
- What the sandbox does not cover (the provider client and its in-process tools) is why
  model 2 uses OS accounts rather than one shared account with stronger sandboxing.
- Any further sandbox change that matters to teams (network policy, setup scripts run
  inside the boundary) is qualified on the single-user path first; team use only
  inherits it.

## Recommended first slice: attributed runs on the GitHub bridge

One trusted team, one persistent Linux host, the existing bridge. No new client, no web
service, no new authentication.

1. **Record who acted.** For runs the bridge starts, record the requesting GitHub login
   and the login behind every decision it applies, as a new
   `forge_actor` record marked "authenticated by GitHub", alongside (never replacing)
   the existing unverified Git provenance. Shown in the status view as an additive field
   and in the posted result.
2. **Split submit from decide.** Add an optional `deciders` list to the bridge
   configuration, defaulting to today's allowlist, so behavior is unchanged unless set.
3. **Per-person cap through the existing run cap.** An optional per-login period cap in
   the bridge configuration. At start, the remaining allowance becomes the run's
   `--max-cost-usd`; at zero the bridge posts the refusal to the issue and starts nothing.
   The posted result includes the run's cost and the person's period total.
4. **Keep containment on.** The bridge refuses uncontained runs when more than one login
   is configured.

Out of scope for the slice: per-person credentials (model 2), a team store across hosts,
a web or VS Code team view, Slack or Jira, and multi-team tenancy.

Tests, all deterministic with the fake provider: a reply from a login on the allowlist
but not in `deciders` is refused at a plan gate; the applied decision records that
login; a person at their cap gets a refusal and no run; a second run's cap is the
allowance left after the first's reservation; an uncontained configuration is refused
on a multi-login bridge. No live run is needed: the slice changes no transport or
provider path.

## Open questions for the owner

- Whether unknown or unpriced usage blocks admission once a person has a cap.
- Whether the first slice should use one shared credential (model 1), or wait for
  per-person OS accounts (model 2).
- Whether the posted result should show the person's period total to everyone who can
  read the issue, or only on the host.
