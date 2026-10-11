
COMPLETE FIGMA SOURCE INVENTORY (manifest version 2)
The catalog is derived from hash-bound, read-only page metadata for every approved
file and page. Implement every discovered page/Section-level screen frame exactly
once as a case per approved state/viewport; nested layout frames are not separate
screens. Do not omit a page or frame to make verification smaller. Keep every
component identity and variant, including variants reused from another file, and
all listed variables, typography, assets and prototype transitions in planning and
implementation coverage. Resolve each inventory_blocker with the actual source
font/asset or keep whole-task completion unverified. Never substitute a guessed
font or asset. The file revision plus source snapshots and assets are hash-bound;
drift requires a fresh review boundary. Figma is read-only.
Plans include design_coverage with this manifest_hash, cases ({id, criterion_ids,
milestone_ids}) for every case exactly once, and responsive_derivations ({target_id,
behavior, basis, exact_match:false, criterion_ids, milestone_ids}) for each
responsive_targets row with an empty reference_case_id. Every ownership milestone
must include the case's implementation paths in affected_paths. Supplied target
references retain exact visual comparison; absent ones use approved_constraints
when constraints are provided or derived_behavior otherwise. Never claim exact
matching to an absent responsive reference.
