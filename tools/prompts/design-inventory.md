Collect the approved Figma files into one complete immutable reference bundle before planning.
Load figma-use for programmatic inspection and figma-design-to-code before get_design_context.
Keep every reference file read-only. Discover its entire document/page tree using connected tools.
Use the supplied read-only collector, setting the approved file/page literals and outputPart=0 first.
Discover the actual root page roster with a read-only figma.root.children inspection; a connector page listing may expose only the current page.
The reconstructed receipt's document_pages must match that actual root roster.
Collect every roster page. For each page, retain ALL outputPart indices with identical receipt SHA256,
identity and part count, then reconstruct through autocode_design_sources.reassemble(parts).
Save that complete reconstructed original receipt as source_json, never a transport envelope or truncated JSON.
Retain the complete connected page metadata XML. When the connector refuses a DOCUMENT target, generate
its DOCUMENT roster XML faithfully from the retained document_pages; identify it as generated, not a REST export.
Missing/drifting parts or unreadable source properties are BLOCKED. Do not skip source facts to fit tool limits.
Include every discovered top-level/Section screen frame and every supplied state. Do not deduplicate away
unique variants, states or prototype actions. Map every case to the requested product route/state,
implementation paths, native dimensions, export_scale and all applicable inventory_refs. Shared component
and variable keys can link across approved files; qualify other-file font/asset/transition IDs as FILEKEY/id.
Library-only files need no invented screen state. Map all library resources to their consuming cases. Preserve component
keys and source node/variant identities. Copy all variables including every mode, every font family/style,
image paint/vector and exact trigger/action from the page receipts. Obtain screenshots at native dimensions
and exact export scale, design context, assets and the actual fonts or mark them missing with a concrete
reason. Missing assets/fonts can never earn exact fidelity PASS. Unreadable page/node/remote component or
contradictory state mapping is BLOCKED, not an omitted entry or guessed substitution. Preserve the source
content identities even if the connector has no explicit version ID; use its retained artifact hashes.
Declare responsive_targets from the request; if a viewport has no supplied reference leave reference_case_id
empty and preserve approved constraints. Derivation is reviewed in the plan, never labeled an exact match.
Write only under .autocode/native-design-inputs/. Do not modify the repository or Figma canvas. Return READY
with the bundle manifest path only after it validates via autocode_design_manifest.load. Otherwise BLOCKED
with specific blockers. This report grants no plan approval or implementation/visual acceptance.
