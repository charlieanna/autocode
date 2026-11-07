# C. Independent verification obligations (#1891)

Planning input for the first owner checkpoint on #1867, read with
[stage policy](stage-policy.md). Read-only audit of master `08ddeb2a3` (2026-10-22). Equal
quality on a shorter path stays unproven until [qualification](qualification.md) runs.

## What already exists

- **Independence pairs.** `autocode_verifier_independence.PAIRS` has exactly three
  pairs: Builder and Tester, Builder and Completion Reviewer, Planner and Plan Reviewer.
  Two routes clash when they share a model identity or family (GLM, MiMo, Claude,
  OpenAI). On a single-vendor engine (Claude provider, Codex engine) only an identical
  model clashes. `--single-model`, a new-run choice, turns the rule off. A clash pauses
  with `PAUSED_CROSS_MODEL`. **Tester and Completion Reviewer are not required to differ
  from each other.** Under the fixed defaults both run GPT-6 Sol, at high and medium
  effort.
- **The deterministic completion gate** (`autocode_completion`). A passing model report
  is not enough. Completion also needs: git delivery ready; protected oracles, brief
  evidence and risk evidence ready for the current revision; every replayed check
  receipt completed; every pinned evidence file unchanged; the regression proof
  complete; the validation bound to the current contract, task and source revision; no
  missing human review; no blocking finding in the validation, the decision or the
  findings ledger; every criterion verified with evidence; every check exited 0.
- **Command evidence.** `verify_checks` binds each cited check to an executed event or
  a hashed capture receipt. `require_executed_passes` refuses a PASS with no checks or a
  failing one. The check replay reruns every cited check and the approved plan's
  commands in a clean copy. A timeout, interruption or missing collection never
  authorizes a proof (`autocode_command_receipt`); uncertain ownership pauses with
  `PAUSED_VERIFICATION_UNCERTAIN`.
- **Test proof rules.** `docs/named-test-proof.md`: a `test:` case passes with the
  change and not before; a `guard:` case passes before and after; results come from the
  test runner, not an exit code. `docs/protected-tests.md`: tests that existed before
  the first model call are archived, and an edited original must still pass.
  `autocode compare-baseline` compares failure identities and grants no exception.
- **Regression proof** (`autocode_regression`): new or changed tests fail on the base
  and pass now; preserve cases pass on both. Bound to the source revision, case scope,
  contract digest and runtime identity.
- **Pre-build tests** run only with spec-first or blind tests. Spec-first is off for a
  plain CLI run and on in the `full` run mode, which is the only mode clients offer.
  The Builder may add lines to the Tester's test files but not change or remove them.
- **Human-only criteria** need a bound user receipt; a technical PASS with a pending
  human review completes only with that approval.
- **A combined pass already exists.** Under reviewer routing (`glm_first_v1`) the
  `astra_checkpoint` stage asks one read-only call for "independent validation AND
  milestone judgment" and returns both a validation object and a decision object
  (`tools/prompts/completion-checkpoint.md`). It runs on the Completion Reviewer's route,
  so the Builder pair still applies. It needs a user approval bound to the contract hash
  and refuses to run with milestone checkpoints on. No command-line action enables it
  today.

## Obligation matrix

What covers each responsibility now, and on the proposed combined pass. "Runner" means
deterministic code that no model output can satisfy.

| Obligation | Runner | Tester today | Completion Reviewer today | Combined pass |
| --- | --- | --- | --- | --- |
| Requested behavior per criterion | Check replay, named-test proof, regression proof, every check exit 0 | Per-criterion PASS/FAIL/NOT_VERIFIED with command evidence | Marks every criterion verified with evidence | `validation.criterion_results` plus the decision; the same gate reads both |
| Approved scope | Assignment scope, stray and package writes, git delivery | Not asked | "Never invent extra scope" | Same as Completion Reviewer |
| Bug cause addressed | Regression proof: the reproduction fails on the base and passes now | Judges the fix against the diagnosis | Judges completion | Same evidence; one judgment instead of two |
| Regression adequacy | Fail-before/pass-after; protected originals; behavioral-test check; mutation survivors reported, not enforced | Judges edge cases and failure behavior | — | Same; one judgment |
| Current execution | Clean replay; receipts; source-revision binding | Cites executed checks | Reads them | Same |
| Source review beyond tests | — | Reads the change and source | Reads the change and source | One read |
| Original request covered | Brief-evidence observations | — | `source_verdict` when the contract has a source trace | Same as Completion Reviewer |
| Unresolved findings | Findings ledger: only the reviewer that raised a finding resolves it, in a fresh report | Raises findings; only a later Tester report resolves them | Raises its own; cannot resolve the Tester's | One reviewer raises and later resolves every finding |
| Human-only criteria | Missing human reviews block | Reports pending human criteria | — | Same |
| Milestone and next task | — | — | CONTINUE, REWORK or COMPLETE | Decision object |

