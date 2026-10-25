# Investigator timeouts: the window was only a kill (#1902)

Date: 2026-10-25. Evidence: the live comments on #1902 (retained TUI chat `20261023-004044-chat-afb1d2`).

## What the live runs showed

- MiMo V2.6 Pro and Flash through Kilo hit the 600 s stage deadline on every Investigator attempt. One
  trace had 57 requests and 103 unique shell commands, none of them a reproduction probe.
- GLM-5.3 at Medium returned a report at 507.8 s, 92 s inside the window; the same model at Medium timed
  out a plan revision after nine minutes of reasoning, and at Low returned a report in time (#1941).
- Three prompt-only corrections (#1903, #1916, #1917) reached the live provider; none changed the outcome.

## Cause

The stage window was enforced only as a hard kill at its end. The runner already reads every completed tool
call (`ActivityMonitor`), but nothing checked, while the stage ran, whether the Investigator was trying to
reproduce the bug or only reading source. A model that explores instead of probing therefore used the
whole window and left no report. The exact retry then started with a pointer to the old prompt and no
statement of what had already been read.

## Change

`autocode_investigation_progress` classifies each completed call, in both provider event shapes, as
exploration or reproduction. After `limits.investigation_exploration_budget` (default 30) exploration calls
with no reproduction attempt, the supervisor stops the stage with `timeout_kind=progress`; the existing
timeout pause and retry question apply. The retry handoff gains `explored_paths`, `explored_calls`,
`reproduction_attempts` and `stop_kind`. See `docs/execution.md`.

## Not established

The default of 30 comes from two traces, not a calibration. Whether MiMo or GLM then converges on a real bug
within the window is a provider behavior that only a bounded live run can show; none was run for this change.
The lighter-variant answer for a timed-out job (the lever that worked at #1941) is not part of this change:
`--answer route-ROLE=MODEL` names a model only.
