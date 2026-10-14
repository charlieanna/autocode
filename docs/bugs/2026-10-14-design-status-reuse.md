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
authentication. The original Native20 55-second timeout's full cause remains
unproven, and its frozen source, run and receipts were not changed.
