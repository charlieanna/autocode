
IMPLEMENTATION CAPTURE FRESHNESS
Use implementation_captures.current as the explicit image bundle for visual review.
Missing/stale bundles require a fresh `visual-capture --config <project config>`.
The command records exact source, fixture, inputs, loaded response bytes and viewport;
capture under .autocode/captures is immutable. Keep historical bundles and failures.
The config copies reference_hash and source_paths from this packet, and declares case (id/route/state/viewport),
fixture (a project Playwright setup/teardown module), inputs, assets (URL/path pairs),
ready (selector/attribute/equals conditions), and build_command (argv, or [] for source assets).
The fixture establishes the actual page state. All browser responses must match declared
local assets. Generated assets require a build in this capture operation. Use the task's
existing Playwright/browser installation; respect worker permissions and readiness failures.
Prepare all fixtures and separate per-case configs before the first capture; keep their
inputs unchanged across the review. Write those configs, any fixture you create and every
comparison/review artifact under .autocode/evidence/ so they do not modify captured source.
A later repair re-verifies each cited file: under .autocode/ the runner accepts only that
directory, the run directory, capture bundles and retained design inputs. Never edit an existing capture bundle.
For design_results copy capture_ref and capture_sha256 with the exact candidate_ref.
For native Figma without an inventory report implementation_captures receipts and cite
their candidate images in criterion evidence. Inspect those images independently against
the design. Freshness and hashes establish provenance only; never infer visual PASS from them.
