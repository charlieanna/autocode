# An in-place proof copied ignored files the run wrote (#529)

An in-place run's regression proof copies git-ignored generated sources from
the candidate checkout into every scratch tree, including the base tree. A
file the Builder adds there can fail a test only on the base copy, so a test
the change breaks looks pre-existing and the proof passes.

A new in-place run saves `generated_sources_at_start`, the byte hash of those
files at creation (`autocode_run_setup`). The proof copies a file only while
it still matches. A file added or rewritten during the run is left out, and
the proof notes that. A run saved before the key existed copies none. A task
worktree still copies the project checkout's current generated sources.
