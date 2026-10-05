# Preserve and acknowledge an active-time pause

Issues: [#378](https://github.com/charlieanna/autocode/issues/378) and
[#379](https://github.com/charlieanna/autocode/issues/379).

## Reproduced behavior

A real OpenCode run exhausted an explicit active-time allowance at a stage
boundary. After an informational response, a larger total was saved without
resuming. A bare resume correctly displayed the still-unacknowledged time pause.

Two subsequent commands exposed different bugs:

- Reasserting the saved total with `--resume-paused --max-seconds N` did nothing,
  although elapsed active time was below that total.
- Changing only `--max-stage-seconds` with `--resume-paused` invalidated the
  published request's settings binding. Persistence reinterpreted its display
  as a generic blocker, losing the time-pause origin, and admitted another real
  model call without acknowledgment of the total-time pause.

Changing the total and resuming in one command already worked on the tested
revision. That part of the older #378 report was not reproduced.

## Correction

A matching explicit total with remaining headroom acknowledges the exact time
pause, including when that total is already saved or its informational response
has been consumed. The response hold is evaluated after acknowledgment.
Unrelated settings retire and reissue the operational request under the new
settings, preserving its time-pause origin. Merely retaining a bare pause status
is insufficient because explicit budget flags bypass generic escalation.

This does not reset elapsed time, increase the total beyond the supplied value,
approve a plan, or acknowledge a permission or goal-change request. An exhausted
total still cannot admit a stage. Recovery advice describes reasserting a saved
total, including the explicit zero value that disables the time cap.

## Verification protocol

The disposable fixture starts a real model request under a one-second total so
that the first completed stage reaches the actual time boundary. It then uses
public CLI actions to supply information, save a predeclared 600-second total,
and change only the stage timeout from 300 to 240 seconds. Every negative control
must preserve accounting and launch no provider. Explicitly reasserting 600 is
the positive recovery control. Original reports, failures, counters and limits
are retained; checkpoints are never edited to manufacture a pause or pass.

Native model exports and source fingerprints are checked separately from CLI
status. CLI regressions additionally cover exhausted caps and unrelated request
scopes. The local validation record retains separate baseline, patched-run and
suite outcomes; a completed task requires fresh proof and source-bound replay.

The fresh patched live run completed the feature with nine verified criteria,
eight independent checks, unchanged original tests, and source-bound proof and
replay. Seven native exports confirmed GPT-6 Luna, GPT-6 Sol and GPT-6.1 Sol.
It used 465.061 active seconds under the planned 600-second total and retained one
Requirements report repair. All five negative controls launched no provider and
preserved elapsed accounting before the matching saved-cap acknowledgment.
