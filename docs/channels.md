# Channel bridge: GitHub issues as the front door

[← Back to README](../README.md)

The channel bridge runs `autocode bridge` on the same persistent Linux host as
the core. A team member opens an issue with the bridge label; the bridge starts
a **durable** run (it survives the bridge, the terminal and the SSH session).
When the run reaches a human gate — Requirements questions, the plan approval,
a pause — the bridge posts it to the issue. The requester replies in the issue;
the bridge takes exactly the action the status view names. Between gates nobody
is needed: the worker plans, builds, tests and reviews on its own, and the
finished change comes back as a draft pull request carrying the run's canonical
evidence.

GitHub is the first channel (issue in [#2035](https://github.com/charlieanna/autocode/issues/2035),
a slice of the remote-service roadmap
[#2022](https://github.com/charlieanna/autocode/issues/2022)). Slack and Jira
adapters follow on the same bridge core; this page is their contract too.

## Set up the host

One trusted persistent Linux host, prepared like any durable-workflow host:
systemd user manager 254 or newer with linger already enabled (the bridge never
configures the host; `modes list --json` names the reason when it is missing),
a clone of the repository to build in, an engine the runs will use, and a
GitHub token with access to read issues and post comments (`GITHUB_TOKEN`).

```sh
autocode bridge check     # config, project, GitHub token, durable availability
autocode bridge run       # the daemon; one per host (a lock file refuses a second)
```

Everything is configured through the environment:

| Variable | Meaning |
| --- | --- |
| `AUTOCODE_BRIDGE_GITHUB_REPO` | `OWNER/REPO`, the single repository this bridge serves. |
| `AUTOCODE_BRIDGE_PROJECT` | the persistent checkout to build in (default: the daemon's directory). |
| `AUTOCODE_BRIDGE_LABEL` | the label that takes an issue (default `autocode`). |
| `AUTOCODE_BRIDGE_ACTORS` | a JSON array of GitHub logins allowed to steer runs. Replies from anyone else are ignored. |
| `AUTOCODE_BRIDGE_OPTIONS` | a JSON array of launch options (engine, models, `--test-command`). Every human or run action flag is refused here. |
| `AUTOCODE_BRIDGE_POLL_SECONDS` | seconds between polls (default 10). |
| `AUTOCODE_BRIDGE_MAX_ACTIVE` | the most bridged runs in flight at once, a whole number from 1. Unset, every labeled issue starts as soon as it is seen. See [Many issues at once](#many-issues-at-once). |
| `AUTOCODE_BRIDGE_OPEN_PR` | `1` to also push the branch and open the draft PR on completion; the default only prepares it. |
| `AUTOCODE_BRIDGE_OWNED` | `1` for the explicit no-systemd mode: runs live with the daemon process, as the [GitHub Action](https://github.com/charlieanna/autocode/blob/master/examples/github-issue/issue_action.py)'s runs do. A daemon restart interrupts them; prefer the durable mode on a host that supports it. |

## The requester's flow

1. Open an issue describing the outcome you want and add the bridge label.
   AutoCode takes it, comments to say so, and starts the run.
2. Answer in a comment when the run asks a question. Each question carries its
   id and, when they exist, its options; a single open question takes the whole
   comment, several take `ID: your answer` lines.
3. Read the plan it posts — the acceptance criteria, how each will be checked,
   constraints and permission boundaries. Reply `approve` to accept it, or
   reply with what to change (that goes back to the Requirements Gatherer, and
   the revised plan is posted for a fresh decision).
   If the run is one of a change set (`autocode changeset link`), the bridge
   also posts the other runs. Once every run waits with a complete plan, it
   posts their plans too, and `approve set` approves every plan together
   ([change sets in clients](task-run.md#change-sets-in-clients)). `approve`
   still releases this run alone.
4. Walk away. If the run pauses or a role's model runs out of quota, the
   bridge posts why and what to reply (`resume`, or `model NAME`).
5. On completion the bridge commits the change on the run's branch and
   prepares the evidence pull request (and opens it when configured).

## Many issues at once

Label as many issues as you like. With `AUTOCODE_BRIDGE_MAX_ACTIVE=2` the bridge
works on two at a time and queues the rest: a queued issue gets one comment
saying it waits, and starts when an issue in progress finishes or stops, lowest
issue number first. Each issue still has its own run, branch, conversation and
pull request.

- **What holds a slot.** An issue holds its slot from the start of its run until
  that run is delivered, stops with nothing left to ask, or can no longer be
  resumed. A run waiting for your answer or approval keeps its slot, so the
  number of issues asking for you at once is bounded too. A start the bridge
  refused holds none.
- **What the limit does not do.** It only decides when a queued issue starts.
  Your answer or approval always continues its own run, and a follow-up on a
  delivered issue takes a slot again, so more runs than the limit can be in
  flight for a while; no new issue starts until the count is back under it.
- **Queued issues.** Only an issue that is still open and still carries the label
  starts; remove the label to take it off the queue. Comments written while it
  waited cannot answer its run. `autocode bridge status` lists it with the gate
  `queued`, and `autocode bridge check` prints the limit.
- **A run whose state can no longer be read** counts as it did when it was last
  read; remove its record to free a slot it still holds.

Unset, the bridge behaves as before the limit existed. Removing the limit later
starts every issue still queued. There is no total cost cap across issues yet:
each run keeps the cap its launch options give it.

## The rules the bridge keeps

The bridge is a client of the [task-run interface](task-run.md), exactly like
the terminal chat and the headless conformance client:

- **Tokens never leave the host.** Every decision is taken against a fresh
  status view through `autocode_taskrun`, with the current token read from that
  view and passed in the child's environment. Issue comments carry questions,
  options and plan text — never a control token.
- **Free text never approves.** Only the exact words `approve`, `approved`,
  `y`, `yes` or `lgtm` at a plan gate count, and only when a complete plan was
  posted first. Anything else becomes feedback to the planning stage.
- **An approval binds to the plan that was shown.** The bridge records the
  token of the plan it posted; if the plan changed since, the approval is
  refused and the new plan is posted instead. `approve set` binds the same way
  to the change set's plans it posted, and takes only the set approval
  `autocode changeset status` names. Words written before a gate was
  posted can never answer it: each gate remembers which comments it has already
  consumed.
- **Person-only stops stay person-only.** Pauses, retries and blockers are
  relayed with their reason, and acted on only by an allowlisted actor's
  explicit reply. Nothing resumes on its own.
- **Refusals are reported, never retried.** When the core refuses an action
  (a stale token, an answer outside the options), the bridge posts the refusal
  and re-reads the run. A comment the status view cannot act on is posted as a
  gap to report against the CLI or the view — the bridge works around nothing.
- **One bridge, one action at a time per run.** The daemon holds a host lock;
  each run takes one action per comment, and the durable worker envelope owns
  every advancing command.

Between gates the bridge only watches: the run itself runs to its next stop
under the durable worker, whether or not the daemon is up (it reconciles from
its records when it returns).

## Operating

`autocode bridge status [--json]` lists every bridged issue, its current gate
and its run. Records live under `$XDG_STATE_HOME/autocode/bridge` (override
with `AUTOCODE_BRIDGE_HOME`), owner-private, outside every task workspace; the
per-issue worktrees, run state and the `autocode issue` records they carry stay
in the project's `.autocode/issues`, so a person can always pick a run up with
`autocode chat --run-dir ...` or `autocode issue status N` on the host.

A refused start (durable execution unavailable, a missing engine, a leftover
branch) is posted to the issue and recorded with its reason; the bridge does
not retry it automatically. Remove the record to let it try again. Under a
limit, a refused start's slot goes to the next queued issue in the same poll.
