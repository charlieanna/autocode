# Retained verification output (#806)

Successive Analyst probes and report repair reused `scratch-command.log`.
Their supervision records were separate, but later commands truncated output
still referenced by earlier records. A real-model run exposed two invalid
output hashes even though its recorded processes had all exited.

Each command now captures output in an exclusive file beside the requested
log. Admission, checkpoint and result bind that capture. The requested log
is an independent latest-output copy for callers that read it after execution;
it must not be used to authenticate an earlier command.

A regression runs two actual commands against one requested log and verifies
both retained captures, receipt hashes, admissions and checkpoints after the
latest copy changes. The live failure is preserved; fresh multi-probe model
qualification is required separately.
