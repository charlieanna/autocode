# Process observer liveness

The run guard misclassified status when the source CLI placed `--run-dir` and
`--workspace` before `--status`. It also missed writers when macOS reports the
interpreter as `Python` or when an installed `autocode` entry point is used.
The guard now uses a strict argument-vector reader grammar and one interpreter
predicate; ps text identifies only possible runners and grants no observer
authority without authenticated exact argument boundaries.
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

The owned host fixture now holds a genuine source status command at its render
boundary while the real editor submits the named answer; it first holds a
separate synthetic writer and requires refusal. A local offline smoke confirmed
the status process reaches that boundary and returns public JSON after release.
The Linux editor-host run and its result are still pending.

The expanded guard, job-route, architecture and run-state selection passed 120
tests; the extension unit suite passed 784 tests with 22 skipped. The isolated
worktree's first extension unit attempt lacked node_modules and failed during
TypeScript loading; using the existing local dependencies corrected setup.

The first actual Linux editor-host attempt on a87c45afd confirmed the held
writer caused the named answer to be refused, but the editor did not display
the CLI's exit-2 error; it never reached the concurrent-reader phase. The log
is preserved at `/private/tmp/autocode-4031-a87-editor-failure.log`. That
independently discovered UI error-reporting defect is being fixed separately.

A further review found that even leading status text can be part of a renamed
runner's filename. The previous a87 guard fails the real renamed-writer and
missing-exact-argv controls; the tightened guard passes the focused tests.
Only exact process arguments can identify a reader; inaccessible argv remains
blocked. Actual-editor verification and exact-head CI remain pending.

Review also covered interpreter executables beneath directories with spaces.
For every process row referencing the run, the guard now first classifies its
authenticated actual arguments; without them, ps text only holds a possible
runner. The 64 focused guard checks and 127-test guard, job-route, architecture and run-state selection pass on these revised product bytes. Full Ruff and mypy pass.

The a87 Linux containment job separately reproduced the existing Rustup
`update-hashes` read-only failure in both write modes; the qualified #4024
fix must reach master before current-head CI can pass. The retained log is
`/private/tmp/autocode-4031-a87-linux-containment.log`.

The common entry parser also preserves the `chat` viewer across source,
console and module entry points without broadening status interpreter flags.
The new viewer control fails on 9cf8defe4 and passes on the candidate. Final
local checks passed 65 guard tests and 128 guard, job-route, architecture and
run-state tests, plus full Ruff/format and mypy. #4035's separate candidate
passed its Linux actual-editor refusal/retry test; it has not yet reached
master or completed its required CI.

The guard now distinguishes a process confirmed gone after `ps` from denied,
zombie or mismatched identity; only the confirmed-gone row is skipped. The same
fallback also recognizes the finite Codex and OpenCode runner verbs when their
executable paths contain spaces. The gone-reader, unknown spaced-console and
spaced-provider behavioral controls fail on 815ddfe20 and pass on the candidate.
Final checks for these product bytes: 70 guard tests, 133 focused tests, full
Ruff/format, and mypy over 993 tools files passed.

Integrated #4035's qualified editor refusal fix from master f5c47648: the
combined editor case preserves the visible refusal and unchanged token, then
holds a real status render during the accepted answer and checks its result.
The merged CI-selection consumers pass 240 focused tests. The integrated host
has not yet run; #4024's Rustup editor fix and the final required CI remain open.
