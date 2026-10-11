# Remaining false PASS cases in the base-definition proof

PR #910 fixed G1, G3 and G6 of #652. This follow-up addresses G2, G4, G5
and G7. The subsequent G9/G10/G11 work is described below.

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


## False refusals and redundant work (G9, G10, G11)

The definition now starts from the suite command's package scripts, including
lifecycle hooks, nested script calls, package-directory flags and workspace
runs. Unrelated start/build/deploy scripts do not make a valid change
unverifiable. Opaque task runners and unparsed workspace options keep
conservative script roots. Literal template imports are distinguished from
computed imports. When a runner's closure is incomplete, a change confined to
known product code and retained test/definition paths can still be tested;
a changed input of unknown role remains UNVERIFIED.

The additional proof's package manifests retain base scripts, hooks, workspace
membership, configuration and custom fields while using the candidate's
`imports`, `exports`, `type`, `main` and `module` loading fields. This lets the
candidate's implementation load under the original tests. A new manifest may
supply only those loading fields; a deleted or linked manifest stays on base.
A changed loading map that also routes a runner or its configuration is
UNVERIFIED, because applying it could change which old tests execute.

The extra suite is omitted when a regression failure is already established,
for a plain known non-JavaScript runner without package/JavaScript inputs, or when its
retained inputs are unchanged. Added or edited tests still require the extra
run: an added test can exit an in-process runner before old tests execute.
Unknown runners and shell wrappers retain the check, including unusual JavaScript
file layouts without a package manifest.
Effective-base contents are read lazily and cached through one Git batch
process, including when a reviewed patch is applied.

`tests/test_definition_refusals.py` covers the positive changes, redundant-run
counts, unchanged selectors, nested hooks and the negative loading-map and
early-exit controls with real commands. `tests/test_suite_definition_policy.py`
checks package selection and field merging. The catalog scenario
`feature-node-package-loading` drives the full CLI through a feature that adds
both package maps while retaining unrelated deployment scripts. Its oracle
checks the original guards, public APIs, named regression cases and real module
resolution; its broken controls include wrong behavior and vacuous tests.
