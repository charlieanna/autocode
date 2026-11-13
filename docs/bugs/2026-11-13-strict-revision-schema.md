# Sparse Planner revisions contradicted their generation schema (#3407)

A Kilo greeting pilot found a Core protocol error. The Planner was told to
return `changes: {}` and omit unchanged engineering fields. The strict schema
actually sent to it required every engineering field inside `changes`.
Canonical-schema tests passed without exercising that transformation.

## The contradiction

`autocode_plan_revision_reference.model_schema` offers an exact-bound reference
to the current contract, with optional engineering changes. `autopilot` pins
`model_output_schema(request.schema)`. That adapter makes every object property
required for structured generation, including `deliverables`, `end_to_end_flow`,
`technical_approach`, `milestones` and `initial_task` inside the reference.

The actual `v3-revise_plan.json` pin has SHA-256
`4cf45d58d221eee7290c8487777f92f357aaaee2e6b0b20a0e519dfa715bf266`.
The prompt telling the Planner to omit unchanged fields has SHA-256
`46deab540a4de8936d5f3c8ef886754b3fc4ccf36ba080818f97542aea56d12a`.
Both came from runtime `5e4c870a76645b15e9deb7204eb09f9c6c55a556`.

An offline reproduction composes the canonical schema, strict generation
adapter and reference expansion. An empty change object passes the canonical
reference and preserves the saved contract exactly; the resulting body passes
ordinary domain checks. The strict reference rejects the same change object
for missing fields. This is a Core contradiction even though the transcript
alone cannot establish why the model chose each particular value.

In the live run, subsequent repairs filled unchanged fields with empty lists.
Those are explicit replacements, so they deleted retained deliverables and the
end-to-end flow. The domain checks correctly refused both. The Investigator
identified the destructive delta and directed a minimal citation-only patch;
that repair passed without weakening admission.

## Correction and permanent check

The reference generation schema offers `null` for unchanged engineering fields
and for its optional criteria and trace-reuse fields. A strict response can
therefore provide every required key while expressing omission unambiguously.
Normalization removes those null placeholders only where the actual pinned
reference schema advertises them, before metadata processing can expand the
reference. The existing expander and domain guards then check the canonical
report. Raw reported output remains retained.

Old pins keep their old instructions and validation. Full contract nulls,
explicit empty-list replacements, false trace-reuse flags, stale references
and protected-field edits keep their existing checks. No generic JSON Schema
validator, approval rule, model route or budget is changed.

The regression composes the strict generation schema with pinned report loading
and ordinary expansion, including a real criteria delta and a supplied trace.
It covers no-op and partial changes, old pins and the refusal controls. Testing
that complete boundary would have caught this contradiction when sparse
revisions were introduced; testing only the canonical schema did not.

## Live evidence and limits

This was a baseline investigation, **not live verification of the correction**.
The run is
`20261113-120525-build-a-deterministic-greeting-cli-named-greet-p-4177873d`.
Its diagnostic `autocode bundle` manifest SHA-256 is
`cdb34d9b4fe31b4197fefb5ea066d6486657bb7ff084068dcb41b7c96c4f972f`.
The bundle omits replay recording with the message that concurrent Builder
dispatch is unsupported; it is diagnostic evidence, not a qualified replay.
There were no Builder attempts.

The Linux ARM64 fixture used Kilo 7.8.3 and the owner's saved choices:

| Reached work | Model | Effort |
| --- | --- | --- |
| Requirements, Planner and its report repairs | `zai-coding-plan/glm-5.3` | low |
| Plan Reviewer | `xiaomi-token-plan-sgp/mimo-v2.6-pro` | high |
| Independent Investigator for the stuck Planner | `xiaomi-token-plan-sgp/mimo-v2.6-pro` | high |
| Acceptance-test authoring before implementation | `xiaomi-token-plan-sgp/mimo-v2.6-pro` | high |

The external scenario driver started `greenfield-greeting-cli` with a passthrough
saved-stage profile, `--spec-first`, `--max-cost-usd 0`, a 1,800-second active
allowance and a 600-second stage bound. Continuations used the public CLI:
`--answer`, `--feedback`, `--resume-paused`, exact-token `--approve-goal`, and
`--no-chat`. Requirements and exported plan revision 5 were actually reviewed
under the owner's agent delegation; the approval does not claim personal owner
review. The plan's goal hash is
`f2f8868f310ed79646e1dfb4e69975fd817342cd91f05f4e2cdda61808de8394`.

The first malformed draft needed a 338.189-second repair. The later revision
had two rejected repairs (98.560 and 60.780 seconds), a 426.597-second
Investigator, then a successful 68.370-second repair. Both original syntax
failures remain retained; no incomplete JSON was accepted by adding delimiters.

The test driver also had setup and argument errors, including an unsupported
`--as` on `--answer`; the accepted Requirements result was retained while the
driver was corrected. Later model-free bootstrap mistakes were corrected
before task actions. All failed attempts remain. Wall time includes these
repairs and agent review waits, so it is not a model-only latency measurement.

One public boundary pause preserved the pending work before an operator extended
the active allowance from 1,800 to 3,600 seconds. The cost cap remained zero,
the planning-call cap remained 20, and routes and stage efforts were retained.
At the fixed continuation fixture bound, the public Stop now action ended the
still-running acceptance-test author. Its 435.321-second attempt is retained.
The stopped container's collection is a coherent checkpoint at
`PAUSED_JOB_FAILURE`, with no active attempts.

The final record has 17 attempts and 30 observed provider requests, one of which
has no observed finish. Usage is incomplete. The known historical cost estimate
is $0.4503756; the complete cost and provider invoice are unavailable. No
`greet.py`, `test_greet.py` or `README.md` was delivered. This sample does not
establish completed delivery, current release reliability or a competitor
comparison.
