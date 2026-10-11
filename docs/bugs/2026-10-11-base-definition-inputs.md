# Remaining false PASS cases in the base-definition proof

PR #910 fixed G1, G3 and G6 of #652. This follow-up addresses G2, G4, G5
and G7; the issue's false-refusal and performance cases remain separate work.

The additional suite run must execute the base's test definition against the
candidate's product code. Four gaps let it report PASS for broken behavior:

- Its scratch tree lived below the candidate, so Yarn or pnpm could inherit
  candidate configuration that disabled test execution. It now runs in an
  owned temporary directory outside the project. A candidate-local temporary
  directory is refused, and cleanup covers the actual directory used.
- A configuration symlink was retained without retaining its target. The
  definition now follows links from the effective Git tree, including chains,
  directory links and missing targets. External or cyclic links cannot provide
  a bound proof; replacing a retained input's parent is also refused.
- A zero exit from the extra run hid missing or skipped passing-test identities.
  Its report now receives the existing completeness, collection and preservation
  checks. Missing evidence remains UNVERIFIED; observed regressions remain FAIL.
- Changes were measured against the raw base even when a reviewed patch applied.
  Product paths changed by that patch now receive the candidate's current
  contents or deletion, including reversions that disappear from the raw-base
  diff. The reviewed tests remain in the definition used to judge that code.

`tests/test_base_definition_proof.py` exercises the four cases through the public
verification and regression-proof functions using real Git, Node, npm, Yarn and
pnpm. A local reporter fixture supplies the per-test identities for G5. Controls
cover valid linked configuration, a valid review follow-up and incomplete reports.
`tests/test_suite_definition_inputs.py` checks link and scratch-directory bounds.
The existing T13/T14 fault controls verify cleanup at the actual proof location.
