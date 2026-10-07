# Risk-appropriate proof: qualification and the remaining #451 work

The assessment ran on origin/master `ddc940f`; the branch
`claude/issue-451-risk-proof` is rebased onto `d0919ad`. No live model ran;
every run used the scripted provider. These are planted controls (#455), not
failure rates of real builds.

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
  pinned transcript. The ordinary REWORK route delivers it to the Builder. The
  runner closes the finding only when its own observation passes on a later
  source.
- Recognition is less tied to wording (`tools/autocode_risk_acceptance.py`):
  - A constructor may name its storage argument anything path-like.
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

## After: qualification on the branch

Run on `6b6ff2f` (the branch before this note), on a loaded Linux machine,
one test module at a time. Evidence is in the session scratchpad only.

| Check | Result |
| --- | --- |
| `scenarios/run.py check ladder-18-durable-lease-queue ladder-19-transactional-outbox` | All 15 lines `ok`. Both seeds fail, both references pass 5/5, and all 11 broken variants fail `hidden_tests_pass` |
| `tests.test_outbox_oracle`, `tests.test_lease_queue_oracle` | Every ladder-19 variant fails exactly its pinned hidden tests. `racy-create-order` fails only `test_concurrent_creation_once`, and `non-atomic-claim` only `test_concurrent_claims_are_unique` |
| `tests.test_risk_cli` (10 public CLI tests) | OK (26 tests with the two oracle modules, 699 s) |
| Supervisor alone, contention promised, ten runs each (load average 29 on 4 CPUs) | Both references PASS 10/10. `non-atomic-claim` and `racy-create-order` are refused 10/10. The slowest supervisor took 3.3 s of its 30 s cap |
| `scenarios/run.py run --fake` (whole catalog) | 64 entries: 62 PASS, including both references at 6/6. `feature-refund-window` is NOT_EXERCISED (oracle 5/5, but the Resolver never ran, the same on master `4b58ebe`). `stuck-planner-citation` is SKIPPED: its Investigator is a live model |
| `tools/run_suite.py --changed 711a264 --jobs 1` | SUITE |

`--changed` ran from the outbox-pin commit rather than from `origin/master`.
That commit adds `tests/test_outbox_oracle.py` to `tests/suite_slow.json`, and
`--changed` treats any change to that file as a reason to run the whole suite.
Both oracle modules ran on their own (above).

Every ladder-18 and ladder-19 variant, run end to end with
`scenarios/run.py run ID --fake --fake-solution VARIANT`:

| Variant | Branch outcome | Runner finding (open, assigned to the correction task) |
| --- | --- | --- |
| ladder-18 reference | PASS: `TASK_COMPLETE`, oracle 6/6; replay PASS with six reaped workers | none |
| ladder-19 reference | PASS: `TASK_COMPLETE`, oracle 6/6; replay PASS with six reaped workers | none |
| ladder-18 `process-local-tokens` | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | Restart reused a lease token or accepted a stale acknowledgment/release |
| ladder-18 `unfenced-ack` | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | the same |
| ladder-18 `late-expiration` | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | Lease identity, payload or exact deadline changed |
| ladder-18 `non-atomic-claim` (new) | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6; master: FALSE_COMPLETE | Concurrent claims leased one job twice and left another unclaimed |
| ladder-19 `ack-with-exception-rollback` | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | Hard-kill lost the unacknowledged event or changed its stable identity |
| ladder-19 `ack-before-send` | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6 | the same |
| ladder-19 `racy-create-order` (new) | HONEST_BLOCKER at `RESOLVER_PENDING`, oracle 5/6; master: FALSE_COMPLETE | Concurrent create_order for one key raised or returned a non-boolean: raised ValueError: duplicate order |
| ladder-19 `replay-changes-event-id` | HONEST_BLOCKER at `PAUSED_INVALID_OUTPUT`, oracle 4/6; the Tester reports FAIL, and the refusal names the failing criteria | none: no replay runs after a FAIL |
| ladder-18 `sqlite-integer-range`, ladder-19 `sqlite-integer-range`, `overflowing-limit` | FALSE_COMPLETE, oracle 5/6 (outside #451) | none: replay PASS |

In every refused run the Builder ran twice and no report repair ran. The runner
reported its finding to both Tester validations.

These new tests fail on master `4b58ebe`:

- In `tests.test_risk_cli`, five fail:
  - The corrected token mutant stops at `PAUSED_INVALID_OUTPUT` instead of
    completing.
  - The `db_path` token mutant reaches `TASK_COMPLETE`.
  - The paraphrased honest brief has no lifecycle record.
  - The unsupported wording stops with the generic "requires exactly one
    independent observation".
  - `non-atomic-claim` reaches `TASK_COMPLETE`.
- The unit tests fail too: `test_risk_protocols` (contention), `test_risk_acceptance`
  (paraphrases, missing-fact refusal, contention promise), and
  `test_risk_disclosure` and `test_risk_findings` (new modules). In all, 7
  failures and 11 errors.

## What is still open

- **Security boundaries and performance limits.** No runtime proof exists for
  either, and no catalog control plants one (ladder-16, ladder-22, ladder-23).
- **Contention beyond the two declared APIs.** Any other concurrency sentence is
  only disclosed, never raced.
- **Brief-output failures.** A failed brief-output observation
  (`brief_acceptance`, #452) still takes the rejected-report route that lifecycle
  failures used to take. A progressive run whose product claim needs the replay
  also refuses a FAIL replay as before.
- **Large integers.** The `sqlite-integer-range` and `overflowing-limit` mutants
  still complete falsely. This is outside #451; it belongs with #452.
- **Scripted Tester report repair.** It still answers with the wrong contract
  identity (`scenarios/harness/fake_codex.py`). No lifecycle failure uses that
  route any more.
- **Unsupported wording.** Recognition is still a finite grammar. Wording that
  states a fact some other way is refused: the run stops with the missing facts
  listed until a person restates them.
- **Live work.** Live qualification of the proof (#524) is owed. Diagnosis
  quality cannot be measured by fake runs, because the fake Investigator cannot
  diagnose; it stays live-only work in #59.
