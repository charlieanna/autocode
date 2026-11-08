# Planner revision refusals identify the failed check

A public planning recovery reported `Plan revision reference is stale, malformed or
not a Planner revision`, then accepted a rewritten report. Seven different admission
checks produced that same error. The public message did not identify which check
failed, and the later generic “Its work stood” message did not establish that only
metadata changed. This change adds evidence for future refusals; it does not repair
the report or claim a reduction in model requests, tokens or latency.

The existing error prefix stays intact. A fixed suffix identifies the first failed
check, in the same order as before:

| Suffix after `plan_revision_reference:` | Failed check |
| --- | --- |
| `wrong_stage` | The owning stage is not `glm_revise`. |
| `current_reference_unavailable` | The core has no current contract token. |
| `reference_mismatch` | The reported reference differs from the current token. |
| `saved_contract_unavailable` | The saved predecessor body is not an object. |
| `unexpected_reference_fields` | The reference has an unoffered top-level field. |
| `changes_not_object` | The proposed changes are not an object. |
| `unexpected_change_fields` | The proposed changes include an unoffered field. |

For example, the public recovery problem can end with
`[plan_revision_reference:unexpected_reference_fields]`. The suffix contains no
reported field names or values, stage text, token, hash, path or model prose. The
existing failure accounting retains the error and the existing `recoveries.problem`
projection displays it; there are no new state keys, status fields or actions.
Diagnostics grant no authority. The accepted-reference path, criteria and trace
checks, protected-change receipts, independent review and exact-plan approval are
unchanged. Stale and foreign concrete references remain refused.

## Earlier observation: UNAVAILABLE

The earlier failure has no recorded branch reason. The source-only audit did not
open its private reports, prompts, events or state, and no trusted original-context
classification was supplied through the public interface. The repair queue pins
the original schema and report artifacts and records a contract hash, but does not
record these seven predicate outcomes or an atomic predecessor-body snapshot
alongside the error. Whether retained historical inputs could authenticate every
needed original fact has not been established. The current post-repair contract
cannot supply that missing evidence. This patch deliberately does not reconstruct
or guess the old reason; its classification remains **UNAVAILABLE**.

## Deterministic evidence

The new unit regression exercises all seven reasons with privacy canaries, confirms
first-failure order and short-circuit safety, and rejects stale, foreign and alias
strings. Existing exact-preservation, trace, criteria-authority and coverage tests
remain in the focused checks.

A public CLI fixture supplies one extra reference field, then repairs its own
report. It checks that the public recovery displays only the fixed reason, the
rejected field/value and contract hash are absent from recovery prose, the run
stops at exact plan approval, and a stale approval token is refused. Separate
public CLI cases retain refusal of stale references, protected edits and dropped
criteria. These use the offline provider on supported Linux with Python 3.12;
they establish diagnostic and gate behavior, not real-model effectiveness.
