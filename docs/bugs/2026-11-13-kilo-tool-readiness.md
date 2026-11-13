# Refuse a Kilo tool policy that cannot capture before task spend (#3366)

The original local candidate was based on `2c8e96939`, on
`codex/kilo-tool-readiness-3366`. It is separate from the Resolver recovery fix.

The reported Kilo Tester inherited `bash: ask`, which AutoCode correctly
restricted to deny. The defect was launching the unusable stage after earlier
stages had spent tokens, then showing its permission question to the person.

AutoCode now resolves the selected Kilo agents with model-free native debug
commands before task-stage dispatch, including a Kilo Tester behind another
tool's Requirements stage. A policy that denies the supplied capture prefix
stops with `PAUSED_TOOL_CONTAINMENT` and configuration guidance. It preserves
operator permissions, routes, caps and task state. Ordinary tools-enabled
launches resolve the policy again; tools-off repairs and access probes keep
their existing scope. A repair deferred at startup is followed by admission
before ordinary stages resume.

The same capture-prefix builder supplies the stage handoff and readiness check.
Ordered permission rules, literal brackets, wildcards and the selected default
agent are respected. An unrelated allowed shell command does not establish
capture permission. A selector depending on an unknown per-stage sandbox or
report/schema/prompt path is refused before planning. This checks the supplied
prefix, not every command a model might later request; it grants no capability
or reusable launch authority. Native-owned runs retain their original isolated
agent construction and conformance; ordinary readiness never looks up their
generated agents in the operator's configuration. Independent review caught
that integration gap, and a focused build-loop regression fails without the
native-owner exemption.

## Evidence

Supported Linux, offline providers, network disabled:

```sh
python -m unittest -v \
  tests.test_kilo_tool_readiness_cli \
  tests.test_tool_admission_cli \
  tests.test_capture_event_contract.CaptureEventContractTests.test_builder_handoff_captures_without_run_store_discovery
```

All five tests passed in 22.730 seconds (34.939 seconds including container
setup). Both new cases use a configured Codex-shaped Requirements provider and
only a Kilo Tester. Native debug selects a verification agent whose shell is
`ask` or `deny`, while the ordinary `code` agent allows it. Each candidate case
stops before any primary or Kilo model request, with zero model attempts, no
pending permission question, and the selected models, efforts and caps intact.
The existing source/package admission regressions and actual capture handoff
also pass.

With the new CLI tests on unchanged `2c8e96939`, both cases fail the status
assertion: Requirements launches once and reaches `WAITING_FOR_USER` instead
of the setup refusal. That clean baseline ran in 22.938 seconds. Earlier fixture
trials with ownership/schema setup errors and pipe-cleanup timeouts were kept
as unqualified attempts; they are not the regression proof.

Host-safe tool admission passed seven tests, including the native-owner
regression. The handoff and architecture checks also passed.
The new black-box CLI consumer is mapped to its product dependencies in CI;
its selection regression fails without that mapping, and all 105 selection
tests pass, including retention when the consumer is classified as slow.
Mocked Kilo policy, readiness and containment checks passed; the final new
readiness class passed 11 tests, and the actionable guidance assertion passed
after its wording update. Ruff check and format check passed over
`tools/ tests/ scenarios/` (3,316 files), and `git diff --check` passed.

The retained Linux fixture archive SHA-256 is
`ebc9db7a485e6b17bc19c2f2b28198f4a35865e77949967bccf33831c880db80`;
the candidate identity record SHA-256 is
`acc35bb1b11113c0aea53f8aaaf5e2c7dbc9e6538ee5d05aadcfa531074e82df`.
These identify test artifacts, not an `autocode bundle` manifest. The CLI
regression bytes match the tested snapshot. The six application modules have
identical Python ASTs; only the build loop's condition wrapping changed for Ruff
after the Linux run. Host-safe unit and CI-selection changes were verified
separately. No live Kilo/model compatibility result is claimed. Publication
checks after rebasing are recorded in the pull request.
