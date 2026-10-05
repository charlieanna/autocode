# Figma design and implementation

[← Back to README](../README.md)

The native connected-Figma workflow uses Codex with the Figma plugin. Requirements planning,
plan review, plan finalization, validation, and completion decisions use the review model
tier (Sol); the review and decision steps default to Sol High. Figma editing uses the
Builder model tier (Terra). OpenCode is not needed for this path. The plugin must
be available in Codex CLI sessions; a connection only in another app is insufficient.

```sh
# Design only (both commands are equivalent):
autocode-ui "Design a responsive team operations dashboard"
autocode ui "Design a responsive team operations dashboard"

# Design, review, then hand the accepted Figma result to the implementation runner:
autocode ui "Build a responsive team operations dashboard" --build

# Refine a selected file:
autocode-ui "Refine the project detail layout" --figma-file "https://www.figma.com/design/FILEKEY/Project"

# Implement an existing file, or import a completed design run:
autocode "Build the supplied design" --figma-file "https://www.figma.com/design/FILEKEY/Project"
autocode --ui-run /path/to/project/.autocode-ui/runs/RUN
```

The UI path first runs a complete planning exchange: a Requirements Planner drafts
the brief, an independent Plan Reviewer challenges it, the planner revises it, and
a Plan Finalizer accepts it or requests rework. It then runs Figma Builder →
Design Validator → Completion Owner, with build/review rework. Models remain
configurable separately from these role names. Every attempt has separate prompts,
event logs and structured reports under `.autocode-ui/runs/`. Failed planning or
design reviews cannot produce a build handoff.
By default, planning allows one rework and design allows two. Use
`--max-plan-reworks none --max-reworks none` to continue review loops until accepted
or blocked. Numeric limits remain available; `0` allows no rework. The selected
limits are recorded in the run state.
An accepted handoff records the Figma URL and hashes of the brief and review reports;
modified or incomplete artifacts are rejected when imported. The implementation
roles inspect the live Figma reference again, since the file can change after design.

