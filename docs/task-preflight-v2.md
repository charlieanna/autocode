# Worker and design prerequisites

Start with `--task-preflight qualification/preflight.json`. Version 2 retains
the [basic manifest](task-preflight.md)'s `inputs`, `checks`, phases, recovery
and resume flow. Every check adds `execution: "runner"` or `"worker"` and
`timeout_seconds` (positive finite seconds, default 120). Runtime files may
also be project-relative. Check arguments and fixtures are operator-approved
inputs, never commands chosen by a model.

```json
{
  "version": 2,
  "inputs": [],
  "runtime_files": [],
  "checks": [{
    "id": "named-imports", "phase": "planning", "execution": "runner",
    "argv": ["{python}", "{runtime}/autocode_preflight_unittest.py", "--named", "tests.test_example"],
    "contract": {"kind": "command"}, "timeout_seconds": 120,
    "recovery": "Correct the named import while preserving the test, then resume"
  }]
}
```

Admission runs at the provider launch boundary, including parallel workers
and report repairs. Worker probes use the actual selected executable, role,
model, sandbox and scrubbed child environment in both the workspace and its
nested source copy. Native Codex uses its model-free `sandbox` helper with the
selected read-only/workspace-write policy. OpenCode uses its actual agent and
`debug agent --tool`; effective permissions are checked first because that
diagnostic path auto-allows `ask`. Unresolved approvals block admission; the
runner never grants permissions. Read-only planning can check design files
with `read` even when `bash` is denied. OpenCode tool permissions are not an OS
sandbox. Command providers currently fail closed for required
worker probes: they have no supported model-free adapter. Runner checks cannot
substitute for a required worker check. No probe falls back to a model call.

Version 2 checks poll pause requests and clean up their process group. Reuse
requires unchanged bindings, including the worker, approved plan, command,
runtime helper sources, inputs, packages and configuration. Set `reuse: false`
for capabilities whose availability can change without a file change.

## Browser and deterministic fixture setup

Add a check like this to `checks`:

```json
{
  "id": "browser-setup", "phase": "build", "execution": "worker",
  "reuse": false, "timeout_seconds": 120,
  "argv": ["node", "{runtime}/autocode_preflight_browser.cjs", "qualification/browser.json"],
  "contract": {
    "kind": "browser",
    "viewport": {"width": 652, "height": 188, "device_scale_factor": 1},
    "canvas": {"alpha": "composite", "background": "#ffffff"},
    "fonts": ["Inter"], "ready": ["catalogue-completed", "catalogue-nonempty"]
  },
  "recovery": "Restore the approved browser/fonts or correct fixture initialization, then resume"
}
```

The browser configuration supplies the same `viewport`/`canvas`, plus
`fixture: "qualification/browser-fixture.cjs"`,
`fonts: [{"family":"Inter","path":"qualification/Inter-Regular.woff2"}]`
and these explicit `ready` conditions:

```json
[
  {"id":"catalogue-completed", "selector":"#catalogue", "attribute":"data-complete", "equals":"true"},
  {"id":"catalogue-nonempty", "selector":"#catalogue", "attribute":"data-nonempty", "equals":"true"}
]
```

The fixture exports `async setup({page, context})` and
`async teardown({context})`. Initialize the approved deterministic state in
setup and release servers/resources in teardown, including on failure. The
readiness attributes must reflect completed fixture/application state; do not
set them just to satisfy a probe. Tests of a later unavailable state must start
from the completed nonempty catalogue. This cannot prove every test race-free.

The helper uses the project's installed Playwright (`playwright_module` can
select an approved installed package), launches Chromium, loads the actual
font bytes, checks viewport/DPR, captures PNG bytes at the expected dimensions,
then closes the context/browser. `executable` can select an approved browser;
none is installed automatically. `readiness_timeout_ms` defaults to 10000
inside the outer deadline. Zero exit alone is insufficient: one structured
`AUTOCODE_PREREQUISITE={...}` result must match every contract field and confirm
setup, launch, capture and teardown. Declare fixture/config/font files in
`inputs` and external browser/package identities in `runtime_files`.
Add a distinct check with `phase: "validate"` when visual review also needs it.

## Figma inputs and readable context

A version 2 visual task requires `design` alongside `checks`. Store the
approved [export manifest](figma.md) and its files in a non-ignored public
directory. Every declared file/frame/state needs one readiness case:

```json
{
  "manifest": "qualification/design/manifest.json",
  "cases": [{
    "id": "composer.empty", "encoding": "mcp-text",
    "context_parts": ["qualification/readable/part-0000.txt"],
    "assets": ["qualification/send.svg"],
    "fonts": [{"family":"Inter", "path":"qualification/Inter-Regular.woff2"}],
    "canvas": {"alpha":"composite", "background":"#ffffff"}
  }]
}
```

Include the manifest, screenshots, raw context, every context part, asset and
font in `inputs` with approved hash/size/mode. The public manifest must match
the run's retained design manifest and any explicit Figma file/node URL. Both
source roots are checked. Missing exports, omitted frames, altered references
and inputs lost from the source copy block dispatch. Remote Figma access is
not inferred from a URL or login: export the approved references first.
Preflight never edits the Figma source or substitutes implementation captures.

Before approving the prerequisite manifest, split long escaped context into
lossless parts that provider read tools can display completely:

```sh
python tools/autocode_preflight_design.py qualification/design/context.json \
  --encoding mcp-text --output qualification/readable
```

Encodings are `text`, `json-string`, or `mcp-text` (ordered MCP text blocks).
Concatenated parts must equal the complete decoded source exactly; each part
is at most 1600 UTF-8 bytes. They are not summaries. The gate records raw and
decoded hashes, actually reads parts and PNG exports through the worker, and
gives every stage the ordered paths and provenance. A matching worker browser
contract must declare the viewport/DPR, canvas and fonts; its font receipt
must identify the exact approved files. `alpha: "preserve"` requires
`background: null`; `composite` requires an explicit six-digit color.
These are capture prerequisites, never visual fidelity approval.

## Named proof eligibility

Use `contract.kind: "proof"`, an explicit `test_changes_allowed` boolean and
cases such as
`[{"id":"C1","selector":"test_product.T.test_c1","kind":"fail_to_pass"}]`.
Kinds are `fail_to_pass` and `preserve`; selectors carry the criterion ID using
the same naming rule as the proof engine. Approved plan changes invalidate
readiness and must match the case bindings.

The bundled unittest helper's arguments are
`["{python}","{runtime}/autocode_preflight_proof.py","--inventory","qualification/controls.json"]`.
The inventory has `selectors` and
`controls: {"baseline":"qualification/base","reference":"qualification/reference","broken":"qualification/broken"}`.
Each directory contains its approved test and corresponding implementation.
Each runs in a fresh interpreter in both source contexts, including unittest
setup/teardown. Keep the fixture sources public and hash-bound.

Every named case must pass the reference and fail its broken control.
Fail-to-pass cases must fail the baseline; preserve cases must pass it.
ERROR, SKIP, aggregate-only output, omitted selectors and teardown failures
block readiness. Fail-to-pass proof requires added/changed tests, so a policy
forbidding all test changes blocks that incompatible contract before dispatch.
Obtain an approved correction rather than relabelling a failed run, removing
protected tests or weakening a proof marker. Fixture controls qualify the
proof interface; the final candidate still needs fresh independent proof.
