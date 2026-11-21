# Scoped SemVer reference repair fixture

This reconstructs six JSON-interchange criteria from the retained SemVer audit;
it is not a replay of the original twelve-criterion task. It fetches the public
python-semver base `6adf8765f6e21910f1f0c13151ce84f32f8d431d`, then reconstructs the product files in a private reference copy using exact
source anchors and pinned fixture snippets. They derive from the retained local
checkpoint `78c461a4`; that checkpoint is not a public upstream ref. The
reconstructed source omits an unneeded module export list and redundant
negative-number guard, and the fixture checks all three product hashes.

The initial AC6 is deliberately weaker than the archival test. The scripted
Tester runs a real source-bound export mutation and the ordinary core must
request stronger tests. The strengthened test retains the archived assertions;
the repair prepends a subprocess import probe without deleting those assertions.
The diagnostic superset appends broad invalid-field checks to AC3; the other five
repaired functions remain unchanged. The `control-*.py` checks execute real
local tests or mutations and refuse unexpected source and test hashes. They do
not create proof receipts or waive surviving mutants.

The upstream project is fetched separately and remains subject to its own
license; these test artifacts capture the scoped regression scenario. The
fixture invokes only a scripted provider, with no live model credentials. The configured cost limit is zero, which means uncapped; the fake provider is what prevents live spend.

The scripted review's reconstructed trigger executes two independent controls:
one exposes the missing AC3 diagnostic assertion on an offered source line, and
one demonstrates the missing AC6 export assertion without inventing a line
binding. The writer strengthens both. The core must then observe the original
reference setup rejection, request and validate a preserving repair, and run
the normal proof. The actual editor test only passes on `TASK_COMPLETE` with
verified delivery; a pause remains a test failure with preserved diagnostics.

This fixture does not replay the full original task, evaluate GLM or MiMo, or
establish the security of another coverage collector. The retained checkpoint
revision is local audit source, not a public SemVer ref.
