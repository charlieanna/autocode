# Single-host team service: contract proposal (#2023)

Status: **proposed, not implemented or approved**. Source audit: master
`51728f73b031036f84d6575577596b53c9290df1`, 2026-10-27. This document is the
consolidated service/client-extension decision for #2022 and #2023. Merging
this proposal does not authorize runtime implementation or expose a host.
The [synthetic envelopes](examples/team-service-v1.json) are design fixtures,
not service responses or qualification results.

The intended outcome is a verified pull request after a person submits work
and disconnects. A durable worker is only one component of that outcome.
Keep the existing core controller, TaskRun, supported Linux execution boundary,
Requirements and exact-plan approval, separate Tester and Completion Reviewer,
routes, efforts, usage, recovery and spending semantics. No automatic merge,
deployment, Jira/Slack adapter, fleet or multi-team tenancy is proposed here.

## Decision package

The recommended first release uses one prepared persistent Linux host, one
operator-owned repository registry, one OIDC login integration, a private
service database and a thin authenticated web client. Notifications use the
per-person service inbox and **SMTP email as the single outbound channel**.
No action is accepted through email. These are proposed choices, not defaults
silently enabled by this document.

The owner decision must cover the client extension, the permission/delegation
rules below, the OIDC/session boundary, email notifications, the proposed
trusted-actor core addition and the implementation scope #2024–#2032. The
identity-provider instance, repositories, recipients, resource limits, routes
and bounds are operator installation inputs; no real accounts or host settings
are configured here. A separately agreed bounded pilot and thresholds remain
#2033. No paid trial or production deployment is included in this package.

## Responsibility and trust

| Component | Owns | Cannot do |
| --- | --- | --- |
| Authentication / #2024 | Validated principal, sessions, membership and permission revisions | Trust request `actor`, Git identity or workspace configuration as login |
| Service registry / #2026 | Approved repository IDs, refs, setup/check profiles and credential references | Accept arbitrary URLs, host paths or task-written policy |
| Gateway / #2027 | Authorize and translate opaque IDs to public TaskRun operations | Import CLI/controller internals, read private task state or construct task policy |
| Admission / #2028 | Persist and schedule submissions, reconcile public launch receipts | Become a second stage controller or replay an uncertain launch |
| Execution boundary / #2025 | Prepared task workspace, process/storage/secret isolation | Treat a worktree or shared provider home as an isolation boundary |
| Core | Run lifecycle, locks, decisions, provider supervision, proof and completion | Infer remote authentication from Git email or `--as` |
| Web client / #2029 | Render authorized projections and explicit human choices | Receive raw core tokens, CLI argument arrays or host paths |
| Inbox / #2030 | Durable per-recipient attention and delivery notifications | Approve, resume, retry or change limits on acknowledgement |
| Publisher / #2031 | Reconcile publication of an immutable verified result | Publish arbitrary working HEAD, force-push or auto-merge |
| Operations / #2032 | Health, audit, backup/restore, drain and pinned upgrade records | Delete uncertain attempts or manufacture recovered completion |

Operator policy, service records and identity/publishing secrets live outside
all agent-editable workspaces. Distinct execution identities and the supported
kernel boundary prevent task processes from reading them. The service process
and pinned core runtime are trusted; task code, generated reports, workspace
Git/configuration and artifact HTML are untrusted. This is a trusted-team
single-host boundary, not a hostile multi-tenant SaaS claim. Actual enforcement
is a release gate in #2025, not established by this proposal.

## Authentication and authorization

