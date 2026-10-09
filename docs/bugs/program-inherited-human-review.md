# Inherited human-review guidance

Issue #814 was exposed by an actual OpenCode program using Codex Luna, Astra
and Sol. Its approved health criterion had `human_review: false`, while the
generated brief told every inherited criterion to preserve `true`. The Plan
Reviewer identified the contradiction and the Planner paused for clarification.

Workstream briefs now tell models to preserve any true review obligation
present in the inherited definition. This agrees with the existing inheritance
guard, which still rejects removing an explicitly required true flag. False or
omitted flags do not create an additional human-review obligation. Each child
plan still requires its own approval.

Pure checks cover false, true and omitted flags alongside inherited journeys,
and confirm that the guard continues protecting explicit true obligations.
