# A pinned reliability campaign declaration

[`arena/campaign.json`](../arena/campaign.json) is the **PLANNED** first
protocol slice of [#634](https://github.com/charlieanna/autocode/issues/634).
It declares 13 tasks and two repetitions each: 26 scheduled rows. It contains
no run results, and no campaign has been executed for this change. It is a
measurement specification, not a command accepted by the current Arena or
scenario CLI. Automatic execution, result ingestion, portfolio reporting and
fresh holdout rotation remain separate work in #634.

The tasks include the three hard Python Arena cases; the existing bug-fix,
feature and greenfield scenarios; the transactional import, ledger, durable
queue, outbox and event-replay ladder; interrupted verification reuse; and a
review followed by a fix in the same run. Verification reuse is explicitly
**fixture** evidence because its interruption comes from the scripted fault.
Other tasks declare live execution. Their results must be reported separately.

## What is pinned

The source revision fixes the complete AutoCode and evaluator inputs. The
manifest additionally records the selected profile, policy file hashes, full
upstream base/reference commits and checks, and each task's entire definition
tree, including briefs, seed, reference, broken controls and hidden tests.
The `build-comparison` profile is copied from the existing profile definition;
its existing role keys, models, efforts and extra options are preserved.

A definition hash is SHA-256 of compact ASCII JSON containing sorted rows
`[repository_relative_path, git_mode, sha256_of_blob_bytes]`. Symlinks use
their Git blob bytes and `120000` mode; they are not followed. `files` counts
the rows. Tests read the local Git objects at the declared source revision,
so later catalogue/profile changes do not rewrite this protocol's inputs.
The manifest can be tested without importing an oracle, preparing projects,
fetching upstream repositories or launching a provider:

```sh
.venv/bin/python -B -m unittest -v tests.test_reliability_campaign
```

The declared Python, psutil, native provider and package versions are future
admission requirements, not evidence that those environments were qualified
for all tasks. Before any attempt, capture and freeze its installed project
and test dependencies and the actual runtime identities. Verify the original
failing and passing controls, preserve all setup failures, and obtain the
normal live-spend/approval authorization. The current Arena option whitelist
does not consume this profile declaration; a future executor must prove its
actual configuration matches, rather than assume a profile name applied it.

## Limits and attempt accounting

Scenario `max_steps` and harness wall time come from their original catalogue
definitions, including the 90-minute ISO-week case and five-minute interrupted
verification case. There is no blanket timeout override. Arena keeps the
documented 3,600-second **per CLI invocation** timeout, 120-second oracle bound
and three-iteration cap. These are not a 3,600-second total campaign ceiling;
this declaration adds no total campaign ceiling or budget refill.

Freeze the manifest hash before admission. Every schedule ID must appear
exactly once in the published attempt ledger, including failures, errors,
skips, unexercised or interrupted work and unattempted `DEFERRED` rows. Keep
the native status/verdict, actual models/efforts, independent oracle evidence,
elapsed time, interventions and spent budget. Unknown usage or cost stays
`null`, never zero. Publish failed comparisons beside successes; do not replace
a failed repetition with a successful retry. A new attempt needs a new declared
schedule, with the original evidence retained.

AutoCode saying complete alone is not delivery proof. Preserve the existing
Arena and scenario verdicts: an Arena `STOPPED` result does not by itself prove
that stopping was appropriate. Fixture passes do not qualify a live version;
incomplete attempts block comparison and no result automatically promotes code.
The Arena holdout split consists of **public historical** cases, not private
or contamination-free holdouts. This finite protocol makes no statistical
reliability or model-effectiveness claim.
