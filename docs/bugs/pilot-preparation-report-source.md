# Preparation reports changed source after plan publication

The preparation coordinator could write its report inside the project after
TaskRun had published a plan. That report changed the source frontier bound to
the approval. A subsequent status correctly rejected the old authority, leaving
the caller without the approval it expected. The ordinary in-project report
regression reproduces this on master.

The coordinator now chooses a fresh run destination through TaskRun's existing
public input and writes the final preparation report before starting the run.
The first source observation and the eventual approval include those report
bytes. A report outside the project follows the same ordering. Report-write
failure stops before any run or provider, and a report cannot overwrite a
declared immutable input. No source file is ignored and no approval is reused
after its source changes.