Use an established OIDC library with authorization-code login and PKCE S256.
Pin one configured issuer and client audience, exact redirect URI and signing
algorithm policy; validate signatures, issuer, audience, expiry, state and
nonce. Stable person identity is the `(issuer, subject)` pair, mapped to an
operator-managed opaque `principal_id`; display name and email grant nothing.
These protocol choices follow [OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html)
and [OAuth security BCP](https://www.rfc-editor.org/rfc/rfc9700.html), rather than
a new password or token parser.

Terminate TLS at one configured reverse proxy on the prepared host. The gateway
listens only on a private Unix socket, accepts forwarding metadata only from
that proxy, and never consumes forwarded user/role headers as authentication.
Use an opaque, server-side, revocable browser session with Secure, HttpOnly,
SameSite=Lax cookie, a configured short idle/absolute expiry and CSRF protection
for every write. Do not put OIDC tokens in browser storage or task environments.
No anonymous endpoints expose task/project inventory. Health without task data
may be exposed only through operator configuration.

Permissions are deny-by-default and scoped to projects and, where needed,
tasks. `read` does not imply `submit`; `submit` does not imply `decide`.

| Operation | Reader | Submitter | Designated decider | Operator |
| --- | --- | --- | --- | --- |
| Inventory, status, approved artifacts | Authorized project/task only | Same | Same | Only with separate read grant |
| Submit a new task | No | Granted project only | Only with submit grant | Only with submit grant |
| Answer, feedback, approve exact displayed plan, named pause/recovery action | No | Only with explicit task `decide` grant | Assigned task only | No implicit task decision |
| Acknowledge inbox | Own inbox only | Own inbox only | Own inbox only | Own inbox only |
| Change registry, membership or drain service | No | No | No | Yes, audited |
| Expand limits/routes or delegate protected choices | No | No implicit grant | Only an explicitly presented, allowed core action and separate authority | No bypass through service administration |

The task owner receives read access through a recorded grant; permission to
submit alone is not a reusable right to inspect someone else's task. Task
ownership/decider changes require a recorded authorized operation, recheck the
recipient's access and never transfer an already approved decision to a new
plan. No owner-controlled arbitrary sharing is included in v1.

Check current membership, policy revision and task grant on **every** read,
artifact fetch and decision application. Revocation invalidates sessions and
future authority, including already queued human decisions and unsent email.
It does not kill a worker or approve recovery. A revoked owner's running task
becomes operator attention for reassignment through policy; the core retains
its actual state. Filter list results and return the same `not_found` response
for unknown and inaccessible object IDs. Audit the internal distinction.

## Repository and execution policy

A registry entry is operator-owned and immutable by version. A submission pins
its project/repository ID, canonical remote identity, resolved base commit,
registry version and execution-profile digest. An allowed base ref is resolved
server-side once; changing its remote target later cannot silently rebase the
submitted task. Revalidate access and current operator revocations at dispatch.
A revoked policy blocks dispatch; it does not silently substitute a new one.

An entry contains:

- Stable project/repository ID, canonical forge/repository identity, allowed
  base refs and service branch namespace. No request-supplied remote/path.
- Toolchain/runtime image or prepared-host profile identity; reproducible setup
  and required checks as operator-reviewed argument arrays, working directory
  and allowed environment keys. Execute setup inside the task boundary.
- Route/effort profile and existing core limits/policies; user-visible before
  acceptance. A named profile selects approved choices; the service cannot
  replace models or turn on compact planning to make a job pass.
- Credential references separated into read-checkout, task-model and publisher
  capabilities. References resolve outside workspaces. No privileged service
  home, login/session store or publication token is copied to task processes.
- Host/project/repository active-worker limits, queue/scratch/storage bounds,
  retention class, allowed task owners/deciders and publication destinations.
- Accounting coverage policy: v1 preserves the core's accounting cap and
  explicit unknowns. Admission is blocked when an operator-required price or
  provider allowance is unavailable; it never calls an estimate a guaranteed
  bill. Monthly/person quotas or new strict spending guarantees are not added.

Before model work, validate repository/ref/credential access, supported durable
mode, containment, setup and check availability. Setup that runs untrusted
repository code must already be isolated. Separate invalid submissions from a
later unavailable prerequisite. No quiet downgrade to owned execution, another
engine or weaker isolation. Same-repository tasks use distinct workspace/branch
identities plus existing checkout locks. Multi-repository coordination is not
silently enabled: current durable anchors refuse automatic coordination, so
v1 rejects unsupported spanning scope rather than delivering a partial result.

## Public v1 envelopes

Use `/api/v1` and JSON with a version discriminator. Reject unknown request
fields, invalid types and unsupported versions. IDs are opaque service IDs;
clients never send or receive run directories, core control tokens, shell
strings, CLI arrays or credential references. Server-only mappings bind each
ID to the allowed public core handle. Text is untrusted data; preserve its
multiline/Unicode content within documented size limits.

| Route / envelope | Required request fields | Response and authority |
| --- | --- | --- |
| `POST /tasks` / submission | `version`, project/repository IDs, allowed `base_ref`, requirements, acceptance criteria, `execution_profile_id`, explicit `delegation` record | Durable admission receipt, task ID, payload digest, resolved base/policy identities; no execution promise |
| `GET /tasks` / inventory | Authorized filters and opaque cursor | Only visible tasks; no hidden repository counts or IDs |
| `GET /tasks/{id}` / status | Opaque task ID | Independent admission/execution/delivery state, observation freshness, accounting coverage, permitted artifacts and current decision offer |
| `POST /tasks/{id}/decisions` / decision | `version`, opaque `decision_id`, exact observed revision, explicit offered choice and typed payload | Durable decision receipt; applied, refused or pending/unknown. Never immediate claimed completion |
| `GET /tasks/{id}/decisions/{receipt}` | Receipt ID | Durable action outcome or explicit unresolved reconciliation |
| `GET /tasks/{id}/artifacts/{id}` | Opaque authorized artifact ID | Allowlisted bounded inert content, digest/type/size; no arbitrary path/open redirect |
| `GET /inbox`, `POST /inbox/{id}/acknowledge` | Recipient session; explicit read acknowledgement | Local unread state only; no task action |
| delivery in status | No caller-supplied repository/commit/PR URL | Verified result identity plus prepared/publishing/published/blocked/unknown state and reconciled PR URL |

All writes carry an `Idempotency-Key` in a bounded documented format. Store the
canonical validated request and its digest transactionally before returning
`202 accepted`; do not acknowledge a memory-only queue item. Scope uniqueness
to principal + operation + object + key. The same request returns its original
receipt. A different request under the same key returns `409 idempotency_conflict`.
Uniqueness is per key, not semantic deduplication of two intentionally new tasks.
Retain tombstones for at least the task/decision retention period; an expired
key cannot create a second task through a retry. Database failover/restore must
preserve that boundary or hold admission pending operator reconciliation.

Canonical payloads use validated typed fields, sorted object keys and UTF-8 JSON
with no insignificant whitespace, preserving array order and exact text without
Unicode normalization. Use integer bounds or decimal strings, never non-finite
numbers. Reject duplicate JSON keys before canonicalization. The service pins a
canonicalization version with the receipt; an upgrade cannot reinterpret an old
idempotency key.

Error codes: `unauthenticated`, `not_found`, `invalid_submission`,
`unsupported_version`, `idempotency_conflict`, `stale_decision`,
`permission_revoked`, `policy_unavailable`, `capacity_unavailable`,
`observation_unavailable`, `core_refused`, and `outcome_unknown`. Return a
correlation ID and retry guidance, with no secret/core token or host path.
An HTTP error or timeout is never authority to repeat a provider operation.

## Current core capabilities and additive gaps

| Service operation | Current public capability | Required boundary / gap owner |
| --- | --- | --- |
| New durable submission | `modes list --json`, `TaskRun.start_durable`, `inspect_launch` | #2028 persists server-owned binding and reconciles one launch |
| Read current execution/proof | `TaskRun.status(inspect_evidence=True)`, `evidence_report(require_current=True)` | #2027 filters/redacts the projection; unavailable is not a failed task |
| Continue or answer a gate | Fresh named action via shared `NamedCommand` and `TaskRun.dispatch_durable` | #2024/#2027 add trusted actor/assertion receipt support before person actions ship |
| Show the actual plan | `TaskRun.show_goal`, current displayed-plan token and public view | Gateway stores private token and exact rendered bytes, exposing only its own decision ID |
| Current code diff | Status `code_checkpoints` named comparison | #2027 executes only that capability; no arbitrary Git/path RPC |
| Per-person service inbox | Public attention identity plus service-owned recipient/read records | #2030 must not repurpose core same-UID attention acknowledgement as remote per-person state |
| Read arbitrary artifact | No universal authenticated download operation is established | #2027 adds a public bounded capability for each supported kind, otherwise refuses it |
| Verified general-task PR | Existing issue/coordination delivery, not a universal TaskRun publisher | #2031 adds/reuses a public named capability and immutable receipt; no private-state workaround |
| Remote actor / lost decision outcome | No verified service actor or assertion-id lookup is established | #2024/#2027 additive core contract below; local Git provenance cannot fill this gap |

The table is an audited distinction between existing methods and proposed
capabilities. It is not a list of features already implemented by the service.

## Human decisions and attributable core actions

Before offering a decision, the gateway reads fresh public status, verifies
its task binding and named action, and saves a private observation record.
Its opaque `decision_id` binds task incarnation/turn, action kind, exact rendered
plan/question bytes and digest, core checkpoint/token, policy revision and
expiry. Only the human-readable content and service ID leave the host. A plan
approval is a separate explicit choice against that exact displayed plan;
fetching it, free text, a notification click or a previous approval is not consent.

At application, reauthenticate/re-authorize, re-read current status and compare
all bindings. A changed plan/action or revoked grant refuses the decision and
requires a new display. Pass only the fresh status-named argument array through
the existing shared client-action machinery and durable transport. The core
still checks its own token under its lock. Two people acting on the same gate
cannot authorize two different actions: serialize application per bound gate;
one receipt wins and the other is stale/refused. Never reuse the winner's
receipt as the other person's approval.

**Confirmed core gap:** `autocode_approval_provenance` and
`autocode_required_approver` explicitly record unverified Git/`--as` claims.
They are not a remote authentication boundary. The current GitHub bridge
allowlist and service-side authorization cannot turn those claims into verified
core actor provenance. Do not set Git email or `--as` to impersonate a user.

Proposed additive capability, owned by #2024/#2027 before remote decisions ship:

1. An operator-configured trusted service issuer attests the validated principal,
   authorization revision and receipt ID for one exact core decision binding.
   The binding includes run incarnation/turn, current action/checkpoint/token
   digest, rendered-content digest, decision payload digest, expiry and a unique
   single-use assertion ID. The authenticated actor is never supplied by the
   task workspace or remote request body.
2. Use an established JWS implementation with pinned Ed25519 keys and `EdDSA`,
   following [RFC 8037](https://www.rfc-editor.org/rfc/rfc8037.html), and a pinned
   issuer/key policy outside agent-editable storage; no custom cryptographic parser.
   The signing key stays with the service, outside provider access. Transport
   the assertion privately alongside the exact named action, never as model
   context, public argv, an artifact or a new arbitrary CLI command. The precise
   TaskRun transport/descriptor is part of that additive implementation review.
3. The core verifies the assertion, exact current binding, trusted issuer and
   allowed service policy under its decision lock before mutation. Consume its
   assertion identity atomically with the decision event and expose an additive
   `authenticated_actor`/service-receipt audit projection. An unknown outcome
   must be queryable by assertion/receipt identity without replaying the action.
4. Existing local Git provenance remains explicitly unverified. No configured
   trust means the new assertion is refused, never treated as a local approval.
   Service-authorized subject grants must be separate from `require-approver`'s
   local Git-email guard; a task cannot remove a trusted service guard by editing
   project configuration. Existing local behavior is preserved.

Until this addition exists and is qualified, the gateway must refuse remote
person decisions. It must not claim #2024/#2027 complete using only an HTTP audit
row. Contract and fixture work can proceed meanwhile. Core status, artifacts
and lifecycle remain public interfaces; no private task-state access is added.

Upfront delegation is explicit, never inferred from requirements. The default
submission records `{"mode":"none"}`. A separately authorized submitter may
select `{"mode":"core_policy","profile_id":...,"accepted_digest":...}`
for an operator-approved **existing core recovery-policy profile**. The service
must display its exact existing flags, authority, maximums, financial guard and
non-delegable decisions before recording that authenticated grant. It passes
only that pinned new-run policy to TaskRun; it does not implement recovery or
increase authority later. For example, an explicitly selected profile may use
the existing `--autoresolver-managed-limits` semantics, including its existing
ceilings and explicit-cap/unknown-usage exclusions. It cannot substitute routes,
remove a financial cap, waive evidence or authorize an uncertain provider replay.
The selected profile is installation policy, not authorized by this proposal.

Requirements confirmation, approval of an exact new plan, scope/permission
changes, unsupported-effort choices and any core person-only recovery remain
human gates. The notification names the designated decider. No blanket
preapproval of future plans, `skip-to-build` or invented answers is introduced.
A policy stop with no current named action remains held. Qualify both the
no-delegation path and an explicit existing-policy grant without treating them
as equivalent authority. Any broader company delegation is a separate decision.

## Admission, execution and delivery are separate

A task projection keeps these axes instead of using one misleading `success`:

- Admission: `accepted`, `queued`, `preparing`, `dispatching`, `dispatched`,
  `blocked`, `rejected`. A committed receipt proves admission, not a launch.
- Execution: `not_started`, `running`, `waiting_for_person`, `paused`, `blocked`,
  `failed`, `core_complete`, `unknown`. Public observation includes
  `observed_at`, `available`, `current`, core runtime and incarnation identity.
  A stale/unavailable observation never erases the last result or offers actions.
- Delivery: `not_ready`, `prepared`, `publishing`, `published`, `blocked`,
  `unknown`. Core completion is not PR publication. Current completion receipt,
  exact verified commit/tree, approved destination and publication identity bind
  delivery. Later follow-up work has a new turn/result identity.

Dispatch commits a unique intent before `TaskRun.start_durable`; reconcile its
public launch receipt with `inspect_launch`. Losing the start reply does not
prove no worker exists. Do not call start again until public reconciliation
establishes the outcome or an explicit supported recovery decision authorizes
it. Queue fairness is bounded round-robin across projects and FIFO within a
project, subject to registry/host limits and core checkout exclusion. Held work
releases queue scheduling slots only when the actual worker state supports it;
no scheduling policy grants a task resume or abort.

Publication intent binds service task/result, destination and exact verified
commit before a remote side effect. Reconcile by branch/commit/PR identity after
a lost response. Changed working HEAD must not replace the immutable verified
result. A newer submitted base policy, stale evidence or unexpected remote
branch blocks publication; never force-push to resolve it. Existing issue and
coordination publication machinery is reused where its public capability fits.
Missing general-task publication/evidence retrieval must be added as named public
capabilities; #2031 cannot inspect issue-private files or invent CLI commands.

Accounting projects the public `usage`, `cost` and `efficiency` records with
coverage, pricing basis, failed attempts and unknown stages. Preserve queue,
provider-active, observed running-wall, human-wait and repair-wait categories;
missing time is unknown. Use [existing cost definitions](cost-reporting.md).
Subscription/API-equivalent estimates are not invoices, and stage-boundary caps
are not guaranteed bills. #1867's broader policy remains unchanged.

## Inbox, artifacts and operations

Each logical notification binds recipient, task incarnation/turn and public
attention/delivery identity. Persist unread state and an outbound outbox outside
workspaces. Recheck current ownership/access before send or display; suppress
superseded attention. Use the operator-configured SMTP relay and recipient
mapping, with no destinations from task text. Email carries a generic state and
an authenticated task link, not source, secrets, decision tokens or action links.
A delivery timeout is `unknown`, not proof email was unsent; durable retry policy
must preserve one logical notification and acknowledge possible duplicate mail.
Inbox acknowledgement changes only the service recipient record, not core local
attention receipts or task authority.

Artifacts are public-capability IDs resolved and authorized server-side. No raw
filesystem browsing, symlink following or arbitrary HTML execution. Logs are
bounded/redacted; treat Markdown/HTML/SVG as untrusted inert content with an
appropriate download/content policy. A URL possession never substitutes for
current access. If no public artifact capability exists, return unavailable
and add the core capability before exposing that artifact.

Audit append-only service events for admission, authorization/revocation,
decision intent/outcome, dispatch intent/reconciliation, notification and
publication identities. Include the authenticated principal and policy revision;
exclude secrets and core tokens. The service's audit receipt is correlated to,
not substituted for, the core's actual event/evidence. Drain stops new dispatch,
not running workers. Pinned-runtime upgrades cannot adopt uncertain attempts.
Back up service records and supported core recovery artifacts consistently;
restore holds dispatch until launch/publication ownership is reconciled. Cleanup
requires current no-worker ownership and retention policy; uncertainty retains
data. #2032 supplies and qualifies the actual runbooks and mechanisms.

## Shared qualification fixtures

Use two deliberately small **independent** repositories under test-only IDs:
`repo-pager` (Python unittest paging CLI) and `repo-totals` (Python pytest
aggregation function). Pin their seed commits, setup profiles, external oracles
and broken variants in #2026; avoid installations by using prepared environments.
These are proposed fixtures, not the historical live runs or an authorization
to spend on two new tasks. #2033 agrees any live sample separately.

The synthetic v1 envelopes name `person-a`, `person-b`, these repositories and
stable service IDs. They contain no real identity, credential or host path.
The matrix below is required behavior for the dependent implementations; none
is marked tested by this document.

| Case | Required observation / negative control | Owner |
| --- | --- | --- |
| Two people, two projects | A reads/submits/decides pager; B totals; swapped task/artifact/receipt IDs disclose nothing | #2024/#2027 |
| Same repository concurrency | Distinct branches/workspaces, bounded scheduling and existing checkout locks; cross-task writes denied | #2025/#2028 |
| Duplicate admission and lost acknowledgement | Same principal/key/payload returns one task; changed payload conflicts; death before commit has no accepted receipt | #2028 |
| Auth forgery | Actor/header/Git-email/workspace edits, wrong issuer/audience/nonce, expired session and CSRF never authorize | #2024 |
| Revoked access | Revoke after display, before apply/send; no mutation, artifact read or email; worker not silently killed | #2024/#2030 |
| Conflicting decisions | A/B hold one gate; one applies, the other's receipt is refused/stale; no second model dispatch | #2027 |
| Stale plan or turn | Revised plan/follow-up invalidates the old display; no automatic reapproval | #2027/#2029 |
| Lost decision receipt | Reconnect queries the durable receipt/assertion identity; no second invocation from timeout alone | #2027/#2028 |
| Lost launch reply / service death | Inspect the committed launch identity, retain unknown ownership and never blindly start again | #2028/#2032 |
| Blocked task / unavailable status | Separate actual blocked state from unavailable observation; no speculative Resume or fabricated current completion | #2027/#2029 |
| Browser close and protected question | Durable worker continues; inbox/email persists; authenticated fresh exact decision resumes only that gate | #2029/#2030 |
| Unknown pricing / in-flight cost | Subtotal and coverage remain explicit; operator-required coverage blocks admission; no guaranteed bill claim | #2028/#2033 |
| Missing setup/isolation | Preflight refusal before provider launch; no fallback to shared home or owned mode | #2025/#2026 |
| False green product | Unrelated successful command and broken variant cannot satisfy approved criteria or delivery | #2026/#2031 |
| Moved HEAD / lost PR reply | Publish only immutable verified commit or block; reconcile one known PR, no overwrite/force-push | #2031 |
| Artifact/notification attack | Other-task IDs, path/symlink escape, active HTML and task-specified email cannot leak or advance work | #2024/#2027/#2030 |
| Reboot / restore / upgrade | Every accepted receipt accounted for; interrupted/unknown provider work uses core recovery; no fabricated replay | #2032 |

Component tests precede real supported-Linux containment/HTTP/browser/fault
integration. Existing lifecycle evidence at its pinned revisions is reused but
is not a service pass. Final pilot gates include the #2022 vague-brief evidence
gap, a same-task follow-up, exact-commit independent acceptance and agreed cost,
time and human-intervention thresholds. No threshold is invented here.

## Implementation order and open decisions

After owner acceptance of this package, #2024 and #2027 implement identity and
the additive core actor boundary; #2025/#2026 establish isolated repository
fixtures; #2028 implements durable admission against those interfaces. #2029,
#2030, #2031 and #2032 may develop against the shared envelopes concurrently,
but real submission/decisions/publication wait for their qualified dependencies.
No unrelated TUI presentation or compact-planning rollout is a prerequisite.

Still required before #2023 closes:

1. Owner accepts or amends this precise service extension, including OIDC,
   permission matrix, the explicit existing-core-policy delegation boundary, SMTP and the trusted-actor core
   addition. This records scope; it grants no plan approval for a user's task.
2. Operator supplies actual issuer/client/proxy, host, repository/profile,
   membership, limits, retention and notification configuration at installation;
   dependent implementations must validate them instead of assuming values.
3. #2033 obtains explicit pilot thresholds/routes/spend/time bounds before live
   measurement. Missing original comparison/vague-brief inputs remain missing.
4. Focused implementation and integrated qualification evidence establishes the
   contract; a merged proposal or green documentation CI is not that evidence.
