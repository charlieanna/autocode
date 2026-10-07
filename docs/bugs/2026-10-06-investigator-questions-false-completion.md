# An unresolved investigation was marked complete

A live bug-fix run on 2026-10-06 stopped after workflow recognition and the
Investigator. Its provider could not launch the configured tool host, so no
repository command or test executed. The Investigator accurately reported
`not_reproduced`, described the setup failure, and asked for the host to be
restored. AutoCode nevertheless exposed `TASK_COMPLETE` and `done: true`.

The setup failure invalidates that run as live qualification evidence. It also
revealed a separate deterministic defect: every `not_reproduced` report ended
the task, including reports with unanswered questions. This path bypassed the
build completion gate because a negative diagnosis is a separate job outcome.
Both compared revisions had the same transition; there was no evidence that
the Investigator imported or exercised either version of the target project.

The evaluator had copied the Codex executable and ripgrep while omitting the
required `codex-code-mode-host` companion. Its replacement preserves the entire
installed platform layout, including resources and helper binaries. Offline
startup checks passed; they do not establish live tool execution.

An unreproduced report with questions now waits for reproduction context.
The existing AutoResolver publication and answer APIs retain the original
report hash and accepted stage, issue ordinary questions with no delegated
default, and return to the Investigator after the last answer. The next
handoff includes authenticated saved answers and the prior diagnosis. A report
that repeats an already answered question is rejected instead of entering
Requirements discovery. A negative diagnosis with no questions retains its
existing completion behavior; it does not claim that a code fix was built.

Regression coverage drives this through the public TaskRun interface:
pause with `done: false`, answer without launching a model, republish remaining
questions with fresh tokens and unchanged report pins, resume investigation
with saved context, and reject stale or altered report evidence. The new public
regression fails on the compared master revision, which completes instead of
waiting. Focused tests also preserve a question-free negative diagnosis and the
reproduced-bug path.

Report validation also rejects duplicate question text after trimming surrounding
whitespace. Index-based question IDs otherwise give those duplicates separate
identities, so answering one would leave the same question pending again.
The check rejects the report before writing its note or publishing questions.
