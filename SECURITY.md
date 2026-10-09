# Security policy

AutoCode launches AI coding agents on your own machine, in a Git workspace you
choose, with whatever tool permissions the underlying provider (OpenCode, Codex
or a configured command tool) grants. That makes security reports important,
and it also sets the boundary of what counts as one.

## Report a vulnerability privately

**Do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting: open the repository's
**Security** tab and choose **Report a vulnerability**. (Maintainer: this must
be enabled once under *Settings → Code security → Private vulnerability
reporting*.)

If that is not available, email the maintainer at the address on their GitHub
profile with the subject line `AutoCode security`.

Please include: the version or commit, the provider and engine in use, the
exact steps or a saved run (with credentials removed), and what you think the
impact is. You will get an acknowledgement within 7 days and an assessment
within 30. There is no bug bounty.

## What counts as a vulnerability here

The project's promises are about **approval, evidence and workspace scope**.
A report is in scope if it shows any of these can be broken:

- **Approval bypass.** Code is built, changed or accepted without the exact
  displayed plan revision or artifact being approved; a resume, answer, or
  feedback action is treated as approval; a stale token is accepted.
- **False completion.** The completion gate reports "ready" or "complete"
  without independent validation evidence for the identified candidate, or
  accepts evidence from a different source revision, or lets a finding close
  by omission or by a fix *claim*.
- **Workspace escape.** A Builder or reviewer stage writes outside its task
  worktree or declared ownership, edits the parent checkout, or reads
  ancestor/sibling directories in a way the role's prompt and permissions
  forbid.
- **Credential exposure.** Provider credentials, tokens or secrets are written
  into `state.json`, run artifacts, dashboard storage, logs, or handoff
  exports.
- **Dashboard exposure.** The local dashboard becomes reachable from other
  hosts, or an unauthenticated request can start, resume or mutate a run.
- **Unsafe file handling.** Symlink or path-traversal tricks in run
  directories, intervention inboxes, or registry imports that write or read
  outside the intended paths.

## What is not a vulnerability

These are documented limits (see the README's *Limitations and trust
boundary*); reports about them are welcome as ordinary issues but are not
security reports:

- AutoCode is a **local, trusted-workspace tool**, not a multi-tenant service.
  A process with write access to the same files can tamper with state and
  evidence. Integrity checks are not a boundary against that.
- Provider tool permissions and after-the-fact source checks are **not an OS
  sandbox**. OpenCode, Codex and external command tools each have their own,
  different isolation. Credential-like environment variables are withheld from
  agents and from the tests the runner executes (see
  [Providers](docs/providers.md#environment-variables-agents-see)), but an agent
  with shell access can still read files your user account can read, such as
  `~/.aws/credentials`. Run AutoCode as a user without credentials you would not
  hand to a model.
- A model producing wrong, insecure, or low-quality **application code** is a
  quality problem for the review stages, not a vulnerability in AutoCode.
- Running your own project's commands (tests, dev servers) does what those
  commands do.

## Supported versions

Only the latest commit on `master` receives fixes. There are no maintained
release branches yet.
