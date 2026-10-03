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

See also: [Models and escalation](models.md) · [Execution and completion](execution.md)

## Multi-file reference inventory (opt-in)

New implementation runs can receive an exported reference bundle through
`--figma-manifest /absolute/path/to/bundle/manifest.json`. This input works with
Codex and OpenCode and preserves the selected role routes, pins and limits.
It does not change the native `--figma-file` authentication requirements. When
combined with native Figma input, that file must also be declared in the manifest.

Version 1 declares every approved file and frame separately from its implementation
cases. Every declared frame needs at least one case; states and responsive viewports
use distinct stable case IDs. For example:

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
Saved runs cannot replace or add a manifest; start a new run for a changed inventory.

Planning and execution contexts carry the full inventory and its hash. The
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

Coverage and capture provenance do not guarantee pixel fidelity. They cannot detect
a file/frame absent from the supplied inventory or determine whether a comparison
artifact's conclusion is visually correct. Automatic discovery and plan coverage
remain in issue #250; image comparison and independent visual adjudication remain
in issue #251. Capture freshness is checked separately from those judgments.
The offline provider tests use synthetic images to exercise completion gates;
the optional Chromium tests exercise real capture acquisition. Neither performs
a live Figma/model review.