A [multi-component build](task-lanes.md#a-component-with-a-figma-design) can also
give an individual component a `ui_run` or `figma_file` in `components.json`.
The design is supplied before the build, and only that component receives it.

`--build` starts the normal implementation brief and orchestration in Codex. Its
initial product brief still needs approval. Subsequent visual checks use Figma
comparisons and independent validation by default; they do not require another
human visual approval. Add `--figma-review human` to a new implementation run if
that review is wanted. Explicitly supplied review requirements still take precedence.
The workflow uses the connected Figma editing tools, not an assumed Figma Make API.

The Figma and code paths use the same durable Autocode stage driver. `autocode-ui`
is an installed alias for `autocode ui`, not a separate orchestration product. Each
target supplies its own prompts, report schemas and acceptance rules while sharing
stage selection, checkpoint persistence, retry/skip behavior and terminal-state
handling. With `--build`, the accepted Figma target hands off to the code target.

`autocode ui "Preview the workflow" --dry-run` writes prompts without model calls or
Figma edits and never emits an accepted handoff. If a design stage is interrupted,
inspect its saved reports and Figma file before starting a fresh run with
`--figma-file`; existing run directories are never overwritten.

For an assigned implementation or review task, Figma instructions cover the affected
visual work. Test/parser/harness-only repairs can reuse applicable design evidence;
they do not require a fresh canvas inspection unless they affect presentation or verify
a visual criterion. Final visual acceptance requirements remain in force.
An implementation capture must still match the current source snapshot when used
for acceptance; see [implementation capture bundles](visual-captures.md).

## Executable pixel comparison

`autocode visual-check` is an opt-in, provider-neutral check for **exported** reference
bundles. It runs a project-owned capture command afresh, then compares every declared
case with its pinned reference. A mismatch exits 1; invalid or unavailable evidence
exits 2. Exit 0 means the declared pixel policy passed, **not** that independent
visual review, behavior, accessibility or complete Figma discovery passed.

Install the optional image dependency with `python -m pip install 'autocode-supervisor[visual]'`
(from this checkout, `python -m pip install -e '.[visual]'`). The capture fixture owns
its browser dependency; the comparator does not download browsers, fonts or assets.

Keep the policy, reference bundle and capture fixture in normal project source, not
in ignored directories or `.autocode/`. An approved verification command should pin
the exact policy file hash:

```sh
python -m autocode_cli.autocode_visual_check --workspace . --policy visual-policy.json --policy-sha256 APPROVED_SHA256
```

Use that full command as a non-human criterion's `verification_method` or an approved
task's `validation_plan`. The existing runner replays prescribed commands in a clean
copy even if the Validator reports an unrelated successful check. The bare interactive
alias `autocode visual-check ...` is also recognized for replay; other AutoCode task
commands are not admitted as verification checks. The chosen Python interpreter must
have AutoCode and its visual extra installed.

Compute the hash when reviewing the policy, not dynamically inside the verification
command. Changing a baseline, removing a case or relaxing a tolerance requires a
reviewed policy change and a newly approved command; verification never auto-updates it.
The source revision also binds the project-owned fixture. Its implementation still
needs review: a dishonest fixture could copy reference pixels instead of rendering.

### Policy and references

Example `visual-policy.json` (replace digests with actual file SHA-256 values):

```json
{
  "version": 1,
  "manifest": {"path": "design/manifest.json", "sha256": "MANIFEST_SHA256"},
  "capture_command": ["python", "tests/capture_visual.py"],
  "timeout_seconds": 120,
  "cases": [{
    "id": "workspace.desktop", "channel_tolerance": 0,
    "max_changed_ratio": 0.0, "regions": []
  }]
}
```

The manifest uses the [exported-reference version-1 format](#multi-file-reference-inventory-opt-in)
below. Comparison policy stays separate rather than extending that format implicitly.
Its case IDs must exactly match the reference inventory.

Paths in `manifest` are relative to the policy; artifact paths are relative to the
manifest. All inputs must remain within the workspace, with no symlink components.
Every declared file/node needs a case, and every case needs exactly one comparison
and capture. This verifies the declared inventory, not that no Figma frame was omitted
from the inventory itself. Exports and context are read-only.
Declared `implementation_paths` must exist as source-owned files or nonempty source
directories. Missing paths, symlinks and ignored implementation inputs are refused;
directory declarations cannot quietly include ignored build outputs. List the actual
source files and rebuild generated assets inside the capture fixture when needed.
Include the capture fixture and its project-owned data/configuration in those paths,
not just the application's visible components. The checker does not discover an
arbitrary command's dependency graph or authenticate external tool installations.

The exact default is zero channel tolerance and zero differing pixels. Explicit
tolerances must be finite: `channel_tolerance` is an integer from 0 to 254 and
`max_changed_ratio` is at least 0 and less than 1. A pixel differs when its maximum
RGBA channel difference exceeds the channel tolerance. No masks are supported.
For important small controls, add stricter reference-pixel rectangles to `regions`:
`{"id":"submit","x":100,"y":200,"width":80,"height":32,"max_changed_ratio":0}`.
A failing region fails the case even if its global changed-pixel fraction is allowed.
The narrow decoder accepts single-frame, at-most-8-bit PNGs interpreted as sRGB.
Embedded ICC profiles, orientation/EXIF, chromaticity/HDR metadata and nonstandard
gamma are refused rather than silently ignored. Export normalized sRGB PNGs first;
untagged PNGs, valid sRGB intents and the standard PNG gamma value 0.45455 are accepted.

Native CSS viewport, browser device scale and reference export scale are distinct.
The command requires candidate dimensions `round(viewport * device_scale_factor)`
and reference dimensions `round(viewport * export_scale)`. If these resolutions differ,
only the candidate is resampled to the declared export dimensions with the recorded
LANCZOS normalization. A 1024px-wide export of a 1440px frame is not permission to
render at a 1024px CSS viewport. Prefer native-resolution reference exports when
checking fine detail: downsampling necessarily loses information.

### Capture fixture

The command runs `capture_command` as an argument array, without implicit shell
expansion, from the workspace root. It provides:

- `AUTOCODE_VISUAL_MANIFEST`: absolute path to the verified reference manifest.
- `AUTOCODE_VISUAL_OUTPUT`: a new empty directory for this attempt's captures.

The fixture must start and stop its own application/browser, render the actual
application for every specified route/state at the native viewport, and write PNGs
plus `captures.json` in that output directory:

```json
{
  "version": 1,
  "cases": [{
    "id": "workspace.desktop", "path": "workspace.png",
    "state": "building", "route": "/workspace",
    "viewport": {"width": 1440, "height": 900, "device_scale_factor": 1}
  }]
}
```

Use a pinned browser, local fonts/assets, deterministic data, explicit theme/locale,
disabled animations and a readiness assertion plus `document.fonts.ready`. Assert
the actual route, viewport and UI state; reporting requested metadata alone does not
prove them. Missing fonts/assets or unavailable states must make the fixture fail.
Require separate functional and accessibility checks. The fixture must not mutate
source or references while rendering, or rely on an old ignored build being current.

Every attempt retains `report.json`, its capture log, candidates, differences and
overlays under a new `.autocode/visual-checks/` directory. Existing output directories
are refused rather than overwritten. Reports bind source before/after, policy,
manifest, input and image hashes, comparator identity, per-case measurements and
difference bounds. They explicitly say independent visual review was not performed
and browser provenance is project-owned rather than authenticated by the collector.
Reference and candidate digests bind the exact immutable byte buffers decoded for
comparison, not a later reread of their paths. A temporary replacement restored before
the final source check still fails its approved-image or captured-image digest check.
Capture supervision also watches the checker's lifetime so a clean-replay timeout
does not orphan the capture process group. Fixtures must still clean up any resources
they explicitly detach outside that group.

The complete JSON report is also printed to stdout. Clean replay removes its scratch
tree, including images created there; the runner's hashed command log preserves the
JSON measurements. Retain the ordinary workspace attempt's image artifacts as
criterion evidence for inspection; do not cite deleted scratch paths as live evidence.

This check does not retrofit existing runs, collect Figma inputs automatically, or
replace the [capture-provenance mechanisms](visual-captures.md) delivered in #291.
Connecting the comparator to those collector-owned captures is separate from this
project-owned command. Discovery, independent image review and efficiency work remain
tracked in #250, #251 and #255. See the [implementation plan](plans/figma-visual-comparison.md).

### Opt-in live qualification

The reproducible qualification uses a real local Chromium reference and the existing
`codex-only` live model profile. Despite its name, that profile uses OpenCode transport;
its exact role routes are defined in `scenarios/harness/profiles.py`. It is not a live
Figma integration test, and does not claim independent visual judgment or efficiency.

Install `.[visual-test]` and Chromium before running. From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/visual_check_live.py \
  --profile codex-only --i-authorize-live-model-spend \
  --timeout-minutes 15 --max-steps 24 \
  --out .scenario-runs/visual-check-live-qualification
```

Without the spend flag, nothing launches. Each authorized invocation creates a new
retained attempt, freezes its reference/capture/policy inputs, plants visual defects,
and asks real models to repair the HTML and add one targeted visual regression test.
A supplied interaction test stays frozen and passes even before the visual repair.
The runner must prove the new regression fails on original code and passes on the fix;
the qualification never waives that bugfix gate. It approves only a displayed non-human
criterion containing the exact pinned command. PASS requires actual Builder/Validator
receipts, current public completion, successful clean replay of that command, unchanged
protected inputs and runtime, a zero-difference final capture, and independently
rejected sidebar-offset and missing-control variants. Functional click assertions
must pass even in those visually broken controls.

The driving limit is 15 minutes and 24 public CLI calls; runtime limits are 720 seconds,
300 seconds per stage and three iterations. Setup/scoring commands are separately
bounded. It never grants additional budget, changes model routes or billing setup,
approves human review, or automatically resumes an operational pause. Time limits are
not a dollar cap. `summary.json`, command logs, approval receipts, usage and images
remain in the attempt directory, including on failures; unknown cost is not zero.
Routine CI runs only the harness's no-spend guard tests, not this live command.

See also: [Models and escalation](models.md) · [Execution and completion](execution.md)

## Multi-file reference inventory (opt-in)

New implementation runs can receive an exported reference bundle through
`--figma-manifest /absolute/path/to/bundle/manifest.json`. This input works with
Codex and OpenCode and preserves the selected role routes, pins and limits.
It does not change the native `--figma-file` authentication requirements. When
combined with native Figma input, that file must also be declared in the manifest.

Version 1 declares every approved file and frame separately from its implementation
cases. Every declared frame needs at least one case; states and responsive viewports
use distinct stable case IDs. Version 1 remains supported for existing exported
bundles. New complete inventories should use version 2, which adds read-only page
metadata snapshots and component, variable, font, asset, and prototype-transition
catalogs. The runner discovers page/Section-level screen frames from each complete
page snapshot and requires a case for every discovered frame. Nested layout frames
are not treated as screens. A frame omitted from `cases` therefore fails intake
before a paid stage rather than disappearing from the denominator.

Version 2 adds `native_size` and `inventory_refs` to `cases` and replaces each version-1 `{key,nodes}`
file row with this source inventory shape:

```json
{
  "version": 2,
  "responsive_targets": [],
  "files": [{
    "key": "FILEKEY",
    "revision": "Figma file version or complete-content identity",
    "metadata_xml": {"path": "references/file.xml", "sha256": "<actual SHA256>"},
    "pages": [{"id": "1:1", "name": "Workspace", "metadata_xml": {
      "path": "references/workspace-page.xml", "sha256": "<actual SHA256>"
    }, "source_json": {"path": "references/workspace-source.json", "sha256": "<actual SHA256>"}}],
    "screen_states": [{"page_id": "1:1", "node_id": "1:10", "state": "default",
      "viewport": {"width": 1440, "height": 900, "device_scale_factor": 1}}],
    "components": [{"key": "stable-library-component-key", "name": "Button",
      "source_page_id": "1:1", "source_node_id": "1:20",
      "variants": [{"page_id": "1:1", "node_id": "1:2", "properties": {"Type": "Primary"}}]}],
    "variables": [{"key": "stable-variable-key", "name": "surface", "kind": "COLOR",
      "value": {"r": 1, "g": 1, "b": 1}, "collection": "Foundation", "mode": "Light",
      "source_id": "VariableID:123", "mode_id": "light"}],
    "fonts": [{"id": "font-inter", "family": "Inter", "style": "Regular", "status": "available",
      "artifact": {"path": "references/Inter-Regular.woff2", "sha256": "<actual SHA256>"}}],
    "assets": [{"id": "logo", "page_id": "1:1", "node_id": "1:3", "name": "Logo",
      "mime_type": "image/svg+xml", "status": "available",
      "artifact": {"path": "references/logo.svg", "sha256": "<actual SHA256>"}}],
    "transitions": [{"id": "open-settings", "source_node_id": "1:10",
      "target_node_id": "1:11", "trigger": "click",
      "action": {"type": "NODE", "destinationId": "1:11", "navigation": "NAVIGATE"}}]
  }],
  "cases": [{
    "id": "workspace.desktop.default", "file_key": "FILEKEY", "page_id": "1:1",
    "node_id": "1:10", "state": "default", "route": "/workspace",
    "native_size": {"width": 1440, "height": 900},
    "implementation_paths": ["src/workspace.js"],
    "inventory_refs": {"components": ["stable-library-component-key"],
      "variables": ["stable-variable-key"], "fonts": ["font-inter"],
      "assets": ["logo"], "transitions": ["open-settings"]},
    "viewport": {"width": 1440, "height": 900, "device_scale_factor": 1},
    "export_scale": 1,
    "artifacts": {
      "screenshot": {"path": "references/workspace.png", "sha256": "<actual PNG SHA256>"},
      "design_context": {"path": "references/workspace.json", "sha256": "<actual context SHA256>"}
    }
  }]
}
```

The file `metadata_xml` is the complete read-only document tree and must enumerate
all of the file's pages. Each page's `metadata_xml` is its complete read-only page
tree. Both are stored as hash-bound relative artifacts. A page omitted from the
manifest while present in the document tree fails intake. Every discovered screen
frame must have an explicit `screen_states` row, and each row must match exactly one
case with the same state and viewport. That makes an omitted approved state/viewport
case a hard intake error. The state table is an explicit inventory: native inspection
must include interactive and lifecycle states from the approved design, rather than
inferring them from the frame name. Transitions must point to source nodes under
covered screen frames or mapped component definitions. Every listed component, variable, font, asset, and transition
must be mapped by at least one case through `inventory_refs`; unknown and duplicate
references fail intake. Builder handoffs contain only cases whose implementation
paths overlap the assigned task paths and their linked inventory, while preserving
all variants of a selected shared component. Tester handoffs carry the same slice and
a complete case roster; the original full manifest remains retrievable by its hash-bound path.
Planning and completion review retain the complete manifest. Each component entry binds a source set/component node; for a
component set, every component variant in its page metadata must be listed. Shared
components with the same stable library key are merged for handoff, while all
distinct variant node IDs and properties are retained. Variables with the
same stable key must have identical definitions, retaining each file's local source ID. Available fonts and assets require
hash-bound local bytes. Unavailable references are represented with
`"status":"missing"` and a concrete `reason`; they remain visible blockers and
prevent overall completion. They cannot be replaced with guessed values.

Each page also retains a `source_json` receipt from the read-only
`tools/figma_inventory_page.js` collector. It includes original REST node properties,
rich-text font families/styles, component and variant identities, instance references,
all local variable modes plus remote bindings/aliases, image/vector resources and
full prototype trigger/actions. Receipt node IDs must exactly match the complete XML.
Empty declarations cannot hide resources reported by the source. A screen must map
its actual resources and the resources in every variant of its linked components.
Library-only files may have an empty state table; qualify their font/asset/transition
references as `FILEKEY/id` in consuming cases. Component and variable references use
stable library keys across files. Missing remote definitions stop intake.

These checks reconcile approved source receipts; they do not authenticate a
hand-edited snapshot as a live connector response or infer an interaction state that
inspection did not record. Explicit state collection and independent per-screen
visual review remain required.

The reviewed product contract includes `design_coverage`: the manifest hash and
one `{id, criterion_ids, milestone_ids}` row per case. Each named milestone must
own an implementation path for that case. The approval brief displays the mapping
and source blockers, and an omitted case refuses plan installation and approval.
Independent design reports retain this exact criterion mapping.

Declare requested responsive targets separately as `{id, source_case_id,
reference_case_id, viewport, constraints}`. Use a supplied case ID when an exact
reference exists at that viewport. With no supplied reference, keep
`reference_case_id` empty; the plan must then document its behavior in
`design_coverage.responsive_derivations`, identify its criterion and milestone,
use the supplied constraints or explicitly record derived behavior, and set
`exact_match` to `false`. The runner refuses a claim of exact matching to an absent
responsive reference.

Version 1 example:

```json
{
  "version": 1,
  "files": [{"key": "FILEKEY", "nodes": ["422:1495"]}],
  "cases": [{
    "id": "workspace.desktop.empty",
    "file_key": "FILEKEY",
    "node_id": "422:1495",
    "state": "empty conversation",
    "route": "/workspace",
    "implementation_paths": ["src/workspace.js", "src/workspace.css"],
    "viewport": {"width": 1440, "height": 900, "device_scale_factor": 1},
    "export_scale": 1,
    "artifacts": {
      "screenshot": {"path": "references/workspace.png", "sha256": "<actual PNG SHA256>"},
      "design_context": {"path": "references/workspace.json", "sha256": "<actual context SHA256>"}
    }
  }]
}
```

Artifact paths are portable paths relative to the manifest's directory; symlinks
that escape it, missing files and conflicting hashes are refused. The PNG export
dimensions must equal the native CSS viewport multiplied by `export_scale`.
A scaled 1024×640 reference export does not imply a 1024 CSS-pixel application.
The rendered candidate dimensions instead use `device_scale_factor`.
The exact exported context should include component, token, font and asset details
needed to implement the case; this version does not automatically collect a live
Figma file or package separately referenced fonts/assets.

Before any paid stage, the runner validates the declared inventory and copies the
references to `.autocode/design-inputs/<manifest-hash>/` in the new task workspace.
This also retains ignored inputs or bundles outside the repository when a task gets
an isolated worktree. Deleting the original exports later does not invalidate this
retained copy. Changed retained inputs pause execution as `PAUSED_DESIGN_REFERENCE`.
The ordinary `--figma-manifest` flag remains a new-run input. Use the audited stopped-run
revision command below to propose a new v2 inventory.

Planning carries the full inventory and its hash; Builder and Tester receive their
relevant slices with the original inventory retrievable. The
independent Validator reports `design_manifest_hash` and `design_results`, one row
per case, with `id`, `status` (PASS/FAIL/NOT_VERIFIED), `criterion_ids`,
`candidate_ref`, `comparison_ref`, `capture_ref` and `capture_sha256`.
A passing row needs passing approved criteria,
a PNG candidate at the declared viewport/device scale, and a separate nonempty
comparison artifact inside the task workspace. The reference itself cannot be cited
as the rendered candidate. `capture_ref` identifies a current
[browser capture manifest](visual-captures.md), and `capture_sha256` pins that
manifest. Its candidate must be the exact image in the review. The runner pins
the capture inputs and artifacts alongside the independent check evidence and
rechecks them at completion. FAIL/NOT_VERIFIED rows may leave the evidence
fields empty when acquisition is unavailable.

Intermediate milestones can explicitly leave future cases NOT_VERIFIED. Whole-task
completion requires every case PASS in the same current independent validation;
omissions, duplicates, stale manifests, changed evidence or wrong dimensions refuse
completion. Existing contract, regression, replay and human acceptance gates remain
mandatory. `--status` adds `view.design`: inventory/hash, reported source revision
and cases without a reported PASS. It deliberately leaves
`current_visual_acceptance` unknown: the status projection alone authenticates no
current source or screenshot.

Coverage and capture provenance do not guarantee pixel fidelity. A v1 declaration cannot discover
an omitted frame; v2 detects omissions against complete retained source receipts.
Neither determines whether a comparison artifact's conclusion is visually correct.
Live multi-file connector qualification is a separate acceptance check for #250; image comparison and independent visual adjudication remain
in issue #251. Capture freshness is checked separately from those judgments.
The offline provider tests use synthetic images to exercise completion gates;
the optional Chromium tests exercise real capture acquisition. Neither performs
a live Figma/model review.


### Native connected-file intake

```sh
autocode "Implement the approved workspace" --engine codex \
  --figma-file "https://www.figma.com/design/FILEA?node-id=1-2" \
  --figma-additional-file "https://www.figma.com/design/FILEB"
```

Repeat `--figma-additional-file` for each additional approved file. The existing
single controller begins with **Design inventory** on the saved Requirements model
route, before Requirements/Planner approval. This collector never authorizes
implementation. It must retain all approved files, pages, frames, states and
resources, use the read-only collector and pass the v2 loader. The source may be a
screen or library component URL; the supplied starting node must be present.
Unreadable nodes/pages, missing remote component definitions or conflicts pause as
`PAUSED_DESIGN_INPUT` with the concrete reason. Resume the same run after the input
is available. No alternate model or transport is selected. Native authentication
requirements remain; exported bundles work with either supported engine.

A supplied complete v2 export can satisfy intake directly. It must include every
native URL's file and node. Extra explicitly supplied export files remain part of
the approved inventory. A v1 export remains valid for older exported workflows,
but a new native intake must gather the complete v2 inventory before planning.

### Reviewed reference changes

At a stopped, reconciled boundary, inspect `--status`, then propose the replacement:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run \
  --revise-figma-manifest /path/to/updated/manifest-v2.json \
  --expected-design-hash INSPECTED_CURRENT_HASH \
  --design-change-reason "The approved Settings screen gained a validation state"
```

`TaskRun.revise_design(manifest, expected_hash, reason)` exposes the same operation.
The command retains both versions, approval/completion history, the prior independent
report and a case-level added/removed/changed/unchanged audit. It pauses at
`PAUSED_DESIGN_INPUT_CHANGED`; it neither launches a model nor approves a new plan.
Resume for the existing independent planning exchange and explicit plan approval.
Models, pins and limits stay saved. An active/uncertain attempt or pending artifact
review must be reconciled first; changing the reference cannot bypass that boundary.

Case identity binds its exact screenshot/context, route/state/viewport, linked
resources, original node/variant properties and responsive targets. Only affected
reference cases lose applicable historical evidence. Unchanged case results are
reusable only when the original independent report, comparison and collector-owned
capture are hash-bound and still match the current source/browser/build inputs.
They remain historical results, never newly generated PASS verdicts. Whole-task
acceptance still requires a current independent report covering every case and the
updated approved plan. A partial passing case or functional PASS cannot complete it.

The offline catalog scenario `figma-complete-inventory` accepts a sound two-file
bundle and rejects an omitted unique frame and an omitted component variant while
its functional tests stay green. Public CLI tests cover native acquisition,
restart, plan refusal, reference revision and completion gates with a scripted
provider. Those receipts qualify the protocol; they do not qualify the live Figma
connector or certify pixel fidelity. Run live connector qualification only against
explicitly approved files and with separately authorized model spend.

An audited native reference revision also records the previous and current file URLs.
Removed entry nodes fall back to the current file root; replacement files appear in the
reviewed input list. Engine, authentication, role routes, pins and limits stay unchanged.
Identical relocated reference bytes keep their case identity; file names alone do not
require fresh visual proof. Shared token aliases are reconciled by library key while
original file-local IDs remain in the retained source receipts.
