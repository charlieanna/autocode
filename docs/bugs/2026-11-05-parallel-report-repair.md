# Combined Builder report repair, 2026-11-05

The ten-milestone LedgerLab live qualification exposed a report-repair handoff
defect on runtime `5201aaa82fec933b52feeae262390b75a6515574`. The Orchestrator
integrated independent Builders B and C, but report-only repairs were not told
which milestones belonged to their combined validation task.

## Observed failure

The live run is
`20261105-163655-build-ledgerlab-a-substantial-offline-event-anal-868a9cec`.
A had already been accepted. B/C's six delivered files matched their worker
manifests and the integration checkpoint. Their delivered suite passed 40 tests;
the independently prepared A/B/C oracle passed nine groups, with a supplementary
Unicode timestamp group also passing. These checks did not grant completion.

After the first combined Tester timed out, the Completion Reviewer proposed an
internal batch ID as a milestone. Its repair selected approved milestone B but
narrowed its prose to B alone, while the actual task still required B and C.
The fresh Tester omitted C. The core correctly refused the incomplete inventory.
Its first report repair then included A, B and C, and was also correctly refused.
The next repair had neither tools nor an inline current batch inventory; it
returned non-JSON prose proposing to inspect run state. Bounded repairs exhausted
and the Investigator took over. No incomplete batch was accepted.

The redacted failure bundle's manifest SHA-256 is
`2a422ca86ccc09a5cf6c6c2e6c31e46c8c478baf60d547d391113f7b7a424ebf`.
The bundle and failed attempts remain under the ignored local directory
`.scenario-runs/orchestrator-heavy-live-20261105/`.

## Change and regression

Report-only Tester and decision-stage repairs now receive the current task's
exact milestone inventory, approved primary milestone and acceptance criteria.
The instructions preserve the combined scope and failed or missing evidence;
they do not authorize an overall PASS without passing evidence for every member.
Existing exact-set, evidence, source and approval gates are unchanged.

A controller regression verifies the actual repair request for five stages.
All five subcases fail without the handoff and pass with it. The context module's
38 tests pass. A supported-Linux CLI regression builds and accepts M1, builds
M2/M3 concurrently, and deliberately omits one member from their combined report.
It requires one repair of exactly M2/M3, without rebuilding or adding accepted
M1, and verifies that the undelivered dependent M4 still prevents completion.
The unchanged base fails that regression through the same bounded repair
exhaustion as the live run (28.882 seconds).

The candidate Linux changed-test selection ran 725 tests in 36 modules (968
seconds): 34 modules passed, including both graph tests (198.1 seconds). The
architecture module lacked the installed package; this also failed on the base,
then passed all 19 tests with the candidate package installed (30.884 seconds).
The report-delivery module initially failed a Git verification-copy command.
Both tests passed on the base (76.801 seconds) and on the frozen candidate
extracted to the container's Linux filesystem (36.248 seconds). No runtime edits
were needed for these two reruns. The original failing selection is retained,
rather than represented as a single green run. Ruff check and format passed.

The frozen candidate source archive SHA-256 is
`0fca2f5945e31cc4526fe1362a13a6c68ca1aa539bea539dc9ed4dd39c69ecb2`;
its base is `4813c890e77fe12e065cedd8dc50a89d7b8c754c`. Only this evidence
note was updated afterward. The resumed live qualification is still in progress.
This record does not claim that the heavy workload completed.

## Separate limits

The first combined Tester received a roughly 2.35 MB prompt, largely changed-code
proof receipts. It ran for 2,235.7 seconds before the idle watchdog stopped it.
This change does not reduce that payload or establish acceptable review latency.
The later Tester also identified two missing behavioral assertions in the
delivered B tests, despite the independent oracle passing those inputs. Those
evidence gaps still require Builder repair and independent review.

Live providers use Kilo's portable macOS path. Linux fake-provider checks are
separate evidence and do not qualify live Linux provider execution.
