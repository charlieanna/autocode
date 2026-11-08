# Portable native captured checks: contract audit

Design and characterization only; no replay capability or runtime change is
authorized by this note. Initially audited `origin/master`
`127e3105df7d345ef10a9486030c22f46399ecea`, then reconciled and rechecked against
`afb953886b871471b4c0dbdb5db99a83771c7d57` in independent clone
`/tmp/autocode-native-replay-contract-20261107`, branch
`audit/native-replay-portability-contract`. The primary checkout is untouched.

**Finding:** v1 can relocate source, recorded answers and archival check data;
it cannot recreate authenticated native capture/copy dependencies or a current
native execution owner. Preserve [#2847](https://github.com/charlieanna/autocode/pull/2847)'s
early export refusal, now merged at the reconciled base above. Its export/binding
delta was reviewed; the focused contract tests pass with it present. Its existing
export-refusal suite was not duplicated here. Capture batching
[#2846](https://github.com/charlieanna/autocode/pull/2846) and its follow-up repairs
remain with their current owner. No duplicate issue or PR was opened.

## What already works, and what does not

Two contracts must remain distinct: ordinary native-provider tool captures are
authenticated by `autocode_support.verify_checks`; protected native checks use
`NativeCheckExecution` and `autocode_visual_protection`. Accepting an ordinary
capture receipt does not establish protected native execution custody.

| Identity/dependency | Current behavior and portable disposition |
| --- | --- |
| Source HEAD, file bytes, executable bits, explicit source inventory | Recording snapshots/blobs/Git objects retain them. `verify_source` checks exact HEAD/files before dispatch. `source_revision` digests HEAD/files, not the workspace path; preserve it when those inputs match. Preserve `source_paths` separately. Never substitute the final tree for missing intermediate bytes or test files. |
| Attempt, nonce, report path, source revision | `autocode_replay_bindings.bindings` recognizes an exact structured `capture_context`; transport supplies the current report and fresh capture nonce. Unknown original identities stay invalid. This is field rebinding, not proof a command ran. |
| Capture JSON/raw output/tool event | `autocode_capture_command` saves argv, exit and full-output digest. `verify_checks` matches command/result, retained log, and event receipt/attestation, or exact attempt context for report-file providers. Receipt JSON embedded in native output is an opaque string to replay bindings; a relocated report reference does not rewrite it or copy its target. |
| Verification copy | `autocode_verification_copy.execution` requires the manifest hash, physical owned layout, workspace/run/tree, source revision, joint inputs and retained file/directory identities. Relocating identical manifest bytes fails. The manifest includes fresh Git metadata and may link task-local dependency directories; those bytes/links are not portable authority. |
| Exported artifacts | v1 exports selected stage artifacts and check-summary/log/supervision payloads, not a closed inventory of every report-referenced capture and copy input. `.autocode` is excluded from ordinary source materialization. Recorded-check copying preserves old receipt bytes as historical data; it does not repin them. |
| Task/contract/session/gates | Existing symbols and dispatch matching preserve semantic scope and routes. CLI shows the plan again, checks criteria, constraints, permissions, exclusions and Git delivery, then takes the current token. No archived token grants approval. |
| Named proof/mutation identities | Existing proof, exact file/hash/line gap and unique mutation-citation bindings are reusable patterns. They do not supply capture/copy ownership. Keep named cases, repetitions, fail-on-base/pass-on-candidate proof and changed-line evidence in the existing independent gates. |
| Native owner and execution receipts | `NativeCheckExecution` requires the exact `NativeRunContext` and `ParentSink`; readonly lineage authenticates the original approval, assignment, source and collected review. Journal IDs, protected namespace/lock inodes, owner PID, held-root process births, supervision and kernel policy belong to that execution. Fresh receipt authentication also checks an in-memory production sequence. None can be rebound from an archive. |

`NativeCheckExecution.copy` already refuses task dependency directories,
source links/submodules, overlays and unsupported probes. Its exact approved
readonly checks and fixed Git setup are useful foundations, not a portable
replay API. Browser capture adds runtime pins, listener/socket identities and
transport nonces; keep it outside the first slice. Existing v1 limits on
concurrent Builders, protected tests and visual/Figma startup also remain.

## Required security contract

An imported manifest hash establishes integrity relative to an admitted pin,
**not exporter identity**. An attacker can rewrite an entire unsigned bundle and
all its hashes. Neither `authentication: intact`, `original_valid: true`, a
receipt hash nor a native-looking JSON shape is an independent trust root.
Original custody depends on trusted core ownership and protected execution.
Imported bytes must never manufacture that custody or current completion.

The smallest future inventory should key each capture by recording/dispatch,
stage occurrence, exact source snapshot and task/contract, semantic approved
check identity (argv, named cases, repetition), original receipt digest and raw
output digest. Its copy dependency entry should enumerate exact relative input
paths, bytes/modes and source selection, linked to the original manifest digest.
Runtime-generated Git metadata is reconstructed, not imported as configuration.
Absolute original paths are lookup labels only. Derive any new receipt reference
from a unique authenticated inventory entry and the new owner's output slot,
never from report prose, matching basenames or model-selected paths.

| Threat | Required refusal/acceptance boundary |
| --- | --- |
| Traversal, symlink, special file, replacement race | Reuse bounded no-follow reads and literal relative-path validation at admission and use. Reject aliases, escapes, ambiguous paths, duplicate identities and incomplete closure before writes. Do not follow archived absolute paths or dependency links. Refuse symlink/submodule dependencies in the initial native capability. |
| Stale code/test or changed mode | Authenticate every intermediate source, copied input and named case before execution and after quiescence. Changed bytes, mode, HEAD, cases, scope or repetition invalidate the binding. A newer passing tree cannot repair missing historical inputs. |
| Forged receipt/old nonce | Validate original evidence only as history. Generate a new context, copy manifest, supervised receipt and log from actual execution. Do not edit an old receipt or rehash it into a current one. Redacted/missing required bytes stay refused. |
| Copied command or event | Export/import/emission never execute archived strings. A future execution request must resolve to an exact current approved check; derive invocation/instrumentation through current core parsers. Opaque embedded shell programs, old scratch paths and ad hoc checks without that mapping remain refused in the first slice. |
| Cross-run substitution | Bind the recording pin, dispatch occurrence, approved task, source and check inventory together. A same-command/same-source receipt from another run or attempt cannot supply this execution's nonce, process owner or approval. |
| Trust-boundary confusion | Keep archived observations and fresh results separately labelled. No archive creates `NativeRunContext`, parent sink, process birth, lock, journal selector or provider credential. Current checks must finish under fresh ownership before independent review and completion accept them. Simulated results retain `done=false`, no current proof and no completion pins. |

Reject global path replacement, parsing arbitrary output JSON into authority,
accepting old nonces, copying a protected journal as current, widening copy
validation, treating a digest as a signature, and falling back to ordinary
unsandboxed execution when native acquisition fails. These would erase the
distinction the existing gates enforce.

## Smallest proposed patch sequence

These are proposed owners and acceptance criteria, not permission to implement.

1. **Replay/custody owner: non-executing dependency inventory.** Add a focused
   closure validator and a versioned, bounded public export projection supplied
   by the core that already owns the evidence. Do not scan model paths or read
   private state from a client. Keep #2847 refusal active. Acceptance: exact
   capture/event/log/copy closure is recognized deterministically; tampered,
   missing, redacted, ambiguous, foreign-run and escaping dependencies refuse
   with zero execution. Existing recordings retain their behavior. Any new
   public field needs schema and TaskRun inventory coverage. **Recommended next
   implementation slice:** this inventory only, after reconciling with #2847's
   owner; no fresh-native completion claim.
2. **Native execution/custody owner: fresh replay verification authority.** Define
   an explicit replay-origin input to current core ownership without claiming
   the archived Builder/reviewer actually ran now. Build a new readonly copy,
   kernel boundary, journal, process family and result slots for a narrow
   dependency-free approved-check set. Acceptance on supported Linux: real
   collection/quiescence and cleanup, changed-code and cross-run negative
   controls, preserved exact approval, no fallback; old native selectors fail.
   This slice is blocked by the authority decisions below.
3. **Replay owner with independent verifier review: wire bindings and qualify.**
   Give only validated inventory identities explicit binding semantics; preserve
   original receipt bytes separately and let new execution emit new receipts.
   Replace refusal only for the fully supported shape. Acceptance: public CLI
   fresh-directory replay of one fixed saved capture case; same code/test bytes,
   exact plan/token and named cases, fresh independent checks, stale/forged
   controls refused, archival mode never completes currently. No live models or
   broad catalog is needed for this feature's initial deterministic proof.

## Unresolved decisions before execution support

The first bounded follow-up adds [descriptive refusal diagnostics](../replay.md)
over the public projection's existing event/answer/snapshot bytes. This is the
claim-inventory portion of step 1 only: it deliberately reports missing receipt,
log and copy custody instead of following model-selected paths. Exact portable
closure validation still needs core-owned pinned selectors for those dependencies;
the current projection cannot establish them. No status field, import trust,
receipt rebinding or native execution authority is added by that follow-up.

- **Import trust:** which trusted party authenticates an exported closure across
  machines? Recommend untrusted historical data plus fresh checking by default;
  if authenticated origin is required, specify a separately trusted pin or
  signed attestation and its key/revocation model. A signature still cannot
  establish current execution. No trust/key configuration change is made here.
- **New ownership lineage:** how does replay-origin source qualify for protected
  readonly checking without an original native Builder/review? Recommend an
  explicit distinct lineage reviewed by the custody owner; never synthesize
  old chronology. Existing operator-supplied-source lineage is not automatic
  authority to insert a replay into it.
- **Command/dependency portability:** choose supported interpreter/runtime
  equivalences and obligation IDs, and whether non-plan Tester commands can
  ever qualify. Recommend the first slice admit only dependency-free regular
  source, exact approved commands and existing supported parsers. Absolute
  scratch paths, setup-generated dependencies and external runtimes remain
  refused until their own authenticated mapping exists.
- **Version and simulation:** choose a new recording version versus an explicit
  capability extension, and whether native-dependent archival simulation is
  useful independently. Recommend a new version for closure semantics, with
  strict v1 refusal preserved and simulation never gaining execution authority.

## Deterministic evidence and limits

`tests/test_native_replay_portability_contract.py`: **12 passed**, no skips,
2.368 seconds on macOS with the repository venv interpreter. It checks exact
structured versus opaque bindings; transplanted copy, changed source/modes and
manifest aliases; old-context, forged-event, command and raw-log refusals;
archival byte preservation without execution/completion pins; concrete native
owner refusal; and public replay CLI traversal/symlink refusal before run start.
Existing fixture helpers are reused unchanged; only fixed Git preparation runs.
These tests pass on unchanged runtime by design: they characterize the boundary,
not a new feature's fail-before/pass-after proof. Initial Ruff import ordering
failed and was corrected before the passing run.

Existing `tests.test_replay_cli.ReplayApprovalTest`: **5 passed**, no skips,
0.012 seconds, covering refreshed exact tokens and changed scope/delivery terms.
The first attempt used the wrong class (`ReplayDriverTest`) for those five
selectors and produced five loader errors; no behavior tests ran in that attempt.
The corrected class above passed. After #2847 merged, the same 17 tests passed
together in **2.190 seconds**, no skips, on the reconciled runtime. This rerun
was required because master changed the recording exporter and bindings.
Exact successful final checks, from the clone:

```sh
AUDIT_PY=/Users/ankurkothari/Documents/workspace/autocode/.venv/bin/python
$AUDIT_PY -m unittest -v tests.test_native_replay_portability_contract tests.test_replay_cli.ReplayApprovalTest
/Users/ankurkothari/Documents/workspace/autocode/.venv/bin/ruff check tools/ tests/ scenarios/
/Users/ankurkothari/Documents/workspace/autocode/.venv/bin/ruff format --check tools/ tests/ scenarios/
git diff --cached --check
```

No supervised provider, archived command, live model, full scenario catalog,
Linux native execution or fresh native end-to-end replay ran. This audit does
not qualify kernel containment or claim the proposed capability works. Runtime,
existing export-refusal tests, shared fixture generators and CI remain unchanged.
