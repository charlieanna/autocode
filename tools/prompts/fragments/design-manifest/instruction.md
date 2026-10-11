
DESIGN COVERAGE INVENTORY
The retained design_manifest is the complete approved file/frame/state inventory.
For version 2, page metadata is retained from read-only Figma inspection. Every
page-level or Section-level screen frame discovered in that source must have at
least one case; nested layout frames are not separate screens. Component variants,
variables, fonts, assets and prototype transitions are inventoried across files,
with identical shared components merged by stable Figma key without losing unique
variants. Missing or unreadable fonts/assets stay explicit blockers; never invent
substitutions or silently omit an entry. Treat any missing metadata page as an
incomplete inventory, not a smaller approved scope.
Use exact exported PNGs, design context, routes, implementation paths and native CSS
viewport; export_scale describes reference pixels, not the browser CSS width.
Keep every case in the approved plan and map it to acceptance criteria. Do not
infer criterion mappings from later model reports. The approved
contract constraints must include exactly one machine-readable mapping:
VISUAL_CASE_CRITERIA={"case-id":["criterion-id"]}
Use every declared case ID exactly once with nonempty unique approved criterion
IDs; this mapping is part of the approved contract hash. Missing mappings leave
visual acceptance unverified. Do not edit
references or replace them with screenshots of the implementation. Missing access
or proof remains NOT_VERIFIED, never an invented PASS or human acceptance.
Intermediate tasks may leave future cases NOT_VERIFIED; overall completion needs
fresh independent PASS for every case on the same source and approved contract.
The independent Validator reports design_manifest_hash and design_results with each
case ID exactly once, mapped criterion_ids, status, candidate_ref, comparison_ref,
capture_ref and capture_sha256 from the current implementation capture bundle.
A PASS needs a current rendered PNG at viewport * device_scale_factor and a separate
comparison artifact describing reference comparison and functional/state checks.
Do not cite a reference PNG as the candidate or use a passing test log as a capture.
