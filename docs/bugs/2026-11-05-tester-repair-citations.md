# Tester repair needs the enclosing proof citation

The live ten-milestone LedgerLab run reached a combined B/C review with 40 passing
project tests. The Tester correctly rejected two normalization gaps whose guards
had no delivered assertion, but cited individual mutation logs and raw coverage
files. Those paths are outside changed-code reason admission: the enclosing
runner proof, authenticated checks and inventoried source/test files are accepted.
The report-only repair had the error and rejected draft but no explicit enclosing
proof reference. One repair emitted a shell command despite having no tools; the
next JSON report retained the invalid citations. All attempts remain archived.

The repair handoff now names the current source's enclosing proof and explains
which references to use. It explicitly preserves rejections and missing evidence,
forbids invented execution, and retains ordinary source/check/citation admission.
A missing or stale proof produces no citation context. This changes no evidence
gate, application source, model route, limit or approval.

A public-CLI scripted regression introduces an invalid nested proof citation. On
unchanged master e436ff47b, both repair attempts fail and the run remains stopped
(10.101 seconds). With the fix, one repair accepts the enclosing proof and the
first milestone advances without another Builder. The dependent work remains
incomplete. The existing repeated fan-out/join and exact-batch repair checks pass.

Supported Linux focused qualification: 83 tests in four modules pass in 110
seconds (114.198 seconds including snapshot/package setup). Controller tests
cover Tester and checkpoint repair, retained FAIL/reject content, stale/missing
proofs and non-Tester stages. Existing changed-line admission tests still reject
unauthenticated references. Ruff check/format and git diff --check pass.

Evidence is retained under `.scenario-runs/orchestrator-heavy-live-20261105/`:
`citation-base-cli`, `citation-candidate-focused`, `citation-build-identity.json`
and frozen base/candidate archives. Candidate archive SHA-256:
`7d7bfab3f013b2d7d4593ecc0dae7fdcd3cd14544aa6df5340372e4571c303c4`.
The required changed-test selection passes: 518 tests in 27 modules, four at a
time, in 280 seconds on supported Linux (`citation-changed`).
The live run still needs independent B/C acceptance and the remaining waves;
this regression is not evidence that the full live workload completed.
