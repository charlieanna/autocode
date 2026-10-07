# Risk-appropriate proof: qualification and the remaining #451 work

The assessment ran on origin/master `ddc940f`. The branch
`claude/issue-451-risk-proof` was rebased onto `d0919ad`, then merged with master
`87d8db3` and `0591e76`. No live model ran; every run used the scripted provider. These are
planted controls (#455), not failure rates of real builds.

## Before: what master did

- **Catalog controls.** `scenarios/run.py check ladder-18-durable-lease-queue
  ladder-19-transactional-outbox` printed `ok` on every line. But `check` only
  asserts that a broken variant fails `hidden_tests_pass`, and only ladder-18's
  failure sets were pinned by a test. A ladder-19 variant that stopped importing
  would still read `ok`.
- **Known mutants.** The runner's lifecycle replay refused both
  `process-local-tokens` and `ack-with-exception-rollback` before completion
  (HONEST_BLOCKER, oracle 5/6). It refused them by rejecting the Tester's PASS
  report. The Tester's report repair cannot change the product, so each attempt
  and each `--resume-paused` spent one Tester call and two report repairs. The
  Builder never saw the failing observation.
- **Wording.** The proof only fired on the catalog's exact sentences.
  - With `LeaseQueue(path)` renamed to `LeaseQueue(db_path)`, the token mutant
    reached `TASK_COMPLETE` with no lifecycle record (FALSE_COMPLETE, oracle 4/5).
  - With "token is fresh and opaque on every claim" reworded to "every claim
    issues a new unguessable token", the honest reference could never be approved.
    It stopped at `PAUSED_REPEATED_FAILURE` with "requires exactly one independent
    observation", a message that named neither the gap nor the fix.
- **Contention.** Both briefs promise it ("Enqueue and claims must be atomic
  under contention"; "Concurrent create_order requests for the same key commit
  once"), but both protocols ran one worker at a time. Nothing in the runtime
  could refuse a non-atomic claim.
- **Refusal message.** A Tester FAIL (`replay-changes-event-id`) was refused with
  a message blaming the missing lifecycle proof.

## What changed

- `tests/test_outbox_oracle.py` pins the hidden-test failure set of every
  ladder-19 variant. `ack-with-exception-rollback` fails only
  `test_repeated_crash_after_sink_acceptance_before_ack`, at crash positions 0, 2
  and 4.
- A failed lifecycle observation is now a product finding, not a report error
  (`tools/autocode_risk_findings.py`). The Tester's report stands, and the replay
  is saved with verdict `FAIL`. The runner opens a blocking finding with source
  `runner`, naming the failed promise, what the fixed protocol does and the
  pinned transcript. The ordinary REWORK route delivers it to the Builder.
  - Only the runner closes the finding: its own observation must pass on a source
    it has not failed on. The row lists every revision it failed on
    (`failed_revisions`), because a race can be missed: the same racy source
    validated again, after `--resume-paused` or reverted to, must not close it.
  - An approved amendment that removes the observation retracts the finding.
    Completion still needs every current observation to pass.
- Recognition is less tied to wording (`tools/autocode_risk_acceptance.py`):
  - A constructor may name its storage argument anything path-like. An argument
    other than `path` counts only when the API names the family's core methods
    (`enqueue`, `claim` and `ack`; `create_order` and `publish`). Otherwise
    `TodoList(filename)` with `claim(item)` and "must survive restart" became an
    unsupported declaration whose plan could never be approved. Now its sentence
    is only disclosed.
  - A renamed argument must also say what it is in the constructor's own
    sentence (queue, lease or job; outbox, order or event; the class name
    counts). A renamed-argument call that declares nothing is no declaration
    boundary, so `Logger(log_file)` between `LeaseQueue(path)` and its methods
    no longer takes them. `Name(path)` splits a source as before.
  - The token-freshness fact accepts word-order and synonym paraphrases.
  - An unsupported declaration's refusal lists each missing fact in words and
    says how to supply it.
- Contention is raced when, and only when, the declaration's own text states it
  for that API (`contention_atomicity`, `tools/autocode_risk_protocols.py`).
  After the three lifecycle phases, the same supervisor starts three more
  interpreters on a second database and releases them together at two barriers:
  - Queue: each enqueues the same twelve jobs, and each job must be accepted
    exactly once. Each contender then claims four times: twelve different jobs
    and twelve different tokens.
  - Outbox: each creates the same six orders. Each order must return `True`
    exactly once and never raise, and then all three must see the same six
    orders and events.
  The transcript (version 2) pins the barrier ticks and the contenders'
  receipts. The bound is six owned workers, still within the 30-second cap.
- Two new catalog controls exercise the race: ladder-18
  `broken/non-atomic-claim` chooses a job and leases it in separate
  transactions, and ladder-19 `broken/racy-create-order` checks for a replay and
  commits in separate transactions. Both widen their race window with a 20 ms
  pause, so the refusal is deterministic. Without the pause the same
  check-then-act code was caught in 6 of 20 (queue) and 2 of 20 (outbox)
  three-process trials, and the hidden thread tests caught it about as often. A
  contention PASS is therefore evidence that no lost or duplicated write showed
  up, not proof of atomicity.
- The completion refusal names a failed validation's criteria first, and names a
  failed observation with its receipt.
- The status view adds `evidence.findings[].source` and
  `evidence.unverified_risk_claims` (`tools/autocode_risk_disclosure.py`). The
  second field is a disclosure only: it lists the durability and concurrency
  sentences that no runner protocol exercises. A sentence the protocol races is
  no longer listed.

A run approved before this change whose brief now reads differently (a
contention promise, or a path-like constructor argument other than `path`) no
longer matches its saved lifecycle manifest; its plan must be approved again.

## Review of the branch (2026-10-07)

The first implementation stopped before its review; the reviews scheduled after
it were cut off before reporting. Each finding below, from the assessment and
from the first review pass, was reproduced again on the branch merged with
`0591e76` (`6d7f84e` and later):

| Finding | Status | Evidence |
| --- | --- | --- |
| `LeaseQueue(db_path)` let the token mutant complete | Fixed | CLI test: the renamed run binds the observation and stops on the runner finding |
| A reworded honest brief could never be approved | Partly fixed | The token paraphrase completes with proof (CLI). Other wording still refuses until restated, naming each missing fact |
| A lifecycle FAIL went to a report repair; the Builder never saw it | Fixed | CLI test: a corrected mutant completes after a second Builder run; the mutant runs below show two Builder runs and no report repair |
| The scripted Tester's report repair has the wrong contract identity | Open, outside this route | No lifecycle failure takes that route now; brief-output failures (#452) still do |
| No contention proof | Fixed for the two declared APIs | Two new mutants, both refused; a missed race can no longer close a runner finding |
| Outbox failure sets not pinned | Fixed | `tests.test_outbox_oracle` |
| Refusal blamed the lifecycle proof for a Tester FAIL | Fixed | `tests.test_risk_findings` |
| Large-integer mutants complete | Open, outside #451 | #452 |
| No security or performance proof | Open, outside this PR | Separate issue |
| `docs/task-run.md` lacked `risk_acceptance` | Fixed | Docs below |
| New: renamed storage arguments turned ordinary tasks into unapprovable plans | Fixed | `tests.test_risk_acceptance`, `tests.test_risk_disclosure` |
| New: a PASS on the same racy source closed the runner finding | Fixed | `tests.test_risk_findings` |
| New: removing the promise left its runner finding blocking forever | Fixed | `tests.test_risk_findings` |
| New (second pass): `Logger(log_file)` between `LeaseQueue(path)` and its methods took the API, so the honest brief stopped on an unsupported `Logger` queue (master: supported `LeaseQueue`) | Fixed in `6d7f84e` | `tests.test_risk_acceptance` (fails before the fix: 5 failures) |
| New (second pass): `tools/autopilot.py` grew by 4 lines | Fixed in `6d7f84e`: the whole-product claim is computed once; same line count as master | `wc -l`, `tests.test_architecture` |

## After: qualification on the branch

Unless noted, run on the merged branch with the review fixes (`c136a68`), on a
Linux machine with 4 CPUs at load average 17 to 31 (other sessions' suites).
Evidence is in the session scratchpad only.

| Check | Result |
| --- | --- |
| `scenarios/run.py check ladder-18-durable-lease-queue ladder-19-transactional-outbox` | All 15 lines `ok`. Both seeds fail, both references pass 5/5, and all 11 broken variants fail `hidden_tests_pass` |
| `tests.test_outbox_oracle`, `tests.test_lease_queue_oracle`, `tests.test_risk_runtime`, `tests.test_risk_targets` | 46 tests OK. Every ladder-19 variant fails exactly its pinned hidden tests. `racy-create-order` fails only `test_concurrent_creation_once`, and `non-atomic-claim` only `test_concurrent_claims_are_unique` |
| `tests.test_risk_cli` (10 public CLI tests) | OK in 120 s |
| Architecture and unit modules (`test_architecture`, `test_risk_acceptance`, `_disclosure`, `_findings`, `_protocols`, `_evidence`, `_obligations`, `test_taskrun`, `test_progressive_run_view`) | 150 tests OK |
| Supervisor alone, contention promised, ten runs each (on `6b6ff2f`, load average 29) | Both references PASS 10/10. `non-atomic-claim` and `racy-create-order` are refused 10/10. The slowest supervisor took 3.3 s of its 30 s cap |
| `scenarios/run.py run --fake` (whole catalog) | 64 entries: 62 PASS, including both references at 6/6. `feature-refund-window` is NOT_EXERCISED (oracle 5/5, but the Resolver never ran; reported the same on master `4b58ebe`). `stuck-planner-citation` is SKIPPED: its Investigator is a live model |
| `tools/run_suite.py --changed --all-fast --jobs 2` | Pending |

The branch changes `tests/suite_slow.json`, so `--changed` runs every module,
the slow ones included.

Every ladder-18 and ladder-19 variant, run end to end with
`scenarios/run.py run ID --fake --fake-solution VARIANT` on `6b6ff2f`; the four
marked again were rerun on `c136a68` with the same outcome:

| Variant | Branch outcome | Runner finding (open, assigned to the correction task) |
| --- | --- | --- |
| ladder-18 reference | PASS: `TASK_COMPLETE`, oracle 6/6; replay PASS with six reaped workers | none |
| ladder-19 reference | PASS: `TASK_COMPLETE`, oracle 6/6; replay PASS with six reaped workers | none |
| ladder-18 `process-local-tokens` (again) | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | Restart reused a lease token or accepted a stale acknowledgment/release |
| ladder-18 `unfenced-ack` | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | the same |
| ladder-18 `late-expiration` | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | Lease identity, payload or exact deadline changed |
| ladder-18 `non-atomic-claim` (new; again) | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6; master: FALSE_COMPLETE | Concurrent claims leased one job twice and left another unclaimed |
| ladder-19 `ack-with-exception-rollback` (again) | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | Hard-kill lost the unacknowledged event or changed its stable identity |
| ladder-19 `ack-before-send` | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | the same |
| ladder-19 `racy-create-order` (new; again) | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6; master: FALSE_COMPLETE | Concurrent create_order for one key raised or returned a non-boolean: raised ValueError: duplicate order |
| ladder-19 `replay-changes-event-id` | HONEST_BLOCKER at `PAUSED_INVALID_OUTPUT`, oracle 4/6; the Tester reports FAIL, and the refusal names the failing criteria | none: no replay runs after a FAIL |
| ladder-18 `sqlite-integer-range`, ladder-19 `sqlite-integer-range`, `overflowing-limit` | FALSE_COMPLETE, oracle 5/6 (outside #451) | none: replay PASS |

In every refused run the Builder ran twice and no report repair ran. The runner
reported its finding to both Tester validations; in the reruns its
`failed_revisions` names the one source both Builders produced.

These new tests fail on master:

- In `tests.test_risk_cli`, five fail (on `4b58ebe`):
  - The corrected token mutant stops at `PAUSED_INVALID_OUTPUT` instead of
    completing.
  - The `db_path` token mutant reaches `TASK_COMPLETE`.
  - The paraphrased honest brief has no lifecycle record.
  - The unsupported wording stops with the generic "requires exactly one
    independent observation".
  - `non-atomic-claim` reaches `TASK_COMPLETE`.
- The unit modules `test_risk_acceptance`, `test_risk_protocols`,
  `test_risk_findings` and `test_risk_disclosure` run against master `87d8db3`'s
  `tools/`: 8 failures and 11 errors (34 tests). The two new modules fail to
  import, and the contention and paraphrase tests fail.

## What this PR covers

| #451 criterion | Decision |
| --- | --- |
| Controls qualified through the catalog, failing for the intended reason | Covered: `check` plus exact failure sets pinned for every ladder-18 and ladder-19 variant |
| The runtime's own proof rejects both known mutants before completion | Covered for the two declared API families, including the held-out `db_path` and token rewordings and the two contention mutants |
| Honest references complete; other jobs do not silently acquire the requirement | Covered: both references complete; only ladder-18 and ladder-19 declare an observation across the catalog; other durability and concurrency sentences are disclosed, never required |
| Bounded, owned cleanup, failures kept across restart, no budget replenishment | Covered: at most six owned and reaped workers within 30 s; a failure stays open across restart and on the same source; no report repair is spent on it; the rework uses the ordinary bounded repair loop |
| Independent holdout preserved | Partly: the runtime protocol stays separate from the hidden tests, and held-out rewordings are tested. Live qualification is still owed |
| Diagnosis quality qualified separately | Not covered: fake runs cannot measure it (#59) |

This PR should reference #451, not close it.

## What is still open

- **Security boundaries and performance limits.** No runtime proof exists for
  either, and no catalog control plants one (ladder-16, ladder-22, ladder-23).
- **Contention beyond the two declared APIs.** Any other concurrency sentence is
  only disclosed, never raced. A stated contention promise with a negation in
  the same clause ("so a job is never leased twice") is not raced either; it is
  disclosed.
- **Brief-output failures.** A failed brief-output observation
  (`brief_acceptance`, #452) still takes the rejected-report route that lifecycle
  failures used to take. A progressive run whose product claim needs the replay
  also refuses a FAIL replay as before.
- **Reviewer routing.** Under the opt-in `glm_first_v1` routing, the checkpoint
  report holds the validation and the decision together, so its decision is
  written before the runner's observation. A COMPLETE there is refused (fail
  closed), not turned into REWORK. No test exercises this route.
- **Large integers.** The `sqlite-integer-range` and `overflowing-limit` mutants
  still complete falsely. This is outside #451; it belongs with #452.
- **Scripted Tester report repair.** It still answers with the wrong contract
  identity (`scenarios/harness/fake_codex.py`).
- **Unsupported wording.** Recognition is still a finite grammar. Wording that
  states a fact some other way is refused: the run stops with the missing facts
  listed until a person restates them. `Name(path)` with only part of a family's
  API and a durability word is still an unsupported declaration, as before this
  branch; a renamed argument is not. Any other `Name(path)` call between the
  constructor and its methods (`Logger(path)`) still takes them, as on master.
- **Live work.** Live qualification of the proof (#524) is owed. Diagnosis
  quality cannot be measured by fake runs, because the fake Investigator cannot
  diagnose; it stays live-only work in #59.
