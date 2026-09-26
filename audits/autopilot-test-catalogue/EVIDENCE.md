# T06 — Command evidence and candidate identity (EVD-01..EVD-18)

Executed on 2026-09-24. Baseline checkout `153d89ceeaa50d6350f9230775036d8da8e89681`;
T06 work committed on top (T00 `672ccff`, T05 `49b3a7b`). Runner:
`.venv/bin/python` 3.14.6, macOS darwin 25.6.0 arm64. Offline throughout:
expected command equivalence comes from the independent `CommandOracle` plus
real subprocess ground truth (commands actually run under `/bin/zsh` inside
temporary directories); no model, network or user project touched.

Commands:

```
env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t06      # EVD-01..EVD-18
env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest discover -s tools -t . -p 'test_*.py'   # full suite
```

Results: 18/18 executed, 18 PASS (after the EVD-03 fix), 0 FAIL, 0
environment blocks. Evidence bundles:
`.tmp-autopilot-testkit/artifacts/EVD-*/NN/`.

## Defect found and fixed — EVD-03 (P0)

`autocode_support._command_bodies` unwrapped the login-shell wrapper
(`/bin/zsh -lc BODY`) by taking the third shlex token without checking that
the wrapper consumed the entire line. Trailing executable content was
silently dropped, so `same_command` accepted:

- `/bin/zsh -lc 'printf hi' && rm -rf /tmp/x` ≡ `printf hi`  (accepted before)
- `/bin/zsh -l -c 'printf hi' extra argument` ≡ `printf hi`  (accepted before)
- `/bin/zsh -lc 'printf hi' | tee /tmp/x` ≡ `printf hi`      (accepted before)

That violates EVD-03's "must not happen: ignoring trailing executable text"
and invariant I05/I08: success evidence for a printing command could prove a
claim whose operator chain executes additional programs.

- **Reproduction (preserved):** `EVD-3` bundle attempts 01–08 record `FAIL`
  with the exact expected/observed rows, on the unfixed tree at `49b3a7b`.
  Replay: `git checkout 49b3a7b -- tools/autocode_support.py &&
  .venv/bin/python -m unittest tools.test_catalogue_t06` → EVD-03 FAIL;
  restore with `git checkout HEAD -- tools/autocode_support.py`.
- **Fix (narrowly scoped):** the wrapper is unwrapped only for exactly
  `*/zsh -lc BODY` (3 tokens) or `*/zsh -l -c BODY` (4 tokens); any other
  line stays whole, so a trailing `&& …` / `| …` / extra args no longer
  matches the bare body. Identical full lines still compare equal. No other
  behavior of `same_command`/`verify_checks` changed.
- **Verification:** EVD-03 PASS post-fix; `tools/test_autocode.py`
  wrapper/quoting regressions still pass; full suite 690 tests OK (baseline
  648 pre-catalogue, all green).

## Scenario map

