# Provider report overwrote its own transport log

An OpenCode reviewer treated the raw stage `.jsonl` evidence path as its report
output and opened it with `w`. The runner was still recording stdout through
its existing descriptor. Earlier events disappeared, later writes left NUL
holes, and the stage stopped without a trustworthy completion event. The
Planner backend paused, which kept its dependent dashboard integration waiting.

The runner now creates raw event files exclusively with owner-read permission,
while retaining the writable descriptor inherited by provider stdout. A normal
file write through the published pathname fails instead of truncating evidence.
The provider instructions explicitly identify this path as read-only input and
require the final report in the assistant response. Existing logs and symlinks
are refused rather than reused.

A subprocess regression attempts the same overwrite, then emits a complete
OpenCode response: the overwrite is denied, all events remain parseable, and
the terminal report still loads. Additional checks preserve interrupted output
and reject existing log paths. This protects against accidental writes; it is
not a sandbox against a process deliberately changing permissions or replacing
files under the same OS user.
