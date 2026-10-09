# Retained verification output (#806)

Successive Analyst probes and report repair reused `scratch-command.log`.
Their supervision records were separate, but later commands truncated output
still referenced by earlier records. A real-model run exposed two invalid
output hashes even though its recorded processes had all exited.

Each command now captures output in an exclusive file beside the requested
log. Admission, checkpoint and result bind that capture. Callers read the
returned output path. The requested path stays untouched because old runs may
still cite its bytes; writing a latest-output copy there invalidates old pins.

A regression runs two actual commands against one requested log and verifies
both retained captures, receipt hashes, admissions and checkpoints while an
existing legacy log remains unchanged. The live failure is preserved; fresh
multi-probe model qualification is required separately.
