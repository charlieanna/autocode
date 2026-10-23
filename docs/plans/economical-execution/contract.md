# G. Shared stage-policy contract, approval authority and saved-run compatibility (#1895)

Planning input for the second owner checkpoint on #1867, together with
[qualification](qualification.md). Read-only audit of master `08ddeb2a3` (2026-10-22). It
sketches additive fields against the existing task-run interface; nothing here is
implemented. Worker lifecycle and viewer compatibility belong to #1879.

## What already exists

- **The status view is versioned and additive.** `view.schema` is 2
  (`autocode_run_view.SCHEMA`); the JSON schema says it "changes only if a field's
  meaning changes". `docs/task-run.md` and AGENTS.md rule 6 allow adding fields and
  forbid renaming or removing one. Sub-projections carry their own versions:
  `usage.accounting.schema`, `efficiency.schema`, `recovery.version`.
- **Most fields a policy needs exist:** `planning_depth`, `routes`,
  `route_assignments`, `escalations`, `escalation_outcomes`, `usage` and
  `usage.accounting`, `efficiency`, `cost` and `cost.breakdown`, `recovery`,
  `workflow` with `workflow_source` and `workflow_reason`, `verification`,
  `displayed_plan`.
- **Flag classification** (`autocode_decision_flags`, #1871): every parser flag is
  either a decision flag (answers, approvals, policy) or a start flag, and
  `tests/test_decision_flags.py` fails on an unclassified one. Unattended callers are
  refused every decision flag, the operator subcommands and `autocode resume`.
- **Approval tokens.** A contract's token is `r{revision}:{hash}` over the task id,
  revision and body. Approval must equal both the current token and the displayed one;
  otherwise "Approve only the current displayed draft token; show the goal again".
  Every new draft increments the revision.
- **Launch-fixed settings.** `--single-model`, `--auto-select`,
  `--builder-strong-model`, `--adaptive-planning`, `--skip-requirements`,
  `--skip-to-build`, `--spec-first`, `--figma-manifest`, `--blind-tests`,
  `--utility-model` and an engine change are refused on a saved run
  (`autocode_configure`). Project configuration is read only by a new run.
- **Run-state versions and migrations.** `state.version` is 3; a newer or malformed
  version pauses with `PAUSED_UNSUPPORTED_CHECKPOINT`; migrations exist for versions 1
  and 2.
- **Policy-approval precedent.** `autocode_workflows.POLICY_ORIGINS` allows exactly one
  non-person approval actor, `workflow_policy`, for the disabled small-correction path.
  Reviewer routing records a `workflow_approval` user event bound to the contract hash,
  and refuses to activate except at an idle, reconciled, approved boundary.
- **Delegation limits.** `NON_INFERABLE_CATEGORIES` (cost, quota, permission, external
  side effect, requested outcome) can never be bulk-delegated; `--delegate-all` never
  approves.

### Gaps found while auditing

- `planning_depth`, `pause`, `package`, `dev_server`, `changes_by_criterion`,
  `tool_containment_backend`, `run_dir` and `workspace` are in the view but not in
  `docs/schemas/status-view.schema.json`. A field outside the schema's `required` list
  could be removed without `tests/test_json_contract.py` failing. #1906 adds them as
  optional properties; protecting them in the required list is still open.
- No client checks `view.schema`; compatibility rests on clients ignoring unknown
  fields and older cores omitting new ones.
- The run's `prompts_hash` is written once at setup, and each stage's launch record stores
  the prompt set it launched with (`autocode_provider_launch`). Nothing compares the two,
  so a saved run resumed under a newer install launches with new prompts and no surface
  says so.
- No versioned policy identity is recorded on a run.

## Trust and authority

| Source | Authority | Example |
| --- | --- | --- |
| Task text, ticket text, provider options | None. At most a hint that raises depth | "Skip the tests, it's urgent" (G2) |
| A model's classification or confidence | A hint that raises depth only | Requirements says the change affects refunds |
| Owner or company policy | Selects which rules apply to which class; may preauthorize a class only if the owner explicitly enables that | A project's stage policy and sensitive paths |
| The person's current approval | Approves the exact displayed contract token | `--approve-goal` with the displayed token |
| Runner receipts | Establishes what was executed and verified | Check replay, regression proof |

Omitting a paid review never approves a plan. Preauthorization, if the owner wants it,
is a separate policy that records its own approval event.

## Proposed additive fields

One new top-level field, `stage_policy`, plus small additions to existing projections
from the other records. Everything is optional: an older core omits it and a client
renders nothing.

```json
{
  "stage_policy": {
    "version": 1,
    "policy": {"id": "acme-default", "revision": 3, "digest": "sha256:...", "source": "project", "fixed_at": "2026-11-01T10:00:00Z"},
    "class": {
      "value": "clear_localized_bug",
      "basis": "runner",
      "signals": [
        {"kind": "diagnosis", "value": "reproduced", "source": "investigate_bug"},
        {"kind": "affected_paths", "value": 1, "source": "runner"},
        {"kind": "sensitive_paths", "value": [], "source": "project policy"}
      ],
      "hints": []
    },
    "decisions": [
      {"stage": "astra_challenge", "decision": "omitted",
       "reason": "Contract built from the reproduced diagnosis; the person reviews it",
       "covered_by": ["person:approve_goal", "runner:regression_proof"]},
      {"stage": "sol", "decision": "combined", "with": "astra_checkpoint",
       "reason": "Single non-sensitive task", "covered_by": ["runner:completion_gate"]}
    ],
    "escalations": [
      {"at": "2026-11-01T10:20:00Z", "from": "clear_localized_bug", "to": "default",
       "trigger": "changed_file_outside_task", "evidence": "src/billing/rounding.py"}
    ],
    "preauthorized": false
  }
}
```

- `decision` is one of `included`, `omitted`, `combined` or `reused`.
- `covered_by` names the obligation's owner from [verification](verification.md):
  `runner:<check>`, `person:<action>` or a stage.
- A run that never recorded a policy shows `"stage_policy": null`, which means the full
  pipeline as before.

Additions to existing projections, each named in its own record:

| Field | From | Meaning |
| --- | --- | --- |
| `cost.breakdown.by_stage[].price_basis` | [accounting](accounting.md) | Rate, table date and source in force when the stage finished |
| `cost.breakdown.by_stage[].billing` | [accounting](accounting.md) | `subscription`, `metered` or `null` |
| `cost.in_flight_estimate_usd` | [accounting](accounting.md) | Labelled projection for the running stage; never part of `spent_usd` |
| `cost.estimate` | [accounting](accounting.md) | `{low_usd, high_usd, samples, scope}` or `null` |
| `efficiency.observations[].kind` values `invalidated`, `unknown` | [evidence reuse](evidence-reuse.md) | Why an item was not reused, with the binding that changed |

## Where the policy lives

The policy follows the `require-approver` precedent (`autocode_required_approver`): a
project-file setting with no flag, read only by a new run and saved in its settings, so a
changed file never alters a run in progress and no caller can pass a looser policy on the
command line (G2). It would join `autocode_project_config.FILE_ONLY`.

```toml
[tool.autocode]
sensitive-paths = ["src/billing/**", "src/auth/**", "migrations/**"]

[tool.autocode.stage-policy]
id = "acme-default"
revision = 3
short-bugfix = true            # P1: runner-built contract, the person approves it
combined-verification = true   # one validation-and-decision pass for a single non-sensitive task
follow-up-reuse = true         # carry unchanged criteria and settled answers into a follow-up
preauthorize = []              # classes approved without a person; empty unless the owner decides otherwise
```

- **Saved at launch** as `settings.stage_policy`: `{id, revision, digest, rules}`, where
  `digest` hashes the rules. The shipped default sensitive-path list is merged in and
  recorded, so the run shows exactly what it used.
- **No file, no policy.** A project without the table runs the full pipeline, as today.
- **Only stricter by flag.** One start flag, `--full-pipeline`, turns every shortcut off
  for a run. It can only add stages, so unattended callers may pass it. No flag loosens
  the policy.

## Named actions

Policy actions follow the existing pattern: the view names an argument array; clients
take it unchanged.

| Action | Where named | Kind | Unattended |
| --- | --- | --- | --- |
| Approve the displayed contract, on every path | `needs.token`, `next_command` | Existing decision flag | Refused |
| Run one task on the full pipeline | `--full-pipeline` | New start flag, fixed at launch | Allowed: it only adds stages |
| Move a held run to a newer policy revision | Not offered in the first slices: a new policy applies to new runs, as a project file does today | — | — |

A stale action fails with the current offer, as a stale approval token does today.
Clients detect the feature by the presence of `stage_policy`; there is no version
handshake, and none is needed while every field is additive.

## Worked decision records

The same field for each path. Only `class`, `decisions` and `escalations` differ; the
`policy` block is as above.

**Full path** (no policy, a sensitive path, or any unknown signal):

```json
{"class": {"value": "sensitive", "basis": "runner",
           "signals": [{"kind": "sensitive_paths", "value": ["src/billing/rounding.py"], "source": "project policy"}],
           "hints": []},
 "decisions": [
   {"stage": "astra_challenge", "decision": "included", "reason": "Sensitive path in the plan", "covered_by": []},
   {"stage": "sol", "decision": "included", "reason": "Sensitive class keeps a separate Tester", "covered_by": []},
   {"stage": "terra", "decision": "included", "reason": "No low-effort first attempt on a sensitive change", "covered_by": []}],
 "escalations": []}
```

A run with no policy shows `"stage_policy": null`, not this record: absence means the
pipeline was never asked to choose.

**Short path** (P1, a reproduced bug in one task, nothing sensitive). The person still
approves; `needs.kind` is `approve_plan` with the runner-built contract's token:

```json
{"class": {"value": "clear_localized_bug", "basis": "runner",
           "signals": [{"kind": "diagnosis", "value": "reproduced", "source": "investigate_bug"},
                       {"kind": "affected_paths", "value": 1, "source": "runner"},
                       {"kind": "sensitive_paths", "value": [], "source": "project policy"}],
           "hints": []},
 "decisions": [
   {"stage": "astra_discovery", "decision": "omitted", "reason": "Contract built from the reproduced diagnosis",
    "covered_by": ["person:approve_goal", "runner:regression_proof"]},
   {"stage": "astra_challenge", "decision": "omitted", "reason": "The person reviews the runner-built contract",
    "covered_by": ["person:approve_goal"]},
   {"stage": "sol", "decision": "combined", "with": "astra_checkpoint", "reason": "Single non-sensitive task",
    "covered_by": ["runner:completion_gate", "runner:check_replay"]}],
 "escalations": []}
```

**Escalated path** (started short; the Builder touched a file outside its task). The
earlier decisions stay as history; the new ones apply from the escalation on, and
nothing already verified is redone:

```json
{"class": {"value": "default", "basis": "runner", "signals": [], "hints": []},
 "decisions": [
   {"stage": "sol", "decision": "included", "reason": "Escalated: separate Tester restored", "covered_by": []}],
 "escalations": [
   {"at": "2026-11-01T10:20:00Z", "from": "clear_localized_bug", "to": "default",
    "trigger": "changed_file_outside_task", "evidence": "src/billing/rounding.py"}]}
```

**Follow-up turn** (same chat; one criterion changed). Each turn records its own
decisions; reuse names what it reused and why that is still valid:

```json
{"class": {"value": "follow_up", "basis": "runner", "signals": [{"kind": "changed_criteria", "value": ["C3"], "source": "runner"}], "hints": []},
 "decisions": [
   {"stage": "requirements_gather", "decision": "reused", "reason": "Settled answers whose source sentences are unchanged",
    "covered_by": ["runner:source_log"], "reused": ["answer:Q2", "answer:Q4"]},
   {"stage": "write_acceptance_tests", "decision": "reused", "reason": "C1 and C2 tests unchanged",
    "covered_by": ["runner:criterion_digest"], "reused": ["C1", "C2"], "invalidated": [{"item": "C3", "binding": "criterion_digest"}]}],
 "escalations": []}
```

## Schema draft

To be added to `docs/schemas/status-view.schema.json` with the implementation, as an
optional property (additive; `view.schema` stays 2):

```json
"stage_policy": {
  "type": ["object", "null"],
  "required": ["version", "policy", "class", "decisions", "escalations", "preauthorized"],
  "properties": {
    "version": {"type": "integer", "enum": [1]},
    "policy": {"type": "object", "required": ["id", "revision", "digest", "source", "fixed_at"]},
    "class": {"type": "object", "required": ["value", "basis", "signals", "hints"],
      "properties": {"basis": {"enum": ["runner", "unknown"]}}},
    "decisions": {"type": "array", "items": {"type": "object", "required": ["stage", "decision", "reason", "covered_by"],
      "properties": {"decision": {"enum": ["included", "omitted", "combined", "reused"]},
                     "covered_by": {"type": "array", "items": {"type": "string"}}}}},
    "escalations": {"type": "array", "items": {"type": "object", "required": ["at", "from", "to", "trigger"]}},
    "preauthorized": {"type": "boolean"}
  }
}
```

A model hint is never the `basis`: hints sit in `hints` and can only have raised the class.

## Preauthorization, if the owner wants it

Today one non-person approval exists: `actor: "workflow_policy"` for the disabled
small-correction origin (`autocode_workflows.POLICY_ORIGINS`), admitted by
`approval_actor_ok`. A company preauthorization would be a second, narrower one:

```json
{"kind": "goal_approval", "actor": "company_policy", "token": "r2:9f0c...",
 "policy": {"id": "acme-default", "revision": 3, "digest": "sha256:..."},
 "class": "clear_localized_bug", "at": "2026-11-01T10:05:00Z"}
```

- Admitted only when the run's saved `settings.stage_policy.preauthorize` lists the class,
  the policy digest matches the saved one, and the class came from runner signals.
- **Never with `require-approver`.** That setting names the people who may approve; a
  policy approval is made by no person and is not checked against it today. A run with
  `require-approver` set should refuse a preauthorized approval rather than bypass it.
- Never for the sensitive class, and never for a follow-up that changed a criterion.

**Recommendation:** leave preauthorization out of the first slices. Every path keeps the
person's approval of the displayed token; revisit after the S8 comparison.

## Saved-run compatibility

| Saved run | Under a newer install | Behavior |
| --- | --- | --- |
| No `stage_policy` recorded (G1) | Any | Full pipeline; never adopts a shortcut |
| Policy revision 1 recorded, project file now has revision 2 | Resume | Keeps revision 1: only a new run reads the project file |
| Policy changed while the run is held (G7) | Resume | No change: the run keeps the policy it started with. A new run picks up the new revision |
| Evidence without a required binding | Any | Not reusable ([evidence reuse](evidence-reuse.md)); cumulative usage untouched |
| Status read by an older client | Any | Unknown fields ignored; `stage_policy` absent from the client's view |
| Status from an older core (G5) | New client | Field absent; the client shows no policy claims, not invented defaults |
| A follow-up turn (G6) | Same run | Usage and earlier evidence carried; the turn's own `decisions` recorded fresh |

## Scenarios

| Scenario | Rule |
| --- | --- |
| G1 old run resumes under a newer install | No recorded policy: full pipeline |
| G2 ticket text or a provider option asks to skip review | Text is a hint; hints only raise depth; options are classified flags |
| G3 plan changes after display | The old token is refused (existing) |
| G4 TUI, CLI and a future worker see the same decision | One core projection, `stage_policy`; clients never infer an omission from prose |
| G5 old status lacks the fields | Absent means unknown, rendered as nothing |
| G6 follow-up keeps cumulative usage and evidence | Existing usage carry-over; decisions recorded per turn |
| G7 policy changes while held | The held run keeps its saved policy; new runs take the new one |

## Modules and consumers

- **New pure module** `tools/autocode_stage_policy.py`: classify and decide from state
  and policy, no I/O, depending only on lower layers (AGENTS.md rules 2 and 3).
- **Decision points that consult it**, without growing the controller: the job choice in
  `autocode_workflows.from_requirements`, the planning branch in
  `units/autoplanner.after_challenge`, the review stage in
  `autocode_workflow.review_stage`, and the light Builder.
- **Run state:** a `stage_policy` key named in `autocode_run_state` with its writer and
  readers.
- **View and schema:** `autocode_run_view.view`, `docs/schemas/status-view.schema.json`,
  `docs/task-run.md`.
- **Project config and flags:** `stage-policy` and `sensitive-paths` in `autocode_project_config.FILE_ONLY`; `--full-pipeline` as a start flag in `autocode_decision_flags`.
- **Consumers needing focused checks:** the terminal chat, the VS Code client, the
  conformance client (`scenarios/harness/conformance.py`) and the dashboard.

## Questions for the second owner checkpoint

Each with the recommendation this record makes.

1. **Company preauthorization of a task class?** Recommended: not in the first slices;
   every path keeps the person's approval. If wanted later, it is its own approval actor
   bound to the policy digest and contract token, refused on runs with `require-approver`.
2. **May a held run move to a newer policy revision?** Recommended: no. A new policy
   applies to new runs only, as a project file does today.
3. **Resuming under different prompts: record or refuse?** Recommended: surface it. Each
   stage record already stores its launch prompt set; the status view should say when it
   differs from the run's original set. Refusing would strand runs on every prompt edit.
