# A failed Git read made a saved chat appear missing

During live VS Code verification, the original chat handle intermittently
reported that its task worktree was missing or belonged to another project.
The directory and Git identity were intact, and retrying the read succeeded.

Task-worktree resolution caught every `ValueError` and `OSError` from metadata
inspection as a negative identity result. That included a failed Git query,
which establishes neither absence nor different ownership. This also affected
the original-handle readers used by status, event streaming and evidence export.

Worktree metadata now distinguishes an unreadable Git identity from a negative
identity check. Resolution preserves that inspection error. The CLI reports the
read failure without claiming the chat disappeared, and VS Code keeps its
existing read-error behavior: retain the conversation, block stale actions and
retry status reads. Neither ownership checks nor execution admission change.

Regressions fail on the original implementation for Git failures in either the
project or worktree, including command and OS errors. They check both original
handle resolution and resume from the project. A read-only CLI status regression
checks the error visible to a client, exit status, absence of a traceback and
preservation of the saved record. Existing wrong-project and wrong-run checks
still reject those targets.

An actual VS Code chat was checked with a controlled failure of the Git identity
query during read-only status. It showed the inspection error, retained the
conversation and unsent draft, and kept focus in the composer. Restoring the
normal status reader cleared the read error on the next poll, preserving the
draft and the run's original result. No model or recovery action was started by
the fault check.
