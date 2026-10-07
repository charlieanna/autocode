# An in-place proof copied ignored files the run wrote (#529)

An in-place run's regression proof copies git-ignored generated sources from
the candidate checkout into every scratch tree, including the base tree. A
file the Builder adds there can fail a test only on the base copy, so a test
the change breaks looks pre-existing and the proof passes.

The initial fix saved `generated_sources_at_start`, the byte hashes at creation.
The follow-up captures generated sources and ignored vendor inputs under the run
lock before prerequisites or providers start. `autocode_launch_inputs.record`
writes `launch_sources` once and pins a manifest of bytes and modes. Added ignored
files are omitted with a note; changed, removed, or damaged captured inputs make
proof unverified. Older generated-only records require a new run.

Clean checks and caches use the same inventory. Cached completion, recovered
reports, and artifact approvals recheck it before advancing; completed status
marks evidence stale after input drift. Separate task worktrees retain their
project checkout dependency policy.
