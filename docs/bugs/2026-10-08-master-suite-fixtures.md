# Master suite fixtures after admission and Compose changes

Master run [37766122806](https://github.com/charlieanna/autocode/actions/runs/37766122806)
failed five tests across two modules. The failures came from test fixtures that
still described the earlier contracts, rather than failed cleanup or weaker
production admission.

The failed-smoke branch of `tests.test_multicomponent` sliced the recorded Docker
command before the newly pinned Compose file. The CLI test now checks the whole
cleanup command: the local endpoint, the second invocation's project and owned
manifest, all teardown flags, and successful cleanup.

Four `tests.test_catalogue_t12` cases reuse one lifecycle browser result. Its
disposable fixture depended on the host's OpenCode installation and omitted
current conversation-profile models. The browser assertions also expected raw
catalogue errors, which the dashboard now sanitizes. The fixture supplies a
canned external transport probe and derives its catalogue from the production
profile. The production readiness checks still run. Browser coverage retains
loading, empty, failed and recovered states at three viewports, and also checks
missing/unsupported transport and hidden provider diagnostics.

The broader dashboard gate exposed three more stale positive fixtures. Two
model-preservation tests narrowed the catalogue below the full required profile;
they now use the existing complete fixture. The sidebar harness now loads the
production-derived catalogue and transport signal through the real form-readiness
path before submitting. Model preservation, restart, attachment and project-scope
assertions remain in place.

Track the repair in [#759](https://github.com/charlieanna/autocode/issues/759).
The broader component and cloud work remains in #57 and #668.
