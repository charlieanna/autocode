# Native toolchain and Go proof qualification

The macOS OpenCode containment policy could deny selected Homebrew tools and
Python extension dependencies even after its shell-only readiness probe passed
(#424). Read-only validation also could not create Go build outputs. During the
live reproduction, passing Go tests failed to bind to annotated approved names
(#460). A separate kernel probe reproduced access to another owned process's
synthetic environment marker (#457).

The changes resolve native loader dependencies without running selected programs
outside containment, probe selected tools through the actual provider, deny
cross-process inspection while preserving self-inspection, and provide a
source-bound verification copy with protected existing inputs. Accepted checks
still require independent clean-source replay. Go matching preserves explicitly
annotated identifiers and framework information without weakening proof outcomes.

## Fresh live evidence

On base `ce18f2d939167b0453cdccf2a638c38b2445c4db`, OpenCode 1.18.33 ran
`port-policy-go` with GLM 5.3 for requirements/planning/building and GPT-6 Sol for
plan review/testing/completion. The `build-comparison` profile was copied into
an ephemeral `build-comparison-planner-medium` profile; its only change was
planner effort high to medium. Limits remained 1800 active seconds, 360 seconds
per stage, four iterations, 45 wall minutes and 40 harness steps.

Run `20261005-035210-port-the-c-reference-policy-in-reference-policy--efb7a70b`
completed in 1068.922 active seconds: TASK_COMPLETE, 14/14 independent oracle
checks, nine real-model stages, no report repairs or stage timeouts. All six
runner check replays succeeded. Audit verified retained output/receipt hashes,
12 source-bound capture receipts and their protected copy manifests, unchanged
runtime files and original limits. Three named Go cases bound successfully;
the old matcher bound zero on the same retained inputs. The Validator built Go
binaries and ran a Python-driven CLI matrix inside the verification copy.

This is one successful Go/Python campaign on macOS/OpenCode 1.18.33. Node/npm
and process-isolation checks used actual local executables and the kernel,
but this campaign does not establish live Node, Rust, other-provider, MiMo or
Resolver coverage. External dependency caches and custom wrappers remain
unqualified; #424 stays open. Unsupported tools stop before model launch.

## Failed runs and supplemental checks

Earlier live runs are retained as failures: R1 exhausted its original budget;
R2 passed the product oracle but stopped after proof/Python-helper failures and
an Investigator timeout; R3, with high planner effort, stopped on invalid output
after planning timeouts. The medium-effort pass does not establish a statistical
cost or reliability improvement and does not erase those failures.

Changed-file checks passed 1418 tests across 93 modules. The supplemental fake
catalog had 60 PASS, one NOT_EXERCISED and one SKIPPED. The full suite ran 4211
tests across 291 modules and failed two stale tests, both reproduced on the
unchanged base. The path fixture now resolves macOS temporary-directory aliases;
the repair test verifies preservation of the original failed evidence through
the current handoff behavior. Both corrected modules passed all 50 tests.
This is not a claim that a subsequent full-suite run passed.

## Integration review controls

Declared negative controls may use the native `/bin/sh` or `/bin/bash`.
Discovery rejects project/PATH shell wrappers before execution, records the
fixed shell's loader dependencies and probes it inside containment. Actual
local controls run the expected failing test through both shells, then require
kernel denial of source writes and reads of an unrelated synthetic file.

The final protected-copy check used a correctly bound Homebrew Python 3.14.6
virtualenv with native interpreter symlinks. Copied virtualenv executables
remain an unsupported discovery layout under #424; the setup must stop before
model launch rather than widening filesystem access.
