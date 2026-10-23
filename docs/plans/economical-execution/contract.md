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
- `prompts_hash` is recorded at launch but never compared on resume, so a saved run
  resumed under a newer install launches with the new prompts.
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
    "policy": {"id": "default", "revision": 1, "source": "project", "fixed_at": "2026-11-01T10:00:00Z"},
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

## Named actions

Policy actions follow the existing pattern: the view names an argument array; clients
take it unchanged.

| Action | Where named | Kind | Unattended |
| --- | --- | --- | --- |
| Approve the displayed contract | `needs.token`, `next_command` | Existing decision | Refused |
| Move a held run to a newer policy revision | `stage_policy.next_command`, only at an idle, reconciled boundary | New decision flag | Refused |
| Opt a new run into a policy | `--stage-policy ID` | New start flag, fixed at launch | Allowed |

A stale action fails with the current offer, as a stale approval token does today.

## Saved-run compatibility

| Saved run | Under a newer install | Behavior |
| --- | --- | --- |
| No `stage_policy` recorded (G1) | Any | Full pipeline; never adopts a shortcut |
| Policy revision 1 recorded, install has revision 2 | Resume | Keeps revision 1, as a saved run keeps its adaptive-planning setting |
| Policy changed while the run is held (G7) | Explicit move | Only at an idle, reconciled boundary, with a recorded event bound to the contract hash; an approved plan whose path changes needs a new approval |
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
| G7 policy changes while held | The explicit move action above |

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
- **Flags:** `autocode_decision_flags` for the two new flags.
- **Consumers needing focused checks:** the terminal chat, the VS Code client, the
  conformance client (`scenarios/harness/conformance.py`) and the dashboard.

## Questions for the second owner checkpoint

1. Is company preauthorization of a task class wanted at all? If so, which classes, and
   is it recorded as its own approval actor bound to the policy revision and contract hash?
2. Should a held run ever move to a newer policy revision, or should a new policy apply
   to new runs only?
3. Should resuming a saved run under different prompts be recorded or refused? Today it
   is silent.
