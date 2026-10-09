# Current implementation capture bundles

[← Figma implementation](figma.md)

Visual acceptance in runs using `--figma-file`, an accepted UI handoff, or
`--figma-manifest` needs implementation images bound to the current workspace.
A PNG from an earlier build cannot establish acceptance for a later build, even
when a reviewer gives its report the latest source revision.

Run from the task workspace after the implementation and capture fixture are ready:

```sh
autocode visual-capture --config capture.json --timeout 120
```

The command uses Node and the project's existing Playwright Chromium installation.
It does not call a model or install dependencies. The project supplies a fixture
that starts its server, selects the requested state and tears down resources.
The collector owns the fresh browser context and screenshot operation. Worker
permissions still apply; a denied browser/server operation must be addressed
before visual acceptance can proceed.

Copy `reference_hash` from `implementation_captures` in the stage context. For an
exported inventory, copy the exact case ID, route, state and viewport too. For a
native Figma URL, use stable case IDs for the approved visual requirements.
Prepare all fixtures and separate config files for each case before capturing;
editing a shared config for the next case would invalidate the previous capture.
Write those configs, any fixture created for the review, and comparison/review
artifacts under `.autocode/evidence/` so they do not change the captured source
snapshot, and leave existing capture bundles intact. A later repair re-verifies
every file a report cites, so under `.autocode/` the runner accepts citations only
from `.autocode/evidence/`, the run directory, capture bundles (a manifest and the
artifacts it lists, for the run's design reference) and the retained design inputs.
A Validator report citing anything else there is rejected when it is accepted.

```json
{
  "reference_hash": "<64-character reference hash from the stage context>",
  "case": {
    "id": "workspace.desktop.empty",
    "route": "/workspace",
    "state": "empty conversation",
    "viewport": {"width": 1440, "height": 900, "device_scale_factor": 1}
  },
  "fixture": "tests/capture-workspace.cjs",
  "inputs": [],
  "assets": [{"url": "/workspace", "path": "index.html"}],
  "ready": [{"selector": "body", "attribute": "data-state", "equals": "empty"}],
  "build_command": []
}
```

Here is a minimal fixture for a static `index.html` with `data-state="empty"` on
its body. Application fixtures should establish the actual approved state and
await its readiness before returning from `setup`.

```js
const http = require('node:http');
const fs = require('node:fs');
let server;
exports.setup = async ({page}) => {
  server = http.createServer((req, res) => {
    if (req.url !== '/workspace') {
      res.writeHead(404); res.end(); return;
    }
    res.writeHead(200, {'content-type': 'text/html'});
    res.end(fs.readFileSync('index.html'));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  await page.goto(`http://127.0.0.1:${server.address().port}/workspace`);
};
exports.teardown = async () => {
  if (server) {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
};
```

Declare every loaded document, stylesheet, script, image, font and data response
in `assets`, with an origin-relative URL (including its query string) or an exact
HTTP(S) URL and the workspace file whose bytes it serves. The collector hashes
the response bodies actually received by the browser. A separate HTTP fetch
cannot substitute for those observations. Redirects, failed requests, undeclared
responses and mismatched bytes refuse capture. Pending requests at readiness or
new requests afterward also refuse capture; stop polling and await fixture data
before `setup` returns. Service workers are blocked in the fresh browser context.

Use a production build/preview for dev servers that transform files in transit.
Set `build_command` to its argv, for example `["npm", "run", "build"]`, and point
the fixture and assets to those outputs. Ignored/generated outputs require a
successful build during this capture operation. Its command, result, source
revision and output hashes are retained. Keep generated outputs Git-ignored so
the build does not change the source snapshot. Files named in `inputs` add exact
hashes for other dependencies, including ignored configuration. All file paths
must be normalized paths relative to the workspace; symlink inputs are refused.
Optional `playwright_module` and `executable` select existing local installations.

Successful output contains `capture_ref`, `capture_sha256`, `candidate_ref` and
`visual_acceptance: null`. Each attempt gets a new directory under
`.autocode/captures/`. The manifest records source, reference, fixture, collector,
input and artifact identities, readiness, route and viewport. Changes during
capture refuse publication. Failures retain their available logs and artifacts;
previous captures are never overwritten. Do not commit these generated bundles.

Stage contexts explicitly list current bundles, rejected captures with reasons,
and unavailable inventory cases. The independent reviewer opens those candidates
and compares them with the design. Exported-inventory reports copy the receipt
into each `design_results` row alongside `candidate_ref` and `comparison_ref`.
Native Figma reports use `implementation_captures` entries containing only
`capture_ref` and `capture_sha256`, and cite their candidate images in criterion
evidence. Partial nonvisual milestones can leave this array empty; whole-task
visual acceptance cannot. An additional historical image in passing criterion
evidence cannot be hidden beside a fresh receipt.

Report decoding and completion recheck provenance and pinned evidence. Source
changes after capture, missing manifests, changed images/fixtures/build outputs,
wrong reference/case/state/viewport and substituted candidates refuse acceptance.
Older runs with unbound screenshots need fresh captures and independent review.
Freshness never changes a FAIL or NOT_VERIFIED judgment into PASS.

This protocol assumes the project's fixture, build command and local tools are
honest. It detects stale or inconsistent evidence; it is not an attestation
against a malicious process fabricating every input and receipt. A native Figma
reference hash binds the supplied URL, not the remote file's current contents;
reviewers must still inspect that design. Selecting all required states and
judging visual fidelity remain independent obligations.

## Verification without model spend

The regular suite includes scripted-provider completion tests and provenance
guards. To also exercise real Chromium acquisition with an installed browser:

```sh
AUTOCODE_TEST_PLAYWRIGHT=/absolute/path/to/node_modules/playwright \
AUTOCODE_TEST_CHROMIUM=/absolute/path/to/chromium \
  .venv/bin/python -m unittest -v tests.test_visual_capture
```

Omit `AUTOCODE_TEST_CHROMIUM` to use Playwright's default installed browser.
Set `AUTOCODE_TEST_CAPTURE_EVIDENCE` to a fresh ignored directory to retain the
disposable browser test projects. Without the Playwright opt-in, these real
browser tests are explicitly skipped; offline guard tests still run. These
checks do not establish live Figma fidelity or model review quality.
