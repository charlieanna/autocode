# Deterministic Figma visual comparison

## Goal and current base

Make visible implementation differences executable acceptance failures instead of
relying only on model assertions. Preserve functional, accessibility and independent
visual review requirements. A pixel match is not proof of correct behavior or UX.

The initial implementation was made in the user's current checkout without
replacing unrelated uncommitted work or modifying existing runs. PR qualification
uses an isolated branch based on current master, carrying only the visual-check
changes. Merged PRs #267 and #291 supply exported-reference coverage and
browser-capture provenance. Neither is reimplemented as a competing controller.
The comparison capability belongs in focused, provider-neutral modules and the
existing approved-command replay path.

## Implementation slice

1. Add an opt-in `autocode visual-check` command, also runnable as a Python module.
   Recognize only this AutoCode subcommand in approved verification-command parsing;
   do not admit commands that launch other task runs.
2. Consume the version-1 exported Figma reference manifest vocabulary from #267.
   Keep comparison policy separate: pin the manifest by SHA-256, list every case,
   declare the project-owned capture command and explicit numeric tolerances.
   Pin the policy itself in the approved verification command. Never automatically
   update baselines, drop cases, mask regions, or relax tolerances.
3. Execute capture in a new workspace-local evidence directory on every invocation.
   Require a complete capture inventory at declared native CSS viewports/device
   scales. Hash current source before and after capture; refuse source or reference
   drift, missing images, invalid dimensions, path escapes and reused output paths.
4. Compare normalized PNG pixels using an optional Pillow dependency. Respect native
   viewport versus export scale; never reinterpret a scaled export as a CSS viewport.
   Exact comparison is the default. Report changed pixel counts/fractions, bounding
   boxes and optional strict region results. Retain difference and overlay images.
5. Return nonzero for mismatch or unavailable/invalid evidence. Retain versioned
   reports and input/output hashes. Label deterministic comparison separately from
   independent visual acceptance and authenticated browser provenance.
6. Require the command in an approved non-human acceptance criterion. Existing
   clean-copy replay then executes it even if a Validator omits it. The project
   capture fixture owns setup, deterministic states, fonts/assets and cleanup.

## Test gates

- Pure image controls: exact match, shifted layout, small missing control, changed
  typography-like regions, color/alpha differences, explicit tolerances, strict
  regions, wrong dimensions and scaled exports.
- Fail-closed inputs: malformed/nonfinite policy values, omitted/duplicate cases,
  unreadable/truncated images, hash changes, symlink/path escapes and empty input.
- CLI controls: fresh capture, stale output refusal, capture failure/timeout,
  source/reference mutation, immutable per-attempt evidence and meaningful exits.
- Real local browser fixture where available: unchanged reference passes; a CSS
  layout defect fails despite unchanged functional assertions.
- Existing approved-command replay rejects a planted visual mismatch even when
  a report supplies only an unrelated passing check.
- Run architecture tests, affected suite, fake scenarios and harness tests. Run
  broader regression coverage for the dispatch/dependency changes. The follow-up
  request explicitly authorizes bounded real-model qualification for the PR; that
  opt-in test must require `--i-authorize-live-model-spend`. Figma editing and
  modification of other active runs remain outside scope.

## Follow-up boundaries

- #250: automatic connected-Figma inventory and complete component/font/asset
  discovery; mechanically checked plan coverage.
- PR #291: integrate comparison with its browser/source/served-asset provenance;
  its capture mechanism is already merged and must not be duplicated.
- #251: independent image-capable adjudication and automatic per-slice repair,
  beyond deterministic pixel differences and existing verification rework.
- #255: matched live workload measurements, including incomplete runs; this slice
  cannot establish time/token savings or exact autonomous Figma delivery.

Keep the parent issues open until their broader acceptance criteria are met.

## Implementation and qualification

Implementation and measured qualification outcomes are tracked in
[issue #297](https://github.com/charlieanna/autocode/issues/297) and its pull request.
Retain failed and incomplete attempts alongside successful controls; a live call,
partial source change or pixel match alone is not a completed live qualification.

The command, policy validation and PNG comparison live in focused
`autocode_visual_check`, `autocode_visual_policy` and `autocode_visual_diff` modules.
The existing approved-command parser recognizes the new check without admitting
other AutoCode task-launch commands. CI installs the optional image dependency and
has a separate required-browser qualification job. Usage and fixture contracts are
documented in [Figma implementation](../figma.md#executable-pixel-comparison).

Regression coverage includes null/missing digests, source-declaration drift,
temporary image substitution, duplicate PNG headers, malformed metadata and
parent-death cleanup. Input digests bind the exact decoded byte buffers. Unsupported
color/orientation metadata is refused rather than ignored; enormous or empty
declared images are refused before capture.

The live harness uses the public `displayed_plan` projection, exposed only for the
currently displayed sealed contract. It checks actual typed criterion fields and
revision/hash/token identities, never model-authored prose headings or review labels.
It requires a model-authored regression plus a protected interaction test. Both trusted
test commands use verbose discovery so the runner can prove fail-to-pass and
pass-to-pass cases independently. It does not relax the core bugfix completion gate.

Run the strict focused gate with real Chromium required:

```sh
AUTOCODE_REQUIRE_VISUAL_BROWSER=1 .venv/bin/python -W error -m unittest \
  tests.test_visual_diff tests.test_visual_check tests.test_visual_check_browser \
  tests.test_visual_check_live tests.test_verification_plan tests.test_architecture \
  tests.test_taskrun.RunViewTests
```

Also run the affected suite, fake catalog, harness checks and a clean wheel-install
smoke test. Do not run the paid qualification concurrently with bulk CLI/browser
tests: resource contention can consume its bounded stage deadlines. The explicit
[live command](../figma.md#opt-in-live-qualification) requires spend consent and
retains every attempt under `.scenario-runs/`. Its full report, source/model identities,
approval records, runner regression proof and fresh pixel evidence are all required.
Unknown or incomplete results are not passes; unknown costs are not zero.

The deliverable is an opt-in deterministic verification slice, not a universal
automatic Figma acceptance gate. Complete discovery, authenticated capture
integration, independent visual judgment and matched efficiency trials remain open.
