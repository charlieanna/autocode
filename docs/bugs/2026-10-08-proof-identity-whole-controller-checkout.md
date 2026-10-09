# A regression proof refused because another test wrote into the AutoCode checkout

Found by master CI (#665). Runs 37700381494 and 37700535104 failed
`tests.test_verification_config`: the proof in
`test_stopped_run_uses_explicit_repository_gate_and_keeps_approved_settings` came back
`UNVERIFIED - Execution context changed while proving the candidate`. The same test
passed on the run before and on a local machine.

## Why

- **The identity bound the whole checkout.** `autocode_verify.execution_identity`
  binds AutoCode's own editable install. CI's venv has one (`pip install -e`). The
  binding was `util.snapshot` of the whole checkout: every tracked file, plus every
  untracked file that is not ignored.
- **Tests write into that checkout.** CI runs test modules four at a time in that
  same checkout, and some of them create temporary folders at its root.
  `tests/test_visual_acceptance.py` does it with `TemporaryDirectory(dir=<repo>)`.
  A watcher on a local full-suite run also saw folders from the stop-holder and
  registry fixtures.
- **So an unrelated test could flip the proof.** When such a folder appeared or
  vanished between a proof's two identity readings, the proof was refused. Which
  modules overlap depends on scheduling, so the failure came and went.
- **Not reproducible locally at first.** A clone's venv without the editable install
  has no such binding.

## Fixed

- **The binding is narrowed.** The editable install exposes only its package
  directory: pyproject maps `autocode_cli` to `tools/`. The identity now binds that
  directory's files and nothing else in the checkout.
- **The refusal names what changed.** It now lists which identity fields differed.
- **Reproduction.** With the editable install and `tests.test_visual_acceptance`
  looping beside it, the CI test failed 3 times out of 3 on master and passed 3 out
  of 3 with the fix.
- **Unit test.** `tests/test_verification_schedule.py`
  `test_controller_editable_binds_its_package_not_the_rest_of_its_checkout`.

## Still open

Tests that write into the repository checkout are a hygiene problem of their own:
they leave the checkout dirty while they run. They should use the system temporary
directory unless they need the repository's path.
