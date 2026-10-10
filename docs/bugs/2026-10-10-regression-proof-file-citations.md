# Regression proof citations must name files

A TypeScript Arena trial reached a passing runner regression proof, but its Tester
report cited `regression_proof PASS <source revision>` as a finding's evidence.
The report failed validation because evidence references must identify actual
project files. Report repair repeated the same error before investigation.

The Tester instruction told the model to cite the proof's verdict and source
revision as evidence. It now explicitly names the existing `regression_proof.path`
for `evidence_refs`, with the verdict and source revision described in the summary.
The handoff already supplies that artifact path; no new state or artifact is needed.
File containment, byte hashing, source freshness, runner proof authentication, and
missing-evidence rejection remain enforced.
