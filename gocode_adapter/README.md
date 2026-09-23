# Autocode GoCode Adapter

This package runs a pinned, unchanged `charlieanna/autocode` checkout while
substituting GoCode for its provider transport. Autocode still owns its workflow,
prompts, schemas, gates, retries, state, dashboard handlers, UI, and storage.

## Install and pin upstream

```sh
python3 -m venv .venv
.venv/bin/pip install /path/to/gocode_adapter
.venv/bin/autocode-gocode sync \
  --upstream /path/to/clean/autocode-clone \
  --checkout /path/to/pinned-autocode \
  --record /path/to/adapter-state/pin.json
```

`sync` fetches `origin/master`, verifies the versioned structural seams, and
creates or reuses a clean detached worktree. It never changes upstream source or
its remotes. The old sync-only invocation without the `sync` word remains
supported.

GoCode must be in managed mode with a current broker login:

```sh
gocode mode managed
gocode auth login
```

## Run from any project folder

```sh
/path/to/.venv/bin/autocode-gocode run \
  --checkout /path/to/pinned-autocode -- \
  "Describe the task" --workspace "$PWD"
```

Resume with the same command and Autocode's normal run directory:

```sh
/path/to/.venv/bin/autocode-gocode run \
  --checkout /path/to/pinned-autocode -- \
  --run-dir /absolute/path/to/.autocode/runs/RUN
```

Launch the original dashboard through the same pin and transport:

```sh
/path/to/.venv/bin/autocode-gocode dashboard \
  --checkout /path/to/pinned-autocode -- \
  --workspace "$PWD" --port 8765
```

The default routes are Sol medium for `glm`, Sol high for the legacy `astra`
role, Terra medium for `terra`, and Sol high for `sol` and completion. The legacy
terminal alias `openai/gpt-6-astra` resolves to the manifest-pinned GoCode Claude
Opus 5 shim; it never invokes Anthropic or OpenCode directly.

## Boundaries

The compatibility manifest pins the GoCode shim, its delegated native binary,
the canonical Codex script, and the Claude shim by path and SHA-256. Authentication,
endpoint, bearer-route fingerprint, and every executable identity are rechecked
before launch. Credentials are injected only into the child environment and are
never written to launch descriptors, reports, or pin records.

macOS does not provide a supported atomic validate-and-exec operation for the
installed Node script. The adapter therefore executes canonical installed paths
after a final identity check. A malicious same-user replacement in the tiny
interval between that check and `exec` is outside the guarantee; this limitation
is explicit rather than hidden behind a non-working `/dev/fd` or copied-binary
scheme.

Planning conversations use the upstream-authored conversation prompt and a
managed Responses request with `tools: []`; they do not receive a repository or
model tools.