| ID | Status | Coverage before | Executed by | Notes |
|---|---|---|---|---|
| EVD-01 | PASS | partial (4 operator variants in `test_autocode.test_same_command_preserves_shell_quoting`) | `test_catalogue_t06.UnitEvidenceCase.test_evd01_quoted_operators_are_not_executed_operators` | adds command-substitution variant; matrix re-executed with real subprocess ground truth + oracle |
| EVD-02 | PASS | full (`test_same_command_normalizes_wrapper_on_either_side`) | `...test_evd02_legitimate_wrapper_accepted` | adds `verify_checks`-level acceptance of a wrapped event |
| EVD-03 | PASS (after fix) | gap | `...test_evd03_wrapper_with_trailing_executable_text_rejected` | defect reproduced then fixed, see above |
| EVD-04 | PASS | partial (command-mismatch only) | `...test_evd04_printed_pass_is_not_an_executed_check` | printed-PASS cannot authorize a different claimed check; honest citation of the printer is accepted |
| EVD-05 | PASS | full (`test_runtime_reports` step-marker/mixed-session cases) | `...test_evd05_wrong_kind_event_references_rejected` | missing/text/tool-start refs rejected with the unique-command fallback explicitly non-rescuing |
| EVD-06 | PASS | gap | `...test_evd06_fallback_requires_a_unique_match` | unique executed-command fallback accepted + rewritten to the real event id; ambiguous rejected |
| EVD-07 | PASS | partial (contradiction only) | `...test_evd07_reported_exit_contradiction_rejected` | adds missing-exit normalization preserving actual exit 1 |
| EVD-08 | PASS | partial (report-file flow in `test_command_flow`) | `...test_evd08_receipt_from_another_attempt_rejected` | capture-context binding: cross-attempt receipt rejected, owning attempt accepted |
| EVD-09 | PASS | partial (changed bytes only) | `CompletionEvidenceCase.test_evd09_drifted_or_deleted_pinned_evidence_blocks_completion` | adds deleted-artifact and symlink-retarget variants |
| EVD-10 | PASS | partial (untracked/exec-bit/submodule in `test_autocode`) | `...test_evd10_source_drift_variants_change_candidate_identity` | adds tracked-dirty-edit, deletion and symlink-retarget; existing three referenced, not duplicated |
| EVD-11 | PASS | gap | `...test_evd11_stale_capture_cannot_approve_new_candidate` | capture pinned on C1 cannot approve changed C2; COMPLETE rejected at the gate |
| EVD-12 | PASS | gap (mapped to the supported mechanism) | `...test_evd12_silent_reference_change_is_not_fidelity` | frozen-reference swap detected through evidence-hash pinning; UI reference-manifest framing maps to pins (see scoped note) |
| EVD-13 | PASS | partial (completion-level in `test_goals`) | `...test_evd13_criterion_coverage_is_exact` | apply-level matrix: duplicate/unknown rejected transactionally; missing applied but completion blocked |
| EVD-14 | PASS | gap | `...test_evd14_pass_with_empty_check_set_rejected` | `PAUSED_INVALID_OUTPUT: Sol PASS lacks successful executed checks`, state unchanged |
| EVD-15 | PASS | partial (NOT_VERIFIED statuses in `test_goals`) | `...test_evd15_unavailable_verification_stays_unverified` | environment limitation recorded, no completion, no implementation batch |
| EVD-16 | PASS | gap | `...test_evd16_source_drift_during_verdict_application_is_caught` | deterministic linearization point: patched snapshot mutates source at the verdict's identity recheck; stale verdict rejected, edit preserved |
| EVD-17 | PASS | gap | `UnitEvidenceCase.test_evd17_tampered_receipt_rejected` | changed command / changed output hash / wrong capture context / outside path all rejected; original receipt untouched |
| EVD-18 | PASS | gap | `...test_evd18_fresh_verification_after_stale_rejection_completes` | stale-rejected run completes on fresh C2 verification; C1 history retained in `validation_archive` |

## Scoped notes (not failures)

- **EVD-12 framing:** this codebase pins evidence by content hash rather
  than a UI reference-image manifest; the tested guarantee is "a changed
  reference cannot satisfy pinned evidence and cannot complete the run,"
  which is the invariant the scenario drives at. A frozen reference-manifest
  artifact specific to FX04 belongs to the dashboard fixture tasks (T12).
- **EVD-16 layer:** the race is exercised at the verdict-application
  boundary with a deterministic patched-snapshot barrier (the documented
  linearization point), not with true parallel processes; process-level
  barrier schedules remain T14/SYS-03 work.

## Harness notes

- `CommandOracle` decides equivalence independently (strict single-wrapper
  unwrap requiring full-line consumption); production `same_command` is the
  system under test, never the expectation source.
- Real subprocess ground truth (exit codes/output under `/bin/zsh`)
  backs every executed-vs-printed operator variant in EVD-01/EVD-04.
- `forbid_real_launches` patches `run_role` on every controller-level
  application; `operations.json` in each bundle is empty of launches.
