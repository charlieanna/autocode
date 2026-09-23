# Autocode Provider Adapter

This package supplies the independently installable GoCode provider for
`charlieanna/autocode`. Autocode owns provider selection, workflow, prompts,
schemas, gates, retries, state, dashboard handlers, UI, and storage.

## Install and pin upstream

```sh
python3 -m venv .venv
.venv/bin/pip install /path/to/autocode_provider_adapter
.venv/bin/autocode-provider sync \
  --upstream /path/to/clean/autocode-clone \
  --checkout /path/to/pinned-autocode \
  --record /path/to/adapter-state/pin.json
```

`sync` fetches `origin/master`, verifies the versioned structural seams, and
creates or reuses a clean detached worktree. It never changes upstream source or
its remotes. The old sync-only invocation without the `sync` word remains
supported.

The built-in provider is OpenCode. Select GoCode explicitly when needed. The
normal local installation uses the GoCode **unmanaged** certificate route; the
adapter accepts it only when GoCode reports both client service and inference
endpoint as reachable. It does not change your GoCode mode or login:

```sh
gocode status
```

## Run from any project folder

```sh
/path/to/.venv/bin/autocode-provider run \
  --checkout /path/to/pinned-autocode --record /path/to/adapter-state/pin.json -- \
  "Describe the task" --workspace "$PWD"

# Or select GoCode. The wrapper validates the pin, then forwards the provider
# choice to upstream Autocode's own --provider option.
/path/to/.venv/bin/autocode-provider run \
  --provider gocode --checkout /path/to/pinned-autocode --record /path/to/adapter-state/pin.json -- \
  "Describe the task" --workspace "$PWD"
```

Once this package is installed, the wrapper is optional:

```sh
python /path/to/pinned-autocode/tools/autocode.py --provider gocode \
  "Describe the task" --workspace "$PWD"
```

Resume with the same command and Autocode's normal run directory:

```sh
/path/to/.venv/bin/autocode-provider run \
  --checkout /path/to/pinned-autocode --record /path/to/adapter-state/pin.json -- \
  --run-dir /absolute/path/to/.autocode/runs/RUN
```

Launch the original dashboard through the same pin and transport:

```sh
/path/to/.venv/bin/autocode-provider dashboard \
  --checkout /path/to/pinned-autocode --record /path/to/adapter-state/pin.json -- \
  --workspace "$PWD" --port 8765
```

The default routes are Sol medium for `glm`, Sol high for the legacy `astra`
role, Terra medium for `terra`, and Sol high for `sol` and completion. The legacy
terminal alias `openai/gpt-6-astra` resolves to the manifest-pinned GoCode Claude
Opus 5 shim; it never invokes Anthropic or OpenCode directly.

Provider names other than `opencode` are plug-ins. Install a package named
`autocode-provider-<name>` which exports `create_provider()` to add a provider
such as KiloCode; upstream Autocode then accepts `--provider <name>` without
changing stages or role names. KiloCode has no bundled driver yet, so selecting
it fails before an agent launch until that plug-in is installed.

## Boundaries

The compatibility manifest pins the GoCode shim, its delegated native binary,
the canonical Codex script, and the Claude shim by path and SHA-256. Authentication
and route health, plus every executable identity, are rechecked before launch.
Credentials are never written to launch descriptors, reports, or pin records.

macOS does not provide a supported atomic validate-and-exec operation for the
installed Node script. The adapter therefore executes canonical installed paths
after a final identity check. A malicious same-user replacement in the tiny
interval between that check and `exec` is outside the guarantee; this limitation
is explicit rather than hidden behind a non-working `/dev/fd` or copied-binary
scheme.

Planning conversations use the upstream-authored conversation prompt and a
GoCode-routed Responses request with `tools: []`; they do not receive a
repository or model tools.
