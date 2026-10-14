# Figma status inventory reuse (#1227)

On base `0bf4a8a56`, one public `autocode_run_view.view` projection built the
same v2 inventory catalog twice: manifest verification built it during validation,
then design coverage discarded that result and parsed the catalog again.
`test_status_builds_one_fresh_authenticated_inventory_per_projection` fails on
the base with `1 != 2` (0.048 s).

Manifest verification now exposes its authenticated catalog for that projection.
Resource blockers come from the same catalog. The default `verify` interface still
returns its original record, and every inspection rechecks structure, manifest
identity, retained index, file hashes and PNG dimensions. No cross-call cache or
run-state change is involved; status still claims no current visual acceptance.

Fifteen focused fake checks pass: five status checks (0.726 s), including a
reference changed between reads, and ten inventory/capture/completion controls
(0.530 s). Pinned Ruff and mypy checks pass. Evidence is retained outside Git in
`autocode-1227-status-evidence-20261014`.

A measurement bounded to 30 s reached the public view with a complete synthetic
two-file, 20-state reference inventory: 0.017883 s, one catalog construction and
50 fresh file-hash calls. All 20 cases remained unaccepted. This measures the
view projection only, not CLI startup, Git/source inspection, separate visual
authority/completion gates, or model behavior. Those gates retain their independent
authentication. Historical reusable captures with an older reference hash still
authenticate old/current manifests per case; that unchanged path can build
additional catalogs within a projection. The one-catalog measurement applies to
the no-history, unaccepted fixture. The original Native20 55-second timeout's full cause remains
unproven, and its frozen source, run and receipts were not changed.

A follow-up on base `8059c83d7` removes a separate duplicate traversal of saved
captures. Projection previously computed `reusable_results` through `gaps`, then
computed it again for the retained-results field. One projection now computes
that list once and passes it to a private pure gap calculation. Public `gaps`,
completion checks and each later projection retain fresh evidence verification.

The public-view regression reaches two real pinned offline capture bundles under
an older reference manifest, with a newly added case in the current manifest.
The exact baseline projection fails with four capture verifications instead of
two (0.223 s). The repair uses two verifications and one source snapshot on each
of two separate projections; the new case remains unaccepted. Source, capture
and comparison edits between reads invalidate the affected retained results.
Seven focused status/capture/completion checks pass in 1.657 s, and all 13
architecture checks pass in 9.626 s. Ruff and mypy 2.4.0 checks pass.
No cross-request cache or visual PASS is introduced.
Historical per-case manifest authentication is unchanged, and this does not
attribute the original 55-second read failure or claim its full repair.
