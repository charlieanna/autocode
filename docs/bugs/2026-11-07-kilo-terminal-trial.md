# Bounded Kilo terminal trial: correct candidate, unfinished delivery

The laptop trial used runtime `2393d6de07095fa0a9c2684dd3e57b884ce46f6c`, run
`20261107-014240-chat-2d3e9e`. Its redacted bundle manifest SHA256 is
`a3f90580f51f5e76e022340fc0553bead3bec7c2a509d1a9946f3ef596dcbe94`.

The fixture's `double(n)` returned `n + 1`. The request was integer doubling,
three failing unittest cases, the existing input-1 guard, and no dependencies.
An external check of the retained candidate passed all four tests and 2,007
integer cases, including large positive and negative integers, without bytecode
writes. The workflow remained incomplete.

## Reached stages and limits

Real Kilo stages used GLM-5.3 for Planner, Tester and Completion Reviewer, and
MiMo-V2.6-Pro for Plan Reviewer and Builder. Corrected stages ran at high effort;
the small-task Plan Reviewer recorded medium under its high ceiling.

The Builder exceeded its 600-second limit while preparing its report. Its
partial stream repeatedly discussed command/report formatting; that observation
does not establish a latency root cause. Core verification ran and the Completion
Reviewer required further independent validation. The original wall watchdog
interrupted that Tester after approximately 44 minutes wall time and 22m56s of
recorded model-attempt duration. Public liveness confirmed every owned process
stopped with no cleanup error. Reopening reconciled the run to
`PAUSED_STAGE_ABANDONED`, with all 17 attempts retained and no new dispatch.

Known usage was 675,358 input and 64,383 output tokens. Accounting was incomplete;
actual charges and subscription-quota deductions were unavailable. Historical
price estimates are not invoice evidence.

## Harness failures and signal diagnosis

The first capture helper wrote inside the fixture during read-only review;
AutoCode correctly refused that result. Importing `tests.terminal_screen` also
ran the test package's intentional configuration isolation. Passing the resulting
environment into live chat hid the saved stage choices and selected fallback
routes. Capturing the real environment before importing the helper corrected
the same run. No-dispatch comparisons preserved the saved GLM/MiMo routes with
and without role pins. Global credentials and preferences were unchanged.

The final stop sequence printed `Operation not permitted`. The CLI retained no
traceback, so its exact kernel-denied signal cannot be identified from this run.
Provider-free regressions independently demonstrate two client defects in
`ChatClient.advance`: a denied child/group signal escapes as `PermissionError`,
closing observation, and a late stop key signals an already-exited child's old
group. The focused correction checks for exit first and reports a refused signal
while continuing to observe the owned child. This requires no host permission
change and does not change provider cleanup or completion authority.
