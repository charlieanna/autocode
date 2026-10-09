# Breaking AutoCode's guarantees

This suite attacks the controller through its real CLI. It asks whether AutoCode
can falsely complete, lose accepted evidence, repeat work after a crash, bypass
approval, or silently replenish a spent budget. It reuses the greeting catalog's
seed, reference and independently checkable behavior, so failures are about the
workflow rather than the difficulty of the application.

The provider is scripted. Faults alter its reports and actual command choices,
interrupt owned processes, or fail an OS write in a disposable project. No live
model is called. No AutoCode runtime module is imported or patched, and private
run state is never fabricated or edited. Investigator probes may read evidence
explicitly exposed in their provider handoff.

| Group | Attacks and controls |
| --- | --- |
| Evidence | False exit claims, replayed events, substituted commands, wrong contract/task, edits after validation, vacuous passing tests, self-authored receipts, undeclared writes; honest completion control |
| Recovery | Truncated reports, exhausted repairs, valid/missing/uncited Investigator inputs, permission interruption after accepted validation; successful repair and citation controls |
| Lifecycle | Concurrent continuation, live and completed orphan providers, killed workers, explicit abandonment, stale/replayed approvals, persisted token limits; successful ordinary and resumed builds |
| Persistence | Sustained disk-full/I/O failure, transient write failure, and controller death immediately before/after atomic approval replacement; exact approval-token and single-build checks |
| Planning | Missing test-package scaffolding; bounded correction before approval, or refusal before any Builder |

Run from the repository root with the project's virtualenv interpreter and process
inspection permission:

```sh
.venv/bin/python scenarios/adversarial.py --list
.venv/bin/python scenarios/adversarial.py --jobs 4
.venv/bin/python scenarios/adversarial.py --group evidence recovery --jobs 2
.venv/bin/python -m unittest -v scenarios.test_adversarial_lifecycle
```

Each execution retains a fresh directory under `.scenario-runs/`, including test
outcomes, full assertion errors, public CLI output, provider injection traces,
source hashes and cleanup receipts. `--out` must name a new directory. The runner
returns nonzero for failed guarantees, errors, skips, or source changes during the
run. There are no expected-failure exemptions. These tests are a separate
diagnostic gate: known product defects remain visibly red.

A caught attack counts only when the test proves it reached the intended stage.
An early launch blocker or broken positive control is not successful protection.
Review failed assertions against their injection evidence before labeling them
product defects. Positive controls demonstrate that simply refusing every run
cannot pass the suite. No model-quality conclusion follows from scripted attacks.

Signals target retained test-owned process identities. FIFO handshakes coordinate
crashes and concurrent continuations; the tests do not guess timing with fixed
sleeps. Storage hooks match one exact checkpoint path and exist only in the
test's environment. Other AutoCode runs and their state are untouched.