### What one pass covers and what it loses

The combined pass meets every **runner** obligation identically, because the
completion gate reads the same fields from either form. What it gives up is a second
model reading of the same change. Under the fixed defaults that second reading is the
same model family at a lower effort, so it is a second sample, not a different
checker.

Two gaps had to close before combining; the first is now fixed:

1. **`source_verdict` was not read by any gate.** #1913 now refuses a completion beside
   a FAIL verdict or an uncovered sentence and sends it back through report repair. A
   combined pass inherits that check. Whether one reader's verdict is enough for
   "original request covered" is still for the qualification to show.
2. **One reviewer resolves its own findings.** With separate passes a Tester finding
   can be resolved only by a later Tester report, so the Completion Reviewer cannot wave
   it through. In a combined pass the same reviewer raises and later resolves every
   finding: the ledger rule still holds, but the cross-check between two reviewers is
   gone. Proposed: a blocking finding raised in a combined pass is resolved only with a
   replayed passing check that cites the finding's reproduction.

### When a separate pass stays

- The sensitive class from [stage policy](stage-policy.md).
- Changes across interfaces or components, and any milestone run (the existing guard
  already refuses reviewer routing with milestone checkpoints on).
- Any criterion reported NOT_VERIFIED by the combined pass: a targeted Tester call
  runs, as reviewer routing already describes ("the Validator is a targeted escalation").
- A Builder attempt beyond the first.

## Pre-build test authoring

- **Keep it for bug fixes and new behavior** where it supplies the red half of
  red-then-green proof before the Builder can shape the tests.
- **Candidate omission:** when the Investigator's reproduction is a named test that
  already fails on the base, a separate `test:` case for the same behavior adds no new
  proof. Whether the Investigator's `test_cases` and `test_paths` are always runnable
  named tests needs checking before this is proposed as policy.
- **Builder-written or modified tests** are judged by the regression proof, the
  protected originals, the behavioral-test check and the untouched-lines rule. No
  change proposed.

## Failure handling

| Case | Today | Proposed |
| --- | --- | --- |
| Flaky check | No flake classification or retry; a non-reproducing check rejects the report, then bounded repair | Keep. A retry that turns a failure into a pass would hide defects. Record repeated non-reproduction as a finding for a person |
| Missing regression | Regression proof incomplete blocks completion | Keep |
| Infrastructure failure | Interrupted or timed-out commands never authorize proof; uncertain ownership pauses | Keep |
| Protected test edited | Original must still pass in a separate copy | Keep |
| Untestable finding | Stays open; a person decides | Keep |
| Human-only validation | Bound user receipt | Keep; never manufactured |

## Failure-injection specification for #1896

Each scenario names the existing coverage to keep and what a shorter path needs added.

| Scenario | Existing coverage | Added for a shorter path |
| --- | --- | --- |
| C1 plausible code, superficial tests, independent oracle fails | `hybrid-lazy-validator`; test-scenarios "lying builder" and "vacuous tests" | Same planted defect through the combined pass |
| C2 new regression passes on the buggy code or with the implementation absent | Regression proof and named-test proof tests | Same through P1, where no Plan Reviewer saw the tests |
| C3 baseline tests pass, reproduction fails | #1868 tests in `tests/test_acceptance_tests.py` | Same on P1 |
| C4 PASS without or despite failed command evidence | `require_executed_passes`, `verify_checks` tests | Combined-pass report with a missing check event |
| C5 source changes after verification; blocking finding open | Stale validation and ledger tests | Combined pass followed by a source edit |
| C6 shared unsupported assumption | None | Plant a brief misreading carried into both contract and tests; only the external oracle catches it. Measures what a single read misses |
| C7 human-only acceptance | Human review tests | Same on the combined pass |
| C8 Tester and Completion Reviewer share a model | Allowed by `PAIRS`; no route violation | Combined pass on a shared model detects a planted defect the separate passes catch |
| C9 stronger Builder route clashes with its verifier | `PAUSED_CROSS_MODEL` tests | Same with the combined pass on the Completion Reviewer route |

## Proposed policy changes for the first checkpoint

1. **No change to the independence pairs is needed to combine.** The combined pass
   runs on the Completion Reviewer route, which already must differ from the Builder.
2. **Add the acceptance-test author to `PAIRS`.** Its route `acceptance_tester` is a
   copy of the Tester's route made at launch, so its independence from the Builder rests
   on that copy rather than on a rule.
3. **Who enables the combined pass.** Today reviewer routing needs a per-contract user
   approval with fixed text. Under a stage policy the choice would come from the policy
   ([contract](contract.md)). Keep a per-contract approval, or let the owner's policy
   select it for the eligible class?
4. **Close the finding-resolution gap above** before any qualification run of the
   combined pass; the `source_verdict` gap is closed by #1913.
