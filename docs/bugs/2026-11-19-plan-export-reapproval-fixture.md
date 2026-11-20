# Reapproval fixtures must read the supported plan formats

While checking post-merge CI for #3886, the running master suite exposed two
`StopIteration` errors in `AcceptanceTestsCliTests._run_reapproval`:
[the failing job](https://github.com/charlieanna/autocode/actions/runs/44628167496/job/135928703271#step:14:6243).
Both reproduce on current master `86dfcfb6b5`, before any fixture change.

The fixture exported the reviewed plan, then independently searched for the
closing `---` line of format 1. Current exports have a readable overview and a
marked canonical contract block. A model-free comparison confirmed that this
decoder reads format 1 but raises `StopIteration` for both formats 2 and 3;
the shared `autocode_plan_file.read` returns the exact contract for all three.
The exported plan is valid. The test stopped before checking the reapproval
behavior it was meant to verify.

The fixture now reads its public exported artifact through that shared reader.
It keeps every existing assertion: unchanged criterion semantics, exactly two
approvals, one acceptance-test generation job, retained fail-first results,
fresh passing proof, and no artificial source edit in the unchanged-code case.
The other file consumers were checked: goal-file CLI fixtures already use the
shared reader, the model probe was corrected by #3886, and the delimiter tests
in `test_plan_file` intentionally construct format-1 files.

Plan-file and plan-command changes now keep this public CLI consumer in the
required CI selection even when it is classified as slow. The selection
regression fails without that mapping for both source paths and passes with it.
This covers the dependency that the former ad hoc file decoder hid from import
selection.

Before the fix, the two public reapproval cases failed in 121 seconds. After
the fix, 167 focused tests passed in 215 seconds, including both complete
reapproval flows, supported plan-file behavior, CI selection and architecture.
The affected selection also passes all 460 tests in 21 modules (480 seconds),
including the full 63-case acceptance module. Ruff lint/format and full Mypy
also pass. No application behavior, approval
authority or verification gate changes. This is a test/CI correction, so the
public fake-provider runs supply its verification; no additional paid model
run is needed.
