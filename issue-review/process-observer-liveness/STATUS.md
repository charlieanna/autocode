# Process observer liveness

The run guard misclassified status when the source CLI placed `--run-dir` and
`--workspace` before `--status`. It also missed writers when macOS reports the
interpreter as `Python` or when an installed `autocode` entry point is used.
The guard now uses a strict argument-vector reader grammar and one interpreter
predicate; ps text alone retains its conservative leading-reader grammar.
Real writers, mixed mutating requests, and ambiguous paths remain blocking.

Master f5752a7 behavioral controls: a path-first status was refused; a real
macOS source writer and an installed-console writer were not detected. The
candidate passes all three and the remaining focused controls. A separate live
process matrix using an owned stand-in executable with genuine argument vectors
showed master blocked only the path-first reader while both versions blocked a
mixed mutation and writer. The exact identity of process 4491 in #4022's failed
host job is not known; this observation does not establish that it was a reader.

The focused guard suite (57 tests), Ruff and mypy passed. A deliberate concurrent
status-observer check in the actual VS Code Extension Host and current-head CI
remain to be completed before merge.
